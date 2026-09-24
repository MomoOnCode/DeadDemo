"""Small GUI helpers."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer


def debounced(parent: QObject, fn: Callable[[], None], delay_ms: int = 150) -> Callable[..., None]:
    """Return a slot that runs ``fn`` once, ``delay_ms`` after the last call.

    Pages reload their tables on ``*_changed`` events; a download or parse can fire those many times a
    second, and a full table rebuild each time is what freezes the GUI. Coalescing them keeps one rebuild
    per burst."""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(delay_ms)
    timer.timeout.connect(fn)

    def trigger(*_args) -> None:
        timer.start()

    return trigger
