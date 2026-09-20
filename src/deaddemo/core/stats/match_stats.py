"""Per-match statistics read from SQLite summaries and Parquet bulk data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from deaddemo import paths
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import MatchRepo, MatchRow


def parquet_dir(match: MatchRow) -> Path:
    return Path(match.parquet_dir) if match.parquet_dir else paths.parsed_dir(match.match_id)


def parquet_path(match: MatchRow, name: str) -> Path | None:
    p = parquet_dir(match) / f"{name}.parquet"
    return p if p.exists() else None


def scan(match: MatchRow, name: str) -> pl.LazyFrame | None:
    p = parquet_path(match, name)
    return pl.scan_parquet(p) if p else None


@dataclass
class TimelineEvent:
    match_seconds: float
    kind: str  # 'kill' | 'objective' | 'teamfight' | 'midboss'
    text: str
    team_num: int | None
    tick: int
    hero_id: int | None = None
    x: float | None = None
    y: float | None = None


def networth_timeline(match: MatchRow, every_s: float = 20.0) -> pl.DataFrame:
    """Columns: match_seconds, hero_id, souls (one row per hero per sample)."""
    lf = scan(match, "player_ticks")
    if lf is None:
        return pl.DataFrame({"match_seconds": [], "hero_id": [], "souls": []})
    cols = lf.collect_schema().names()
    souls = pl.col("gold_net_worth") if "gold_net_worth" in cols else pl.col("souls")
    # Bucket by time, keep the last sample per hero per bucket, then forward-fill across a full
    # hero x bucket grid: dead heroes have no rows at all, and a gap must not drag team totals down.
    sampled = (
        lf.select([pl.col("match_seconds"), pl.col("hero_id"), souls.alias("souls"), pl.col("tick")])
        .with_columns(((pl.col("match_seconds") / every_s).floor() * every_s).alias("bucket"))
        .sort("tick")
        .group_by(["hero_id", "bucket"])
        .agg(pl.col("souls").last())
        .collect()
    )
    if sampled.height == 0:
        return pl.DataFrame({"match_seconds": [], "hero_id": [], "souls": []})
    heroes = sampled.select("hero_id").unique()
    buckets = sampled.select("bucket").unique()
    grid = heroes.join(buckets, how="cross")
    return (
        grid.join(sampled, on=["hero_id", "bucket"], how="left")
        .sort(["hero_id", "bucket"])
        .with_columns(pl.col("souls").fill_null(strategy="forward").over("hero_id").fill_null(0))
        .rename({"bucket": "match_seconds"})
        .sort(["hero_id", "match_seconds"])
    )


def team_networth_timeline(match: MatchRow, team_of: dict[int, int], every_s: float = 20.0) -> pl.DataFrame:
    df = networth_timeline(match, every_s)
    if df.height == 0:
        return pl.DataFrame({"match_seconds": [], "team_num": [], "souls": []})
    mapping = pl.DataFrame({"hero_id": list(team_of.keys()), "team_num": list(team_of.values())})
    return (
        df.join(mapping, on="hero_id", how="inner")
        .group_by(["match_seconds", "team_num"])
        .agg(pl.col("souls").sum())
        .sort(["team_num", "match_seconds"])
    )


def damage_summary(match: MatchRow) -> pl.DataFrame:
    """Per attacker hero: hero damage dealt / taken, from the damage parquet."""
    lf = scan(match, "damage")
    if lf is None:
        return pl.DataFrame()
    df = lf.filter(pl.col("attacker_hero_id").is_not_null() & pl.col("victim_hero_id").is_not_null()).collect()
    dealt = df.group_by("attacker_hero_id").agg(pl.col("damage").sum().alias("dealt")).rename(
        {"attacker_hero_id": "hero_id"})
    taken = df.group_by("victim_hero_id").agg(pl.col("damage").sum().alias("taken")).rename(
        {"victim_hero_id": "hero_id"})
    return dealt.join(taken, on="hero_id", how="full", coalesce=True).fill_null(0).sort("dealt", descending=True)


def damage_by_ability(match: MatchRow, hero_id: int) -> pl.DataFrame:
    lf = scan(match, "damage")
    if lf is None:
        return pl.DataFrame()
    return (
        lf.filter((pl.col("attacker_hero_id") == hero_id) & pl.col("victim_hero_id").is_not_null())
        .group_by(["ability_id", "is_melee"])
        .agg(pl.col("damage").sum().alias("damage"), pl.len().alias("hits"))
        .sort("damage", descending=True)
        .collect()
    )


def damage_matrix(match: MatchRow) -> pl.DataFrame:
    lf = scan(match, "damage")
    if lf is None:
        return pl.DataFrame()
    return (
        lf.filter(pl.col("attacker_hero_id").is_not_null() & pl.col("victim_hero_id").is_not_null())
        .group_by(["attacker_hero_id", "victim_hero_id"])
        .agg(pl.col("damage").sum())
        .collect()
    )


def timeline(db: Database, match: MatchRow, hero_name) -> list[TimelineEvent]:
    repo = MatchRepo(db)
    players = {p.hero_id: p for p in repo.players(match.match_id)}
    events: list[TimelineEvent] = []
    for k in repo.kills(match.match_id):
        attacker = k["attacker_hero_id"]
        victim = k["victim_hero_id"]
        assists = json.loads(k["assister_hero_ids"] or "[]")
        text = f"{hero_name(attacker)} killed {hero_name(victim)}"
        if assists:
            text += " (+" + ", ".join(hero_name(a) for a in assists) + ")"
        team = players[attacker].team_num if attacker in players else None
        events.append(TimelineEvent(k["match_seconds"] or 0.0, "kill", text, team, k["tick"], attacker))
    for o in repo.objective_events(match.match_id):
        otype = o["objective_type"]
        if otype == "mid_boss":
            events.append(TimelineEvent(o["match_seconds"] or 0.0, "midboss", "Mid Boss killed", None, o["tick"]))
            continue
        lane = f" (lane {o['lane']})" if o["lane"] else ""
        events.append(TimelineEvent(o["match_seconds"] or 0.0, "objective",
                                    f"{otype.title()}{lane} destroyed", o["team_num"], o["tick"]))
    tf = scan(match, "teamfights")
    if tf is not None:
        for r in tf.collect().to_dicts():
            parts = ", ".join(hero_name(h) for h in (r.get("participants") or []))
            events.append(TimelineEvent(
                float(r.get("start_seconds") or 0.0) - (match.pregame_seconds or 0.0), "teamfight",
                f"Fight: {r.get('kills', 0)} kills, {r.get('hero_damage', 0)} dmg, {r.get('duration_seconds', 0):.0f}s "
                f"[{parts}]", None, int(r.get("start_tick") or 0), None, r.get("center_x"), r.get("center_y")))
    events.sort(key=lambda e: e.match_seconds)
    return events


def build_order(db: Database, match: MatchRow, hero_id: int) -> list[dict]:
    return [dict(r) for r in MatchRepo(db).item_purchases(match.match_id, hero_id)]


def chat(match: MatchRow) -> pl.DataFrame:
    lf = scan(match, "chat")
    return lf.collect() if lf is not None else pl.DataFrame()
