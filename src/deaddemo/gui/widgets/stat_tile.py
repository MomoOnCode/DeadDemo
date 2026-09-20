"""Dashboard tiles: a title, one big value, optional sub-rows of label/value pairs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class StatTile(QFrame):
    def __init__(self, title: str, value: str = "", rows: list[tuple[str, str]] | None = None,
                 subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("StatTile { background: #202020; border: 1px solid #333; border-radius: 6px; }")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("color: #bdbdbd; font-weight: 600; font-size: 12px;")
        lay.addWidget(t)
        self.value = QLabel(value)
        self.value.setStyleSheet("color: #f2f2f2; font-size: 24px; font-weight: 700;")
        self.value.setVisible(bool(value))
        lay.addWidget(self.value)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet("color: #8f8f8f; font-size: 11px;")
            s.setWordWrap(True)
            lay.addWidget(s)
        if rows:
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(1)
            for i, (label, val) in enumerate(rows):
                left = QLabel(label)
                left.setStyleSheet("color: #cfcfcf; font-size: 12px;")
                right = QLabel(val)
                right.setStyleSheet("color: #f2f2f2; font-size: 12px; font-weight: 600;")
                right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                grid.addWidget(left, i, 0)
                grid.addWidget(right, i, 1)
            grid.setColumnStretch(0, 1)
            lay.addLayout(grid)


class TileGrid(QWidget):
    """Lays tiles out in rows of ``columns``; tiles may span more than one column."""

    def __init__(self, columns: int = 6, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.grid = QGridLayout(self)
        self.grid.setSpacing(8)
        self._row = 0
        self._col = 0
        for c in range(columns):
            self.grid.setColumnStretch(c, 1)

    def add(self, tile: QWidget, span: int = 1) -> None:
        span = max(1, min(span, self.columns))
        if self._col + span > self.columns:
            self._row += 1
            self._col = 0
        self.grid.addWidget(tile, self._row, self._col, 1, span, Qt.AlignmentFlag.AlignTop)
        self._col += span

    def newline(self) -> None:
        if self._col:
            self._row += 1
            self._col = 0

    def clear(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._row = self._col = 0


def row(*widgets: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    return w
