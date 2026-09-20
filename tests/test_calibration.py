import numpy as np

from deaddemo.core.viewer.calibration import Calibration, default_calibration, fit_affine


def test_default_calibration_maps_origin_to_center_and_north_up():
    cal = default_calibration("start", None, (1000, 1000), radius=10000)
    center = cal.world_to_px([[0.0, 0.0]])[0]
    assert np.allclose(center, [500, 500])
    north = cal.world_to_px([[0.0, 10000.0]])[0]
    assert np.allclose(north, [500, 0]), "+y (Sapphire base) is the top of the image"
    east = cal.world_to_px([[10000.0, 0.0]])[0]
    assert np.allclose(east, [1000, 500])
    back = cal.px_to_world(np.array([[1000.0, 500.0]]))[0]
    assert np.allclose(back, [10000.0, 0.0])


def test_fit_affine_round_trip():
    rng = np.random.default_rng(1)
    true = np.array([[0.05, 0.01, 300.0], [0.0, -0.05, 400.0]])
    world = rng.uniform(-10000, 10000, size=(8, 2))
    pixel = np.hstack([world, np.ones((8, 1))]) @ true.T
    m, rms = fit_affine(world, pixel)
    assert np.allclose(m, true, atol=1e-6)
    assert rms < 1e-6


def test_calibration_json_round_trip():
    cal = default_calibration("start", "img.png", (512, 512))
    again = Calibration.from_json(cal.to_json())
    assert again.map_name == "start" and np.allclose(again.matrix, cal.matrix) and again.source == "radius"
