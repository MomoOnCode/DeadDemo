"""Local replay library: scan folders, show demos, parse, clean up partial files."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.db.repos import DemoRow
from deaddemo.gui.context import AppContext
from deaddemo.gui.models.demos_model import DemosModel


class DemosPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.model = DemosModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)

        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_scan = QPushButton("Rescan")
        self.btn_parse = QPushButton("Analyze")
        self.btn_reparse = QPushButton("Re-analyze")
        self.btn_open = QPushButton("Open match")
        self.btn_folder = QPushButton("Show in folder")
        self.btn_copy = QPushButton("Copy playdemo command")
        self.btn_drop_ticks = QPushButton("Delete positions")
        self.btn_delete_partial = QPushButton("Delete .partial files")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        for b in (self.btn_scan, self.btn_parse, self.btn_reparse, self.btn_open, self.btn_folder, self.btn_copy,
                  self.btn_drop_ticks, self.btn_delete_partial):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.search)
        layout.addLayout(bar)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.sortByColumn(5, Qt.SortOrder.DescendingOrder)
        self.table.doubleClicked.connect(lambda _i: self._open_selected())
        layout.addWidget(self.table, 1)

        self.info = QLabel("")
        layout.addWidget(self.info)

        self.btn_scan.clicked.connect(self.rescan)
        self.btn_parse.clicked.connect(lambda: self._parse_selected(force=False))
        self.btn_reparse.clicked.connect(lambda: self._parse_selected(force=True))
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_folder.clicked.connect(self._show_in_folder)
        self.btn_copy.clicked.connect(self._copy_playdemo)
        self.btn_drop_ticks.clicked.connect(self._delete_positions)
        self.btn_delete_partial.clicked.connect(self._delete_partials)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        from deaddemo.gui.util import debounced

        reload = debounced(self, self.reload)
        ctx.events.demos_changed.connect(reload)
        ctx.events.settings_changed.connect(reload)
        self.reload()

    # -- data ------------------------------------------------------------------------
    def reload(self) -> None:
        rows = self.ctx.demos.all()
        tags = self.ctx.tags.tags_by_match()
        for r in rows:
            r.tags = tags.get(r.match_id or -1, [])  # type: ignore[attr-defined]
        self.model.set_rows(rows)
        self.table.resizeColumnsToContents()
        n_partial = sum(1 for r in rows if r.status == "partial")
        outdated = sum(1 for r in rows if r.build and self.ctx.install.client_build
                       and r.build < self.ctx.install.client_build and r.status != "missing")
        total = sum(r.size_bytes for r in rows if r.status != "missing")
        msg = f"{len(rows)} demos, {total / 1e9:.1f} GB on disk"
        if n_partial:
            msg += f" — {n_partial} partial download(s) (these break the in-game replay browser)"
        if outdated:
            msg += f" — {outdated} recorded on an older build than the installed client"
        self.info.setText(msg)
        self.btn_delete_partial.setEnabled(n_partial > 0)

    def selected(self) -> list[DemoRow]:
        rows = []
        for idx in self.table.selectionModel().selectedRows():
            r = self.model.row_at(self.proxy.mapToSource(idx))
            if r is not None:
                rows.append(r)
        return rows

    # -- actions ---------------------------------------------------------------------
    def rescan(self) -> None:
        if self.ctx.jobs.is_running("scan"):
            return
        from deaddemo.core.replays import scanner

        install = self.ctx.install
        settings = self.ctx.settings
        extra = [Path(p) for p in settings.extra_replay_dirs]
        known = {r.path: (r.size_bytes, r.mtime, r.status, r) for r in self.ctx.demos.all()}

        def work(progress, cancel):
            candidates = scanner.list_candidates(install.replay_dirs, extra, settings.resolved_download_dir())
            found = []
            for i, (source, p) in enumerate(candidates):
                if cancel.is_set():
                    break
                progress(i, len(candidates), f"Scanning {p.name}")
                stat = p.stat()
                k = known.get(str(p))
                if k and k[0] == stat.st_size and k[1] == stat.st_mtime and k[2] not in ("missing", "error"):
                    r = k[3]
                    found.append(scanner.ReplayFile(path=p, source=source, size_bytes=stat.st_size,
                                                    mtime=stat.st_mtime, is_partial=r.status == "partial",
                                                    match_id=r.match_id, build=r.build, map_name=r.map_name,
                                                    tick_rate=r.tick_rate, total_ticks=r.total_ticks))
                else:
                    found.append(scanner.sniff_header(p, source))
            return found

        def done(found):
            report = scanner.sync_to_db(self.ctx.demos, found)
            self.ctx.status(f"Scan complete: {len(found)} files, {report.added} new, {report.missing} missing")
            self.ctx.events.demos_changed.emit()

        self.ctx.jobs.submit("scan", work, on_finished=done,
                             on_failed=lambda e: self.ctx.status(f"Scan failed: {e.splitlines()[0]}", 10000))

    def _parse_selected(self, force: bool) -> None:
        rows = [r for r in self.selected() if r.status in ("found", "parsed", "error")]
        if not rows:
            self.ctx.status("Select one or more demos to analyze")
            return
        from deaddemo.gui.parse_jobs import start_parse

        for r in rows:
            if r.status == "parsed" and not force:
                continue
            start_parse(self.ctx, r)

    def _open_selected(self) -> None:
        rows = self.selected()
        if rows and rows[0].match_id:
            if self.ctx.matches.get(rows[0].match_id) is None:
                self.ctx.status("Analyze this demo first to view match details")
                return
            self.ctx.events.open_match.emit(rows[0].match_id)

    def _show_in_folder(self) -> None:
        rows = self.selected()
        if not rows:
            return
        p = Path(rows[0].path)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])

    def _copy_playdemo(self) -> None:
        rows = self.selected()
        if not rows:
            return
        p = Path(rows[0].path)
        cmd = f"playdemo replays/{p.stem}" if p.suffix == ".dem" else f"playdemo {p}"
        QApplication.clipboard().setText(cmd)
        self.ctx.status(f"Copied: {cmd}   (open the in-game console with F7 and paste)", 8000)

    def _delete_positions(self) -> None:
        rows = [r for r in self.selected() if r.match_id and self.ctx.matches.get(r.match_id)]
        if not rows:
            return
        from deaddemo.core.parse.runner import delete_positions

        for r in rows:
            delete_positions(self.ctx.db, r.match_id)
        self.ctx.status(f"Deleted position data for {len(rows)} match(es)")
        self.ctx.events.matches_changed.emit()

    def _delete_partials(self) -> None:
        partials = [r for r in self.ctx.demos.all() if r.status == "partial"]
        if not partials:
            return
        names = "\n".join(Path(r.path).name for r in partials)
        answer = QMessageBox.question(self, "Delete partial downloads",
                                      f"Delete {len(partials)} partial file(s)?\n\n{names}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        for r in partials:
            try:
                os.remove(r.path)
                self.ctx.demos.delete(r.id)
            except OSError as exc:
                self.ctx.status(f"Could not delete {r.path}: {exc}", 10000)
        self.ctx.events.demos_changed.emit()
