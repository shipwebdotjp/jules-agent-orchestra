# サブコマンドを整理して、外部から cron などで定期的に叩けばステータスに応じて必要な処理を進めていくモードを advance にしたい。今後、決めるべきこととして仕様として何を詰めていったらいいだろうか？

advance を「cron などから定期実行され、現在の状態に応じて次の必要処理を進めるサブコマンド」にするなら、まず仕様として詰めるべき点は大きく以下です。

1. advance の責務範囲

advance は state を同期し、対象 task の現在 status に応じて、設定で許可された自動処理を1タスク当たり最大1ステップ実行する。

------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

2. 1回の実行で「1ステップだけ進める」か「進められるだけ進める」か

1タスク当たり最大1ステップ実行。

------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

3. コマンドの役割分担と自動化レベルの仕様

advance は手元実行向けの一般用途コマンドで、設定された自動化フラグの範囲で処理を進める。
自動処理だけで進められない状態になった場合は、TTY があるときに限って対話で継続する。
TTY がない環境では対話に入らず、その時点で終了する。

cron は完全自動運用専用コマンドで、定期実行から呼び出すことを前提にする。
cron は対話を行わず、必要な人間判断が発生した時点で何もせず exit 0 で終了する。
cron は内部的には `sync` と同じ状態同期ロジックを使い、`--auto` を指定した場合と同等に、許可された自動処理をすべて実行する。

既に `advance` に `--auto`, `--auto-plan-approval`, `--auto-feedback`, `--auto-merge` があるようなので、それぞれの意味を厳密にした方がよいです。

--auto

--auto は何を含むのか。
• --auto-plan-approval --auto-feedback --auto-merge 全部の alias

--auto-plan-approval

Jules が plan approval 待ちのときに自動承認するか。

--auto-feedback

Codex などで feedback を生成して Jules に送るか。

--auto-merge

PR が作成され、mergeable なら GitHub API で merge するか。

------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

4. 状態遷移モデル

advance の仕様を固めるには、Task/Run の状態遷移を明文化する必要があります。

例えば Task status として、現在概要から見える範囲では:

• pending
• running
• awaiting_plan_approval
• awaiting_user_feedback
• pr_created
• merged
• completed
• failed

のようなものがありそうです。

詰めるべきこと:

status A のとき advance は何をするか
status B に遷移する条件は何か
外部APIの状態とローカル state が矛盾したらどちらを優先するか

表にするとよいです。

例:

Task status             外部状態            advance の処理      自動化条件               次状態
───────────────────────────────────────────────────────────────────────────────────────────────────
running                 Jules 実行中        sync のみ           常に                     running
awaiting_plan_approval  Plan approval 待ち  approve_plan        auto_plan_approval=true  running
awaiting_user_feedback  Jules から応答待ち  feedback 生成/送信  auto_feedback=true       running
pr_created              PR open             PR 状態確認         常に                     pr_created
pr_created              PR mergeable        merge               auto_merge=true          merged
merged                  merged 確認済み     complete            常に                     completed
failed                  -                   何もしない          -                        failed

5. 対象 task の選び方

advance は、`sync` 後の state に含まれる全 task から、`awaiting_plan_approval` / `awaiting_user_feedback` / `pr_created` のいずれかにある task を対象候補とする。

  対象候補が複数ある場合は、`updated_at` が最も新しい 1 件のみを処理対象として選ぶ。

  `updated_at` が同一の task が複数ある場合は、state 内を走査した順で最初に見つかった task を選ぶ。

  対象候補が存在しない場合、advance は何もせず正常終了する。

------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

6. 冪等性(ここは要検討)

cron 実行では最重要です。

同じ advance が何度実行されても、同じ message を何度も送ったり、approve を二重実行したり、merge を二重実行しない必要があります。

決めるべきこと:

• 最後に処理した Jules activity id を保存するか
• 最後に送信した feedback の hash を保存するか
• approve 済み判定をどこで行うか
• merge 済み PR に再 merge を試みた場合の扱い
• state 保存前に落ちた場合の再実行挙動

追加したい state 項目例:

last_activity_id
last_feedback_hash
last_advanced_at
last_advance_action
advance_attempts

7. ロック・同時実行制御

cron が重複起動することがあります。

決めるべきこと:

• .jules-agent/lock のような lock file を使う
• lock timeout を設ける
• ロック取得失敗時の exit code は 0
• state 書き込みの atomicity

おすすめ:

advance はデフォルトでプロジェクト単位のロックを取得する。
既に実行中なら何もせず exit code 0 で終了する。

8. exit code の仕様

• 0: 正常
• 1: 致命的エラー
• 2: 一時的エラー

9. ログ出力・機械可読出力

cron ではログが重要です。

• 通常出力の形式
• --json 対応 (v1以降)
• 実行した run_id, task_id, action を出す
• 何もしなかった理由は出さない
• API エラー詳細をどこまで出すか

jules-agent advance --json

出力例:

{
  "status": "ok",
  "action": "approve_plan",
  "run_id": "RUN-001",
  "task_id": "TASK-001",
  "previous_status": "awaiting_plan_approval",
  "next_status": "running"
}

人間向けには:

TASK-001: awaiting_plan_approval -> approved plan

10. 設定ファイルとの関係

Config があるので、cron 運用では CLI option だけでなく設定ファイルに寄せた方がよいです。

• auto_plan_approval
• auto_feedback
• auto_merge
• merge_method
• github_token
• repo
• codex_bin
• base_url

CLI option が config を上書きする。

11. 失敗時の扱い

自動運用では、失敗をどう state に反映するかが重要です。

• Jules API エラー時
  • state は変更しない
  • attempts を増やす
  • 一定回数で blocked にする
• GitHub API エラー時
• Codex 実行失敗時
• JSON parse 失敗時
• network timeout
• rate limit
• merge conflict
• PR checks pending

失敗状態は failed と blocked を分けるとよいです。

例:

• failed: ツールの実行失敗や復旧困難な状態
• blocked: 人間の判断が必要
• waiting: 外部処理待ち

1.  人間の介入ポイント

完全自動化しない場合、どこで止めるか。

• plan approval は auto_plan_approval=false なら人間確認
• feedback は auto_feedback=false なら自動生成するが送信前に止める
• PR merge は auto_merge=false なら人間確認

13. sync と advance の関係

advance は開始時に必ず sync を行う。
各アクション後は必要に応じて再 sync する。

14. next との関係

next が現在どのような意味かにもよりますが、サブコマンド整理の観点では役割を分けるべきです。

• next: 次の未 dispatch タスクを dispatch する。
• advance: 状態に応じて副作用ありの処理を実行する。
• status: 現在状態の表示。
• sync: 外部状態の取り込みのみ。

この整理はわかりやすいです。

15. PR merge 条件

auto-merge を入れるならかなり重要です。

• mergeable == true だけでよいか
• CI check 成功を確認しない
• required checks を見ない
• draft PR は除外する
• base branch の制限なし
• fork PR は対象外にする
• labels による制御はしない
• Jules が作った PR だけ merge する

16. 状態ファイルのスキーマ拡張

cron 向けに state を充実させるなら、追加検討したい項目です。

Task に追加候補:

last_synced_at
last_advanced_at
last_advance_action
last_error

17. dry-run

v1以降で検討

18. cron 運用例

最終的にこういうユースケースを想定すると仕様が固めやすいです。

*/10 * * * * cd /repo && jules-agent advance --auto-plan-approval --auto-feedback >> logs/jules-agent.log 2>&1

または merge まで:

*/10 * * * * cd /repo && jules-agent advance --auto-plan-approval --auto-feedback --auto-merge >> logs/jules-agent.log 2>&1

このとき必要なのは:

• 非対話
• exit code が安定
• ロックあり
• 冪等
• ログが十分
• 設定ファイルだけで動く

個人的なおすすめ仕様

最初の実装方針としては、以下がよいと思います。

advance は「sync + 状態に応じた最大1アクション」を行う。
デフォルトでは危険な副作用はしない。
plan approval / feedback / merge は個別フラグまたは全体許可フラグで許可する。
cron 利用を前提に冪等・非対話・ロックありにする。

コマンド例:

jules-agent advance

• sync する
• 次に必要な手作業を表示
• 外部への副作用は最小限

jules-agent advance --auto-plan-approval --auto-feedback

• plan approval と feedback は自動
• merge はしない

jules-agent advance --auto-plan-approval --auto-feedback --auto-merge

• merge 条件を満たせば merge まで実行
