以下、**コード実装は含めず**、現時点の方針に合わせた仕様案としてまとめます。

---

# 仕様案: Jules Session 状態管理

## 1. 目的

`jules-agent` CLI から Jules にタスクを dispatch した後、その Jules Session を継続的に操作できるようにする。

対象となる操作は以下。

- Session 状態確認
- plan 承認
- Jules Session への追加メッセージ送信
- 完了後の Pull Request 情報取得
- 後続タスクの dispatch 判断

そのために、dispatch 時に取得した Jules Session ID をプロジェクトローカルに保存する。

---

# 2. 基本方針

## 2.1 Session ID はローカル state に保存する

Jules へ dispatch して Session が作成されたら、返却された Session 情報をローカルに保存する。

保存先は以下。

```text
<repo>/.jules-agent/state.json
```

以後、CLI はこの `state.json` に保存された Session ID を使って Jules API を操作する。

---

## 2.2 `sessions.list` は通常使用しない

CLI が管理対象とする Session は、CLI が dispatch して `state.json` に保存したものだけとする。

そのため、通常フローでは `sessions.list` を使って Session を探索しない。

使用する Jules API の基本方針は以下。

```text
dispatch時:
  sessions.create

状態更新:
  sessions.get

plan承認:
  sessions.approvePlan

追加指示:
  sessions.sendMessage
```

---

## 2.3 復旧機能は持たない

dispatch 成功後、Session 情報を `state.json` に保存する前に CLI がクラッシュした場合、その Session は CLI 管理対象外とする。

この仕様では以下を行わない。

- `sessions.list` による復旧
- Session title / prompt への `task_id` / `run_id` / `correlation_id` 埋め込み
- ローカル state 破損時の自動復元
- Jules Web UI で作成された Session の import
- UI 操作された Session の追跡

復旧や import は将来拡張の対象とし、初期仕様では扱わない。

---

# 3. 管理対象の範囲

## 3.1 CLI が管理する Session

CLI が管理するのは、以下の条件を満たす Session のみ。

```text
jules-agent CLI が dispatch した
かつ
<repo>/.jules-agent/state.json に保存されている
```

---

## 3.2 CLI が管理しない Session

以下の Session は CLI 管理対象外とする。

```text
- Jules Web UI から作成した Session
- 他のツールから作成した Session
- dispatch後にstate.jsonへ保存されなかった Session
- state.jsonから削除された Session
```

これらは CLI からは見えないものとして扱う。

---

# 4. 保存場所

## 4.1 state ファイル

保存先はプロジェクトローカルとする。

```text
<repo>/.jules-agent/state.json
```

例:

```text
my-repo/
  .jules-agent/
    state.json
  src/
  tests/
  pyproject.toml
```

---

## 4.2 Git 管理

`.jules-agent/state.json` は Git 管理しない。

推奨 `.gitignore`:

```gitignore
.jules-agent/
```

理由:

- Session ID は個人・環境依存の状態である
- タスク進行状態は作業者ローカルの実行状態である
- 複数人で共有すると競合や誤操作の原因になる
- Jules Session URL や PR 情報など、ローカル管理用メタデータを含むため

---

# 5. state.json の役割

`state.json` は、Jules 側の完全なコピーではなく、CLI がオーケストレーションするためのローカル状態ファイルである。

保存する主な情報は以下。

```text
- 元の要求
- タスク分解結果
- 実行戦略
- 各タスクのローカル状態
- Jules Session ID
- Jules Session URL
- Jules Session state のキャッシュ
- PR URL
- retry / attempt 状態
```

Jules の最新状態は、必要に応じて `sessions.get` で取得して `state.json` に反映する。

---

# 6. state.json の全体構造

`state.json` は以下のトップレベル構造を持つ。

```json
{
  "schema_version": 1,
  "project": {
    "root": "/path/to/repo",
    "repo": "owner/repo"
  },
  "runs": []
}
```

---

# 7. データモデル

## 7.1 Run

Run は、ユーザーが CLI に投げた大きな要求、または1回の実行単位を表す。

例:

```json
{
  "id": "run_20260516_001",
  "original_task": "APIキーを環境変数のみではなく、設定ファイル ~/.config/jules-agent/config.yml からも読み込めるようにしたい",
  "strategy": "single_session",
  "status": "running",
  "created_at": "2026-05-16T10:00:00Z",
  "updated_at": "2026-05-16T10:05:00Z",
  "tasks": []
}
```

### フィールド

| フィールド | 型 | 必須 | 説明 |
|---|---:|---:|---|
| `id` | string | yes | CLI内のRun ID |
| `original_task` | string | yes | ユーザーが入力した元の要求 |
| `strategy` | string | yes | 実行戦略 |
| `status` | string | yes | Run全体の状態 |
| `created_at` | string | yes | 作成日時 |
| `updated_at` | string | yes | 更新日時 |
| `tasks` | array | yes | このRunに属するタスク |

---

## 7.2 strategy

`strategy` は以下のいずれか。

```text
single_session
sequential_subtasks
parallel_subtasks
```

### `single_session`

1つの Jules Session にまとめて投げる。

```text
要求
  ↓
要件整理
  ↓
Jules Session 1件
```

小さな機能追加や、同じファイル群を触る変更ではこれを優先する。

---

### `sequential_subtasks`

複数タスクに分けるが、同時には dispatch しない。

```text
TASK-001 dispatch
  ↓
完了・レビュー・merge
  ↓
TASK-002 dispatch
```

依存関係がある場合に使用する。

---

### `parallel_subtasks`

複数タスクを同時に dispatch できる。

ただし初期運用では、明示的に許可された場合のみ使用する想定。

---

## 7.3 Run status

Run の状態は以下。

```text
planned
running
completed
failed
cancelled
```

### 意味

| status | 意味 |
|---|---|
| `planned` | タスク計画は作成済みだが、まだdispatchされていない |
| `running` | 1つ以上のタスクが進行中 |
| `completed` | すべてのタスクが完了した |
| `failed` | Run全体が失敗扱いになった |
| `cancelled` | ユーザーにより中止された |

---

# 8. Task

Task は Jules に dispatch する単位を表す。

例:

```json
{
  "id": "TASK-001",
  "title": "Load API key from environment or config file",
  "description": "Allow the CLI to resolve the Jules API key from JULES_API_KEY or ~/.config/jules-agent/config.yml.",
  "prompt": "....",
  "status": "awaiting_plan_approval",
  "depends_on": [],
  "acceptance_criteria": [
    "JULES_API_KEY environment variable is used when present",
    "~/.config/jules-agent/config.yml is read when JULES_API_KEY is absent",
    "config.yml supports an api_key field",
    "environment variable takes precedence over config file",
    "an actionable error is shown when no API key is available",
    "unit tests are added"
  ],
  "out_of_scope": [
    "Adding a full configuration management framework",
    "Changing unrelated CLI behavior"
  ],
  "jules": {
    "session_id": "14073503332666167904",
    "session_name": "sessions/14073503332666167904",
    "session_url": "https://jules.google.com/...",
    "state": "AWAITING_PLAN_APPROVAL",
    "create_time": "2026-05-16T10:01:00Z",
    "update_time": "2026-05-16T10:03:00Z"
  },
  "pull_request": null,
  "attempts": 1,
  "max_attempts": 3,
  "created_at": "2026-05-16T10:00:00Z",
  "updated_at": "2026-05-16T10:03:00Z"
}
```

---

## 8.1 Task fields

| フィールド | 型 | 必須 | 説明 |
|---|---:|---:|---|
| `id` | string | yes | Run内で一意なタスクID |
| `title` | string | yes | タスクタイトル |
| `description` | string | yes | タスク説明 |
| `prompt` | string | yes | Julesへ渡した最終プロンプト |
| `status` | string | yes | CLI側のタスク状態 |
| `depends_on` | array | yes | 依存するタスクID |
| `acceptance_criteria` | array | yes | 受け入れ条件 |
| `out_of_scope` | array | yes | 実装しないこと |
| `jules` | object/null | yes | Jules Session情報 |
| `pull_request` | object/null | yes | PR情報 |
| `attempts` | number | yes | Jules dispatchまたは修正試行回数 |
| `max_attempts` | number | yes | 最大試行回数 |
| `created_at` | string | yes | 作成日時 |
| `updated_at` | string | yes | 更新日時 |

---

# 9. Jules Session 情報

`jules` フィールドは、Jules Session と紐づいた後に設定する。

dispatch 前は `null` でもよい。

```json
"jules": null
```

dispatch 後:

```json
"jules": {
  "session_id": "14073503332666167904",
  "session_name": "sessions/14073503332666167904",
  "session_url": "https://jules.google.com/...",
  "state": "PLANNING",
  "create_time": "2026-05-16T10:01:00Z",
  "update_time": "2026-05-16T10:01:30Z"
}
```

---

## 9.1 Jules fields

| フィールド | 型 | 必須 | 説明 |
|---|---:|---:|---|
| `session_id` | string | yes | Jules Session ID |
| `session_name` | string | yes | `sessions/{session}` 形式のリソース名 |
| `session_url` | string/null | no | Jules Web UI URL |
| `state` | string | yes | Jules 側の Session state |
| `create_time` | string/null | no | Jules Session 作成日時 |
| `update_time` | string/null | no | Jules Session 更新日時 |

---

# 10. Pull Request 情報

Jules Session の `outputs` に Pull Request が含まれる場合、`pull_request` に保存する。

```json
"pull_request": {
  "url": "https://github.com/owner/repo/pull/123",
  "title": "Load API key from config file",
  "description": "..."
}
```

dispatch直後やPR未作成時は `null`。

```json
"pull_request": null
```

---

## 10.1 Pull Request fields

| フィールド | 型 | 必須 | 説明 |
|---|---:|---:|---|
| `url` | string | yes | PR URL |
| `title` | string | no | PRタイトル |
| `description` | string | no | PR本文 |

---

# 11. Task status

CLI側の Task status は以下。

```text
planned
dispatching
dispatched
planning
awaiting_plan_approval
plan_approved
in_progress
awaiting_user_feedback
paused
completed
pr_created
reviewing
needs_fix
waiting_human_review
merged
pr_closed
failed
cancelled
```

---

## 11.1 status の意味

| status | 意味 |
|---|---|
| `planned` | タスクは作成済みだが未dispatch |
| `dispatching` | dispatch処理中 |
| `dispatched` | Jules Session作成済み |
| `planning` | Julesがプラン作成中 |
| `awaiting_plan_approval` | Julesがplan承認待ち |
| `plan_approved` | plan承認済み |
| `in_progress` | Julesが実装中 |
| `awaiting_user_feedback` | Julesがユーザー入力待ち |
| `paused` | Jules Sessionが一時停止中 |
| `completed` | Jules Sessionが完了 |
| `pr_created` | JulesがPRを作成済み |
| `reviewing` | PRレビュー中 |
| `needs_fix` | 修正が必要 |
| `waiting_human_review` | 人間レビュー待ち |
| `merged` | PR merge済み |
| `pr_closed` | PR closed済み、未merge |
| `failed` | 失敗 |
| `cancelled` | 中止 |

---

# 12. Jules state と Task status の対応

Jules API の `Session.state` を CLI 側の Task status に反映する。

| Jules state | Task status |
|---|---|
| `QUEUED` | `dispatched` |
| `PLANNING` | `planning` |
| `AWAITING_PLAN_APPROVAL` | `awaiting_plan_approval` |
| `AWAITING_USER_FEEDBACK` | `awaiting_user_feedback` |
| `IN_PROGRESS` | `in_progress` |
| `PAUSED` | `paused` |
| `FAILED` | `failed` |
| `COMPLETED` | `completed` または `pr_created` |

`COMPLETED` の場合、Session output に Pull Request が含まれていれば `pr_created` とする。

`pr_created` の task は、sync 時に GitHub PR 詳細を確認し、`merged_at` があれば `merged`、`state=closed` かつ未mergeであれば `pr_closed` に更新する。

```text
COMPLETED + PR outputあり
  => pr_created

COMPLETED + PR outputなし
  => completed
```

---

# 13. state.json 更新タイミング

## 13.1 Plan 作成後

Codex による要件整理・タスク計画作成後、dispatch 前に `state.json` を作成または更新する。

この時点の task は `planned`。

```json
{
  "status": "planned",
  "jules": null,
  "pull_request": null
}
```

---

## 13.2 dispatch 開始時

dispatch 開始時、対象 task の status を `dispatching` にする。

```json
{
  "status": "dispatching"
}
```

---

## 13.3 dispatch 成功時

Jules `sessions.create` が成功したら、返却された Session 情報を保存する。

保存する情報:

```text
- session_id
- session_name
- session_url
- state
- create_time
- update_time
```

その後、Task status を Jules state に応じて更新する。

---

## 13.4 dispatch 失敗時

Jules `sessions.create` が失敗した場合、Task status を `failed` にする。

必要に応じて、エラーメッセージを保存するフィールドを追加してもよい。

例:

```json
"last_error": "Jules API returned 401 Unauthorized"
```

---

## 13.5 sync 時

`jules-agent sync` 実行時、`state.json` に保存されている未完了 task について `sessions.get` を呼び、Jules state を更新する。

更新対象:

```text
- jules.state
- jules.update_time
- jules.session_url
- pull_request
- task.status
- task.updated_at
- run.updated_at
```

`sessions.list` は使わない。

---

## 13.6 approve 時

`jules-agent approve <task-id>` 実行時、対象 task の `jules.session_name` を使って `sessions.approvePlan` を呼ぶ。

成功した場合:

```text
- task.status を plan_approved にする
- updated_at を更新する
```

その後の最新状態は、次回 `sync` または `status` 時に `sessions.get` で反映する。

---

## 13.7 send message 時

`jules-agent send <task-id> "message"` 実行時、対象 task の `jules.session_name` を使って `sessions.sendMessage` を呼ぶ。

成功した場合:

```text
- task.updated_at を更新する
- 必要なら last_message_at を保存する
```

---

# 14. ファイル操作仕様

## 14.1 ディレクトリ作成

`state.json` 保存時、以下のディレクトリが存在しなければ作成する。

```text
<repo>/.jules-agent/
```

---

## 14.2 JSON フォーマット

`state.json` は人間が読める形式で保存する。

推奨:

```text
- UTF-8
- インデントあり
- ensure_ascii=false 相当
- 末尾改行あり
```

---

## 14.3 書き込み方式

初期仕様では単純な上書きでよい。

ただし、可能であれば将来的には安全な書き込み方式にする。

```text
state.json.tmp に書く
  ↓
rename で state.json に置き換える
```

今回の仕様では、dispatch成功後保存前クラッシュ問題は扱わないが、JSON破損を減らすために atomic write は検討対象とする。

---

# 15. コマンド仕様案

## 15.1 `run`

要求を受け取り、計画作成、承認、dispatch まで行う。

```bash
jules-agent run "APIキーを環境変数だけでなく設定ファイルからも読みたい"
```

挙動:

```text
1. Codexで strategy / tasks を作成
2. ユーザーに計画を表示
3. 承認されたら state.json に保存
4. strategy に応じて dispatch 対象を決定
5. dispatch
6. session_id を state.json に保存
```

---

## 15.2 `status`

ローカル state を表示する。

```bash
jules-agent status
```

デフォルトでは `state.json` の内容を表示するだけでもよい。

オプションで remote state も取得できるようにしてもよい。

```bash
jules-agent status --sync
```

---

## 15.3 `sync`

`state.json` に保存されている Session ID を使って Jules API から最新状態を取得する。

```bash
jules-agent sync
```

挙動:

```text
1. state.json を読む
2. 未完了 task の jules.session_name を取得
3. sessions.get を呼ぶ
4. state / outputs / PR を反映
5. state.json を保存
```

---

## 15.4 `approve`

Jules の plan を承認する。

```bash
jules-agent approve TASK-001
```

前提:

```text
task.status == awaiting_plan_approval
task.jules.session_name が存在する
```

挙動:

```text
1. state.json から task を探す
2. sessions.approvePlan を呼ぶ
3. 成功したら task.status = plan_approved
4. state.json を保存
```

---

## 15.5 `send`

Jules Session に追加メッセージを送る。

```bash
jules-agent send TASK-001 "環境変数を最優先にしてください"
```

前提:

```text
task.jules.session_name が存在する
```

挙動:

```text
1. state.json から task を探す
2. sessions.sendMessage を呼ぶ
3. 成功したら updated_at を更新
4. state.json を保存
```

---

## 15.6 `next`

`sequential_subtasks` の次の未dispatchタスクを dispatch する。

```bash
jules-agent next
```

挙動:

```text
1. state.json を読む
2. strategy == sequential_subtasks の run を探す
3. depends_on が満たされた planned task を探す
4. その task だけ dispatch
5. session_id を保存
```

依存が満たされたかどうかの判定は、初期仕様では単純に以下でよい。

```text
依存先 task.status == merged
```

または、merge管理をまだ持たない場合は以下でもよい。

```text
依存先 task.status == pr_created / completed
```

ただし、将来的には `merged` を基準にするのが望ましい。

---

# 16. dispatch 戦略

## 16.1 `single_session`

`tasks` は1件のみ。

承認後、その1件を dispatch する。

---

## 16.2 `sequential_subtasks`

承認後、最初の `planned` task のみ dispatch する。

残りは `planned` のまま保存する。

後続タスクは `next` コマンドで dispatch する。

---

## 16.3 `parallel_subtasks`

承認後、全 task を dispatch する。

ただし、初期仕様では parallel dispatch は明示承認または明示オプション付きにするのが望ましい。

例:

```bash
jules-agent run "..." --parallel
```

---

# 17. Jules Session 作成時のパラメータ方針

Session 作成時は、原則として以下を指定する。

```text
requirePlanApproval: true
automationMode: AUTO_CREATE_PR
```

## 17.1 requirePlanApproval

Jules のプランを自動承認しない。

```text
requirePlanApproval = true
```

これにより、Session は plan 作成後に以下の state になる。

```text
AWAITING_PLAN_APPROVAL
```

その後、CLIの `approve` コマンドで承認する。

---

## 17.2 automationMode

Jules に PR 作成まで任せる。

```text
automationMode = AUTO_CREATE_PR
```

これにより、Jules が最終コードパッチを生成したら Pull Request を作成する。

PR 情報は Session `outputs` から取得して `state.json` に保存する。

---

# 18. エラー時の扱い

## 18.1 state.json が存在しない

`status`, `sync`, `approve`, `send`, `next` 実行時に `state.json` が存在しない場合は、エラーを表示する。

例:

```text
No local Jules state found: .jules-agent/state.json
```

`sessions.list` による探索は行わない。

---

## 18.2 task が存在しない

指定された `TASK-001` が `state.json` に存在しない場合はエラー。

```text
Task not found: TASK-001
```

---

## 18.3 task に session がない

`approve` や `send` 対象の task に `jules.session_name` がない場合はエラー。

```text
Task has not been dispatched yet: TASK-001
```

---

## 18.4 Jules API エラー

Jules API がエラーを返した場合:

```text
- CLI はエラーを表示する
- state.json の既存 session_id は削除しない
- 必要なら task.last_error に保存する
```

---

# 19. 非対応事項

初期仕様では以下に対応しない。

```text
- dispatch成功後、保存前クラッシュからの復旧
- sessions.list による Session 探索
- Jules Web UI で作成した Session の import
- UI側で操作された Session の厳密な同期
- 複数マシン間での state 共有
- 複数ユーザーでの共同管理
- state.json の競合解決
- state.json のロック制御
- SQLite 等のDB化
- GitHub PR merge の自動化
- PRレビューエージェントとの統合
```

---

# 20. 今回の具体例

元の要求:

```text
APIキーを環境変数のみではなく、設定ファイル ~/.config/jules-agent/config.yml からも読み込めるようにしたい
```

望ましい plan:

```json
{
  "strategy": "single_session",
  "tasks": [
    {
      "id": "TASK-001",
      "title": "Load API key from environment or config file",
      "description": "Allow API key resolution from JULES_API_KEY or ~/.config/jules-agent/config.yml.",
      "acceptance_criteria": [
        "JULES_API_KEY is used when present",
        "~/.config/jules-agent/config.yml is read when JULES_API_KEY is absent",
        "config.yml supports api_key",
        "environment variable takes precedence over config file",
        "missing config file does not fail by itself",
        "an actionable error is shown when no API key is available",
        "tests are added"
      ],
      "out_of_scope": [
        "Adding a full configuration framework",
        "Changing unrelated CLI behavior",
        "Supporting project-local config files"
      ]
    }
  ]
}
```

dispatch後の `state.json` 例:

```json
{
  "schema_version": 1,
  "project": {
    "root": "/path/to/repo",
    "repo": "owner/repo"
  },
  "runs": [
    {
      "id": "run_20260516_001",
      "original_task": "APIキーを環境変数のみではなく、設定ファイル ~/.config/jules-agent/config.yml からも読み込めるようにしたい",
      "strategy": "single_session",
      "status": "running",
      "created_at": "2026-05-16T10:00:00Z",
      "updated_at": "2026-05-16T10:03:00Z",
      "tasks": [
        {
          "id": "TASK-001",
          "title": "Load API key from environment or config file",
          "description": "Allow API key resolution from JULES_API_KEY or ~/.config/jules-agent/config.yml.",
          "prompt": "Full prompt sent to Jules...",
          "status": "awaiting_plan_approval",
          "depends_on": [],
          "acceptance_criteria": [
            "JULES_API_KEY is used when present",
            "~/.config/jules-agent/config.yml is read when JULES_API_KEY is absent",
            "config.yml supports api_key",
            "environment variable takes precedence over config file",
            "missing config file does not fail by itself",
            "an actionable error is shown when no API key is available",
            "tests are added"
          ],
          "out_of_scope": [
            "Adding a full configuration framework",
            "Changing unrelated CLI behavior",
            "Supporting project-local config files"
          ],
          "jules": {
            "session_id": "14073503332666167904",
            "session_name": "sessions/14073503332666167904",
            "session_url": "https://jules.google.com/...",
            "state": "AWAITING_PLAN_APPROVAL",
            "create_time": "2026-05-16T10:01:00Z",
            "update_time": "2026-05-16T10:03:00Z"
          },
          "pull_request": null,
          "attempts": 1,
          "max_attempts": 3,
          "created_at": "2026-05-16T10:00:00Z",
          "updated_at": "2026-05-16T10:03:00Z"
        }
      ]
    }
  ]
}
```

---

# 21. まとめ

この仕様では、Jules Session 管理をかなりシンプルにする。

```text
- Session ID は <repo>/.jules-agent/state.json に保存する
- CLIが作ったSessionだけをCLIが管理する
- sessions.list は使わない
- 復旧はしない
- UIで作ったものはUIで完結させる
- dispatch後の操作は state.json の session_name/session_id を使う
- Jules plan は requirePlanApproval=true で止め、CLIから approve する
- PRは AUTO_CREATE_PR によりJulesに作らせ、outputsから取得する
```

初期実装としては、このくらい割り切った方がかなり作りやすいです。  
まずはこの仕様で状態管理の軸を作り、必要になったら後から `sync` 強化、復旧、SQLite化、GitHub連携を追加していくのがよいと思います。
