from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.video.sequences import VideoRow
from deaddemo.gui.context import AppContext
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import fmt_clock


class VideosModel(RowTableModel):
    def __init__(self, parent=None):
        super().__init__([
            Column("Match", lambda v: v.match_id, align_right=True),
            Column("Clip", lambda v: v.label or Path(v.path).stem),
            Column("Length", lambda v: v.duration_s, fmt_clock, align_right=True),
            Column("Size", lambda v: (v.width, v.height), lambda t: f"{t[0]}×{t[1]}"),
            Column("FPS", lambda v: v.fps, align_right=True),
            Column("Recorder", lambda v: v.backend),
            Column("Created", lambda v: v.created_at),
            Column("File", lambda v: v.path),
        ], parent)


class VideosPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_open = QPushButton("Play")
        self.btn_folder = QPushButton("Show in folder")
        self.btn_match = QPushButton("Open match")
        self.btn_delete = QPushButton("Delete file")
        for b in (self.btn_open, self.btn_folder, self.btn_match, self.btn_delete):
            bar.addWidget(b)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.model = VideosModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(lambda _i: self._play())
        layout.addWidget(self.table, 1)
        self.info = QLabel("Clips are recorded from the Clips tab of an analyzed match.")
        layout.addWidget(self.info)
        self.btn_open.clicked.connect(self._play)
        self.btn_folder.clicked.connect(self._folder)
        self.btn_match.clicked.connect(self._open_match)
        self.btn_delete.clicked.connect(self._delete)
        ctx.events.videos_changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        self.model.set_rows(self.ctx.videos.all())
        self.table.resizeColumnsToContents()

    def _selected(self) -> VideoRow | None:
        idx = self.table.selectionModel().selectedRows()
        return self.model.row_at(idx[0]) if idx else None

    def _play(self) -> None:
        v = self._selected()
        if v and Path(v.path).exists():
            if sys.platform == "win32":
                os.startfile(v.path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", v.path])

    def _folder(self) -> None:
        v = self._selected()
        if v:
            from deaddemo.gui.widgets.record_dialog import open_folder

            open_folder(Path(v.path))

    def _open_match(self) -> None:
        v = self._selected()
        if v:
            self.ctx.events.open_match.emit(v.match_id)

    def _delete(self) -> None:
        v = self._selected()
        if not v:
            return
        try:
            Path(v.path).unlink(missing_ok=True)
        except OSError as exc:
            self.ctx.status(f"Could not delete: {exc}", 8000)
        self.ctx.videos.delete(v.id)
        self.reload()
