"""boon -> tables + Parquet. Runs in a worker subprocess; must stay Qt-free and picklable.

Output contract (``ParseResult``): small summary rows for SQLite plus Parquet files for the
bulk datasets (``player_ticks``, ``damage``, ``objectives`` and any requested extras).
"""

from __future__ import annotations

import importlib.metadata
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PARSER_VERSION = "1"

# Always loaded; the first three feed SQLite summary tables, the rest are written as Parquet.
CORE_DATASETS = ("players", "kills", "item_purchases", "player_ticks", "damage", "objectives")

PLAYER_TICK_COLS = [
    "tick", "hero_id", "x", "y", "z", "yaw", "health", "max_health", "barrier", "is_alive", "lifestate",
    "level", "souls", "spent_souls", "kills", "deaths", "assists", "last_hits", "denies", "hero_damage",
    "hero_healing", "objective_damage", "has_ultimate_trained", "has_rejuvenator", "kill_streak",
]

DAMAGE_COLS = [
    "tick", "damage", "victim_hero_id", "attacker_hero_id", "victim_health_new", "hitgroup_id", "crit_damage",
    "attacker_class", "victim_class", "ability_id", "damage_type", "citadel_type", "is_melee",
]


@dataclass
class ParseResult:
    match_id: int
    match_row: dict[str, Any] = field(default_factory=dict)
    players: list[dict[str, Any]] = field(default_factory=list)
    kills: list[dict[str, Any]] = field(default_factory=list)
    item_purchases: list[dict[str, Any]] = field(default_factory=list)
    objective_events: list[dict[str, Any]] = field(default_factory=list)
    parquet_dir: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    boon_version: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_db_stub(cls, match_id: int, parquet_dir: str | None) -> ParseResult:
        return cls(match_id=match_id, parquet_dir=parquet_dir or "")


def boon_version() -> str:
    try:
        return importlib.metadata.version("boon-deadlock")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def parse_demo(dem_path: str, out_dir: str, extra_datasets: tuple[str, ...] = ()) -> ParseResult:
    import polars as pl
    from boon import Demo

    from deaddemo.core.parse import tables

    demo = Demo(dem_path)
    available = set(Demo.available_datasets())
    warnings: list[str] = []
    extras = [d for d in extra_datasets if d in available and d not in CORE_DATASETS]
    for d in extra_datasets:
        if d not in available:
            warnings.append(f"dataset {d!r} not available in boon {boon_version()}")
    demo.load(*[d for d in CORE_DATASETS if d != "players"], *extras)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    match_id = int(demo.match_id or 0)
    tick_rate = int(demo.tick_rate or 64)
    start_tick = int(demo.game_start_tick or 0)

    def match_seconds_expr() -> pl.Expr:
        return ((pl.col("tick") - start_tick) / tick_rate).alias("match_seconds")

    # -- bulk datasets -> parquet -------------------------------------------------------
    manifest: dict[str, Any] = {"match_id": match_id, "boon_version": boon_version(),
                                "parser_version": PARSER_VERSION, "datasets": {}}

    def write(name: str, df: pl.DataFrame) -> None:
        path = out / f"{name}.parquet"
        df.write_parquet(path, compression="zstd", compression_level=3)
        manifest["datasets"][name] = {"rows": df.height, "path": path.name, "columns": df.columns}

    ticks = demo.player_ticks
    tick_cols = [c for c in PLAYER_TICK_COLS if c in ticks.columns]
    write("player_ticks", ticks.select(tick_cols).with_columns(match_seconds_expr()))

    dmg = demo.damage
    write("damage", dmg.select([c for c in DAMAGE_COLS if c in dmg.columns]).with_columns(match_seconds_expr()))

    objectives = demo.objectives
    write("objectives", objectives.with_columns(match_seconds_expr()))

    for name in extras:
        try:
            df = getattr(demo, name)
            if isinstance(df, pl.DataFrame):
                if "tick" in df.columns:
                    df = df.with_columns(match_seconds_expr())
                write(name, df)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{name}: {type(exc).__name__}: {exc}")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    # -- summary rows ------------------------------------------------------------------
    winning_team = int(demo.winning_team_num) if demo.winning_team_num is not None else None
    match_row = {
        "match_id": match_id,
        "map_name": demo.map_name,
        "build": int(demo.build) if demo.build is not None else None,
        "game_mode": int(demo.game_mode) if demo.game_mode is not None else None,
        "tick_rate": tick_rate,
        "total_ticks": int(demo.total_ticks or 0),
        "game_start_tick": start_tick,
        "game_over_tick": int(demo.game_over_tick) if demo.game_over_tick is not None else None,
        "regulation_seconds": float(demo.regulation_seconds) if demo.regulation_seconds is not None else None,
        "pregame_seconds": float(demo.pregame_seconds) if demo.pregame_seconds is not None else None,
        "winning_team": winning_team,
        "parser_version": PARSER_VERSION,
        "boon_version": boon_version(),
        "parquet_dir": str(out),
        "has_ticks": 1,
    }

    players = tables.scoreboard_rows(demo, ticks, winning_team)
    kills = tables.kill_rows(demo.kills, start_tick, tick_rate)
    items = tables.item_rows(demo.item_purchases, start_tick, tick_rate)
    obj_events = tables.objective_event_rows(objectives, start_tick, tick_rate)

    return ParseResult(
        match_id=match_id, match_row=match_row, players=players, kills=kills, item_purchases=items,
        objective_events=obj_events, parquet_dir=str(out), manifest=manifest, boon_version=boon_version(),
        warnings=warnings,
    )


def result_to_dict(result: ParseResult) -> dict[str, Any]:
    return asdict(result)
