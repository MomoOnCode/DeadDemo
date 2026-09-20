"""Match history from deadlock-api.com with local/parsed state and download actions."""

from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.gui.context import AppContext
from deaddemo.gui.models.matches_model import MatchesModel, MatchListRow


class MatchesPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.model = MatchesModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)

        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh history")
        self.btn_force = QPushButton("Force refetch")
        self.btn_force.setToolTip("Ask deadlock-api.com to pull fresh data from Valve (limited to once per hour)")
        self.btn_download = QPushButton("Download selected")
        self.btn_download_recent = QPushButton("Download missing from last")
        self.days = QSpinBox()
        self.days.setRange(1, 60)
        self.days.setValue(7)
        self.days.setSuffix(" days")
        self.btn_open = QPushButton("Open match")
        self.btn_parse = QPushButton("Parse local")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        for w in (self.btn_refresh, self.btn_force, self.btn_download, self.btn_download_recent, self.days,
                  self.btn_open, self.btn_parse):
            bar.addWidget(w)
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
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.table.doubleClicked.connect(lambda _i: self._activate())
        layout.addWidget(self.table, 1)
        self.info = QLabel("")
        layout.addWidget(self.info)

        self.btn_refresh.clicked.connect(lambda: self.refresh_history(force=False))
        self.btn_force.clicked.connect(lambda: self.refresh_history(force=True))
        self.btn_download.clicked.connect(self._download_selected)
        self.btn_download_recent.clicked.connect(self._download_recent)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_parse.clicked.connect(self._parse_selected)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        for sig in (ctx.events.history_changed, ctx.events.demos_changed, ctx.events.matches_changed,
                    ctx.events.downloads_changed, ctx.events.settings_changed):
            sig.connect(self.reload)
        self.reload()

    # -- data ------------------------------------------------------------------------
    def reload(self) -> None:
        acct = self.ctx.install.account
        if not acct:
            self.model.set_rows([])
            self.info.setText("No Steam account detected. Set an account id in Settings.")
            return
        history = self.ctx.history.for_account(acct.account_id)
        local_ids = {d.match_id for d in self.ctx.demos.all() if d.match_id and d.status in ("found", "parsed")}
        parsed_ids = {m.match_id for m in self.ctx.matches.all()}
        active = self.ctx.downloads.active_match_ids()
        cat = catalog()
        rows = []
        for h in history:
            salts = self.ctx.history.salts(h.match_id)
            known = None if salts is None else bool(salts.replay_salt)
            rows.append(MatchListRow(h, cat.hero_name(h.hero_id), h.match_id in local_ids, h.match_id in parsed_ids,
                                     h.match_id in active, known))
        self.model.set_rows(rows)
        self.table.resizeColumnsToContents()
        wins = sum(1 for r in rows if r.won)
        self.info.setText(f"{acct.persona_name}: {len(rows)} matches, {wins} wins, "
                          f"{sum(1 for r in rows if r.local)} local, {sum(1 for r in rows if r.parsed)} parsed")

    def selected(self) -> list[MatchListRow]:
        out = []
        for idx in self.table.selectionModel().selectedRows():
            r = self.model.row_at(self.proxy.mapToSource(idx))
            if r is not None:
                out.append(r)
        return out

    # -- actions ---------------------------------------------------------------------
    def refresh_history(self, force: bool) -> None:
        if self.ctx.jobs.is_running("history"):
            return
        acct = self.ctx.install.account
        if not acct:
            self.ctx.status("No Steam account detected; set an account id in Settings", 8000)
            return
        from deaddemo.core.api.client import default_client

        client = default_client()
        account_id = acct.account_id

        def work(progress, cancel):
            progress(0, 0, "Fetching match history")
            entries = client.match_history(account_id, force_refetch=force)
            catalog().load_api()
            return entries

        def done(entries):
            entries.sort(key=lambda e: e.start_time or 0, reverse=True)
            n = self.ctx.history.upsert_many(account_id, [e.to_dict() for e in entries])
            self.ctx.status(f"Match history updated: {n} matches")
            self.ctx.events.history_changed.emit()

        self.ctx.jobs.submit("history", work, on_finished=done,
                             on_failed=lambda e: self.ctx.status(f"History refresh failed: {e.splitlines()[0]}", 12000))

    def _download_selected(self) -> None:

        rows = [r for r in self.selected() if not r.local and not r.downloading]
        if not rows:
            self.ctx.status("Select matches that are not already local")
            return
        self._download_rows(rows)

    def _download_recent(self) -> None:
        cutoff_days = self.days.value()
        rows = [r for r in self.model.rows
                if not r.local and not r.downloading and r.history.start_time
                and (r.history.age_days() or 0) <= cutoff_days]
        if not rows:
            self.ctx.status("Nothing to download")
            return
        self._download_rows(rows)

    def _download_rows(self, rows: list[MatchListRow]) -> None:
        """Resolve salts in a worker first (network), then queue downloads on the main thread."""
        from deaddemo.core.api.service import resolve_salts
        from deaddemo.gui.download_jobs import start_download

        ids = [r.match_id for r in rows]
        db = self.ctx.db

        def work(progress, cancel):
            ok, missing = [], []
            for i, mid in enumerate(ids):
                if cancel.is_set():
                    break
                progress(i, len(ids), f"Resolving replay {mid}")
                try:
                    s = resolve_salts(mid, db=db)
                    if not s.demo_url:
                        s = resolve_salts(mid, db=db, force=True)
                    (ok if s.demo_url else missing).append(mid)
                except Exception:  # noqa: BLE001
                    missing.append(mid)
            return ok, missing

        def done(result):
            ok, missing = result
            started = sum(1 for mid in ok if start_download(self.ctx, mid))
            msg = f"Queued {started} download(s)"
            if missing:
                msg += f"; no replay available for {len(missing)} match(es): " + ", ".join(map(str, missing[:5]))
            self.ctx.status(msg, 12000)
            self.ctx.events.history_changed.emit()

        self.ctx.jobs.submit("resolve-salts", work, on_finished=done,
                             on_failed=lambda e: self.ctx.status(f"Salt lookup failed: {e.splitlines()[0]}", 12000))

    def _open_selected(self) -> None:
        rows = self.selected()
        if rows:
            self._activate_row(rows[0])

    def _activate(self) -> None:
        rows = self.selected()
        if rows:
            self._activate_row(rows[0])

    def _activate_row(self, r: MatchListRow) -> None:
        if r.parsed:
            self.ctx.events.open_match.emit(r.match_id)
        elif r.local:
            self._parse_match(r.match_id)
        else:
            self._download_rows([r])

    def _parse_selected(self) -> None:
        for r in self.selected():
            if r.local and not r.parsed:
                self._parse_match(r.match_id)

    def _parse_match(self, match_id: int) -> None:
        from deaddemo.gui.parse_jobs import start_parse

        demos = [d for d in self.ctx.demos.by_match(match_id) if d.status in ("found", "error", "parsed")]
        if demos:
            start_parse(self.ctx, demos[0])
