from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from deaddemo.core.db.repos import DownloadRow
from deaddemo.gui.context import AppContext
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import fmt_bytes


class _ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        row: DownloadRow | None = index.data(Qt.ItemDataRole.UserRole + 1)
        if row is None or row.status not in ("running", "queued"):
            return super().paint(painter, option, index)
        opt = QStyleOptionProgressBar()
        opt.rect = option.rect.adjusted(2, 2, -2, -2)
        opt.minimum, opt.maximum = 0, 100
        pct = int(100 * row.bytes_done / row.bytes_total) if row.bytes_total else 0
        opt.progress = pct
        opt.text = f"{fmt_bytes(row.bytes_done)} / {fmt_bytes(row.bytes_total)}" if row.bytes_total else fmt_bytes(
            row.bytes_done)
        opt.textVisible = True
        QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, opt, painter)


class DownloadsModel(RowTableModel):
    def __init__(self, parent=None):
        super().__init__(
            [
                Column("Match", lambda r: r.match_id, align_right=True),
                Column("Status", lambda r: r.status),
                Column("Progress", lambda r: r.bytes_done, lambda v: "", align_right=True),
                Column("Started", lambda r: r.started_at),
                Column("Finished", lambda r: r.finished_at),
                Column("File", lambda r: r.dest_path),
                Column("Error", lambda r: r.error),
            ],
            parent,
        )

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.UserRole + 1:
            return self.row_at(index)
        if role == Qt.ItemDataRole.DisplayRole and index.column() == 2:
            r = self.row_at(index)
            if r and r.status == "done":
                return fmt_bytes(r.bytes_done)
            return ""
        return super().data(index, role)


class DownloadsPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.model = DownloadsModel(self)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_retry = QPushButton("Retry")
        self.btn_clear = QPushButton("Clear finished")
        for b in (self.btn_cancel, self.btn_retry, self.btn_clear):
            bar.addWidget(b)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(2, _ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_retry.clicked.connect(self._retry)
        self.btn_clear.clicked.connect(self._clear)
        ctx.events.downloads_changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        selected_ids = {self.model.row_at(i).id for i in sel if self.model.row_at(i)}
        self.model.set_rows(self.ctx.downloads.all())
        self.table.resizeColumnsToContents()
        for i, r in enumerate(self.model.rows):
            if r.id in selected_ids:
                self.table.selectRow(i)

    def _selected(self) -> list[DownloadRow]:
        return [self.model.row_at(i) for i in self.table.selectionModel().selectedRows() if self.model.row_at(i)]

    def _cancel(self) -> None:
        from deaddemo.gui.download_jobs import cancel_download

        for r in self._selected():
            if r.status in ("running", "queued"):
                cancel_download(self.ctx, r.match_id)
                if not self.ctx.jobs.is_running(f"download:{r.match_id}"):
                    self.ctx.downloads.set_status(r.id, "cancelled")
        self.ctx.events.downloads_changed.emit()

    def _retry(self) -> None:
        from deaddemo.gui.download_jobs import start_download

        for r in self._selected():
            if r.status in ("failed", "cancelled"):
                start_download(self.ctx, r.match_id)

    def _clear(self) -> None:
        with self.ctx.db.transaction() as conn:
            conn.execute("DELETE FROM downloads WHERE status IN ('done','failed','cancelled')")
        self.ctx.events.downloads_changed.emit()
