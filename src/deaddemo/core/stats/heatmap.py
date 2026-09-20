"""Position heatmaps: world coordinates -> 2D histogram -> blurred RGBA overlay."""

from __future__ import annotations

import numpy as np
import polars as pl

from deaddemo.core.db.repos import MatchRow
from deaddemo.core.stats.match_stats import scan
from deaddemo.core.viewer.calibration import Calibration
from deaddemo.resources.colormaps import lut


def positions(
    match: MatchRow,
    *,
    hero_ids: list[int] | None = None,
    alive_only: bool = True,
    t_from: float | None = None,
    t_to: float | None = None,
    z_range: tuple[float, float] | None = None,
) -> np.ndarray:
    """Return an (N, 3) array of world xyz samples matching the filters."""
    lf = scan(match, "player_ticks")
    if lf is None:
        return np.zeros((0, 3), dtype=np.float32)
    if hero_ids:
        lf = lf.filter(pl.col("hero_id").is_in(hero_ids))
    if alive_only:
        lf = lf.filter(pl.col("is_alive"))
    if t_from is not None:
        lf = lf.filter(pl.col("match_seconds") >= t_from)
    if t_to is not None:
        lf = lf.filter(pl.col("match_seconds") <= t_to)
    if z_range is not None:
        lf = lf.filter((pl.col("z") >= z_range[0]) & (pl.col("z") <= z_range[1]))
    df = lf.select(["x", "y", "z"]).collect()
    return df.to_numpy().astype(np.float32) if df.height else np.zeros((0, 3), dtype=np.float32)


def kill_positions(match: MatchRow, kills_rows, *, victims: list[int] | None = None,
                   attackers: list[int] | None = None) -> np.ndarray:
    """Approximate kill locations from the victim's position at the kill tick."""
    lf = scan(match, "player_ticks")
    if lf is None or not kills_rows:
        return np.zeros((0, 3), dtype=np.float32)
    rows = [
        k for k in kills_rows
        if (not victims or k["victim_hero_id"] in victims)
        and (not attackers or k["attacker_hero_id"] in attackers)
    ]
    if not rows:
        return np.zeros((0, 3), dtype=np.float32)
    kills = pl.DataFrame({"tick": [int(k["tick"]) for k in rows], "hero_id": [int(k["victim_hero_id"]) for k in rows]})
    ticks = lf.select(["tick", "hero_id", "x", "y", "z"]).collect().sort("tick")
    joined = kills.sort("tick").join_asof(ticks.sort("tick"), on="tick", by="hero_id", strategy="backward")
    return joined.select(["x", "y", "z"]).drop_nulls().to_numpy().astype(np.float32)


def _gaussian_kernel(sigma: float) -> np.ndarray:
    radius = max(1, int(3 * sigma))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-(x**2) / (2 * sigma**2))
    return k / k.sum()


def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return img
    k = _gaussian_kernel(sigma)
    out = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 0, img)
    out = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 1, out)
    return out


def render_heatmap(
    world_xy: np.ndarray,
    calibration: Calibration,
    size: tuple[int, int],
    *,
    bins_px: int = 6,
    blur_sigma: float = 1.5,
    colormap: str = "inferno",
    alpha_max: int = 210,
    clip_percentile: float = 99.0,
) -> np.ndarray:
    """Return an (H, W, 4) RGBA uint8 overlay sized to the minimap image."""
    w, h = size
    out = np.zeros((h, w, 4), dtype=np.uint8)
    if world_xy.shape[0] == 0:
        return out
    px = calibration.world_to_px(world_xy[:, :2])
    gx, gy = max(1, w // bins_px), max(1, h // bins_px)
    hist, _, _ = np.histogram2d(px[:, 1], px[:, 0], bins=[gy, gx], range=[[0, h], [0, w]])
    hist = _blur(hist, blur_sigma)
    if hist.max() <= 0:
        return out
    top = np.percentile(hist[hist > 0], clip_percentile) if np.any(hist > 0) else hist.max()
    norm = np.clip(hist / max(top, 1e-9), 0, 1)
    # upsample to image size (nearest, then the blur already smoothed the grid)
    ys = (np.arange(h) * gy // h).clip(0, gy - 1)
    xs = (np.arange(w) * gx // w).clip(0, gx - 1)
    full = norm[ys][:, xs]
    idx = (full * 255).astype(np.uint8)
    table = lut(colormap)
    out[..., :3] = table[idx]
    out[..., 3] = (np.sqrt(full) * alpha_max).astype(np.uint8)
    return out
