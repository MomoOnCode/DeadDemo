"""Typed repositories over the SQLite catalog.

Each repo wraps a ``Database`` and returns plain dataclasses. Writes are wrapped in a
transaction per call so callers on the main thread can compose them freely.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, fields
from typing import Any, TypeVar

from deaddemo.core.db.database import Database, utcnow_iso

T = TypeVar("T")


def _row_to(cls: type[T], row: sqlite3.Row) -> T:
    names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: row[k] for k in row.keys() if k in names})  # type: ignore[call-arg]


# --------------------------------------------------------------------------- demos


@dataclass
class DemoRow:
    id: int
    path: str
    source: str
    size_bytes: int
    mtime: float
    match_id: int | None
    build: int | None
    map_name: str | None
    tick_rate: int | None
    total_ticks: int | None
    status: str
    parser_version: str | None
    parsed_at: str | None
    error: str | None
    first_seen: str
    last_seen: str


class DemoRepo:
    def __init__(self, db: Database):
        self.db = db

    def all(self) -> list[DemoRow]:
        rows = self.db.conn.execute("SELECT * FROM demos ORDER BY mtime DESC").fetchall()
        return [_row_to(DemoRow, r) for r in rows]

    def by_path(self, path: str) -> DemoRow | None:
        row = self.db.conn.execute("SELECT * FROM demos WHERE path=?", (path,)).fetchone()
        return _row_to(DemoRow, row) if row else None

    def by_id(self, demo_id: int) -> DemoRow | None:
        row = self.db.conn.execute("SELECT * FROM demos WHERE id=?", (demo_id,)).fetchone()
        return _row_to(DemoRow, row) if row else None

    def by_match(self, match_id: int) -> list[DemoRow]:
        rows = self.db.conn.execute(
            "SELECT * FROM demos WHERE match_id=? AND status != 'missing' ORDER BY mtime DESC", (match_id,)
        ).fetchall()
        return [_row_to(DemoRow, r) for r in rows]

    def upsert_found(
        self,
        *,
        path: str,
        source: str,
        size_bytes: int,
        mtime: float,
        match_id: int | None,
        build: int | None,
        map_name: str | None,
        tick_rate: int | None,
        total_ticks: int | None,
        status: str,
        error: str | None,
    ) -> DemoRow:
        now = utcnow_iso()
        existing = self.by_path(path)
        with self.db.transaction() as conn:
            if existing is None:
                conn.execute(
                    """INSERT INTO demos(path, source, size_bytes, mtime, match_id, build, map_name, tick_rate,
                       total_ticks, status, error, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (path, source, size_bytes, mtime, match_id, build, map_name, tick_rate, total_ticks,
                     status, error, now, now),
                )
            else:
                # Keep 'parsed' status when the file is unchanged and already parsed.
                keep_parsed = existing.status == "parsed" and status == "found"
                conn.execute(
                    """UPDATE demos SET source=?, size_bytes=?, mtime=?, match_id=?, build=?, map_name=?,
                       tick_rate=?, total_ticks=?, status=?, error=?, last_seen=? WHERE path=?""",
                    (source, size_bytes, mtime, match_id, build, map_name, tick_rate, total_ticks,
                     "parsed" if keep_parsed else status, error, now, path),
                )
        result = self.by_path(path)
        assert result is not None
        return result

    def touch(self, path: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE demos SET last_seen=? WHERE path=?", (utcnow_iso(), path))

    def mark_missing(self, present_paths: set[str]) -> int:
        rows = self.db.conn.execute("SELECT path FROM demos WHERE status != 'missing'").fetchall()
        gone = [r["path"] for r in rows if r["path"] not in present_paths]
        if gone:
            with self.db.transaction() as conn:
                conn.executemany("UPDATE demos SET status='missing' WHERE path=?", [(p,) for p in gone])
        return len(gone)

    def set_status(self, demo_id: int, status: str, *, error: str | None = None,
                   parser_version: str | None = None) -> None:
        with self.db.transaction() as conn:
            if status == "parsed":
                conn.execute(
                    "UPDATE demos SET status=?, error=NULL, parser_version=?, parsed_at=? WHERE id=?",
                    (status, parser_version, utcnow_iso(), demo_id),
                )
            else:
                conn.execute("UPDATE demos SET status=?, error=? WHERE id=?", (status, error, demo_id))

    def delete(self, demo_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM demos WHERE id=?", (demo_id,))


# --------------------------------------------------------------------------- matches


@dataclass
class MatchRow:
    match_id: int
    demo_id: int | None
    map_name: str | None
    build: int | None
    game_mode: int | None
    tick_rate: int | None
    total_ticks: int | None
    game_start_tick: int | None
    game_over_tick: int | None
    regulation_seconds: float | None
    pregame_seconds: float | None
    winning_team: int | None
    parser_version: str | None
    boon_version: str | None
    parsed_at: str | None
    parquet_dir: str | None
    has_ticks: int


@dataclass
class MatchPlayerRow:
    match_id: int
    hero_id: int
    steam_id: int | None
    player_name: str | None
    team_num: int | None
    start_lane: int | None
    rank: int | None
    kills: int | None
    deaths: int | None
    assists: int | None
    last_hits: int | None
    denies: int | None
    souls: int | None
    hero_damage: int | None
    level: int | None
    kill_participation: float | None
    time_dead_s: float | None
    won: int | None


class MatchRepo:
    def __init__(self, db: Database):
        self.db = db

    def all(self) -> list[MatchRow]:
        rows = self.db.conn.execute("SELECT * FROM matches ORDER BY parsed_at DESC").fetchall()
        return [_row_to(MatchRow, r) for r in rows]

    def get(self, match_id: int) -> MatchRow | None:
        row = self.db.conn.execute("SELECT * FROM matches WHERE match_id=?", (match_id,)).fetchone()
        return _row_to(MatchRow, row) if row else None

    def players(self, match_id: int) -> list[MatchPlayerRow]:
        rows = self.db.conn.execute(
            "SELECT * FROM match_players WHERE match_id=? ORDER BY team_num, souls DESC", (match_id,)
        ).fetchall()
        return [_row_to(MatchPlayerRow, r) for r in rows]

    def store_parse_result(
        self,
        *,
        match_row: dict[str, Any],
        players: list[dict[str, Any]],
        kills: list[dict[str, Any]],
        item_purchases: list[dict[str, Any]],
        objective_events: list[dict[str, Any]],
    ) -> None:
        match_id = int(match_row["match_id"])
        cols = [f.name for f in fields(MatchRow)]
        values = {c: match_row.get(c) for c in cols}
        values["parsed_at"] = utcnow_iso()
        values["has_ticks"] = int(bool(values.get("has_ticks")))
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM matches WHERE match_id=?", (match_id,))  # cascades children
            conn.execute(
                f"INSERT INTO matches({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                [values[c] for c in cols],
            )
            pcols = [f.name for f in fields(MatchPlayerRow)]
            conn.executemany(
                f"INSERT INTO match_players({','.join(pcols)}) VALUES ({','.join('?' for _ in pcols)})",
                [[{**p, "match_id": match_id}.get(c) for c in pcols] for p in players],
            )
            conn.executemany(
                "INSERT INTO kills(match_id, tick, match_seconds, victim_hero_id, attacker_hero_id, assister_hero_ids)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (match_id, k["tick"], k.get("match_seconds"), k.get("victim_hero_id"),
                     k.get("attacker_hero_id"), json.dumps(k.get("assister_hero_ids") or []))
                    for k in kills
                ],
            )
            conn.executemany(
                "INSERT INTO item_purchases(match_id, tick, match_seconds, hero_id, ability_id, change)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (match_id, i["tick"], i.get("match_seconds"), i.get("hero_id"), i.get("ability_id"),
                     i.get("change"))
                    for i in item_purchases
                ],
            )
            conn.executemany(
                "INSERT INTO objective_events(match_id, tick, match_seconds, objective_type, team_num, lane)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (match_id, o["tick"], o.get("match_seconds"), o.get("objective_type"), o.get("team_num"),
                     o.get("lane"))
                    for o in objective_events
                ],
            )

    def set_has_ticks(self, match_id: int, has_ticks: bool) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE matches SET has_ticks=? WHERE match_id=?", (int(has_ticks), match_id))

    def delete(self, match_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM matches WHERE match_id=?", (match_id,))

    def kills(self, match_id: int) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT * FROM kills WHERE match_id=? ORDER BY tick", (match_id,)
        ).fetchall()

    def item_purchases(self, match_id: int, hero_id: int | None = None) -> list[sqlite3.Row]:
        if hero_id is None:
            return self.db.conn.execute(
                "SELECT * FROM item_purchases WHERE match_id=? ORDER BY tick", (match_id,)
            ).fetchall()
        return self.db.conn.execute(
            "SELECT * FROM item_purchases WHERE match_id=? AND hero_id=? ORDER BY tick", (match_id, hero_id)
        ).fetchall()

    def objective_events(self, match_id: int) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT * FROM objective_events WHERE match_id=? ORDER BY tick", (match_id,)
        ).fetchall()


# --------------------------------------------------------------------------- api history / salts


@dataclass
class HistoryRow:
    account_id: int
    match_id: int
    hero_id: int | None
    start_time: int | None
    match_duration_s: int | None
    game_mode: int | None
    match_mode: int | None
    player_team: int | None
    match_result: int | None
    player_match_outcome: int | None
    player_kills: int | None
    player_deaths: int | None
    player_assists: int | None
    denies: int | None
    last_hits: int | None
    net_worth: int | None
    hero_level: int | None
    ranked_display_badge: int | None
    ranked_delta: int | None
    team_abandoned: int | None
    fetched_at: str


@dataclass
class SaltsRow:
    match_id: int
    cluster_id: int | None
    metadata_salt: int | None
    replay_salt: int | None
    demo_url: str | None
    metadata_url: str | None
    fetched_at: str


class HistoryRepo:
    def __init__(self, db: Database):
        self.db = db

    def for_account(self, account_id: int) -> list[HistoryRow]:
        rows = self.db.conn.execute(
            "SELECT * FROM api_match_history WHERE account_id=? ORDER BY start_time DESC", (account_id,)
        ).fetchall()
        return [_row_to(HistoryRow, r) for r in rows]

    def upsert_many(self, account_id: int, entries: list[dict[str, Any]]) -> int:
        cols = [f.name for f in fields(HistoryRow)]
        now = utcnow_iso()
        params = []
        for e in entries:
            d = {c: e.get(c) for c in cols}
            d["account_id"] = account_id
            d["fetched_at"] = now
            if d.get("team_abandoned") is not None:
                d["team_abandoned"] = int(bool(d["team_abandoned"]))
            params.append([d[c] for c in cols])
        with self.db.transaction() as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO api_match_history({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                params,
            )
        return len(params)

    def salts(self, match_id: int) -> SaltsRow | None:
        row = self.db.conn.execute("SELECT * FROM api_match_salts WHERE match_id=?", (match_id,)).fetchone()
        return _row_to(SaltsRow, row) if row else None

    def upsert_salts(self, s: dict[str, Any]) -> SaltsRow:
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO api_match_salts(match_id, cluster_id, metadata_salt, replay_salt,
                   demo_url, metadata_url, fetched_at) VALUES (?,?,?,?,?,?,?)""",
                (s["match_id"], s.get("cluster_id"), s.get("metadata_salt"), s.get("replay_salt"),
                 s.get("demo_url"), s.get("metadata_url"), utcnow_iso()),
            )
        result = self.salts(int(s["match_id"]))
        assert result is not None
        return result


# --------------------------------------------------------------------------- downloads


@dataclass
class DownloadRow:
    id: int
    match_id: int
    url: str
    dest_path: str
    status: str
    bytes_total: int | None
    bytes_done: int
    started_at: str | None
    finished_at: str | None
    error: str | None


class DownloadRepo:
    def __init__(self, db: Database):
        self.db = db

    def all(self) -> list[DownloadRow]:
        rows = self.db.conn.execute("SELECT * FROM downloads ORDER BY id DESC").fetchall()
        return [_row_to(DownloadRow, r) for r in rows]

    def get(self, download_id: int) -> DownloadRow | None:
        row = self.db.conn.execute("SELECT * FROM downloads WHERE id=?", (download_id,)).fetchone()
        return _row_to(DownloadRow, row) if row else None

    def create(self, match_id: int, url: str, dest_path: str) -> DownloadRow:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO downloads(match_id, url, dest_path, status) VALUES (?,?,?,'queued')",
                (match_id, url, dest_path),
            )
            new_id = cur.lastrowid
        result = self.get(int(new_id))
        assert result is not None
        return result

    def update_progress(self, download_id: int, bytes_done: int, bytes_total: int | None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE downloads SET bytes_done=?, bytes_total=? WHERE id=?", (bytes_done, bytes_total, download_id)
            )

    def set_status(self, download_id: int, status: str, *, error: str | None = None) -> None:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            if status == "running":
                conn.execute("UPDATE downloads SET status=?, started_at=?, error=NULL WHERE id=?",
                             (status, now, download_id))
            elif status in ("done", "failed", "cancelled"):
                conn.execute("UPDATE downloads SET status=?, finished_at=?, error=? WHERE id=?",
                             (status, now, error, download_id))
            else:
                conn.execute("UPDATE downloads SET status=?, error=? WHERE id=?", (status, error, download_id))

    def active_match_ids(self) -> set[int]:
        rows = self.db.conn.execute(
            "SELECT match_id FROM downloads WHERE status IN ('queued','running')"
        ).fetchall()
        return {int(r["match_id"]) for r in rows}


# --------------------------------------------------------------------------- tags / annotations


@dataclass
class TagRow:
    id: int
    name: str
    color: str | None


class TagRepo:
    def __init__(self, db: Database):
        self.db = db

    def all(self) -> list[TagRow]:
        rows = self.db.conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        return [_row_to(TagRow, r) for r in rows]

    def create(self, name: str, color: str | None = None) -> TagRow:
        with self.db.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tags(name, color) VALUES (?,?)", (name, color))
        row = self.db.conn.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
        return _row_to(TagRow, row)

    def delete(self, tag_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))

    def tags_for_match(self, match_id: int) -> list[TagRow]:
        rows = self.db.conn.execute(
            "SELECT t.* FROM tags t JOIN match_tags mt ON mt.tag_id=t.id WHERE mt.match_id=? ORDER BY t.name",
            (match_id,),
        ).fetchall()
        return [_row_to(TagRow, r) for r in rows]

    def tags_by_match(self) -> dict[int, list[str]]:
        rows = self.db.conn.execute(
            "SELECT mt.match_id, t.name FROM match_tags mt JOIN tags t ON t.id=mt.tag_id ORDER BY t.name"
        ).fetchall()
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(int(r["match_id"]), []).append(r["name"])
        return out

    def set_match_tags(self, match_id: int, tag_ids: list[int]) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM match_tags WHERE match_id=?", (match_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO match_tags(match_id, tag_id) VALUES (?,?)", [(match_id, t) for t in tag_ids]
            )

    def comment(self, match_id: int) -> str:
        row = self.db.conn.execute("SELECT comment FROM annotations WHERE match_id=?", (match_id,)).fetchone()
        return (row["comment"] or "") if row else ""

    def set_comment(self, match_id: int, comment: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO annotations(match_id, comment, updated_at) VALUES (?,?,?)",
                (match_id, comment, utcnow_iso()),
            )


# --------------------------------------------------------------------------- calibrations


@dataclass
class CalibrationRow:
    map_name: str
    image_path: str | None
    matrix_json: str
    landmarks_json: str | None
    updated_at: str


class CalibrationRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, map_name: str) -> CalibrationRow | None:
        row = self.db.conn.execute("SELECT * FROM calibrations WHERE map_name=?", (map_name,)).fetchone()
        return _row_to(CalibrationRow, row) if row else None

    def upsert(self, map_name: str, image_path: str | None, matrix_json: str, landmarks_json: str | None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO calibrations(map_name, image_path, matrix_json, landmarks_json, updated_at)
                   VALUES (?,?,?,?,?)""",
                (map_name, image_path, matrix_json, landmarks_json, utcnow_iso()),
            )

    def delete(self, map_name: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM calibrations WHERE map_name=?", (map_name,))
