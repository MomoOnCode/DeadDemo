"""Turn boon DataFrames into plain row dicts for the SQLite summary tables."""

from __future__ import annotations

from typing import Any

import polars as pl


def _seconds(tick: int, start_tick: int, tick_rate: int) -> float:
    return (tick - start_tick) / tick_rate


def _to_py(v):
    if hasattr(v, "item"):
        return v.item()
    return v


def scoreboard_rows(demo, ticks: pl.DataFrame, winning_team: int | None) -> list[dict[str, Any]]:
    players = demo.players
    last = ticks.filter(pl.col("tick") == pl.col("tick").max().over("hero_id")).unique(subset=["hero_id"],
                                                                                        keep="last")
    stat_cols = [c for c in ("kills", "deaths", "assists", "last_hits", "denies", "hero_damage", "level")
                 if c in last.columns]
    # "souls" in the summary tables means net worth (what the in-game scoreboard and the API show).
    souls_col = "gold_net_worth" if "gold_net_worth" in last.columns else "souls"
    last = last.select(["hero_id", *stat_cols, pl.col(souls_col).alias("souls")])
    joined = players.join(last, on="hero_id", how="left")
    kp: dict[int, float] = {}
    td: dict[int, float] = {}
    try:
        for r in demo.kill_participation().to_dicts():
            kp[int(r["hero_id"])] = float(r["kill_participation"])
    except Exception:  # noqa: BLE001
        pass
    try:
        for r in demo.time_dead().to_dicts():
            td[int(r["hero_id"])] = float(r["seconds_dead"])
    except Exception:  # noqa: BLE001
        pass
    rows = []
    for r in joined.to_dicts():
        hero_id = int(r["hero_id"])
        team = r.get("team_num")
        rows.append({
            "hero_id": hero_id,
            "steam_id": _to_py(r.get("steam_id")),
            "player_name": r.get("player_name"),
            "team_num": _to_py(team),
            "start_lane": _to_py(r.get("start_lane")),
            "rank": _to_py(r.get("rank")),
            "kills": _to_py(r.get("kills")),
            "deaths": _to_py(r.get("deaths")),
            "assists": _to_py(r.get("assists")),
            "last_hits": _to_py(r.get("last_hits")),
            "denies": _to_py(r.get("denies")),
            "souls": _to_py(r.get("souls")),
            "hero_damage": _to_py(r.get("hero_damage")),
            "level": _to_py(r.get("level")),
            "kill_participation": kp.get(hero_id),
            "time_dead_s": td.get(hero_id),
            "won": int(team == winning_team) if team is not None and winning_team is not None else None,
        })
    return rows


def kill_rows(kills: pl.DataFrame, start_tick: int, tick_rate: int) -> list[dict[str, Any]]:
    return [
        {
            "tick": int(r["tick"]),
            "match_seconds": _seconds(int(r["tick"]), start_tick, tick_rate),
            "victim_hero_id": _to_py(r.get("victim_hero_id")),
            "attacker_hero_id": _to_py(r.get("attacker_hero_id")),
            "assister_hero_ids": [int(x) for x in (r.get("assister_hero_ids") or [])],
        }
        for r in kills.to_dicts()
    ]


def item_rows(items: pl.DataFrame, start_tick: int, tick_rate: int) -> list[dict[str, Any]]:
    return [
        {
            "tick": int(r["tick"]),
            "match_seconds": _seconds(int(r["tick"]), start_tick, tick_rate),
            "hero_id": _to_py(r.get("hero_id")),
            "ability_id": _to_py(r.get("ability_id")),
            "change": r.get("change"),
        }
        for r in items.to_dicts()
    ]


def objective_event_rows(objectives: pl.DataFrame, start_tick: int, tick_rate: int) -> list[dict[str, Any]]:
    """First tick at which each objective entity reached zero health."""
    if objectives.height == 0 or "health" not in objectives.columns:
        return []
    dead = (
        objectives.filter(pl.col("health") <= 0)
        .group_by(["entity_id", "objective_type", "team_num", "lane"])
        .agg(pl.col("tick").min())
        .sort("tick")
    )
    return [
        {
            "tick": int(r["tick"]),
            "match_seconds": _seconds(int(r["tick"]), start_tick, tick_rate),
            "objective_type": r["objective_type"],
            "team_num": _to_py(r.get("team_num")),
            "lane": _to_py(r.get("lane")) if r.get("lane") not in (None, 16777215) else None,
        }
        for r in dead.to_dicts()
    ]
