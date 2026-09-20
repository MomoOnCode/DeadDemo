"""QGraphicsView that draws the minimap, hero markers, kill markers and objectives for one frame."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView

from deaddemo.core.viewer.calibration import Calibration
from deaddemo.core.viewer.frames import FrameSet
from deaddemo.gui.map_assets import MapAssets
from deaddemo.gui.theme import TEAM_COLORS, TEAM_COLORS_DIM


class HeroMarker(QGraphicsEllipseItem):
    def __init__(self, hero_id: int, team: int, label: str, radius: float = 14.0):
        super().__init__(-radius, -radius, 2 * radius, 2 * radius)
        self.hero_id = hero_id
        self.team = team
        self.radius = radius
        self.setZValue(10)
        self.setPen(QPen(QColor("#111111"), 2))
        self.setBrush(QBrush(TEAM_COLORS.get(team, QColor("#888"))))
        self.text = QGraphicsSimpleTextItem(label[:3], self)
        f = QFont()
        f.setPointSizeF(radius * 0.7)
        f.setBold(True)
        self.text.setFont(f)
        self.text.setBrush(QBrush(QColor("#0b0b0b")))
        br = self.text.boundingRect()
        self.text.setPos(-br.width() / 2, -br.height() / 2)
        self.health = QGraphicsEllipseItem(self)  # health arc drawn as ring
        self.health.setRect(-radius - 4, -radius - 4, 2 * radius + 8, 2 * radius + 8)
        self.health.setPen(QPen(QColor("#5fdc6b"), 3))
        self.health.setBrush(Qt.BrushStyle.NoBrush)
        self.health.setStartAngle(90 * 16)
        self.health.setSpanAngle(360 * 16)
        self.setToolTip(label)

    def set_state(self, alive: bool, health_frac: float, z: float) -> None:
        if alive:
            self.setBrush(QBrush(TEAM_COLORS.get(self.team, QColor("#888"))))
            self.setOpacity(1.0)
            self.health.setVisible(True)
            self.health.setSpanAngle(int(-360 * 16 * max(0.0, min(1.0, health_frac))))
            self.health.setPen(QPen(QColor("#5fdc6b") if health_frac > 0.5 else QColor("#f0a030") if health_frac > 0.25
                                    else QColor("#e04040"), 3))
        else:
            self.setBrush(QBrush(TEAM_COLORS_DIM.get(self.team, QColor("#444"))))
            self.setOpacity(0.45)
            self.health.setVisible(False)
        # elevation: outline width hints at height band
        w = 2 if z < 0 else 3 if z < 700 else 4.5
        self.setPen(QPen(QColor("#111111") if z >= 0 else QColor("#6a6a6a"), w))


class MinimapView(QGraphicsView):
    frame_clicked = Signal(float, float)  # world x, y

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#0d0f12")))
        self.assets: MapAssets | None = None
        self.frames: FrameSet | None = None
        self.markers: dict[int, HeroMarker] = {}
        self.kill_items: list[tuple[QGraphicsEllipseItem, int]] = []
        self.objective_items: list = []
        self.trail_items: list = []
        self.show_trails = True
        self.hidden_heroes: set[int] = set()
        self._pixmap_item = None
        self._marker_scale = 1.0

    # -- setup --------------------------------------------------------------------------
    def set_assets(self, assets: MapAssets) -> None:
        self.assets = assets
        self.scene_.clear()
        self.markers.clear()
        self.kill_items.clear()
        self.objective_items.clear()
        self.trail_items.clear()
        self._pixmap_item = self.scene_.addPixmap(assets.pixmap)
        self._pixmap_item.setZValue(0)
        self.scene_.setSceneRect(QRectF(assets.pixmap.rect()))
        self._marker_scale = max(0.6, assets.pixmap.width() / 1024)
        self.fitInView(self.scene_.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_frames(self, frames: FrameSet, labels: dict[int, str]) -> None:
        self.frames = frames
        for m in self.markers.values():
            self.scene_.removeItem(m)
        self.markers.clear()
        for hero in frames.hero_ids:
            m = HeroMarker(hero, frames.team_of.get(hero, 0), labels.get(hero, str(hero)),
                           radius=14 * self._marker_scale)
            self.scene_.addItem(m)
            self.markers[hero] = m
        self._draw_objectives()

    def _draw_objectives(self) -> None:
        if not self.frames or not self.assets:
            return
        for it in self.objective_items:
            self.scene_.removeItem(it)
        self.objective_items.clear()
        cal = self.assets.calibration
        size = {"patron": 11, "shrine": 7, "walker": 8, "barracks": 6}
        for otype, team, x, y in self.frames.objective_positions:
            px = cal.world_to_px([[x, y]])[0]
            r = size.get(otype, 6) * self._marker_scale
            it = self.scene_.addRect(px[0] - r, px[1] - r, 2 * r, 2 * r,
                                     QPen(QColor("#0a0a0a"), 1.5), QBrush(TEAM_COLORS.get(team, QColor("#999"))))
            it.setZValue(5)
            it.setToolTip(f"{otype} ({x:.0f}, {y:.0f})")
            self.objective_items.append(it)

    # -- per frame ---------------------------------------------------------------------
    def show_frame(self, index: int, trail_frames: int = 0) -> None:
        if not self.frames or not self.assets:
            return
        fs = self.frames
        cal: Calibration = self.assets.calibration
        index = max(0, min(index, fs.n_frames - 1))
        xyz = fs.xyz[index]
        px = cal.world_to_px(xyz[:, :2])
        for i, hero in enumerate(fs.hero_ids):
            m = self.markers.get(hero)
            if m is None:
                continue
            if hero in self.hidden_heroes or math.isnan(xyz[i, 0]):
                m.setVisible(False)
                continue
            m.setVisible(True)
            m.setPos(QPointF(px[i, 0], px[i, 1]))
            m.set_state(bool(fs.alive[index, i]), float(fs.health_frac[index, i]), float(xyz[i, 2]))
        # trails
        for it in self.trail_items:
            self.scene_.removeItem(it)
        self.trail_items.clear()
        if self.show_trails and trail_frames > 0 and index > 0:
            start = max(0, index - trail_frames)
            seg = fs.xyz[start:index + 1]
            for i, hero in enumerate(fs.hero_ids):
                if hero in self.hidden_heroes:
                    continue
                pts = seg[:, i, :2]
                ok = ~np_isnan_rows(pts)
                if ok.sum() < 2:
                    continue
                path_px = cal.world_to_px(pts[ok])
                color = QColor(TEAM_COLORS.get(fs.team_of.get(hero, 0), QColor("#888")))
                color.setAlpha(140)
                pen = QPen(color, 2 * self._marker_scale)
                for a, b in zip(path_px[:-1], path_px[1:], strict=False):
                    line = self.scene_.addLine(a[0], a[1], b[0], b[1], pen)
                    line.setZValue(4)
                    self.trail_items.append(line)
        # kills: show for ~6 seconds after they happen
        tick = int(fs.ticks[index])
        for it, _ in self.kill_items:
            self.scene_.removeItem(it)
        self.kill_items.clear()
        for k in fs.kills:
            if k.tick <= tick <= k.tick + 6 * 64 and not math.isnan(k.x):
                p = cal.world_to_px([[k.x, k.y]])[0]
                age = (tick - k.tick) / (6 * 64)
                r = (10 + 18 * age) * self._marker_scale
                color = QColor("#ff4040")
                color.setAlpha(int(220 * (1 - age)))
                it = self.scene_.addEllipse(p[0] - r, p[1] - r, 2 * r, 2 * r, QPen(color, 2.5), Qt.BrushStyle.NoBrush)
                it.setZValue(20)
                self.kill_items.append((it, k.tick))

    # -- interaction -------------------------------------------------------------------
    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.assets:
            sp = self.mapToScene(event.position().toPoint())
            w = self.assets.calibration.px_to_world([[sp.x(), sp.y()]])[0]
            self.frame_clicked.emit(float(w[0]), float(w[1]))
        super().mouseDoubleClickEvent(event)

    def fit(self) -> None:
        self.fitInView(self.scene_.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


def np_isnan_rows(a):
    import numpy as np

    return np.isnan(a).any(axis=1)
