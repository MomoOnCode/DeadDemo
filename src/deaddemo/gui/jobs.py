"""Background job execution for the GUI.

* IO-bound work (scans, API calls, downloads) runs on a ``QThreadPool``.
* CPU-bound demo parsing runs in a spawn ``ProcessPoolExecutor`` so boon's Rust core never
  blocks the UI and a parser crash cannot take the app down.

Workers never touch SQLite; they return data and the main thread stores it.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class JobSignals(QObject):
    progress = Signal(int, int, str)  # done, total, message
    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)


class Job(QRunnable):
    """Run ``fn(progress, cancel_event)`` on the thread pool."""

    def __init__(self, name: str, fn: Callable[..., Any]):
        super().__init__()
        self.name = name
        self.fn = fn
        self.signals = JobSignals()
        self.cancel_event = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(self.signals.progress.emit, self.cancel_event)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            return
        self.signals.finished.emit(result)


class JobManager(QObject):
    """Owns the thread pool and a lazily-created process pool for parses."""

    job_started = Signal(str)
    job_finished = Signal(str)

    def __init__(self, parse_workers: int = 1, parent: QObject | None = None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(6)
        self._parse_workers = max(1, parse_workers)
        self._process_pool: ProcessPoolExecutor | None = None
        self.active: dict[str, Job] = {}

    # -- thread jobs -------------------------------------------------------------------
    def submit(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        on_finished: Callable[[Any], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> Job:
        job = Job(name, fn)
        if on_finished:
            job.signals.finished.connect(on_finished)
        if on_failed:
            job.signals.failed.connect(on_failed)
        if on_progress:
            job.signals.progress.connect(on_progress)
        job.signals.finished.connect(lambda _r, n=name: self._done(n))
        job.signals.failed.connect(lambda _e, n=name: self._done(n))
        self.active[name] = job
        self.job_started.emit(name)
        self.pool.start(job)
        return job

    def _done(self, name: str) -> None:
        self.active.pop(name, None)
        self.job_finished.emit(name)

    def is_running(self, name: str) -> bool:
        return name in self.active

    # -- process jobs ------------------------------------------------------------------
    def process_pool(self) -> ProcessPoolExecutor:
        if self._process_pool is None:
            import multiprocessing

            self._process_pool = ProcessPoolExecutor(
                max_workers=self._parse_workers, mp_context=multiprocessing.get_context("spawn")
            )
        return self._process_pool

    def submit_process(
        self,
        name: str,
        fn: Callable[..., Any],
        *args: Any,
        on_finished: Callable[[Any], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> Job:
        """Run ``fn(*args)`` in the process pool, wrapped in a thread job for signals."""

        def runner(progress, cancel_event):
            progress(0, 0, f"{name}: queued")
            future: Future = self.process_pool().submit(fn, *args)
            while True:
                try:
                    return future.result(timeout=0.25)
                except TimeoutError:
                    if cancel_event.is_set():
                        future.cancel()
                        raise RuntimeError("cancelled") from None

        return self.submit(name, runner, on_finished=on_finished, on_failed=on_failed, on_progress=on_progress)

    def shutdown(self) -> None:
        for job in list(self.active.values()):
            job.cancel()
        self.pool.waitForDone(2000)
        if self._process_pool is not None:
            self._process_pool.shutdown(wait=False, cancel_futures=True)
