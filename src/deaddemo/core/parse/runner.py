"""Glue between the parse pipeline (subprocess-safe) and the database (main thread / CLI)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from deaddemo import paths
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import DemoRepo, DemoRow, MatchRepo
from deaddemo.core.parse.pipeline import PARSER_VERSION, ParseResult, parse_demo


def resolve_target(db: Database, target: str) -> DemoRow:
    """``target`` is a match id known to the catalog or a path to a .dem file."""
    repo = DemoRepo(db)
    if target.isdigit():
        rows = [r for r in repo.by_match(int(target)) if r.status != "partial"]
        if not rows:
            raise FileNotFoundError(f"no local demo for match {target}; run `deaddemo scan` first")
        return rows[0]
    path = Path(target).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    row = repo.by_path(str(path))
    if row is None:
        from deaddemo.core.replays.scanner import sniff_header

        rf = sniff_header(path, "manual")
        row = repo.upsert_found(path=str(path), source="manual", size_bytes=rf.size_bytes, mtime=rf.mtime,
                                match_id=rf.match_id, build=rf.build, map_name=rf.map_name,
                                tick_rate=rf.tick_rate, total_ticks=rf.total_ticks, status=rf.status,
                                error=rf.error)
    return row


def store_result(db: Database, demo: DemoRow, result: ParseResult) -> None:
    matches = MatchRepo(db)
    result.match_row["demo_id"] = demo.id
    matches.store_parse_result(
        match_row=result.match_row, players=result.players, kills=result.kills,
        item_purchases=result.item_purchases, objective_events=result.objective_events,
        player_extras=result.player_extras,
    )
    demos = DemoRepo(db)
    demos.set_status(demo.id, "parsed", parser_version=PARSER_VERSION)
    if demo.match_id != result.match_id:
        with db.transaction() as conn:
            conn.execute("UPDATE demos SET match_id=? WHERE id=?", (result.match_id, demo.id))


def parse_and_store(
    target: str,
    *,
    force: bool = False,
    datasets: tuple[str, ...] | None = None,
    progress: Callable[[str], None] | None = None,
    db: Database | None = None,
) -> ParseResult:
    from deaddemo.settings import Settings

    own_db = db is None
    db = db or Database.open()
    try:
        demo = resolve_target(db, target)
        if demo.status == "parsed" and not force and demo.parser_version == PARSER_VERSION:
            existing = MatchRepo(db).get(demo.match_id) if demo.match_id else None
            if existing:
                if progress:
                    progress(f"match {demo.match_id} already parsed (use --force to reparse)")
                return ParseResult.from_db_stub(existing.match_id, existing.parquet_dir)
        settings = Settings.load()
        ds = datasets if datasets is not None else tuple(settings.extra_datasets)
        if progress:
            progress(f"parsing {demo.path} …")
        out_dir = paths.parsed_dir(demo.match_id or 0)
        result = parse_demo(demo.path, str(out_dir), ds)
        if result.match_id != (demo.match_id or 0):
            final_dir = paths.parsed_dir(result.match_id)
            if final_dir != out_dir:
                if final_dir.exists():
                    shutil.rmtree(final_dir)
                shutil.move(str(out_dir), str(final_dir))
                result.parquet_dir = str(final_dir)
                result.match_row["parquet_dir"] = str(final_dir)
        store_result(db, demo, result)
        if progress:
            progress(f"stored match {result.match_id}")
        return result
    finally:
        if own_db:
            db.close()


def delete_positions(db: Database, match_id: int) -> None:
    m = MatchRepo(db).get(match_id)
    if m is None:
        return
    d = Path(m.parquet_dir) if m.parquet_dir else paths.parsed_dir(match_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    MatchRepo(db).set_has_ticks(match_id, False)
