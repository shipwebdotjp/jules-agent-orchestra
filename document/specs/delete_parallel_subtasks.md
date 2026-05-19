# `parallel_subtasks` 廃止と sequential-only 化

## Summary
`parallel_subtasks` を完全に廃止し、複数タスクに分ける場合は常に `sequential_subtasks` に寄せます。  
`single_session` は単一タスク用として残し、`next` サブコマンドを直列実行の継続手段として使い続けます。

## Key Changes
- `pipeline.py` の strategy 定義・Codex プロンプト・検証を更新し、`parallel_subtasks` を選択肢から削除する。
- `validate_plan()` を実装して、誤って `parallel_subtasks` が返ってきた場合はハードエラーにする。
- `run` 側は、`sequential_subtasks` の複数タスクを「先頭だけ dispatch」する既存の直列フローに統一し、残りは `next` に渡す。
- `dispatch_subtasks` から `sequential_subtasks` 拒否を हटしつつ、strategy に応じた直列 dispatch を明示する。
- `ExecutionStrategy` から `parallel_subtasks` を削除し、CLI/help/README/テストも新しい contract に揃える。
- 既存の `parallel_subtasks` を含む state は互換対象にせず、古い値が見つかったら早めに失敗する前提で整理する。

## Test Plan
- parser smoke test で `parallel_subtasks` が消えたことを確認する。
- plan validation のテストで、`parallel_subtasks` 出力を拒否することを確認する。
- `run` のテストで、単一タスクは従来通り dispatch され、複数タスクは先頭のみ dispatch されることを確認する。
- `next` のテストで、残りの planned task を順番に進められることを確認する。
- README に、直列化方針と `next` による継続手順を反映する。

## Assumptions
- 既存の `parallel_subtasks` を含む state は非互換でよい。
- `parallel_subtasks` の自動変換はしない。誤出力は拒否する。
- `next` が sequential 実行の継続責務を持つ、という現在の役割は維持する。
