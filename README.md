# jules-agent

`jules-agent` is a small CLI that uses Codex to break a task into an execution plan, then submits the resulting work to Jules.

## What it does

1. Takes one task description.
2. Asks Codex whether any clarification is needed before planning.
3. If needed, asks the user the generated clarification questions, rechecks the task, and repeats up to 5 rounds.
4. Asks Codex to return a JSON plan with a strategy and tasks.
5. Shows the proposed plan and asks for confirmation by default.
6. If the plan is rejected, asks for feedback, revises the breakdown, and repeats until approved.
7. Sends the tasks to Jules using the selected strategy.
8. Prints each dispatch result with success/failure, and the session ID when it can be extracted.

## Requirements

- Python 3.12 or newer
- `codex` available on `PATH`
- `jules` available on `PATH`
- `git` available on `PATH`

If you do not run the command inside a git repository, pass `--repo owner/name`.

## Install

From a local checkout:

```bash
pip install -e .
```

## Usage

```bash
jules-agent [flags] <command> [args]
```

### Subcommands

- `run <task>`: Analyze a new task with Codex and dispatch it to Jules.
  - In interactive mode, it may first ask clarification questions before generating a plan.
- `status`: Show the current local state, including runs and tasks. Use `--show-activities` to see the session history.
- `sync`: Synchronize the local state with the Jules API and GitHub (to update PR status).
- `advance [--auto]`: Automatically or interactively advance work across the next active task.
    - In `--auto` mode, it will auto-approve plans (if recommended by Codex) and send auto-replies.
- `approve <task_id>`: Manually approve the proposed plan for a specific task.
- `send <task_id> <message>`: Send a manual message to a task's Jules session.
- `feedback <task_id>`: Enter an interactive feedback loop with Codex to refine a task's plan or reply.
- `review <task_id>`: Manually run Codex review for a task with an open pull request.
- `merge <task_id>`: Manually merge the pull request associated with a task.
- `next`: Dispatch the next task in a sequential run.

### Global Flags

- `--repo owner/name`: Override the target repository.
- `--codex-bin /path/to/codex`: Use a specific Codex executable.
- `--config /path/to/config.toml`: Specify a custom configuration file.

### Examples

```bash
# Start a new task
jules-agent run "Refactor the CLI and add tests"

# Advance all work in auto mode
jules-agent advance --auto
```

## Configuration

`jules-agent` can be configured using TOML files. It searches for configuration in the following locations (in order of increasing priority):

1. `~/.jules-agent.toml`
2. `~/.config/jules-agent/config.toml`
3. `./.jules-agent.toml`
4. `./jules-agent.toml`
5. A custom file specified via `--config`

Settings in the configuration file have lower priority than environment variables and command-line flags.

### GitHub Token

`jules-agent` reads `GITHUB_TOKEN` from the environment, or `github_token` from the TOML configuration file.

Need permissions:
- pull-requests: write
- issues: write
- contents: write

### Supported Settings

```toml
api_key = "your-jules-api-key"
repo = "owner/repo"
github_token = "ghp_your-github-token"
codex_bin = "codex"
base_url = "https://jules.googleapis.com/v1alpha"
merge_method = "rebase"
```

Example:

```bash
jules-agent --repo example-org/example-repo "Split the parser from the dispatcher"
```

## Output

The CLI prints one line per dispatch result:

```text
Jules dispatch result(s): 2
1. [success] [123456] Update the parser
2. [success] [123457] Add tests
```

If Codex fails, the command exits non-zero and includes the command plus captured stdout and stderr.
If a Jules dispatch fails, the CLI prints `failure` for that subtask, shows the captured command output, and exits non-zero after the first failure.
If confirmation mode is enabled and stdin is not interactive, the CLI exits with an error and tells you to use `--no-confirm`.

## How It Works

The Codex step expects JSON shaped like this:

```json
{
  "strategy": "single_session",
  "tasks": [
    { "title": "First task" }
  ]
}
```

`strategy` can be `single_session`, `parallel_subtasks`, or `sequential_subtasks`. Each task can also be a plain string. The dispatcher turns the title and any available details into the prompt passed to `jules new`.

## Development

Run the tests with:

```bash
python -m unittest discover -s tests
```

The tests cover JSON parsing, subtask normalization, session ID extraction, and the end-to-end pipeline error path.
