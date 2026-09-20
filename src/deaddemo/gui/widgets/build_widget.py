"""Final builds: one row per player with item icons grouped by slot, tooltips with item stats."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.assets.tooltips import item_tooltip_html
from deaddemo.core.db.repos import MatchPlayerRow, MatchRow
from deaddemo.core.stats.builds import BuildItem, final_builds, grouped_by_slot
from deaddemo.gui.context import AppContext
from deaddemo.gui.icon_cache import IconCache
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import fmt_clock, fmt_souls, team_color, team_name

ICON = 40
SLOT_COLORS = {"weapon": "#e8a33c", "vitality": "#7fd15f", "spirit": "#b47ee8"}


class ItemIcon(QLabel):
    def __init__(self, item: BuildItem, pixmap: QPixmap | None, parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedSize(ICON + 4, ICON + 4)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = SLOT_COLORS.get(item.slot, "#666")
        self.setStyleSheet(f"border: 1px solid {color}; border-radius: 4px; background: #222;")
        if pixmap is not None:
            self.setPixmap(pixmap)
        else:
            self.setText(item.name[:6])
            self.setStyleSheet(self.styleSheet() + " font-size: 8px; color: #ddd;")
        raw = (catalog().item(item.item_id).raw if catalog().item(item.item_id) else {"name": item.name})
        self.setToolTip(item_tooltip_html(raw, fmt_clock(item.purchased_s)))


class PlayerBuildRow(QFrame):
    def __init__(self, player: MatchPlayerRow, items: list[BuildItem], icons: IconCache, parent=None):
        super().__init__(parent)
        self.player = player
        self.setFrameShape(QFrame.Shape.StyledPanel)
        c = QColor(team_color(player.team_num))
        self.setStyleSheet(f"PlayerBuildRow {{ background: rgba({c.red()},{c.green()},{c.blue()},28); }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        hero_pm = icons.hero(player.hero_id, 40)
        hero_lbl = QLabel()
        hero_lbl.setFixedSize(42, 42)
        if hero_pm:
            hero_lbl.setPixmap(hero_pm)
        lay.addWidget(hero_lbl)
        cat = catalog()
        total = sum((cat.item(i.item_id).cost or 0) if cat.item(i.item_id) else 0 for i in items)
        name = QLabel(f"<b>{player.player_name}</b><br><span style='color:#bbb'>{cat.hero_name(player.hero_id)}"
                      f" · {len(items)} items · {fmt_souls(total)} spent</span>")
        name.setFixedWidth(190)
        lay.addWidget(name)
        groups = grouped_by_slot(items)
        for slot in ("weapon", "vitality", "spirit"):
            box = QHBoxLayout()
            box.setSpacing(3)
            tag = QLabel(slot[0].upper())
            tag.setStyleSheet(f"color: {SLOT_COLORS[slot]}; font-weight: bold;")
            tag.setFixedWidth(12)
            box.addWidget(tag)
            for it in groups.get(slot, []):
                box.addWidget(ItemIcon(it, icons.item(it.item_id, ICON)))
            box.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(box)
            wrap.setMinimumWidth(4 * (ICON + 8) + 20)
            lay.addWidget(wrap, 1)


class BuildWidget(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.icons = IconCache(ctx)
        self.match: MatchRow | None = None
        self.players: list[MatchPlayerRow] = []
        self.builds: dict[int, list[BuildItem]] = {}

        layout = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(split, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QVBoxLayout(self.container)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.container)
        split.addWidget(self.scroll)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.timeline_label = QLabel("Click a player row to see their purchase order")
        bl.addWidget(self.timeline_label)
        def attr(r, name):
            it = catalog().item(r["ability_id"])
            return getattr(it, name) if it else None

        self.timeline_model = RowTableModel([
            Column("Time", lambda r: r["match_seconds"], fmt_clock, align_right=True),
            Column("Action", lambda r: r["change"]),
            Column("Item", lambda r: catalog().item_name(r["ability_id"])),
            Column("Slot", lambda r: attr(r, "item_slot_type") or ""),
            Column("Tier", lambda r: attr(r, "item_tier"), align_right=True),
            Column("Cost", lambda r: attr(r, "cost"), align_right=True),
        ], self)
        self.timeline = QTableView()
        self.timeline.setModel(self.timeline_model)
        self.timeline.verticalHeader().setVisible(False)
        self.timeline.horizontalHeader().setStretchLastSection(True)
        bl.addWidget(self.timeline, 1)
        split.addWidget(bottom)
        split.setSizes([520, 220])
        self.status = QLabel("")
        layout.addWidget(self.status)

    def load_match(self, match: MatchRow, players: list[MatchPlayerRow]) -> None:
        self.match = match
        self.players = sorted(players, key=lambda p: (p.team_num or 0, -(p.souls or 0)))
        cat = catalog()
        self.status.setText("Loading item data…")
        match_id = match.match_id

        def work(progress, cancel):
            cat.load_api()
            return final_builds(self.ctx.db, match_id, cat)

        def done(builds):
            self.builds = builds
            item_ids = {it.item_id for items in builds.values() for it in items}
            hero_ids = {p.hero_id for p in self.players}
            self.status.setText("Fetching icons…")
            self.icons.ensure(item_ids, hero_ids, self._render)

        self.ctx.jobs.submit(f"builds:{match_id}", work, on_finished=done,
                             on_failed=lambda e: self.status.setText(f"Could not load builds: {e.splitlines()[0]}"))

    def _render(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for team in (2, 3):
            header = QLabel(team_name(team))
            header.setStyleSheet(
                f"color: {team_color(team).name()}; font-weight: 600; font-size: 13px; margin-top: 6px"
            )
            self.grid.addWidget(header)
            for p in self.players:
                if p.team_num != team:
                    continue
                row = PlayerBuildRow(p, self.builds.get(p.hero_id, []), self.icons)
                row.mousePressEvent = lambda _e, pl=p: self._show_timeline(pl)  # type: ignore[method-assign]
                row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                self.grid.addWidget(row)
        n = sum(len(v) for v in self.builds.values())
        api = "deadlock-api item data" if catalog().api_loaded else "no item data (offline?)"
        self.status.setText(f"{n} items across {len(self.builds)} players · hover an item for its stats · {api}")
        if self.players:
            self._show_timeline(self.players[0])

    def _show_timeline(self, player: MatchPlayerRow) -> None:
        if not self.match:
            return
        rows = [dict(r) for r in self.ctx.matches.item_purchases(self.match.match_id, player.hero_id)]
        self.timeline_model.set_rows(rows)
        self.timeline.resizeColumnsToContents()
        self.timeline_label.setText(f"{player.player_name} — {catalog().hero_name(player.hero_id)} — purchase order")
