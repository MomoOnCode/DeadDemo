"""Export a parsed match to JSON or a multi-sheet XLSX workbook."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import polars as pl

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import MatchRepo


def _frames(db: Database, match_id: int) -> dict[str, pl.DataFrame]:
    repo = MatchRepo(db)
    match = repo.get(match_id)
    if match is None:
        raise ValueError(f"match {match_id} is not parsed")
    cat = catalog()
    players = pl.DataFrame([asdict(p) for p in repo.players(match_id)])
    if players.height:
        players = players.with_columns(
            pl.col("hero_id").map_elements(cat.hero_name, return_dtype=pl.String).alias("hero")
        )
    kills = pl.DataFrame([dict(r) for r in repo.kills(match_id)])
    if kills.height:
        kills = kills.with_columns(
            pl.col("attacker_hero_id").map_elements(cat.hero_name, return_dtype=pl.String).alias("attacker"),
            pl.col("victim_hero_id").map_elements(cat.hero_name, return_dtype=pl.String).alias("victim"),
        )
    items = pl.DataFrame([dict(r) for r in repo.item_purchases(match_id)])
    if items.height:
        items = items.with_columns(
            pl.col("hero_id").map_elements(cat.hero_name, return_dtype=pl.String).alias("hero"),
            pl.col("ability_id").map_elements(cat.item_name, return_dtype=pl.String).alias("item"),
        )
    objectives = pl.DataFrame([dict(r) for r in repo.objective_events(match_id)])
    summary = pl.DataFrame([asdict(match)])
    return {"Match": summary, "Scoreboard": players, "Kills": kills, "Items": items, "Objectives": objectives}


def export_match_json(match_id: int, dest: Path, *, db: Database | None = None) -> Path:
    own = db is None
    db = db or Database.open()
    try:
        frames = _frames(db, match_id)
    finally:
        if own:
            db.close()
    dest.write_text(json.dumps({k: v.to_dicts() for k, v in frames.items()}, indent=1, default=str), encoding="utf-8")
    return dest


def export_player_xlsx(profile, dest: Path, *, db: Database | None = None) -> Path:
    import xlsxwriter

    from deaddemo.core.stats.player_profile import profile_to_rows
    from deaddemo.core.stats.player_stats import matches_for_player

    cat = catalog()
    own = db is None
    db = db or Database.open()
    try:
        matches = matches_for_player(db, profile.steam_id)
    finally:
        if own:
            db.close()
    overview = pl.DataFrame({"stat": [k for k, _ in profile_to_rows(profile)],
                             "value": [v for _, v in profile_to_rows(profile)]})
    heroes = pl.DataFrame([{"hero": cat.hero_name(h.hero_id), "games": h.games, "wins": h.wins,
                            "win_rate": round(h.winrate, 3), "kills": round(h.kills, 2), "deaths": round(h.deaths, 2),
                            "assists": round(h.assists, 2), "souls_per_min": round(h.souls_per_min, 1)}
                           for h in profile.heroes])
    match_df = pl.DataFrame(matches) if matches else pl.DataFrame()
    with xlsxwriter.Workbook(str(dest)) as wb:
        for name, df in (("Overview", overview), ("Heroes", heroes), ("Matches", match_df)):
            if df.height == 0:
                wb.add_worksheet(name)
                continue
            df.write_excel(wb, worksheet=name, autofit=True, table_style="Table Style Medium 2")
    return dest


def export_match_xlsx(match_id: int, dest: Path, *, db: Database | None = None) -> Path:
    import xlsxwriter

    own = db is None
    db = db or Database.open()
    try:
        frames = _frames(db, match_id)
    finally:
        if own:
            db.close()
    with xlsxwriter.Workbook(str(dest)) as wb:
        for name, df in frames.items():
            if df.height == 0:
                wb.add_worksheet(name)
                continue
            df = df.with_columns([pl.col(c).cast(pl.String) for c, t in df.schema.items() if t == pl.List])
            df.write_excel(wb, worksheet=name, autofit=True, table_style="Table Style Medium 2")
    return dest
