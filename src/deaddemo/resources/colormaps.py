"""Sequential colormaps as 256x3 uint8 lookup tables, built from a few anchor colors.

Single hue-family, light->dark in lightness, so magnitude is readable in both themes and by
colorblind readers (no rainbow)."""

from __future__ import annotations

import numpy as np

_ANCHORS: dict[str, list[tuple[float, tuple[int, int, int]]]] = {
    # position, rgb — inferno-like warm ramp
    "inferno": [
        (0.00, (0, 0, 4)), (0.15, (40, 11, 84)), (0.35, (120, 28, 109)), (0.55, (190, 55, 82)),
        (0.75, (237, 105, 37)), (0.90, (251, 175, 12)), (1.00, (252, 255, 164)),
    ],
    # single-hue teal ramp for a calmer look
    "teal": [
        (0.00, (12, 30, 40)), (0.30, (20, 80, 100)), (0.60, (40, 150, 165)), (0.85, (120, 210, 215)),
        (1.00, (225, 250, 250)),
    ],
    "amber": [
        (0.00, (35, 20, 5)), (0.35, (120, 60, 10)), (0.65, (200, 120, 30)), (0.85, (240, 180, 80)),
        (1.00, (255, 235, 190)),
    ],
    "sapphire": [
        (0.00, (8, 15, 40)), (0.35, (20, 50, 120)), (0.65, (50, 110, 200)), (0.85, (120, 170, 240)),
        (1.00, (210, 230, 255)),
    ],
}


def lut(name: str = "inferno") -> np.ndarray:
    anchors = _ANCHORS.get(name, _ANCHORS["inferno"])
    pos = np.array([a[0] for a in anchors])
    rgb = np.array([a[1] for a in anchors], dtype=np.float64)
    t = np.linspace(0, 1, 256)
    out = np.stack([np.interp(t, pos, rgb[:, c]) for c in range(3)], axis=1)
    return np.clip(out, 0, 255).astype(np.uint8)


NAMES = list(_ANCHORS.keys())
