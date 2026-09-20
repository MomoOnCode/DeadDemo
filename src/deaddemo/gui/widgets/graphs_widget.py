"""Post-game graphs: the in-game graph menu (General / Player-Specific) plus extras."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.db.repos import MatchPlayerRow, MatchRow
from deaddemo.core.stats import graphs
from deaddemo.gui.context import AppContext
from deaddemo.gui.theme import fmt_souls, team_color
from deaddemo.gui.widgets.line_chart import LineChart, Series

# Six warm hues for Amber, six cool hues for Sapphire: team identity stays readable at a glance,
# and a hero is always the same color across graphs of one match (assigned by hero id order).
WARM = ["#f2b134", "#e8743b", "#e05252", "#d45a9a", "#c9a227", "#ff9e80"]
COOL = ["#4a90e2", "#3bb6c8", "#7b7fe6", "#3fb27f", "#a37fe0", "#7fd0e0"]
# Categorical palette for breakdown graphs (fixed order, at most 8 series + Other).
CATEGORICAL = ["#4a90e2", "#f2b134", "#3fb27f", "#e05252", "#a37fe0", "#3bb6c8", "#e8743b", "#c9a227", "#9a9a9a"]


class GraphsWidget(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.match: MatchRow | None = None
        self.players: list[MatchPlayerRow] = []
        self.gctx: graphs.GraphContext | None = None
        self.hero_colors: dict[int, QColor] = {}

        layout = QHBoxLayout(self)
        side = QVBoxLayout()
        layout.addLayout(side)

        side.addWidget(QLabel("Graph type"))
        self.graph_combo = QComboBox()
        self.graph_combo.setMinimumWidth(240)
        self._fill_graph_combo()
        side.addWidget(self.graph_combo)

        mode_row = QHBoxLayout()
        self.btn_player = QPushButton("Player")
        self.btn_team = QPushButton("Team")
        for b in (self.btn_player, self.btn_team):
            b.setCheckable(True)
            mode_row.addWidget(b)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.btn_player, 0)
        self.mode_group.addButton(self.btn_team, 1)
        self.btn_team.setChecked(True)
        side.addLayout(mode_row)

        self.hero_label = QLabel("Hero")
        self.hero_combo = QComboBox()
        side.addWidget(self.hero_label)
        side.addWidget(self.hero_combo)

        side.addWidget(QLabel("Show"))
        self.hero_list = QListWidget()
        self.hero_list.setMaximumWidth(260)
        side.addWidget(self.hero_list, 1)
        row = QHBoxLayout()
        self.btn_amber = QPushButton("Amber")
        self.btn_sapphire = QPushButton("Sapphire")
        self.btn_all = QPushButton("All")
        for b in (self.btn_amber, self.btn_sapphire, self.btn_all):
            row.addWidget(b)
        side.addLayout(row)
        self.description = QLabel("")
        self.description.setWordWrap(True)
        self.description.setMaximumWidth(260)
        self.description.setStyleSheet("color: #aaaaaa")
        side.addWidget(self.description)

        self.chart = LineChart()
        layout.addWidget(self.chart, 1)

        self.graph_combo.currentIndexChanged.connect(self._graph_changed)
        self.mode_group.idClicked.connect(lambda _i: self.refresh())
        self.hero_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self.hero_list.itemChanged.connect(lambda _i: self._apply_visibility())
        self.btn_amber.clicked.connect(lambda: self._select_team(2))
        self.btn_sapphire.clicked.connect(lambda: self._select_team(3))
        self.btn_all.clicked.connect(lambda: self._select_team(None))
        self._series: list[Series] = []
        self._series_keys: list[str] = []

    # -- setup -------------------------------------------------------------------------
    def _fill_graph_combo(self) -> None:
        model = QStandardItemModel(self)
        for group in ("General", "Player-Specific", "Extra"):
            header = QStandardItem(group)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setForeground(QColor("#8a8a8a"))
            model.appendRow(header)
            for spec in graphs.GRAPHS:
                if spec.group == group:
                    it = QStandardItem("   " + spec.label)
                    it.setData(spec.id, Qt.ItemDataRole.UserRole)
                    model.appendRow(it)
        self.graph_combo.setModel(model)
        self.graph_combo.setCurrentIndex(1)

    def current_spec(self) -> graphs.GraphSpec | None:
        sid = self.graph_combo.currentData(Qt.ItemDataRole.UserRole)
        return graphs.GRAPHS_BY_ID.get(sid) if sid else None

    def load_match(self, match: MatchRow, players: list[MatchPlayerRow]) -> None:
        self.match = match
        self.players = sorted(players, key=lambda p: (p.team_num or 0, p.hero_id))
        cat = catalog()
        team_of = {p.hero_id: p.team_num or 0 for p in players}
        self.gctx = graphs.GraphContext(match, team_of, cat.hero_name, cat.ability_name)
        self.hero_colors = {}
        counters = {2: 0, 3: 0}
        for p in self.players:
            team = p.team_num or 0
            palette = WARM if team == 2 else COOL
            idx = counters.get(team, 0)
            counters[team] = idx + 1
            self.hero_colors[p.hero_id] = QColor(palette[idx % len(palette)])
        self.hero_combo.blockSignals(True)
        self.hero_combo.clear()
        for p in self.players:
            self.hero_combo.addItem(f"{cat.hero_name(p.hero_id)} — {p.player_name}", p.hero_id)
        self.hero_combo.blockSignals(False)
        self._graph_changed()

    # -- state -------------------------------------------------------------------------
    def _graph_changed(self) -> None:
        spec = self.current_spec()
        if spec is None:
            return
        single = spec.kind == "single_hero"
        self.hero_label.setVisible(single)
        self.hero_combo.setVisible(single)
        self.btn_player.setEnabled(spec.kind == "per_hero")
        self.btn_team.setEnabled(spec.kind == "per_hero")
        self.description.setText(spec.description)
        self.refresh()

    def _select_team(self, team: int | None) -> None:
        self.hero_list.blockSignals(True)
        for i in range(self.hero_list.count()):
            it = self.hero_list.item(i)
            on = team is None or it.data(Qt.ItemDataRole.UserRole + 1) == team
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.hero_list.blockSignals(False)
        self._apply_visibility()

    def refresh(self) -> None:
        spec = self.current_spec()
        if spec is None or self.gctx is None or self.match is None:
            return
        by_team = self.btn_team.isChecked() and spec.kind == "per_hero"
        hero_id = self.hero_combo.currentData() if spec.kind == "single_hero" else None
        try:
            data = graphs.build_series(spec, self.gctx, by_team=by_team, hero_id=hero_id)
        except Exception as exc:  # noqa: BLE001
            self.ctx.status(f"Graph failed: {exc}", 10000)
            data = []
        if spec.y_unit == "per_min":
            self.chart.y_format = lambda v: f"{v:,.0f}/min"
        elif spec.y_unit in ("souls", "damage"):
            self.chart.y_format = fmt_souls
        else:
            self.chart.y_format = lambda v: f"{v:,.0f}"
        self.chart.empty_text = ("No data for this graph. Reparse the demo to store the healing dataset."
                                 if spec.id.startswith("healing") else "No data for this graph")
        series: list[Series] = []
        self.hero_list.blockSignals(True)
        self.hero_list.clear()
        for i, gs in enumerate(data):
            if gs.hero_id is not None:
                color = self.hero_colors.get(gs.hero_id, QColor("#999999"))
            elif gs.team in (2, 3):
                color = QColor(team_color(gs.team))
            elif spec.kind == "team_only":
                color = QColor("#f2b134")
            else:
                color = QColor(CATEGORICAL[min(i, len(CATEGORICAL) - 1)])
            series.append(Series(gs.name, color, gs.xs, gs.ys, width=2.5 if gs.team is not None
                                 and gs.hero_id is None else 2.0))
            item = QListWidgetItem(gs.name)
            item.setData(Qt.ItemDataRole.UserRole, gs.key)
            item.setData(Qt.ItemDataRole.UserRole + 1, gs.team)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setForeground(color)
            self.hero_list.addItem(item)
        self.hero_list.blockSignals(False)
        self._series = series
        markers = []
        for o in self.ctx.matches.objective_events(self.match.match_id):
            if o["objective_type"] in ("patron", "mid_boss"):
                markers.append((o["match_seconds"] or 0.0, QColor("#888888"), ""))
        self.chart.set_series(series, markers)

    def _apply_visibility(self) -> None:
        for i in range(self.hero_list.count()):
            if i < len(self._series):
                self._series[i].visible = self.hero_list.item(i).checkState() == Qt.CheckState.Checked
        self.chart.update()
