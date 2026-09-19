from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.stats.player_stats import PlayerAgg, matches_for_player, player_overview
from deaddemo.gui.context import AppContext
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import fmt_clock, team_name


class PlayersModel(RowTableModel):
    def __init__(self, parent=None):
        cat = catalog()
        super().__init__(
            [
                Column("Player", lambda r: r.player_name),
                Column("SteamID64", lambda r: r.steam_id, align_right=True),
                Column("Games", lambda r: r.games, align_right=True),
                Column("Win %", lambda r: r.winrate, lambda v: f"{v * 100:.0f}%", align_right=True),
                Column("K", lambda r: r.kills, lambda v: f"{v:.1f}", align_right=True),
                Column("D", lambda r: r.deaths, lambda v: f"{v:.1f}", align_right=True),
                Column("A", lambda r: r.assists, lambda v: f"{v:.1f}", align_right=True),
                Column("KDA", lambda r: r.kda, lambda v: f"{v:.2f}", align_right=True),
                Column("Souls/min", lambda r: r.souls_per_min, lambda v: f"{v:.0f}", align_right=True),
                Column("Top heroes", lambda r: ", ".join(cat.hero_name(h) for h in r.top_heroes)),
            ],
            parent,
        )


class PlayerMatchesModel(RowTableModel):
    def __init__(self, parent=None):
        cat = catalog()
        super().__init__(
            [
                Column("Match", lambda r: r["match_id"], align_right=True),
                Column("Hero", lambda r: cat.hero_name(r["hero_id"])),
                Column("Team", lambda r: team_name(r["team_num"])),
                Column("Result", lambda r: r["won"], lambda v: "" if v is None else ("Win" if v else "Loss")),
                Column("K/D/A", lambda r: f"{r['kills']}/{r['deaths']}/{r['assists']}"),
                Column("Souls", lambda r: r["souls"], align_right=True),
                Column("Length", lambda r: r["regulation_seconds"], fmt_clock, align_right=True),
                Column("Parsed", lambda r: r["parsed_at"]),
            ],
            parent,
        )


class PlayersPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter players…")
        layout.addWidget(self.search)
        split = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(split, 1)

        self.model = PlayersModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        split.addWidget(self.table)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.detail_label = QLabel("Select a player to list their parsed matches")
        bl.addWidget(self.detail_label)
        self.matches_model = PlayerMatchesModel(self)
        self.matches_table = QTableView()
        self.matches_table.setModel(self.matches_model)
        self.matches_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.matches_table.verticalHeader().setVisible(False)
        self.matches_table.horizontalHeader().setStretchLastSection(True)
        self.matches_table.doubleClicked.connect(self._open_match)
        bl.addWidget(self.matches_table, 1)
        split.addWidget(bottom)
        split.setSizes([500, 300])

        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._show_player())
        ctx.events.matches_changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        self.model.set_rows(player_overview(self.ctx.db))
        self.table.resizeColumnsToContents()

    def _selected(self) -> PlayerAgg | None:
        idx = self.table.selectionModel().selectedRows()
        return self.model.row_at(self.proxy.mapToSource(idx[0])) if idx else None

    def _show_player(self) -> None:
        p = self._selected()
        if not p:
            return
        rows = matches_for_player(self.ctx.db, p.steam_id)
        self.matches_model.set_rows(rows)
        self.matches_table.resizeColumnsToContents()
        self.detail_label.setText(f"{p.player_name} — {len(rows)} parsed match(es)")

    def _open_match(self, index) -> None:
        r = self.matches_model.row_at(index)
        if r:
            self.ctx.events.open_match.emit(int(r["match_id"]))
