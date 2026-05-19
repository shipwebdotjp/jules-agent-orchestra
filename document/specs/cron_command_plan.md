# cron サブコマンド仕様策定

## Summary
- `cron` を完全非対話・定期実行専用の入口として追加する。
- `advance` は手元実行向けに残し、同じ中核ロジックを使いながら TTY がある場合だけ対話にフォールバックする。
- 1回の起動で処理する task は最大1件、実行する副作用も最大1ステップに固定する。
- 対象 task は `sync` 後の state から `awaiting_plan_approval` / `awaiting_user_feedback` / `pr_created` / `waiting_human_review` の最新 `updated_at` 1件を選ぶ。同値なら state 走査順を優先する。

## Key Changes
- CLI:
  - `jules-agent cron` を追加する。
  - `cron` は `--auto-plan-approval` /  `--auto-feedback` /  `--auto-merge`  / `--auto` / `--json` を受け取る。
  - `advance` にも同じ auto 解決と `--auto-merge`, `--json` を追加する。
  - `--auto` は plan approval と feedback のみを有効化し、merge は含めない。mergeを自動実行したい場合は`--auto-merge`が必要。
- Config:
  - 既存デフォルトは維持する。つまり `auto_plan_approval=true`, `auto_feedback=false`, `auto_merge=false`。
  - 優先順位は `config` → `--auto` → 個別CLIフラグ/`--no-*`。個別指定が最優先。
- Execution:
  - 開始時に必ず `sync` する。`cron` でも PR sync を含める。
  - `cron` は stdin/TTY を使わない。人間判断が必要な場合は何もせず exit `0`。
  - `advance` は自動処理できなければ、TTY がある場合だけ既存の対話 feedback flow に入る。TTY がなければ no-op exit `0`。
  - action 成功後は対象 task を再 sync し、state を保存する。
- State:
  - `Task` 直下に `advance_state` を追加する。
  - 持つ項目は `last_activity_id`, `last_feedback_hash`, `last_advanced_at`, `last_advance_action`, `advance_attempts`, `last_error`。
  - activities は `activity.id` を優先し、なければ `activity.name` を冪等キーにする。
  - 同じ `last_activity_id` + `last_advance_action` の approve/send は再実行しない。
  - feedback は送信本文の hash を保存し、同一 activity に同一本文を二重送信しない。
  - merge は GitHub PR の merged/open 状態確認を優先し、すでに merged なら state を `merged` に寄せて成功扱いにする。

## State Transitions
- `awaiting_plan_approval`:
  - `auto_plan_approval=true` かつ Codex が承認推奨なら `approve_plan` を1回実行する。
  - 承認非推奨、または auto disabled の場合、`cron` は no-op。`advance` は TTY があれば対話に入る。
- `awaiting_user_feedback`:
  - `auto_feedback=true` なら Codex suggestion を生成して1回送信する。
  - auto disabled の場合、`cron` は no-op。`advance` は TTY があれば対話に入る。
- `pr_created` / `waiting_human_review`:
  - `auto_merge=true` なら merge候補にする。
  - merge条件は open、not draft、未merge、`mergeable == true`、base/head repo一致、PR番号が state.project.repo のPRとして解決できること。
  - required checks / branch protection は GitHub merge API に委ね、APIが拒否したら exit `2`。
  - `auto_merge=false` の場合、`cron` は no-op。`advance` は TTY があれば確認して merge できる。
- `merged`, `pr_closed`, `failed`, `blocked`, その他進行中状態:
  - 副作用なし。`sync` の結果だけ反映する。
- exit code:
  - `0`: 正常、no-op、lock取得失敗、人間判断待ち。
  - `1`: 設定不備、state破損、必須情報欠落など致命的エラー。
  - `2`: Jules/GitHub/Codex/network/rate limit/merge API拒否など一時的または外部起因エラー。

## Locking And Output
- `advance` と `cron` はどちらも `.jules-agent/advance.lock` をプロジェクト単位で取得する。
- `flock` 相当の非ブロッキングロックを使い、取得できない場合は何もせず exit `0`。
- 通常出力は action 実行時だけ短く出す。no-op 理由は原則出さない。
- `--json` は常に1 JSON objectを出す。
- JSON 形は `status`, `action`, `run_id`, `task_id`, `previous_status`, `next_status`, `reason`, `exit_code` を持つ。
- `--dry-run` は今回は実装しない。将来追加できるよう action判定ロジックは副作用実行から分離する。

## Test Plan
- CLI parse smoke:
  - `cron` 追加、`--auto`, 個別 `--auto-*`,  `--json`。
- Auto解決:
  - config値、`--auto`、個別CLI指定、の優先順位。
- Selection:
  - 候補status、最新 `updated_at`、同値時の走査順。
- One-step:
  - 1起動で1 task、1 actionだけ実行されること。
- Noninteractive:
  - `cron` が対話に入らず no-op exit `0` になること。
- Idempotency:
  - 同一 activity の approve/send 二重実行防止。
  - 同一 feedback hash 二重送信防止。
  - merged済みPRの再merge防止。
- Merge guard:
  - draft、closed未merge、repo不一致、`mergeable != true` を mergeしない。
- Lock:
  - lock取得失敗時に exit `0` で副作用なし。
- README:
  - CLIコマンド、フラグ、config設定、cron例、GitHub権限を更新する。

## Assumptions
- `waiting_human_review` は今回から auto_merge 候補に含める。
- `--auto` は merge を含めない。merge は必ず `auto_merge=true` または `--auto-merge` が必要。
- config の既存デフォルトは互換性優先で維持する。
- CI/checks の明示取得は今回入れず、GitHub merge API の拒否を信頼する。
- 詳細な内部文言依存テストは作らず、壊れやすい境界だけを軽く押さえる。
