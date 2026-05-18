# advance 実装 ToDo

`document/specs/advance.md` と現状実装を比較して、足りない部分を実装順に並べたメモです。

## 優先度 P0

1. `advance` の責務を「1回の起動で 1 task に対して最大 1 ステップ」に固定する。現状は `while` で同じ task を続けて処理しうるため、cron から叩いたときに複数回の副作用が起きる設計になっている。

2. 対象 task の選び方を現行実装どおりに固定する。`sync` 後の state から `awaiting_plan_approval` / `awaiting_user_feedback` / `pr_created` を候補にし、`updated_at` が最新の 1 件だけを処理対象にする。

3. `cron` サブコマンドを追加し、完全非対話・完全自動運用専用の入口にする。cron からの呼び出しは `cron` を使い、TTY がないことを前提に人間確認が必要な状態では exit 0 で終了する。

4. `advance` の役割を「対話可能な一般用途コマンド」として確定する。`advance` は設定された auto フラグの範囲で処理を進め、TTY があるときだけ対話にフォールバックする。

5. `--auto`, `--auto-plan-approval`, `--auto-feedback`, `--auto-merge` の意味と優先順位を確定し、`Config` と CLI の両方に反映する。現状は CLI フラグだけで、設定ファイルから自動化レベルを指定できない。

6. 冪等性を担保するための state 項目を追加する。最低でも `last_activity_id`, `last_feedback_hash`, `last_advanced_at`, `last_advance_action`, `advance_attempts` を持たせ、同じ approve / send / merge を二重実行しないようにする。

7. ロックと exit code を実装する。プロジェクト単位の lock file を取り、重複起動時は何もせず終了できるようにする。あわせて `0 / 1 / 2` の終了コード方針を決めて実装する。

## 優先度 P1

8. 状態遷移表を実装に落とす。`awaiting_plan_approval`, `awaiting_user_feedback`, `pr_created`, `merged`, `pr_closed`, `failed`, `blocked` の各状態で、`advance` と `cron` が何をして、どこに遷移するかを明文化してコードに反映する。

9. 失敗時の扱いを分ける。`failed` と `blocked` を分離し、Jules API エラー、GitHub API エラー、Codex 失敗、JSON parse 失敗、ネットワーク断、rate limit、merge conflict などを同じ失敗として扱わないようにする。

10. PR merge 条件を仕様どおりに絞る。現状は `mergeable` だけを見ているので、draft PR の除外、CI / required checks の扱い、fork PR の扱い、repo 一致確認、merge 対象の制限を整理してから実装する。

11. `advance` と `cron` の副作用ログを整理する。実行した `run_id`, `task_id`, `action`, `previous_status`, `next_status` を追跡できるようにし、何もしなかった理由は通常出力ではなく必要な場合だけ出すようにする。

12. `advance` の完了条件を再定義する。現状は task の `updated_at` を更新して保存するだけなので、アクション成功後に再 sync して state を確定する流れを、状態遷移と合わせて整理する。

## 優先度 P2

13. `--json` 出力を追加する。cron や外部オーケストレーションから読めるように、`status`, `action`, `run_id`, `task_id`, `previous_status`, `next_status` を返せるようにする。

14. `dry-run` を検討する。実際の approve / send / merge を行わず、実行予定の action だけ確認できるモードがあると、cron 化前の検証がしやすい。

15. 必要最小限の回帰テストを追加する。深い内部実装テストではなく、CLI フラグ解釈、1 ステップ制御、対象 task 選択、auto 系の分岐、lock / exit code のような壊れやすい境界だけを押さえる。
