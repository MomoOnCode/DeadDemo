"""Record dialog: output settings, capability status, live log, cancel."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from deaddemo.core.db.repos import MatchRow
from deaddemo.core.video import director
from deaddemo.core.video.sequences import Sequence
from deaddemo.gui.context import AppContext

PRESETS = [("Native (2560×1440)", 2560, 1440), ("1080p", 1920, 1080), ("1440p", 2560, 1440), ("4K", 3840, 2160),
           ("720p", 1280, 720)]


class RecordDialog(QDialog):
    def __init__(self, ctx: AppContext, match: MatchRow, sequences: list[Sequence], parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.match = match
        self.sequences = sequences
        self.cancel = threading.Event()
        self.setWindowTitle(f"Record {len(sequences)} clip(s) — match {match.match_id}")
        self.setMinimumSize(760, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        s = ctx.settings
        self.preset = QComboBox()
        for name, w, h in PRESETS:
            self.preset.addItem(name, (w, h))
        idx = next((i for i, (_n, w, h) in enumerate(PRESETS) if (w, h) == (s.video_width, s.video_height)), 1)
        self.preset.setCurrentIndex(idx)
        self.fps = QComboBox()
        self.fps.addItems(["30", "60", "120"])
        self.fps.setCurrentText(str(s.video_fps))
        self.quality = QComboBox()
        self.quality.addItems(["low", "medium", "high", "max"])
        self.quality.setCurrentText(s.video_quality)
        self.backend = QComboBox()
        self.backend.addItem("window — capture the game window even while other windows cover it (no audio)",
                             "window")
        self.backend.addItem("screen — desktop duplication; the game must stay on top (no audio)", "screen")
        self.backend.addItem("engine — startmovie (experimental; blocked on current builds)", "engine")
        self.backend.addItem("auto — engine if the probe says it works, else window", "auto")
        for i in range(self.backend.count()):
            if self.backend.itemData(i) == s.video_backend:
                self.backend.setCurrentIndex(i)
        self.hide_hud = QCheckBox("Hide HUD")
        self.hide_hud.setChecked(s.video_hide_hud)
        self.concat = QCheckBox("Also join all clips into one file")
        self.concat.setChecked(s.video_concat)
        self.limit = QSpinBox()
        self.limit.setRange(1, max(1, len(sequences)))
        self.limit.setValue(len(sequences))
        form.addRow("Resolution", self.preset)
        form.addRow("Frame rate", self.fps)
        form.addRow("Quality", self.quality)
        form.addRow("Recorder", self.backend)
        form.addRow("", self.hide_hud)
        form.addRow("", self.concat)
        form.addRow("Clips to record", self.limit)
        caps = director.Capabilities.load()
        if caps:
            rec = "works" if caps.startmovie_works else "unavailable" if caps.startmovie_works is False else "untested"
            txt = (f"Last probe ({caps.checked_at[:16]}): console {caps.transport or 'none'}, seek "
                   f"{'ok' if caps.seek_works else 'failed'}, engine recorder {rec}")
        else:
            txt = "No probe yet (Settings → Probe game capabilities checks console control and seeking)."
        note = QLabel(
            txt + "\n\nThe game is launched through Steam with -insecure and driven over its remote console; the "
            "window is captured in real time at the chosen size; with the window backend you may keep using the PC, "
            "just do not minimize or resize the game. Close Deadlock first. Your video settings (cfg/video.txt) "
            "and machine convars are snapshotted before the launch and restored once the game has exited. "
            "The engine recorder additionally "
            "edits game/citadel/gameinfo.gi for one launch and restores it."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #bbb")
        layout.addWidget(note)
        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, len(sequences)))
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        layout.addWidget(self.log, 1)
        btns = QHBoxLayout()
        self.btn_start = QPushButton("Start recording")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_close = QPushButton("Close")
        self.btn_cancel.setEnabled(False)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_cancel)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        layout.addLayout(btns)
        self.btn_start.clicked.connect(self._start)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_close.clicked.connect(self.reject)
        self._running = False

    def _settings(self) -> director.RecordSettings:
        w, h = self.preset.currentData()
        s = self.ctx.settings
        return director.RecordSettings(width=w, height=h, fps=int(self.fps.currentText()),
                                       quality=self.quality.currentText(), backend=self.backend.currentData(),
                                       hide_hud=self.hide_hud.isChecked(), concat=self.concat.isChecked(),
                                       output_dir=s.resolved_video_dir(), vcon_port=s.vconsole_port,
                                       launch_mode=s.video_launch_mode)

    def _start(self) -> None:
        rs = self._settings()
        s = self.ctx.settings
        s.video_width, s.video_height, s.video_fps = rs.width, rs.height, rs.fps
        s.video_quality, s.video_backend, s.video_hide_hud, s.video_concat = (rs.quality, rs.backend, rs.hide_hud,
                                                                              rs.concat)
        s.save()
        seqs = self.sequences[: self.limit.value()]
        self.cancel.clear()
        self._running = True
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_close.setEnabled(False)
        install, db, match_id = self.ctx.install, self.ctx.db, self.match.match_id
        signals_log = self._append

        def work(progress, cancel_event):
            def on_progress(i, n, label):
                progress(i, n, label)

            return director.record(install, db, match_id, seqs, rs, signals_log, self.cancel, on_progress)

        def done(results):
            for r in results:
                if r.path:
                    self.ctx.videos.add(match_id, str(r.path), [x.id for x in seqs if x.id], rs.width, rs.height,
                                        rs.fps, rs.backend, r.duration_s, r.sequence.label)
            ok = sum(1 for r in results if r.path)
            self._append(f"Done: {ok}/{len(results)} clip(s) written to {rs.output_dir / str(match_id)}")
            self.ctx.events.videos_changed.emit()
            self._finish()

        def failed(err: str):
            self._append("ERROR: " + err.splitlines()[0])
            self._finish()

        def on_progress(i, n, label):
            self.progress.setValue(i)
            self.progress.setFormat(f"{label}  ({i + 1}/{n})")

        self.ctx.jobs.submit(f"record:{match_id}", work, on_finished=done, on_failed=failed, on_progress=on_progress)

    def _append(self, text: str) -> None:
        from PySide6.QtCore import QMetaObject
        from PySide6.QtCore import Qt as _Qt

        # may be called from the worker thread; hop to the GUI thread
        self._pending = text
        QMetaObject.invokeMethod(self, "_append_gui", _Qt.ConnectionType.QueuedConnection)

    def _finish(self) -> None:
        self._running = False
        self.btn_cancel.setEnabled(False)
        self.btn_close.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.progress.setValue(self.progress.maximum())

    def _cancel(self) -> None:
        self.cancel.set()
        self._append("Cancelling… the game will close after the current step.")

    def reject(self) -> None:
        if self._running:
            self._cancel()
            return
        super().reject()

    # Qt slot for cross-thread log appends
    from PySide6.QtCore import Slot

    @Slot()
    def _append_gui(self) -> None:
        text = getattr(self, "_pending", "")
        if text:
            self.log.appendPlainText(text)
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())


def open_folder(path: Path) -> None:
    import subprocess
    import sys

    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)] if path.is_file() else ["explorer", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])


_ = Qt  # keep import for potential alignment use
