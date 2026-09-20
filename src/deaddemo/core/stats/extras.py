"""Per-player per-match derived stats ("extras").

``compute_extras`` is pure and works on the DataFrames boon produced, so the parser can call it while
the datasets are loaded and ``ensure_extras`` can recompute it later from the Parquet files for
matches parsed before this table existed.
"""

from __future__ import annotations

import json
from typing import Any

import polars as pl

from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import MatchRepo, MatchRow

MULTI_KILL_WINDOW_S = 10.0

EXTRA_COLUMNS = [
    "headshot_hits", "bullet_hits", "bullet_dmg", "spirit_dmg", "melee_dmg", "other_dmg", "hero_healing",
    "self_healing", "objective_damage", "max_kill_streak", "multi2", "multi3", "multi4", "multi5", "multi6",
    "solo_kills", "first_blood", "teamfights", "teamfights_won", "souls_10m", "souls_20m", "level_10m", "level_20m",
]


def _empty(hero_id: int) -> dict[str, Any]:
    d = {c: 0 for c in EXTRA_COLUMNS}
    d["hero_id"] = hero_id
    return d


def multi_kill_counts(kill_seconds: list[float], window_s: float = MULTI_KILL_WINDOW_S) -> dict[int, int]:
    """Cluster sorted kill times where consecutive kills are within ``window_s``; return {size: count}."""
    counts: dict[int, int] = {}
    if not kill_seconds:
        return counts
    times = sorted(kill_seconds)
    size = 1
    for prev, cur in zip(times, times[1:], strict=False):
        if cur - prev <= window_s:
            size += 1
        else:
            if size >= 2:
                counts[min(size, 6)] = counts.get(min(size, 6), 0) + 1
            size = 1
    if size >= 2:
        counts[min(size, 6)] = counts.get(min(size, 6), 0) + 1
    return counts


def compute_extras(
    *,
    hero_ids: list[int],
    team_of: dict[int, int],
    ticks: pl.DataFrame | None,
    damage: pl.DataFrame | None,
    kills: list[dict[str, Any]],
    teamfights: pl.DataFrame | None,
) -> list[dict[str, Any]]:
    """``kills`` rows need tick, match_seconds, attacker_hero_id, victim_hero_id, assister_hero_ids (list)."""
    out = {h: _empty(h) for h in hero_ids}

    # -- from player_ticks --------------------------------------------------------------
    if ticks is not None and ticks.height:
        cols = ticks.columns
        last = ticks.filter(pl.col("tick") == pl.col("tick").max().over("hero_id")).unique(subset=["hero_id"],
                                                                                            keep="last")
        for r in last.to_dicts():
            h = int(r["hero_id"])
            if h not in out:
                continue
            for src, dst in (("hero_healing", "hero_healing"), ("self_healing", "self_healing"),
                             ("objective_damage", "objective_damage")):
                if src in cols:
                    out[h][dst] = int(r.get(src) or 0)
        if "kill_streak" in cols:
            for r in ticks.group_by("hero_id").agg(pl.col("kill_streak").max()).to_dicts():
                if int(r["hero_id"]) in out:
                    out[int(r["hero_id"])]["max_kill_streak"] = int(r["kill_streak"] or 0)
        souls_col = "gold_net_worth" if "gold_net_worth" in cols else "souls"
        if "match_seconds" in cols:
            for minute, key in ((10, "10m"), (20, "20m")):
                snap = (
                    ticks.filter(pl.col("match_seconds") <= minute * 60)
                    .filter(pl.col("tick") == pl.col("tick").max().over("hero_id"))
                    .unique(subset=["hero_id"], keep="last")
                )
                for r in snap.to_dicts():
                    h = int(r["hero_id"])
                    if h in out and (r.get("match_seconds") or 0) >= minute * 60 - 30:
                        out[h][f"souls_{key}"] = int(r.get(souls_col) or 0)
                        out[h][f"level_{key}"] = int(r.get("level") or 0)

    # -- from damage --------------------------------------------------------------------
    if damage is not None and damage.height and "citadel_type" in damage.columns:
        hero_dmg = damage.filter(pl.col("attacker_hero_id").is_not_null() & pl.col("victim_hero_id").is_not_null())
        split = hero_dmg.group_by(["attacker_hero_id", "citadel_type"]).agg(pl.col("damage").sum())
        for r in split.to_dicts():
            h = int(r["attacker_hero_id"])
            if h not in out:
                continue
            key = {1: "bullet_dmg", 2: "spirit_dmg", 3: "melee_dmg"}.get(int(r["citadel_type"] or 0), "other_dmg")
            out[h][key] += int(r["damage"] or 0)
        if "hitgroup_id" in damage.columns:
            bullets = hero_dmg.filter(pl.col("citadel_type") == 1).group_by("attacker_hero_id").agg(
                pl.len().alias("hits"), (pl.col("hitgroup_id") == 1).sum().alias("hs"))
            for r in bullets.to_dicts():
                h = int(r["attacker_hero_id"])
                if h in out:
                    out[h]["bullet_hits"] = int(r["hits"])
                    out[h]["headshot_hits"] = int(r["hs"])

    # -- from kills ---------------------------------------------------------------------
    kills_sorted = sorted(kills, key=lambda k: int(k["tick"]))
    if kills_sorted:
        fb = kills_sorted[0]
        if fb.get("attacker_hero_id") in out:
            out[int(fb["attacker_hero_id"])]["first_blood"] = 1
        if fb.get("victim_hero_id") in out:
            out[int(fb["victim_hero_id"])]["first_blood"] = -1
    per_attacker: dict[int, list[float]] = {}
    for k in kills_sorted:
        a = k.get("attacker_hero_id")
        if a is None or int(a) not in out:
            continue
        a = int(a)
        per_attacker.setdefault(a, []).append(float(k.get("match_seconds") or 0.0))
        assisters = k.get("assister_hero_ids")
        if isinstance(assisters, str):
            assisters = json.loads(assisters or "[]")
        if not assisters:
            out[a]["solo_kills"] += 1
    for a, times in per_attacker.items():
        for size, n in multi_kill_counts(times).items():
            out[a][f"multi{size}"] += n

    # -- from teamfights ----------------------------------------------------------------
    if teamfights is not None and teamfights.height and "participants" in teamfights.columns:
        for f in teamfights.to_dicts():
            participants = [int(p) for p in (f.get("participants") or [])]
            start, end = int(f.get("start_tick") or 0), int(f.get("end_tick") or 0)
            team_kills: dict[int, int] = {}
            for k in kills_sorted:
                if start <= int(k["tick"]) <= end and k.get("attacker_hero_id") is not None:
                    t = team_of.get(int(k["attacker_hero_id"]))
                    if t is not None:
                        team_kills[t] = team_kills.get(t, 0) + 1
            winner = None
            if team_kills:
                best = max(team_kills.values())
                tops = [t for t, n in team_kills.items() if n == best]
                winner = tops[0] if len(tops) == 1 else None
            for p in participants:
                if p in out:
                    out[p]["teamfights"] += 1
                    if winner is not None and team_of.get(p) == winner:
                        out[p]["teamfights_won"] += 1
    return [out[h] for h in hero_ids]


def ensure_extras(db: Database, match: MatchRow) -> bool:
    """Backfill extras for an already-parsed match from its Parquet files. Returns True if written."""
    from deaddemo.core.stats.match_stats import scan

    repo = MatchRepo(db)
    if repo.extras(match.match_id):
        return False
    players = repo.players(match.match_id)
    if not players:
        return False
    team_of = {p.hero_id: p.team_num or 0 for p in players}
    ticks_lf, dmg_lf, tf_lf = scan(match, "player_ticks"), scan(match, "damage"), scan(match, "teamfights")
    ticks = ticks_lf.collect() if ticks_lf is not None else None
    dmg = dmg_lf.collect() if dmg_lf is not None else None
    tf = tf_lf.collect() if tf_lf is not None else None
    kills = [dict(r) for r in repo.kills(match.match_id)]
    rows = compute_extras(hero_ids=[p.hero_id for p in players], team_of=team_of, ticks=ticks, damage=dmg,
                          kills=kills, teamfights=tf)
    repo.store_extras(match.match_id, rows)
    return True


def backfill_all(db: Database, progress=None) -> int:
    repo = MatchRepo(db)
    n = 0
    matches = repo.all()
    for i, m in enumerate(matches):
        if progress:
            progress(i, len(matches), f"Computing stats for {m.match_id}")
        try:
            if ensure_extras(db, m):
                n += 1
        except Exception:  # noqa: BLE001 - one bad match must not stop the sweep
            continue
    return n
