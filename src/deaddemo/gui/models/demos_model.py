from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QColor

from deaddemo.core.db.repos import DemoRow
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import STATUS_COLORS, fmt_bytes


def _fmt_mtime(v: float | None) -> str:
    return datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M") if v else ""


class DemosModel(RowTableModel):
    def __init__(self, parent=None):
        super().__init__(
            [
                Column("File", lambda r: Path(r.path).name),
                Column("Match", lambda r: r.match_id, align_right=True),
                Column("Map", lambda r: r.map_name),
                Column("Build", lambda r: r.build, align_right=True),
                Column("Size", lambda r: r.size_bytes, fmt_bytes, align_right=True),
                Column("Modified", lambda r: r.mtime, _fmt_mtime),
                Column("Status", lambda r: r.status,
                       lambda v: {"parsed": "analyzed", "found": "ready", "partial": "partial download"}.get(v, v)),
                Column("Source", lambda r: r.source),
                Column("Tags", lambda r: ", ".join(getattr(r, "tags", []) or [])),
                Column("Folder", lambda r: str(Path(r.path).parent)),
            ],
            parent,
        )
        self.client_build: int | None = None

    def row_background(self, row: DemoRow) -> QColor | None:
        return STATUS_COLORS.get(row.status)

    def row_foreground(self, row: DemoRow) -> QColor | None:
        return QColor("#dddddd")
