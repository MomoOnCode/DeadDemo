"""Heatmap of player positions (or kill locations) over the minimap."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.db.repos import MatchPlayerRow, MatchRow
from deaddemo.core.stats import heatmap as hm
from deaddemo.gui.context import AppContext
from deaddemo.gui.map_assets import MapAssets, load_map_assets
from deaddemo.gui.theme import fmt_clock, team_color, team_name
from deaddemo.resources.colormaps import NAMES as COLORMAPS


class _Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.base: QPixmap | None = None
        self.overlay: QImage | None = None
        self.setMinimumSize(300, 300)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.base is None:
            return
        side = min(self.width(), self.height())
        x = (self.width() - side) // 2
        y = (self.height() - side) // 2
        p.drawPixmap(x, y, side, side, self.base)
        if self.overlay is not None:
            p.drawImage(x, y, self.overlay.scaled(side, side, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))

    def render_image(self) -> QImage:
        if self.base is None:
            return QImage()
        img = QImage(self.base.size(), QImage.Format.Format_ARGB32)
        p = QPainter(img)
        p.drawPixmap(0, 0, self.base)
        if self.overlay is not None:
            p.drawImage(0, 0, self.overlay)
        p.end()
        return img


class HeatmapWidget(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.match: MatchRow | None = None
        self.players: list[MatchPlayerRow] = []
        self.assets: MapAssets | None = None
        self._overlay_np: np.ndarray | None = None

        layout = QHBoxLayout(self)
        side = QVBoxLayout()
        layout.addLayout(side)
        self.canvas = _Canvas()
        layout.addWidget(self.canvas, 1)

        self.mode = QComboBox()
        self.mode.addItems(["Positions (time spent)", "Deaths", "Kills"])
        side.addWidget(QLabel("Metric"))
        side.addWidget(self.mode)
        side.addWidget(QLabel("Heroes"))
        self.hero_list = QListWidget()
        self.hero_list.setMaximumWidth(240)
        side.addWidget(self.hero_list, 1)
        row = QHBoxLayout()
        self.btn_amber = QPushButton("Amber")
        self.btn_sapphire = QPushButton("Sapphire")
        self.btn_all = QPushButton("All")
        for b in (self.btn_amber, self.btn_sapphire, self.btn_all):
            row.addWidget(b)
        side.addLayout(row)
        self.alive_only = QCheckBox("Alive only")
        self.alive_only.setChecked(True)
        side.addWidget(self.alive_only)
        side.addWidget(QLabel("Time range"))
        self.t_from = QSlider(Qt.Orientation.Horizontal)
        self.t_to = QSlider(Qt.Orientation.Horizontal)
        self.time_label = QLabel("")
        side.addWidget(self.t_from)
        side.addWidget(self.t_to)
        side.addWidget(self.time_label)
        side.addWidget(QLabel("Elevation"))
        self.z_band = QComboBox()
        self.z_band.addItems(["All", "Low (below 0)", "Ground (0–700)", "High (above 700)"])
        side.addWidget(self.z_band)
        side.addWidget(QLabel("Colormap"))
        self.colormap = QComboBox()
        self.colormap.addItems(COLORMAPS)
        side.addWidget(self.colormap)
        side.addWidget(QLabel("Blur"))
        self.blur = QSlider(Qt.Orientation.Horizontal)
        self.blur.setRange(0, 40)
        self.blur.setValue(15)
        side.addWidget(self.blur)
        self.btn_render = QPushButton("Render")
        self.btn_export = QPushButton("Export PNG…")
        side.addWidget(self.btn_render)
        side.addWidget(self.btn_export)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setMaximumWidth(240)
        side.addWidget(self.info)

        self.btn_render.clicked.connect(self.render)
        self.btn_export.clicked.connect(self._export)
        self.btn_amber.clicked.connect(lambda: self._select_team(2))
        self.btn_sapphire.clicked.connect(lambda: self._select_team(3))
        self.btn_all.clicked.connect(lambda: self._select_team(None))
        self.t_from.valueChanged.connect(self._time_changed)
        self.t_to.valueChanged.connect(self._time_changed)
        for w in (self.mode, self.z_band, self.colormap):
            w.currentIndexChanged.connect(self.render)
        self.alive_only.toggled.connect(self.render)
        self.blur.sliderReleased.connect(self.render)
        self.hero_list.itemChanged.connect(lambda _i: self.render())

    def load_player(self, steam_id: int, matches: list[tuple[MatchRow, MatchPlayerRow]]) -> None:
        """Aggregate one player's positions/kills across several parsed matches."""
        if not matches:
            return
        self.player_matches = matches
        self.match, me = matches[0]
        self.players = [me]
        self.assets = load_map_assets(self.ctx, self.match.map_name or "unknown")
        self.canvas.base = self.assets.pixmap
        cat = catalog()
        self.hero_list.blockSignals(True)
        self.hero_list.clear()
        item = QListWidgetItem(f"{me.player_name} — {len(matches)} matches, all heroes")
        item.setData(Qt.ItemDataRole.UserRole, -1)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.hero_list.addItem(item)
        self.hero_list.blockSignals(False)
        total = int(max((m.regulation_seconds or 0) for m, _ in matches))
        for s in (self.t_from, self.t_to):
            s.blockSignals(True)
            s.setRange(0, max(1, total))
        self.t_from.setValue(0)
        self.t_to.setValue(max(1, total))
        for s in (self.t_from, self.t_to):
            s.blockSignals(False)
        self._time_changed()
        self.info.setText(f"{cat.hero_name(me.hero_id)} and others · {len(matches)} matches")
        self.render()

    def load_match(self, match: MatchRow, players: list[MatchPlayerRow]) -> None:
        self.player_matches = None
        self.match = match
        self.players = players
        self.assets = load_map_assets(self.ctx, match.map_name or "unknown")
        self.canvas.base = self.assets.pixmap
        cat = catalog()
        self.hero_list.blockSignals(True)
        self.hero_list.clear()
        for p in sorted(players, key=lambda p: (p.team_num or 0, p.hero_id)):
            item = QListWidgetItem(f"{cat.hero_name(p.hero_id)} — {p.player_name}")
            item.setData(Qt.ItemDataRole.UserRole, p.hero_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setForeground(team_color(p.team_num))
            self.hero_list.addItem(item)
        self.hero_list.blockSignals(False)
        total = int(match.regulation_seconds or 0)
        for s in (self.t_from, self.t_to):
            s.blockSignals(True)
            s.setRange(0, max(1, total))
        self.t_from.setValue(0)
        self.t_to.setValue(max(1, total))
        for s in (self.t_from, self.t_to):
            s.blockSignals(False)
        self._time_changed()
        self.render()

    def _select_team(self, team: int | None) -> None:
        self.hero_list.blockSignals(True)
        team_of = {p.hero_id: p.team_num for p in self.players}
        for i in range(self.hero_list.count()):
            it = self.hero_list.item(i)
            on = team is None or team_of.get(it.data(Qt.ItemDataRole.UserRole)) == team
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.hero_list.blockSignals(False)
        self.render()

    def _time_changed(self) -> None:
        if self.t_from.value() > self.t_to.value():
            self.t_from.setValue(self.t_to.value())
        self.time_label.setText(f"{fmt_clock(self.t_from.value())} – {fmt_clock(self.t_to.value())}")

    def selected_heroes(self) -> list[int]:
        return [self.hero_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.hero_list.count())
                if self.hero_list.item(i).checkState() == Qt.CheckState.Checked]

    def _collect(self, match: MatchRow, heroes: list[int], z, mode: int) -> np.ndarray:
        if mode == 0:
            return hm.positions(match, hero_ids=heroes or [-1], alive_only=self.alive_only.isChecked(),
                                t_from=float(self.t_from.value()), t_to=float(self.t_to.value()), z_range=z)
        kills = [dict(r) for r in self.ctx.matches.kills(match.match_id)
                 if self.t_from.value() <= (r["match_seconds"] or 0) <= self.t_to.value()]
        return hm.kill_positions(match, kills, victims=heroes if mode == 1 else None,
                                 attackers=heroes if mode == 2 else None)

    def render(self) -> None:
        if not self.match or not self.assets:
            return
        heroes = self.selected_heroes()
        z = {1: (-10000.0, 0.0), 2: (0.0, 700.0), 3: (700.0, 100000.0)}.get(self.z_band.currentIndex())
        mode = self.mode.currentIndex()
        player_matches = getattr(self, "player_matches", None)
        if player_matches:
            if -1 not in heroes:
                pts = np.zeros((0, 3), dtype=np.float32)
            else:
                parts = [self._collect(m, [me.hero_id], z, mode) for m, me in player_matches]
                pts = np.concatenate(parts) if parts else np.zeros((0, 3), dtype=np.float32)
        else:
            pts = self._collect(self.match, heroes, z, mode)
        size = (self.assets.pixmap.width(), self.assets.pixmap.height())
        sigma = self.blur.value() / 10.0
        bins = 4 if mode == 0 else 10
        rgba = hm.render_heatmap(pts, self.assets.calibration, size, bins_px=bins, blur_sigma=sigma,
                                 colormap=self.colormap.currentText())
        self._overlay_np = np.ascontiguousarray(rgba)
        h, w, _ = rgba.shape
        self.canvas.overlay = QImage(self._overlay_np.data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
        self.canvas.update()
        self.info.setText(f"{pts.shape[0]:,} samples · {len(heroes)} heroes · "
                          f"{team_name(2)} vs {team_name(3)} · calibration: {self.assets.calibration.source}")

    def _export(self) -> None:
        if not self.match:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export heatmap", f"heatmap_{self.match.match_id}.png",
                                              "PNG (*.png)")
        if path:
            self.canvas.render_image().save(path)
            self.ctx.status(f"Saved {path}")
