from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from typing import Callable

BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_status_callback: Callable[[str], None] | None = None


def set_status_callback(callback: Callable[[str], None] | None) -> None:
    global _status_callback
    _status_callback = callback


@contextmanager
def spinner(message: str = "", *, interval: float = 0.1):
    done = threading.Event()
    chars = BRAILLE

    def spin() -> None:
        i = 0
        while not done.wait(interval):
            frame = f"{chars[i % len(chars)]} {message}"
            cb = _status_callback
            if cb:
                cb(frame)
            else:
                sys.stderr.write(f"\r{frame}   ")
                sys.stderr.flush()
            i += 1

    t = threading.Thread(target=spin, daemon=True)
    t.start()
    try:
        yield
    finally:
        done.set()
        t.join(interval * 2)
        if _status_callback:
            _status_callback("")
        else:
            sys.stderr.write("\r" + " " * (len(message) + 10) + "\r")
            sys.stderr.flush()
