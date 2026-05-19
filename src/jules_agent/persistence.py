from __future__ import annotations

import datetime
import json
from pathlib import Path

from .git import get_git_root
from .models import State


def load_state(cwd: Path) -> State | None:
    root = get_git_root(cwd)
    state_path = root / ".jules-agent" / "state.json"
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return State.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def save_state(cwd: Path, state: State) -> None:
    root = get_git_root(cwd)
    state_dir = root / ".jules-agent"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"

    # Use atomic write: write to .tmp then rename
    tmp_path = state_path.with_suffix(".json.tmp")
    data = state.to_dict()
    tmp_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp_path.rename(state_path)


def generate_run_id(state: State) -> str:
    now = datetime.datetime.now()
    date_str = now.strftime("%Y%m%d")

    # Count existing runs for today to determine sequence number
    today_prefix = f"run_{date_str}_"
    max_seq = 0
    for run in state.runs:
        if run.id.startswith(today_prefix):
            try:
                seq = int(run.id[len(today_prefix) :])
                if seq > max_seq:
                    max_seq = seq
            except ValueError:
                continue

    return f"run_{date_str}_{max_seq + 1:03d}"
