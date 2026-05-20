Command-line arguments

Arguments passed directly when running the CLI can override other configurations for that specific session.

    --acp:
        Starts the agent in Agent Communication Protocol (ACP) mode.
    --allowed-mcp-server-names:
        A comma-separated list of MCP server names to allow for the session.
    --allowed-tools <tool1,tool2,...>:
        A comma-separated list of tool names that will bypass the confirmation dialog.
        Example: gemini --allowed-tools "ShellTool(git status)"
    --approval-mode <mode>:
        Sets the approval mode for tool calls. Available modes:
            default: Prompt for approval on each tool call (default behavior)
            auto_edit: Automatically approve edit tools (replace, write_file) while prompting for others
            yolo: Automatically approve all tool calls (equivalent to --yolo)
            plan: Read-only mode for tool calls (requires experimental planning to be enabled).

                Note: This mode is currently under development and not yet fully functional.

        Cannot be used together with --yolo. Use --approval-mode=yolo instead of --yolo for the new unified approach.
        Example: gemini --approval-mode auto_edit
    --debug (-d):
        Enables debug mode for this session, providing more verbose output. Open the debug console with F12 to see the additional logging.
    --delete-session <identifier>:
        Delete a specific chat session by its index number or full session UUID.
        Use --list-sessions first to see available sessions, their indices, and UUIDs.
        Example: gemini --delete-session 3 or gemini --delete-session a1b2c3d4-e5f6-7890-abcd-ef1234567890
    --extensions <extension_name ...> (-e <extension_name ...>):
        Specifies a list of extensions to use for the session. If not provided, all available extensions are used.
        Use the special term gemini -e none to disable all extensions.
        Example: gemini -e my-extension -e my-other-extension
    --fake-responses:
        Path to a file with fake model responses for testing.
    --help (or -h):
        Displays help information about command-line arguments.
    --include-directories <dir1,dir2,...>:
        Includes additional directories in the workspace for multi-directory support.
        Can be specified multiple times or as comma-separated values.
        5 directories can be added at maximum.
        Example: --include-directories /path/to/project1,/path/to/project2 or --include-directories /path/to/project1 --include-directories /path/to/project2
    --list-extensions (-l):
        Lists all available extensions and exits.
    --list-sessions:
        List all available chat sessions for the current project and exit.
        Shows session indices, dates, message counts, and preview of first user message.
        Example: gemini --list-sessions
    --model <model_name> (-m <model_name>):
        Specifies the Gemini model to use for this session.
        Example: npm start -- --model gemini-3-pro-preview
    --output-format <format>:
        Description: Specifies the format of the CLI output for non-interactive mode.
        Values:
            text: (Default) The standard human-readable output.
            json: A machine-readable JSON output.
            stream-json: A streaming JSON output that emits real-time events.
        Note: For structured output and scripting, use the --output-format json or --output-format stream-json flag.
    --prompt <your_prompt> (-p <your_prompt>):
        Used to pass a prompt directly to the command. This invokes Gemini CLI in a non-interactive mode.
    --prompt-interactive <your_prompt> (-i <your_prompt>):
        Starts an interactive session with the provided prompt as the initial input.
        The prompt is processed within the interactive session, not before it.
        Cannot be used when piping input from stdin.
        Example: gemini -i "explain this code"
    --record-responses:
        Path to a file to record model responses for testing.
    --resume [session_id] (-r [session_id]):
        Resume a previous chat session. Use “latest” for the most recent session, provide a session index number, or provide a full session UUID.
        If no session_id is provided, defaults to “latest”.
        Example: gemini --resume 5 or gemini --resume latest or gemini --resume a1b2c3d4-e5f6-7890-abcd-ef1234567890 or gemini --resume
        See Session Management for more details.
    --sandbox (-s):
        Enables sandbox mode for this session.
    --screen-reader:
        Enables screen reader mode, which adjusts the TUI for better compatibility with screen readers.
    --version:
        Displays the version of the CLI.
    --yolo:
        Enables YOLO mode, which automatically approves all tool calls.
