# jules-agent

`jules-agent` is a small CLI that uses Codex to break a task into an execution plan, then submits the resulting work to Jules.

## What it does

1. Takes one task description.
2. Asks Codex to return a JSON plan with a strategy and tasks.
3. Shows the proposed plan and asks for confirmation by default.
4. If the plan is rejected, asks for feedback, revises the breakdown, and repeats until approved.
5. Sends the tasks to Jules using the selected strategy.
6. Prints each dispatch result with success/failure, and the session ID when it can be extracted.

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
jules-agent "Refactor the CLI and add tests"
```

Optional flags:

- `--repo owner/name` override the Jules repository target
- `--codex-bin /path/to/codex` use a different Codex executable
- `--no-confirm` skip the confirmation loop and dispatch immediately
- `--config /path/to/config.toml` specify a custom configuration file

## Configuration

`jules-agent` can be configured using TOML files. It searches for configuration in the following locations (in order of increasing priority):

1. `~/.jules-agent.toml`
2. `~/.config/jules-agent/config.toml`
3. `./.jules-agent.toml`
4. `./jules-agent.toml`
5. A custom file specified via `--config`

Settings in the configuration file have lower priority than environment variables and command-line flags.

### Supported Settings

```toml
api_key = "your-jules-api-key"
repo = "owner/repo"
codex_bin = "codex"
base_url = "https://jules.googleapis.com/v1alpha"
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
