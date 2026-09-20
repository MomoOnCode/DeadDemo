import numpy as np

from deaddemo.core.stats.heatmap import render_heatmap
from deaddemo.core.viewer.calibration import default_calibration
from deaddemo.resources.colormaps import NAMES, lut


def test_lut_shapes():
    for name in NAMES:
        t = lut(name)
        assert t.shape == (256, 3) and t.dtype == np.uint8


def test_render_heatmap_hotspot_lands_where_points_are():
    cal = default_calibration("start", None, (200, 200), radius=1000)
    pts = np.array([[500.0, 500.0, 0.0]] * 50 + [[-500.0, -500.0, 0.0]] * 5, dtype=np.float32)
    rgba = render_heatmap(pts, cal, (200, 200), bins_px=4, blur_sigma=1.0)
    assert rgba.shape == (200, 200, 4)
    # (500, 500) world -> pixel (150, 50): top-right quadrant should be the brightest
    alpha = rgba[..., 3].astype(int)
    assert alpha[40:60, 140:160].max() > alpha[140:160, 40:60].max() > 0
    assert alpha[90:110, 90:110].max() == 0


def test_render_heatmap_empty():
    cal = default_calibration("start", None, (64, 64))
    rgba = render_heatmap(np.zeros((0, 3), dtype=np.float32), cal, (64, 64))
    assert rgba.sum() == 0
