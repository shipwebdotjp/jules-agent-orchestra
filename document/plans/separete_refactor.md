最終プラン: 3層分離リファクタ（全 handler 一括）
ディレクトリ構造（新設部分）
src/jules_agent/
  services/              ← 新設: Service層
    __init__.py
    options.py           ← Options dataclass群（Run/Advance/Feedback/Merge等）
    results.py          ← OperationResult dataclass
    run_service.py
    import_service.py
    sync_service.py
    status_service.py
    approve_service.py
    send_service.py
    feedback_service.py
    review_service.py
    review_pass_service.py
    merge_service.py
    next_service.py
    delete_service.py
    advance_service.py   ← AdvanceEngine をこちらへ移譲
  utils.py               ← extract_pull_request_number 等の純粋関数
  cli/                   ← 薄い。argparse→Options 変換と表示のみ
  ...(既存 domain層はそのまま)
段階的ステップ
Step 0: 共通基盤
1. services/options.py: コマンドごとの typed Options dataclass（argparse.Namespace の代用）。Config 互換フィールドを含む
2. services/results.py: OperationResult dataclass（exit_code, summary: str, data: dict | None, events: list[Event]を想定。MVP なので最小限）
3. utils.py: extract_pull_request_number を cli/state.py から移動（循環依存解消）
Step 1: ログ・例外インフラ
4. logging を導入: ルート jules_agent logger。pipeline.py, review.py, advance_core.py, cli/state.py の print(..., file=sys.stderr) を logger.warning に、通常 print を logger.info に置換
5. PipelineError の派生として OperationError を新設（parser.exit を置換）。引数にコードとメッセージを持つ
6. codex.py の DEBUG_ENABLED グローバルを logging レベル制御に置換し set_debug は deprecated 化
Step 2: Service抽出（全handler一括）
各 handle_* のビジネスロジックを Service クラス（または関数）に移植:
- 入力: Options + 必要な JulesClient/GitHubClient/State/Config
- 出力: OperationResult
- 副作用: save_state はそのまま許容（MVP）。Service内部で logger 使用、例外送出、parser.exit/isatty 系は全廃
該当 handler 別対応:
- run.py → RunService.create_run（clarification/confirmation ループは Service が「次の問い/確認要求」を OperationResult で返すステートマシン化 or 関数分割。MVPでは入力コールバックDIで維持）
- merge.py → MergeService（parser.exit → OperationError）
- send/approve/review/review_pass → 各 Service
- feedback.py → FeedbackService（run_feedback_loop は入出力コールバックDIを維持しつつ Service 内に配置）
- next/delete/import/status → 該当 Service
- advance_core.py:AdvanceEngine → AdvanceService。sys.stdin.isatty() 判定は Options.interactive フラグで Service 外で決定して注入
Step 3: CLI handler 薄化
7. 各 handle_* を argparse → Options 変換＋ Service 呼出＋ OperationResult 表示 に縮小
8. 中央 main() の if/elif ディスパッチはそのまま（小規模なので registry化不要）
9. parser.exit は Service からの OperationError を catch して parser.exit に変換する thin wrapper 1箇所に集約
Step 4: TUI/API が呼ぶ境界の整備
10. services パッケージが argparse/sys に依存しないことを保証（grepで import argparse/sys.stdin/sys.exit/parser が services/ 配下に出ないように lint的に assortment）
11. UI層共通のインターフェース目安: TUI/API は Options を構築 → Service.call(options) → OperationResult を描画。対話系は UI 側が input_func/output を注入
影響・注意点
- テスト: AGENTS.md 方針に沿って文法/致命的異常のみ確認。tests/ 配下の cli/__init__.py re-export は維持（後方互換）。cli/commands/* の内部 import 先が変わるので import エラーだけ抜く
- README: CLI コマンド/フラグ/config は変更しないため README 更新不要。Service層はユーザに見えない
- State: ファイル単一のまま。save_state が Service 内から呼ばれる現状は維持。WebUI 検討時に改めて Repository 化
受け入れ基準
- python -c "from jules_agent.services import *" が通る
- services/ 配下で grep -rE "import argparse|sys\.exit|sys\.stdin|argparse\.Namespace|parser\.exit" が空
- jules-agent --help および各サブコマンドの振る舞いが現状と同じ
- 既存 pytest が全パス（文法/JSON解析/ID抽出系）