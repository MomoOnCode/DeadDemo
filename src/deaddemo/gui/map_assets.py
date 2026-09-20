"""Minimap image + calibration lookup shared by the viewer and heatmap widgets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QPixmap

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.viewer.calibration import Calibration, default_calibration
from deaddemo.gui.context import AppContext


@dataclass
class MapAssets:
    map_name: str
    pixmap: QPixmap
    calibration: Calibration
    image_path: str | None


def blank_pixmap(size: int = 1024) -> QPixmap:
    from PySide6.QtGui import QColor

    pm = QPixmap(size, size)
    pm.fill(QColor("#14171c"))
    return pm


def load_map_assets(ctx: AppContext, map_name: str) -> MapAssets:
    """User override image > API minimap > blank square; stored calibration > radius default."""
    cat = catalog()
    image_path: Path | None = None
    override = ctx.settings.minimap_images.get(map_name) or ctx.settings.minimap_images.get("*")
    if override and Path(override).exists():
        image_path = Path(override)
    else:
        if cat.map_info() is None:
            cat.load_api()
        image_path = cat.minimap_image("minimap")
    pixmap = QPixmap(str(image_path)) if image_path else QPixmap()
    if pixmap.isNull():
        pixmap = blank_pixmap()
        image_path = None
    size = (pixmap.width(), pixmap.height())
    row = ctx.calibrations.get(map_name)
    if row is not None:
        try:
            cal = Calibration.from_json(row.matrix_json)
            if cal.image_path == (str(image_path) if image_path else None) or cal.source == "manual":
                return MapAssets(map_name, pixmap, cal, str(image_path) if image_path else None)
        except (ValueError, KeyError):
            pass
    radius = cat.map_info().radius if cat.map_info() else 10752.0
    cal = default_calibration(map_name, str(image_path) if image_path else None, size, radius)
    return MapAssets(map_name, pixmap, cal, str(image_path) if image_path else None)


def save_calibration(ctx: AppContext, cal: Calibration) -> None:
    ctx.calibrations.upsert(cal.map_name, cal.image_path, cal.to_json(),
                            None)
