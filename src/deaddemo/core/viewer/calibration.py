"""World (x, y) <-> minimap pixel transforms.

The default transform comes from the map radius published by deadlock-api: the minimap is a
square image covering world coordinates ``[-radius, radius]`` on both axes, with +y pointing up
(toward the Sapphire base at the top of the image). A manual affine calibration fitted from
landmarks can replace it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

DEFAULT_RADIUS = 10752.0


@dataclass
class Landmark:
    name: str
    world: tuple[float, float]
    pixel: tuple[float, float] | None = None


@dataclass
class Calibration:
    map_name: str
    image_path: str | None
    matrix: np.ndarray  # 2x3 affine: [px, py]^T = M @ [x, y, 1]^T
    landmarks: list[Landmark] = field(default_factory=list)
    source: str = "radius"  # 'radius' | 'manual'

    def world_to_px(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, dtype=np.float64)
        if xy.ndim == 1:
            xy = xy[None, :]
        ones = np.ones((xy.shape[0], 1))
        return np.hstack([xy[:, :2], ones]) @ self.matrix.T

    def px_to_world(self, px: np.ndarray) -> np.ndarray:
        px = np.asarray(px, dtype=np.float64)
        if px.ndim == 1:
            px = px[None, :]
        full = np.vstack([self.matrix, [0, 0, 1]])
        inv = np.linalg.inv(full)
        ones = np.ones((px.shape[0], 1))
        return (np.hstack([px[:, :2], ones]) @ inv.T)[:, :2]

    def to_json(self) -> str:
        return json.dumps({
            "map_name": self.map_name, "image_path": self.image_path, "matrix": self.matrix.tolist(),
            "source": self.source,
            "landmarks": [{"name": lm.name, "world": lm.world, "pixel": lm.pixel} for lm in self.landmarks],
        })

    @classmethod
    def from_json(cls, text: str) -> Calibration:
        d = json.loads(text)
        return cls(
            map_name=d["map_name"], image_path=d.get("image_path"), matrix=np.array(d["matrix"], dtype=np.float64),
            landmarks=[Landmark(lm["name"], tuple(lm["world"]), tuple(lm["pixel"]) if lm.get("pixel") else None)
                       for lm in d.get("landmarks", [])],
            source=d.get("source", "manual"),
        )


def default_calibration(map_name: str, image_path: str | None, image_size: tuple[int, int],
                        radius: float = DEFAULT_RADIUS) -> Calibration:
    w, h = image_size
    sx = w / (2 * radius)
    sy = h / (2 * radius)
    matrix = np.array([[sx, 0.0, w / 2.0], [0.0, -sy, h / 2.0]], dtype=np.float64)
    return Calibration(map_name, image_path, matrix, source="radius")


def fit_affine(world: np.ndarray, pixel: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares 2x3 affine mapping world -> pixel. Returns (matrix, RMS error in px)."""
    world = np.asarray(world, dtype=np.float64)
    pixel = np.asarray(pixel, dtype=np.float64)
    if world.shape[0] < 3:
        raise ValueError("need at least 3 landmarks")
    a = np.hstack([world[:, :2], np.ones((world.shape[0], 1))])
    coef, *_ = np.linalg.lstsq(a, pixel[:, :2], rcond=None)  # (3, 2)
    matrix = coef.T
    pred = a @ coef
    rms = float(np.sqrt(np.mean(np.sum((pred - pixel[:, :2]) ** 2, axis=1))))
    return matrix, rms


def landmark_candidates(objectives) -> list[Landmark]:
    """Distinct fixed structures from boon's ``objectives`` DataFrame (mean position per type/team/lane)."""
    import polars as pl

    team_label = {2: "Amber", 3: "Sapphire"}
    lane_label = {1: "left", 4: "mid", 6: "right", 0: ""}
    out: list[Landmark] = []
    if objectives is None or objectives.height == 0:
        return out
    agg = (
        objectives.filter(pl.col("objective_type") != "mid_boss")
        .group_by(["objective_type", "team_num", "lane"])
        .agg(pl.col("x").mean(), pl.col("y").mean())
        .sort(["team_num", "objective_type", "lane"])
    )
    for r in agg.to_dicts():
        name = f"{r['objective_type'].title()} {team_label.get(r['team_num'], r['team_num'])}"
        if r["lane"] in lane_label and lane_label[r["lane"]]:
            name += f" {lane_label[r['lane']]}"
        out.append(Landmark(name, (float(r["x"]), float(r["y"]))))
    return out
