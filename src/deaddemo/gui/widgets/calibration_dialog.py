"""Fit the world->minimap transform by clicking known structures on the image."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from deaddemo.core.db.repos import MatchRow
from deaddemo.core.stats.match_stats import scan
from deaddemo.core.viewer.calibration import Calibration, Landmark, fit_affine, landmark_candidates
from deaddemo.gui.context import AppContext
from deaddemo.gui.map_assets import load_map_assets, save_calibration


class _ClickView(QGraphicsView):
    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self.on_click = on_click
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def wheelEvent(self, event) -> None:  # noqa: N802
        f = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        sp = self.mapToScene(event.position().toPoint())
        self.on_click(sp.x(), sp.y())


class CalibrationDialog(QDialog):
    def __init__(self, ctx: AppContext, match: MatchRow, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.match = match
        self.setWindowTitle(f"Calibrate minimap — {match.map_name}")
        self.resize(1100, 750)
        self.assets = load_map_assets(ctx, match.map_name or "unknown")
        obj = scan(match, "objectives")
        self.landmarks: list[Landmark] = landmark_candidates(obj.collect()) if obj is not None else []
        # pre-fill pixel positions from the current calibration so the user only nudges them
        for lm in self.landmarks:
            px = self.assets.calibration.world_to_px([lm.world])[0]
            lm.pixel = (float(px[0]), float(px[1]))
        for lm in self.assets.calibration.landmarks:  # keep previously saved manual points
            for cand in self.landmarks:
                if cand.name == lm.name and lm.pixel:
                    cand.pixel = lm.pixel

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        layout.addLayout(left)
        left.addWidget(QLabel("1. Select a landmark  2. Double-click its spot on the map  3. Save\n"
                              "Yellow dots are where the current transform puts each structure."))
        self.list = QListWidget()
        self.list.setMaximumWidth(320)
        for lm in self.landmarks:
            self.list.addItem(QListWidgetItem(lm.name))
        left.addWidget(self.list, 1)
        self.btn_image = QPushButton("Use a different minimap image…")
        self.btn_reset = QPushButton("Reset to radius default")
        self.btn_clear = QPushButton("Clear selected point")
        left.addWidget(self.btn_image)
        left.addWidget(self.btn_reset)
        left.addWidget(self.btn_clear)
        self.rms_label = QLabel("")
        left.addWidget(self.rms_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        left.addWidget(buttons)

        self.scene = QGraphicsScene(self)
        self.view = _ClickView(self._clicked)
        self.view.setScene(self.scene)
        layout.addWidget(self.view, 1)
        self.image_path = self.assets.image_path
        self._reload_image(self.assets.pixmap)

        self.btn_image.clicked.connect(self._pick_image)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_clear.clicked.connect(self._clear_point)
        self.list.currentRowChanged.connect(lambda _r: self._draw())
        self._draw()

    def _reload_image(self, pixmap: QPixmap) -> None:
        self.pixmap = pixmap
        self.scene.clear()
        self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._points = []

    def _draw(self) -> None:
        for it in getattr(self, "_points", []):
            self.scene.removeItem(it)
        self._points = []
        current = self.list.currentRow()
        for i, lm in enumerate(self.landmarks):
            if not lm.pixel:
                continue
            r = 7 if i == current else 5
            color = QColor("#ff3030") if i == current else QColor("#ffd23c")
            it = self.scene.addEllipse(lm.pixel[0] - r, lm.pixel[1] - r, 2 * r, 2 * r, QPen(QColor("#000"), 1.5),
                                       QBrush(color))
            it.setZValue(10)
            self._points.append(it)
        self._update_rms()

    def _clicked(self, x: float, y: float) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        self.landmarks[row].pixel = (x, y)
        if row + 1 < self.list.count():
            self.list.setCurrentRow(row + 1)
        self._draw()

    def _clear_point(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.landmarks[row].pixel = None
            self._draw()

    def _reset(self) -> None:
        from deaddemo.core.viewer.calibration import default_calibration

        cal = default_calibration(self.match.map_name or "unknown", self.image_path,
                                  (self.pixmap.width(), self.pixmap.height()))
        for lm in self.landmarks:
            px = cal.world_to_px([lm.world])[0]
            lm.pixel = (float(px[0]), float(px[1]))
        self._draw()

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Minimap image", "", "Images (*.png *.jpg *.webp)")
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                self.image_path = path
                self._reload_image(pm)
                self._reset()

    def _fit(self) -> tuple[np.ndarray, float] | None:
        pts = [lm for lm in self.landmarks if lm.pixel]
        if len(pts) < 3:
            return None
        return fit_affine(np.array([lm.world for lm in pts]), np.array([lm.pixel for lm in pts]))

    def _update_rms(self) -> None:
        fit = self._fit()
        n = sum(1 for lm in self.landmarks if lm.pixel)
        self.rms_label.setText(f"{n} points — RMS error {fit[1]:.1f} px" if fit else f"{n} points (need 3+)")

    def _save(self) -> None:
        fit = self._fit()
        if not fit:
            self.rms_label.setText("Place at least 3 landmarks")
            return
        cal = Calibration(self.match.map_name or "unknown", self.image_path, fit[0], list(self.landmarks),
                          source="manual")
        save_calibration(self.ctx, cal)
        if self.image_path and self.image_path != self.assets.image_path:
            self.ctx.settings.minimap_images[self.match.map_name or "unknown"] = self.image_path
            self.ctx.settings.save()
        self.accept()
