run

プロンプトを直接渡して、非対話モードで opencode を実行します。
Terminal window

opencode run [message..]

これは、スクリプト作成、自動化、または完全な TUI を起動せずに迅速な回答が必要な場合に便利です。例えば。
Terminal window

opencode run Explain the use of context in Go

実行中の opencode serve インスタンスにアタッチして、実行ごとの MCP サーバーのコールドブート時間を回避することもできます。
Terminal window

# Start a headless server in one terminal
opencode serve

# In another terminal, run commands that attach to it
opencode run --attach http://localhost:4096 "Explain async/await in JavaScript"

フラグ
フラグ	ショート	説明
--command		実行するコマンド。引数には message を使用します。
--continue	-c	最後のセッションを続行
--session	-s	続行するセッション ID
--fork		続行時にセッションをフォーク (--continue または --session と併用)
--share		セッションを共有する
--model	-m	プロバイダー/モデルの形式で使用するモデル
--agent		使用するエージェント
--file	-f	メッセージに添付するファイル
--format		形式: デフォルト (フォーマット済み) または json (生の JSON イベント)
--title		セッションのタイトル (値が指定されていない場合は、切り詰められたプロンプトが使用されます)
--attach		実行中の opencode サーバー (http://localhost:4096 など) に接続します。
--password	-p	Basic 認証パスワード（デフォルトは OPENCODE_SERVER_PASSWORD）
--username	-u	Basic 認証ユーザー名（デフォルトは OPENCODE_SERVER_USERNAME または opencode）
--dir		実行ディレクトリ、またはアタッチ時のリモートサーバー上のパス
--variant		モデルバリアント（プロバイダー固有の推論レベル）
--thinking		思考ブロックを表示
--port		ローカルサーバーのポート (デフォルトはランダムポート)

グローバルフラグ

opencode CLI は次のグローバルフラグを受け取ります。
フラグ	ショート	説明
--help	-h	ヘルプを表示
--version	-v	バージョン番号を出力
--print-logs		ログを標準エラー出力に出力
--log-level		ログレベル (DEBUG、INFO、WARN、ERROR)