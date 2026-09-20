"""One parsed match: scoreboard, timeline, graphs, builds, damage, chat, heatmap, notes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.db.repos import MatchPlayerRow, MatchRow
from deaddemo.core.stats import match_stats
from deaddemo.gui.context import AppContext
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import TEAM_COLORS, fmt_clock, fmt_souls, team_color, team_name


class ScoreboardModel(RowTableModel):
    def __init__(self, parent=None):
        cat = catalog()
        super().__init__(
            [
                Column("Player", lambda r: r.player_name),
                Column("Hero", lambda r: cat.hero_name(r.hero_id)),
                Column("Lane", lambda r: r.start_lane, align_right=True),
                Column("Lvl", lambda r: r.level, align_right=True),
                Column("K", lambda r: r.kills, align_right=True),
                Column("D", lambda r: r.deaths, align_right=True),
                Column("A", lambda r: r.assists, align_right=True),
                Column("KP", lambda r: r.kill_participation,
                       lambda v: "" if v is None else f"{v * 100:.0f}%", align_right=True),
                Column("Souls", lambda r: r.souls, fmt_souls, align_right=True),
                Column("LH", lambda r: r.last_hits, align_right=True),
                Column("Denies", lambda r: r.denies, align_right=True),
                Column("Hero dmg", lambda r: r.hero_damage, fmt_souls, align_right=True),
                Column("Dead", lambda r: r.time_dead_s, fmt_clock, align_right=True),
            ],
            parent,
        )

    def row_background(self, row: MatchPlayerRow) -> QColor | None:
        c = QColor(team_color(row.team_num))
        c.setAlpha(60)
        return c


class MatchDetailPage(QWidget):
    back_requested = Signal()

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.match: MatchRow | None = None
        self.players: list[MatchPlayerRow] = []
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_back = QPushButton("← Back")
        self.btn_back.clicked.connect(self.back_requested.emit)
        self.header = QLabel("")
        self.header.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.btn_viewer = QPushButton("Open 2D viewer")
        self.btn_viewer.clicked.connect(lambda: self.match and self.ctx.events.open_viewer.emit(self.match.match_id))
        self.btn_export = QPushButton("Export…")
        self.btn_export.clicked.connect(self._export)
        top.addWidget(self.btn_back)
        top.addWidget(self.header, 1)
        top.addWidget(self.btn_viewer)
        top.addWidget(self.btn_export)
        layout.addLayout(top)

        split = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(split, 1)

        boards = QWidget()
        bl = QHBoxLayout(boards)
        bl.setContentsMargins(0, 0, 0, 0)
        self.board_models: dict[int, ScoreboardModel] = {}
        self.board_tables: dict[int, QTableView] = {}
        for team in (2, 3):
            col = QVBoxLayout()
            lbl = QLabel(team_name(team))
            lbl.setStyleSheet(f"color: {TEAM_COLORS[team].name()}; font-weight: 600;")
            col.addWidget(lbl)
            model = ScoreboardModel(self)
            table = QTableView()
            table.setModel(model)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
            table.setMaximumHeight(220)
            table.doubleClicked.connect(lambda idx, m=model: self._open_player(m.row_at(idx)))
            table.setToolTip("Double-click a player for their profile")
            col.addWidget(table)
            bl.addLayout(col)
            self.board_models[team] = model
            self.board_tables[team] = table
        split.addWidget(boards)

        self.tabs = QTabWidget()
        split.addWidget(self.tabs)
        split.setSizes([260, 600])

        # Timeline tab
        self.timeline_model = RowTableModel([
            Column("Time", lambda e: e.match_seconds, fmt_clock, align_right=True),
            Column("Type", lambda e: e.kind),
            Column("Team", lambda e: team_name(e.team_num) if e.team_num else ""),
            Column("Event", lambda e: e.text),
        ], self)
        self.timeline_table = QTableView()
        self.timeline_table.setModel(self.timeline_model)
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.horizontalHeader().setStretchLastSection(True)
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline_filter = QComboBox()
        self.timeline_filter.addItems(["All", "Kills", "Objectives", "Teamfights"])
        self.timeline_filter.currentIndexChanged.connect(self._fill_timeline)
        tl = QWidget()
        tll = QVBoxLayout(tl)
        tll.addWidget(self.timeline_filter)
        tll.addWidget(self.timeline_table, 1)
        self.tabs.addTab(tl, "Timeline")

        # Graphs tab (the in-game graph menu + extras)
        from deaddemo.gui.widgets.graphs_widget import GraphsWidget

        self.graphs = GraphsWidget(ctx)
        self.tabs.addTab(self.graphs, "Graphs")

        # Builds tab
        from deaddemo.gui.widgets.build_widget import BuildWidget

        self.builds = BuildWidget(ctx)
        self.tabs.addTab(self.builds, "Builds")

        # Damage tab
        dmg = QWidget()
        dml = QVBoxLayout(dmg)
        self.dmg_model = RowTableModel([
            Column("Hero", lambda r: catalog().hero_name(r["hero_id"])),
            Column("Player", lambda r: r.get("player_name", "")),
            Column("Dealt", lambda r: r["dealt"], fmt_souls, align_right=True),
            Column("Taken", lambda r: r["taken"], fmt_souls, align_right=True),
        ], self)
        self.dmg_table = QTableView()
        self.dmg_table.setModel(self.dmg_model)
        self.dmg_table.verticalHeader().setVisible(False)
        self.dmg_table.horizontalHeader().setStretchLastSection(True)
        dml.addWidget(self.dmg_table, 1)
        self.tabs.addTab(dmg, "Damage")

        # Chat tab
        self.chat_view = QTextEdit()
        self.chat_view.setReadOnly(True)
        self.tabs.addTab(self.chat_view, "Chat")

        # Heatmap tab
        from deaddemo.gui.widgets.heatmap_widget import HeatmapWidget

        self.heatmap = HeatmapWidget(ctx)
        self.tabs.addTab(self.heatmap, "Heatmap")

        # Notes tab
        notes = QWidget()
        nl = QVBoxLayout(notes)
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("tags, comma separated")
        self.comment_edit = QTextEdit()
        save = QPushButton("Save notes")
        save.clicked.connect(self._save_notes)
        nl.addWidget(QLabel("Tags"))
        nl.addWidget(self.tags_edit)
        nl.addWidget(QLabel("Comment"))
        nl.addWidget(self.comment_edit, 1)
        nl.addWidget(save)
        self.tabs.addTab(notes, "Notes")

        self.tabs.currentChanged.connect(self._tab_changed)
        self._loaded_tabs: set[int] = set()

    # -- loading -----------------------------------------------------------------------
    def load_match(self, match_id: int) -> None:
        self.match = self.ctx.matches.get(match_id)
        if self.match is None:
            self.header.setText(f"Match {match_id} has not been analyzed yet")
            return
        self.players = self.ctx.matches.players(match_id)
        m = self.match
        winner = team_name(m.winning_team) if m.winning_team else "?"
        self.header.setText(
            f"Match {m.match_id} — {m.map_name} — {fmt_clock(m.regulation_seconds)} — {winner} won"
            f" — build {m.build} — analyzed with boon {m.boon_version}"
        )
        for team, model in self.board_models.items():
            model.set_rows([p for p in self.players if p.team_num == team])
            self.board_tables[team].resizeColumnsToContents()
        self.tags_edit.setText(", ".join(t.name for t in self.ctx.tags.tags_for_match(match_id)))
        self.comment_edit.setPlainText(self.ctx.tags.comment(match_id))
        self._loaded_tabs = set()
        self._tab_changed(self.tabs.currentIndex())

    def _tab_changed(self, index: int) -> None:
        if self.match is None or index in self._loaded_tabs:
            return
        self._loaded_tabs.add(index)
        name = self.tabs.tabText(index)
        try:
            if name == "Timeline":
                self._fill_timeline()
            elif name == "Graphs":
                self.graphs.load_match(self.match, self.players)
            elif name == "Builds":
                self.builds.load_match(self.match, self.players)
            elif name == "Damage":
                self._fill_damage()
            elif name == "Chat":
                self._fill_chat()
            elif name == "Heatmap":
                self.heatmap.load_match(self.match, self.players)
        except Exception as exc:  # noqa: BLE001
            self.ctx.status(f"Could not load {name}: {exc}", 10000)

    def _fill_timeline(self) -> None:
        if not self.match:
            return
        events = match_stats.timeline(self.ctx.db, self.match, catalog().hero_name)
        mode = self.timeline_filter.currentText()
        kinds = {"Kills": {"kill"}, "Objectives": {"objective", "midboss"}, "Teamfights": {"teamfight"}}.get(mode)
        if kinds:
            events = [e for e in events if e.kind in kinds]
        self.timeline_model.set_rows(events)
        self.timeline_table.resizeColumnsToContents()

    def _fill_damage(self) -> None:
        if not self.match:
            return
        df = match_stats.damage_summary(self.match)
        names = {p.hero_id: p.player_name for p in self.players}
        rows = [{**r, "player_name": names.get(r["hero_id"], "")} for r in df.to_dicts()] if df.height else []
        self.dmg_model.set_rows(rows)
        self.dmg_table.resizeColumnsToContents()

    def _fill_chat(self) -> None:
        if not self.match:
            return
        df = match_stats.chat(self.match)
        cat = catalog()
        lines = []
        for r in (df.to_dicts() if df.height else []):
            t = fmt_clock(r.get("match_seconds"))
            lines.append(f"[{t}] ({r.get('chat_type')}) {cat.hero_name(r.get('hero_id'))}: {r.get('text')}")
        self.chat_view.setPlainText("\n".join(lines) or "No chat in this demo (or 'chat' dataset not stored).")

    def _open_player(self, row: MatchPlayerRow | None) -> None:
        if row is not None and row.steam_id:
            self.ctx.events.open_player.emit(int(row.steam_id))

    def _save_notes(self) -> None:
        if not self.match:
            return
        names = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        ids = [self.ctx.tags.create(n).id for n in names]
        self.ctx.tags.set_match_tags(self.match.match_id, ids)
        self.ctx.tags.set_comment(self.match.match_id, self.comment_edit.toPlainText())
        self.ctx.status("Notes saved")
        self.ctx.events.demos_changed.emit()

    def _export(self) -> None:
        if not self.match:
            return
        from deaddemo.core.export.exporter import export_match_json, export_match_xlsx

        path, _ = QFileDialog.getSaveFileName(self, "Export match", f"match_{self.match.match_id}.xlsx",
                                              "Excel (*.xlsx);;JSON (*.json)")
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                export_match_json(self.match.match_id, Path(path), db=self.ctx.db)
            else:
                export_match_xlsx(self.match.match_id, Path(path), db=self.ctx.db)
            self.ctx.status(f"Exported to {path}")
        except Exception as exc:  # noqa: BLE001
            self.ctx.status(f"Export failed: {exc}", 10000)
