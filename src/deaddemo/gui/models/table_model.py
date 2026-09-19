"""Generic column-spec driven table model over a list of row objects."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor


@dataclass
class Column:
    header: str
    getter: Callable[[Any], Any]
    formatter: Callable[[Any], str] = lambda v: "" if v is None else str(v)
    align_right: bool = False
    sort_key: Callable[[Any], Any] | None = None


class RowTableModel(QAbstractTableModel):
    def __init__(self, columns: Sequence[Column], parent=None):
        super().__init__(parent)
        self.columns = list(columns)
        self.rows: list[Any] = []

    def set_rows(self, rows: list[Any]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def row_at(self, index: QModelIndex) -> Any | None:
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        return self.rows[index.row()]

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].header
        return None

    def row_background(self, row: Any) -> QColor | None:  # override in subclasses
        return None

    def row_foreground(self, row: Any) -> QColor | None:
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return col.formatter(col.getter(row))
        if role == Qt.ItemDataRole.UserRole:  # raw value for sorting
            key = col.sort_key(row) if col.sort_key else col.getter(row)
            return key if key is not None else ""
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col.align_right:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.BackgroundRole:
            c = self.row_background(row)
            return QBrush(c) if c else None
        if role == Qt.ItemDataRole.ForegroundRole:
            c = self.row_foreground(row)
            return QBrush(c) if c else None
        return None
