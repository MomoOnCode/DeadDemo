"""Per-player profile: tiles, heroes, trends, aggregate heatmap, matches, notes."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableView,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.stats.player_profile import PlayerProfile, build_profile
from deaddemo.gui.context import AppContext
from deaddemo.gui.icon_cache import IconCache
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import fmt_clock, fmt_souls
from deaddemo.gui.widgets.line_chart import LineChart, Series
from deaddemo.gui.widgets.stat_tile import StatTile, TileGrid


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _n(v: float | None, digits: int = 0) -> str:
    return "—" if v is None else f"{v:,.{digits}f}"


class HeroesModel(RowTableModel):
    def __init__(self, parent=None):
        cat = catalog()
        super().__init__([
            Column("Hero", lambda h: cat.hero_name(h.hero_id)),
            Column("Games", lambda h: h.games, align_right=True),
            Column("Win %", lambda h: h.winrate, lambda v: f"{v * 100:.0f}%", align_right=True),
            Column("K", lambda h: h.kills, lambda v: f"{v:.1f}", align_right=True),
            Column("D", lambda h: h.deaths, lambda v: f"{v:.1f}", align_right=True),
            Column("A", lambda h: h.assists, lambda v: f"{v:.1f}", align_right=True),
            Column("KDA", lambda h: h.kda, lambda v: f"{v:.2f}", align_right=True),
            Column("Souls/min", lambda h: h.souls_per_min, lambda v: f"{v:.0f}", align_right=True),
        ], parent)


class RecentStrip(QWidget):
    clicked = Signal(int)

    def __init__(self, icons: IconCache, parent=None):
        super().__init__(parent)
        self.icons = icons
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)

    def set_matches(self, recent) -> None:
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        cat = catalog()
        for m in recent:
            card = QFrame()
            card.setFrameShape(QFrame.Shape.StyledPanel)
            color = "#245c2e" if m.won else "#5c2424" if m.won is False else "#333"
            card.setStyleSheet(f"QFrame {{ background: {color}; border-radius: 6px; }}")
            card.setFixedWidth(120)
            v = QVBoxLayout(card)
            v.setContentsMargins(6, 4, 6, 4)
            icon = QLabel()
            pm = self.icons.hero(m.hero_id or 0, 36)
            if pm:
                icon.setPixmap(pm)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(icon)
            date = datetime.fromtimestamp(m.start_time).strftime("%m/%d") if m.start_time else ""
            text = QLabel(f"{cat.hero_name(m.hero_id)}\n{date} {fmt_clock(m.duration_s)}\n{m.kda}"
                          + ("\n(analyzed)" if m.parsed else ""))
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text.setStyleSheet("font-size: 11px; color: #eee;")
            v.addWidget(text)
            card.mousePressEvent = lambda _e, mid=m.match_id: self.clicked.emit(mid)  # type: ignore[method-assign]
            card.setToolTip(f"Match {m.match_id}")
            self.lay.addWidget(card)
        self.lay.addStretch(1)


class ItemsPanel(QWidget):
    """'Most bought items' and 'Usual opening buys' with a hero selector (all heroes or one)."""

    def __init__(self, profile: PlayerProfile, parent=None):
        super().__init__(parent)
        self.profile = profile
        cat = catalog()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel("Items for"))
        self.combo = QComboBox()
        self.combo.addItem(f"All heroes ({profile.games} games)", None)
        for h in profile.heroes:
            self.combo.addItem(f"{cat.hero_name(h.hero_id)} ({h.games})", h.hero_id)
        head.addWidget(self.combo)
        head.addStretch(1)
        lay.addLayout(head)
        self.tiles = QHBoxLayout()
        lay.addLayout(self.tiles)
        self.combo.currentIndexChanged.connect(self._render)
        self._render()

    def _render(self) -> None:
        while self.tiles.count():
            w = self.tiles.takeAt(0).widget()
            if w:
                w.deleteLater()
        p = self.profile
        cat = catalog()
        hero = self.combo.currentData()
        if hero is None:
            games, top, opening = p.games, p.top_items, p.first_buys
        else:
            games = next((h.games for h in p.heroes if h.hero_id == hero), 0)
            top, opening = p.items_by_hero.get(hero, []), p.first_buys_by_hero.get(hero, [])
        sub = "bought in N of games"
        self.tiles.addWidget(StatTile("Most bought items", "",
                                      [(cat.item_name(i)[:24], f"{n}/{games}") for i, n in top[:8]] or [("—", "")],
                                      sub), 1, Qt.AlignmentFlag.AlignTop)
        self.tiles.addWidget(StatTile("Usual opening buys", "",
                                      [(cat.item_name(i)[:24], f"{n}/{games}") for i, n in opening[:5]]
                                      or [("—", "")], "among the first three purchases · " + sub),
                             1, Qt.AlignmentFlag.AlignTop)


class PlayerPage(QWidget):
    back_requested = Signal()

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.icons = IconCache(ctx)
        self.profile: PlayerProfile | None = None
        self.steam_id: int | None = None

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.btn_back = QPushButton("← Back")
        self.btn_back.clicked.connect(self.back_requested.emit)
        self.portrait = QLabel()
        self.portrait.setFixedSize(48, 48)
        self.header = QLabel("")
        self.header.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.sub = QLabel("")
        self.sub.setStyleSheet("color: #aaa;")
        self.btn_history = QPushButton("Load history from deadlock-api")
        self.btn_history.clicked.connect(self._load_history)
        self.btn_export = QPushButton("Export XLSX…")
        self.btn_export.clicked.connect(self._export)
        top.addWidget(self.btn_back)
        top.addWidget(self.portrait)
        head = QVBoxLayout()
        head.addWidget(self.header)
        head.addWidget(self.sub)
        top.addLayout(head, 1)
        top.addWidget(self.btn_history)
        top.addWidget(self.btn_export)
        layout.addLayout(top)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # Overview
        self.overview_scroll = QScrollArea()
        self.overview_scroll.setWidgetResizable(True)
        ov = QWidget()
        self.ov_layout = QVBoxLayout(ov)
        self.tiles = TileGrid(6)
        self.ov_layout.addWidget(self.tiles)
        self.ov_layout.addWidget(QLabel("Last matches"))
        self.strip = RecentStrip(self.icons)
        self.strip.clicked.connect(self._open_match)
        strip_scroll = QScrollArea()
        strip_scroll.setWidgetResizable(True)
        strip_scroll.setWidget(self.strip)
        strip_scroll.setFixedHeight(120)
        strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ov_layout.addWidget(strip_scroll)
        self.ov_layout.addStretch(1)
        self.overview_scroll.setWidget(ov)
        self.tabs.addTab(self.overview_scroll, "Overview")

        # Heroes
        self.heroes_model = HeroesModel(self)
        self.heroes_table = QTableView()
        self.heroes_table.setModel(self.heroes_model)
        self.heroes_table.verticalHeader().setVisible(False)
        self.heroes_table.horizontalHeader().setStretchLastSection(True)
        self.heroes_table.setSortingEnabled(False)
        self.tabs.addTab(self.heroes_table, "Heroes")

        # Graphs
        gw = QWidget()
        gl = QVBoxLayout(gw)
        self.trend_combo = QComboBox()
        self.trend_combo.addItems(["KDA per match", "Souls per minute", "Hero damage per minute",
                                   "Kill participation", "Win rate (rolling 10)"])
        self.trend_combo.currentIndexChanged.connect(self._fill_trend)
        gl.addWidget(self.trend_combo)
        self.trend = LineChart(x_format=lambda v: f"match {v:.0f}")
        self.trend.style.margin_right = 150
        gl.addWidget(self.trend, 1)
        self.tabs.addTab(gw, "Graphs")

        # Heatmap
        from deaddemo.gui.widgets.heatmap_widget import HeatmapWidget

        self.heatmap = HeatmapWidget(ctx)
        self.tabs.addTab(self.heatmap, "Heatmap")

        # Matches
        from deaddemo.gui.pages.players_page import PlayerMatchesModel

        self.matches_model = PlayerMatchesModel(self)
        self.matches_table = QTableView()
        self.matches_table.setModel(self.matches_model)
        self.matches_table.verticalHeader().setVisible(False)
        self.matches_table.horizontalHeader().setStretchLastSection(True)
        self.matches_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.matches_table.doubleClicked.connect(
            lambda idx: self._open_match(int(self.matches_model.row_at(idx)["match_id"])))
        self.tabs.addTab(self.matches_table, "Matches")

        # Notes
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
        self._loaded: set[int] = set()

    # -- loading -----------------------------------------------------------------------
    def load_player(self, steam_id: int) -> None:
        self.steam_id = steam_id
        self._loaded = set()
        me = self.ctx.install.account
        hist_id = None
        if me and me.steam_id64 == steam_id:
            hist_id = me.account_id
        from deaddemo.core.stats.extras import backfill_all

        db = self.ctx.db

        def work(progress, cancel):
            backfill_all(db, progress)  # cheap when nothing is missing
            return build_profile(db, steam_id, history_account_id=hist_id)

        self.header.setText("Loading…")
        self.ctx.jobs.submit(f"profile:{steam_id}", work, on_finished=self._apply,
                             on_failed=lambda e: self.ctx.status(f"Profile failed: {e.splitlines()[0]}", 10000))

    def _apply(self, profile: PlayerProfile) -> None:
        self.profile = profile
        p = profile
        self.header.setText(p.name)
        self.sub.setText(f"SteamID64 {p.steam_id} · account {p.account_id} · {p.games} analyzed game(s)"
                         + (f" · {p.hist_matches} in history" if p.hist_matches else ""))
        self.btn_history.setVisible(True)
        tags, comment = self.ctx.player_notes.get(p.steam_id)
        self.tags_edit.setText(", ".join(tags))
        self.comment_edit.setPlainText(comment)
        hero_ids = {h.hero_id for h in p.heroes} | {m.hero_id for m in p.recent if m.hero_id}
        self.icons.ensure(set(), hero_ids, self._render_overview)
        self.heroes_model.set_rows(p.heroes)
        self.heroes_table.resizeColumnsToContents()
        self._tab_changed(self.tabs.currentIndex())

    def _render_overview(self) -> None:
        p = self.profile
        if p is None:
            return
        cat = catalog()
        if p.heroes:
            pm = self.icons.hero(p.heroes[0].hero_id, 48)
            if pm:
                self.portrait.setPixmap(pm)
        t = self.tiles
        t.clear()
        if p.hist_matches:
            k, d, a = p.hist_kda or (0, 0, 0)
            t.add(StatTile("Win ratio (history)", _pct(p.hist_wins / p.hist_matches if p.hist_matches else None),
                           [("Matches", str(p.hist_matches)), ("Wins", str(p.hist_wins)),
                            ("Losses", str(p.hist_matches - (p.hist_wins or 0)))], "all matches deadlock-api knows"))
            t.add(StatTile("Avg K/D/A (history)", f"{k:.1f}/{d:.1f}/{a:.1f}",
                           [("Souls/min", _n(p.hist_souls_per_min))]))
        t.add(StatTile("Win ratio (analyzed)", _pct(p.winrate if p.games else None),
                       [("Games", str(p.games)), ("Wins", str(p.wins)), ("Losses", str(p.games - p.wins)),
                        ("Minutes", _n(p.minutes_played))], "from analyzed games"))
        t.add(StatTile("K/D", f"{p.kd:.2f}", [("KDA", f"{p.kda:.2f}"), ("Kills", str(p.kills)),
                                              ("Deaths", str(p.deaths)), ("Assists", str(p.assists))]))
        t.add(StatTile("Kill participation", _pct(p.kp if p.games else None),
                       [("Kills/match", _n(p.kills / p.games if p.games else None, 1)),
                        ("Deaths/match", _n(p.deaths / p.games if p.games else None, 1))]))
        t.add(StatTile("Souls / min", _n(p.souls_per_min), [("Last hits/min", _n(p.lh_per_min, 2)),
                                                            ("Denies/min", _n(p.denies_per_min, 2)),
                                                            ("Avg level", _n(p.avg_level, 1))]))
        t.add(StatTile("Hero dmg / min", _n(p.hero_dmg_per_min),
                       [("Bullet", fmt_souls(p.extras.get("bullet_dmg"))),
                        ("Spirit", fmt_souls(p.extras.get("spirit_dmg"))),
                        ("Melee", fmt_souls(p.extras.get("melee_dmg")))]))
        t.add(StatTile("Headshot %", _pct(p.headshot_pct),
                       [("Bullet hits", _n(p.extras.get("bullet_hits"))),
                        ("Headshots", _n(p.extras.get("headshot_hits")))], "bullet hits on heroes"))
        t.add(StatTile("Multi kills", "", [(f"{n}K", str(p.extras.get(f"multi{n}", 0))) for n in (2, 3, 4, 5, 6)]
                       + [("Best streak", str(p.extras.get("max_kill_streak", 0)))], "kills within 10 s"))
        fb_inv = p.extras.get("first_blood_attacker", 0) + p.extras.get("first_blood_victim", 0)
        t.add(StatTile("First blood", _pct(p.extras.get("first_blood_wins", 0) / fb_inv if fb_inv else None),
                       [("Taken", str(p.extras.get("first_blood_attacker", 0))),
                        ("Given", str(p.extras.get("first_blood_victim", 0))),
                        ("Solo kills", str(p.extras.get("solo_kills", 0)))], "win rate when involved"))
        tf = p.extras.get("teamfights", 0)
        t.add(StatTile("Teamfights", str(tf), [("Won", str(p.extras.get("teamfights_won", 0))),
                                               ("Win %", _pct(p.extras.get("teamfights_won", 0) / tf if tf else None)),
                                               ("Per match", _n(p.per_game("teamfights"), 1))]))
        t.add(StatTile("Healing & objectives", "", [
            ("Hero healing/match", fmt_souls(int(p.per_game("hero_healing")))),
            ("Self healing/match", fmt_souls(int(p.per_game("self_healing")))),
            ("Objective dmg/match", fmt_souls(int(p.per_game("objective_damage")))),
            ("Time dead", _pct(p.time_dead_pct if p.games else None))]))
        t.add(StatTile("Tempo", "", [
            ("Souls @10", fmt_souls(int(p.per_game("souls_10m")))), ("Level @10", _n(p.per_game("level_10m"), 1)),
            ("Souls @20", fmt_souls(int(p.per_game("souls_20m")))), ("Level @20", _n(p.per_game("level_20m"), 1))],
            "average across analyzed games"))
        t.add(StatTile("Lanes", "", [(lane, f"{g} · {_pct(w / g if g else None)}") for lane, (g, w) in p.lanes.items()]
                       or [("—", "")], "games · win rate"))
        t.newline()
        t.add(StatTile("Nemesis", "", [(cat.hero_name(h), f"{n} deaths") for h, n in p.nemesis] or [("—", "")]))
        t.add(StatTile("Favourite victims", "", [(cat.hero_name(h), f"{n} kills") for h, n in p.victims]
                       or [("—", "")]))
        t.add(StatTile("Teammates", "", [(s.name[:18], f"{s.count} · {_pct(s.winrate)}") for s in p.teammates[:6]]
                       or [("—", "")], "games together · win rate"), span=2)
        t.add(StatTile("Opponents", "", [(s.name[:18], f"{s.count} · {_pct(s.winrate)}") for s in p.opponents[:6]]
                       or [("—", "")], "games against · your win rate"), span=2)
        t.newline()
        t.add(ItemsPanel(p), span=4)
        if p.ranks:
            last = p.ranks[-1]
            t.add(StatTile("Ranked badge", str(last[1]),
                           [(datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), str(b)) for ts, b in p.ranks[-5:]],
                           "from ranked matches in history"), span=2)
        self.strip.set_matches(p.recent)

    def _tab_changed(self, index: int) -> None:
        if self.profile is None or index in self._loaded:
            return
        self._loaded.add(index)
        name = self.tabs.tabText(index)
        if name == "Graphs":
            self._fill_trend()
        elif name == "Heatmap":
            pairs = []
            for pm in self.profile.per_match:
                m = self.ctx.matches.get(pm["match_id"])
                if m and m.has_ticks:
                    me = next((p for p in self.ctx.matches.players(m.match_id) if p.steam_id == self.steam_id), None)
                    if me:
                        pairs.append((m, me))
            self.heatmap.load_player(self.steam_id or 0, pairs)
        elif name == "Matches":
            from deaddemo.core.stats.player_stats import matches_for_player

            self.matches_model.set_rows(matches_for_player(self.ctx.db, self.steam_id or 0))
            self.matches_table.resizeColumnsToContents()

    def _fill_trend(self) -> None:
        p = self.profile
        if p is None:
            return
        rows = p.per_match
        xs = [float(i + 1) for i in range(len(rows))]
        mode = self.trend_combo.currentIndex()
        if mode == 0:
            ys = [(r["kills"] + r["assists"]) / max(r["deaths"], 1) for r in rows]
            fmt = lambda v: f"{v:.2f}"  # noqa: E731
        elif mode == 1:
            ys = [r["souls_per_min"] for r in rows]
            fmt = lambda v: f"{v:,.0f}"  # noqa: E731
        elif mode == 2:
            ys = [r["dmg_per_min"] for r in rows]
            fmt = lambda v: f"{v:,.0f}"  # noqa: E731
        elif mode == 3:
            ys = [r["kp"] * 100 for r in rows]
            fmt = lambda v: f"{v:.0f}%"  # noqa: E731
        else:
            ys = []
            for i in range(len(rows)):
                window = rows[max(0, i - 9):i + 1]
                ys.append(100 * sum(1 for r in window if r["won"]) / len(window))
            fmt = lambda v: f"{v:.0f}%"  # noqa: E731
        self.trend.y_format = fmt
        self.trend.empty_text = "Analyze at least two games for this player"
        self.trend.set_series([Series(self.trend_combo.currentText(), QColor("#4a90e2"), xs, ys)])

    # -- actions -----------------------------------------------------------------------
    def _open_match(self, match_id: int) -> None:
        if self.ctx.matches.get(match_id):
            self.ctx.events.open_match.emit(match_id)
        else:
            self.ctx.status(f"Match {match_id} has not been analyzed yet (download/analyze it from the Matches page)")

    def _load_history(self) -> None:
        if not self.profile:
            return
        from deaddemo.core.api.client import default_client

        account_id = self.profile.account_id
        client = default_client()

        def work(progress, cancel):
            return client.match_history(account_id)

        def done(entries):
            self.ctx.history.upsert_many(account_id, [e.to_dict() for e in entries])
            self.ctx.status(f"Loaded {len(entries)} matches for account {account_id}")
            self.load_player(self.steam_id or 0)

        self.ctx.jobs.submit(f"history:{account_id}", work, on_finished=done,
                             on_failed=lambda e: self.ctx.status(f"History failed: {e.splitlines()[0]}", 10000))

    def _save_notes(self) -> None:
        if not self.steam_id:
            return
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        self.ctx.player_notes.set(self.steam_id, tags, self.comment_edit.toPlainText())
        self.ctx.status("Notes saved")

    def _export(self) -> None:
        if not self.profile:
            return
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog

        from deaddemo.core.export.exporter import export_player_xlsx

        path, _ = QFileDialog.getSaveFileName(self, "Export player", f"player_{self.profile.steam_id}.xlsx",
                                              "Excel (*.xlsx)")
        if path:
            try:
                export_player_xlsx(self.profile, Path(path), db=self.ctx.db)
                self.ctx.status(f"Exported to {path}")
            except Exception as exc:  # noqa: BLE001
                self.ctx.status(f"Export failed: {exc}", 10000)
