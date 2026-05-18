  1. 最優先: 壊れた state.json を無言で「未初期化扱い」にしてしまう
      - src/jules_agent/pipeline.py:785 の load_state は JSONDecodeError や KeyError を握りつぶして None を返します。
      - その結果、src/jules_agent/cli/__init__.py:196 側が「state が無い」と判断して新規 State を作り直します。
      - 既存の履歴が壊れていた場合でもエラーにならず、run や save_state で上書きされて履歴消失につながります。ここは fail closed にした方がいいです。
  2. sync が changeSet の型を取り違えていて、PR 反映で落ちる可能性がある
      - src/jules_agent/cli/state.py:167 で changeSet = output.get("changeSet", []) としているのに、直後に changeSet.get("gitPatch", None) を呼んでいます。
      - changeSet が非空の list や別型で来ると例外になり、外側の except で False 扱いになって task の同期が止まります。
      - changeSet を dict として検証するか、API 実際の形に合わせて処理を分岐させる必要があります。
  3. 状態ファイルの排他制御がなく、並行実行で更新ロストや run ID 重複が起きる
      - src/jules_agent/pipeline.py:792 の save_state はロックなしで直接 rename しています。
      - 同時に複数の CLI が動くと、最後に書いたプロセスが前の更新を消しやすいです。
      - さらに src/jules_agent/pipeline.py:807 の generate_run_id は現在の state を走査して採番しているので、同時起動で同じ run ID を作る可能性があります。
      - これは refactor というよりデータ整合性の問題なので、ロック導入を先にやるべきです。
  4. 端末出力に未サニタイズの外部文字列をそのまま出していて、ターミナル escape injection の余地がある
      - src/jules_agent/cli/commands/status.py:23 は task.title、session_url、PR URL をそのまま表示しています。
      - run_feedback_loop でも Codex 由来の suggestion / explanation を raw で出します。
      - 悪意ある文字列が ANSI 制御コードを含むと、表示改ざんや端末操作の誘導につながります。最低でも制御文字除去を入れた方がいいです。
  5. merge コマンドが mergeable の一回判定に依存していて、誤って失敗しやすい
      - src/jules_agent/cli/commands/merge.py:47 は if not pr_details.get("mergeable") で即終了します。
      - mergeable がまだ確定していないケースや一時的な falsey 値を、単純に「マージ不可」とみなします。
      - 少なくとも再取得やリトライを入れないと、マージ可能な PR でも弾くことがあります。

  補足のリファクタ候補

  - src/jules_agent/cli/commands/run.py:187 と src/jules_agent/cli/commands/next.py:27 は session 作成・状態更新・保存ロジックがかなり重複しています。修正漏れの温床です。
  - src/jules_agent/pipeline.py:392 の validate_plan が空実装なので、名前に反して何も検証していません。ここは少なくとも不要なら削除、必要なら実装すべきです。