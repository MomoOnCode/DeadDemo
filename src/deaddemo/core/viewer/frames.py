"""Sampled per-tick player state for 2D playback, as dense numpy arrays."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from deaddemo.core.db.repos import MatchRow
from deaddemo.core.stats.match_stats import scan


@dataclass
class KillEvent:
    tick: int
    match_seconds: float
    attacker: int | None
    victim: int | None
    x: float
    y: float


@dataclass
class ObjectiveEvent:
    tick: int
    match_seconds: float
    objective_type: str
    team_num: int | None
    x: float
    y: float


@dataclass
class FrameSet:
    ticks: np.ndarray  # (T,)
    seconds: np.ndarray  # (T,) match seconds
    hero_ids: list[int]  # (P,)
    team_of: dict[int, int]
    xyz: np.ndarray  # (T, P, 3)
    alive: np.ndarray  # (T, P) bool
    health_frac: np.ndarray  # (T, P)
    souls: np.ndarray  # (T, P)
    level: np.ndarray  # (T, P)
    kills: list[KillEvent] = field(default_factory=list)
    objectives: list[ObjectiveEvent] = field(default_factory=list)
    objective_positions: list[tuple[str, int, float, float]] = field(default_factory=list)  # type, team, x, y

    @property
    def n_frames(self) -> int:
        return int(self.ticks.shape[0])

    def index_for_tick(self, tick: int) -> int:
        return int(np.clip(np.searchsorted(self.ticks, tick), 0, self.n_frames - 1))

    def index_for_seconds(self, s: float) -> int:
        return int(np.clip(np.searchsorted(self.seconds, s), 0, self.n_frames - 1))


def load_frames(match: MatchRow, team_of: dict[int, int], step: int = 4) -> FrameSet | None:
    lf = scan(match, "player_ticks")
    if lf is None:
        return None
    cols = lf.collect_schema().names()
    souls_expr = (pl.col("gold_net_worth") if "gold_net_worth" in cols else pl.col("souls")).alias("souls")
    df = (
        lf.filter((pl.col("tick") % step) == 0)
        .select(["tick", "match_seconds", "hero_id", "x", "y", "z", "health", "max_health", "is_alive", souls_expr,
                 "level"])
        .collect()
        .sort(["tick", "hero_id"])
    )
    if df.height == 0:
        return None
    hero_ids = sorted(set(df["hero_id"].to_list()))
    hero_index = {h: i for i, h in enumerate(hero_ids)}
    ticks = np.array(sorted(set(df["tick"].to_list())), dtype=np.int64)
    tick_index = {t: i for i, t in enumerate(ticks.tolist())}
    T, P = len(ticks), len(hero_ids)
    xyz = np.full((T, P, 3), np.nan, dtype=np.float32)
    alive = np.zeros((T, P), dtype=bool)
    health = np.zeros((T, P), dtype=np.float32)
    souls = np.zeros((T, P), dtype=np.int32)
    level = np.zeros((T, P), dtype=np.int16)
    seconds = np.zeros(T, dtype=np.float32)

    ti = np.fromiter((tick_index[t] for t in df["tick"].to_list()), dtype=np.int64, count=df.height)
    hi = np.fromiter((hero_index[h] for h in df["hero_id"].to_list()), dtype=np.int64, count=df.height)
    xyz[ti, hi, 0] = df["x"].to_numpy()
    xyz[ti, hi, 1] = df["y"].to_numpy()
    xyz[ti, hi, 2] = df["z"].to_numpy()
    alive[ti, hi] = df["is_alive"].fill_null(False).to_numpy()
    mh = df["max_health"].fill_null(1).to_numpy().astype(np.float32)
    health[ti, hi] = df["health"].fill_null(0).to_numpy().astype(np.float32) / np.maximum(mh, 1)
    souls[ti, hi] = df["souls"].fill_null(0).to_numpy()
    level[ti, hi] = df["level"].fill_null(0).to_numpy()
    seconds[ti] = df["match_seconds"].to_numpy()

    fs = FrameSet(ticks=ticks, seconds=seconds, hero_ids=hero_ids, team_of=team_of, xyz=xyz, alive=alive,
                  health_frac=health, souls=souls, level=level)
    return fs


def attach_events(fs: FrameSet, kills_rows, objective_rows, objectives_lf: pl.LazyFrame | None) -> None:
    """Kill markers use the victim's last known position; objectives use their parquet positions."""
    hero_index = {h: i for i, h in enumerate(fs.hero_ids)}
    for k in kills_rows:
        victim = k["victim_hero_id"]
        x = y = float("nan")
        if victim in hero_index:
            i = fs.index_for_tick(int(k["tick"]))
            pos = fs.xyz[i, hero_index[victim]]
            x, y = float(pos[0]), float(pos[1])
        fs.kills.append(KillEvent(int(k["tick"]), float(k["match_seconds"] or 0), k["attacker_hero_id"], victim, x, y))
    positions: dict[tuple[str, int, int], tuple[float, float]] = {}
    if objectives_lf is not None:
        agg = (
            objectives_lf.group_by(["objective_type", "team_num", "lane"])
            .agg(pl.col("x").mean(), pl.col("y").mean())
            .collect()
        )
        for r in agg.to_dicts():
            positions[(r["objective_type"], int(r["team_num"] or 0), int(r["lane"] or 0))] = (float(r["x"]),
                                                                                              float(r["y"]))
            if r["objective_type"] != "mid_boss":
                fs.objective_positions.append((r["objective_type"], int(r["team_num"] or 0), float(r["x"]),
                                               float(r["y"])))
    for o in objective_rows:
        key = (o["objective_type"], int(o["team_num"] or 0), int(o["lane"] or 0))
        alt = (o["objective_type"], int(o["team_num"] or 0), 16777215)
        pos = positions.get(key) or positions.get(alt) or (float("nan"), float("nan"))
        fs.objectives.append(ObjectiveEvent(int(o["tick"]), float(o["match_seconds"] or 0), o["objective_type"],
                                            o["team_num"], pos[0], pos[1]))
