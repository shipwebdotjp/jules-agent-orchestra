from __future__ import annotations

import sys
import threading
import uuid
from contextlib import contextmanager
from typing import Callable

BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_status_callback: Callable[[str], None] | None = None
_stack: list[tuple[str, str]] = []
_lock = threading.Lock()


def set_status_callback(callback: Callable[[str], None] | None) -> None:
    global _status_callback
    _status_callback = callback


@contextmanager
def spinner(message: str = "", *, interval: float = 0.1):
    global _stack
    done = threading.Event()
    chars = BRAILLE
    spinner_id = str(uuid.uuid4())

    with _lock:
        _stack.append((spinner_id, message))

    def spin() -> None:
        i = 0
        while not done.is_set():
            with _lock:
                if not _stack or _stack[-1][0] != spinner_id:
                    top_is_not_me = True
                    current_message = ""
                else:
                    top_is_not_me = False
                    current_message = _stack[-1][1]

            if not top_is_not_me:
                frame = f"{chars[i % len(chars)]} {current_message}"
                cb = _status_callback
                if cb:
                    cb(frame)
                else:
                    sys.stderr.write(f"\r{frame}   ")
                    sys.stderr.flush()
                i += 1

            if done.wait(interval):
                break

    t = threading.Thread(target=spin, daemon=True)
    t.start()
    try:
        yield
    finally:
        done.set()
        t.join(interval * 2)
        with _lock:
            # We filter out this spinner from the stack
            _stack = [entry for entry in _stack if entry[0] != spinner_id]
            if not _stack:
                if _status_callback:
                    _status_callback("")
                else:
                    sys.stderr.write("\r" + " " * (len(message) + 10) + "\r")
                    sys.stderr.flush()
            else:
                # Nitpick from review: trigger an immediate refresh for the next item in stack
                next_message = _stack[-1][1]
                # We can't easily get the next item's frame because we don't know its 'i'
                # but we can at least show its message with a placeholder or first frame.
                # Actually, the thread for the next item will pick it up on its next tick.
                # If we want immediate, we'd need a more central management.
                # For now, let's just let the other threads handle it.
                pass
