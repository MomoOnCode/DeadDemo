"""Higher-level API operations that also persist into the local database.

Replay salt resolution order:
1. Local database row (fresh, or already holding a replay salt).
2. deadlock-api.com ``/v1/matches/{id}/salts``.
3. Steam Game Coordinator via the ``deaddemo-gc`` helper, when the user has logged in.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from deaddemo.core.api.client import DeadlockApiClient, ReplayUnavailable, default_client
from deaddemo.core.api.models import MatchHistoryEntry, MatchSalts
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import HistoryRepo, SaltsRow
from deaddemo.core.gc import provider as gc

log = logging.getLogger("deaddemo.history")

SALTS_RETRY_S = 60 * 60
FORCE_REFETCH_MIN_INTERVAL_S = 60 * 60  # deadlock-api allows one Valve refetch per hour per player


def should_force_refetch(newest_history_ts: int | None, newest_local_ts: int | None, last_force_ts: float,
                         now: float, min_interval_s: float = FORCE_REFETCH_MIN_INTERVAL_S) -> bool:
    """Ask deadlock-api to re-pull from Valve when we hold local evidence (a demo or parsed match of ours)
    newer than anything its history knows, and the hourly budget allows it."""
    if now - last_force_ts < min_interval_s:
        return False
    if newest_local_ts is None:
        return False
    return newest_history_ts is None or newest_local_ts > newest_history_ts + 60


@dataclass
class HistoryFetch:
    account_id: int
    api_entries: list[MatchHistoryEntry] = field(default_factory=list)
    gc_entries: list[dict] = field(default_factory=list)
    forced: bool = False
    notes: list[str] = field(default_factory=list)


def fetch_history_sources(account_id: int, *, client: DeadlockApiClient | None = None, force_api: bool = False,
                          use_gc: bool = True, gc_pages: int = 3,
                          progress: Callable[[int, int, str], None] | None = None) -> HistoryFetch:
    """Network half of a history refresh (worker-safe): deadlock-api, then the Steam GC when available.
    Persist the result with ``apply_history`` on the thread that owns the database."""
    client = client or default_client()
    out = HistoryFetch(account_id, forced=force_api)
    if progress:
        progress(0, 0, "Fetching match history from deadlock-api")
    out.api_entries = client.match_history(account_id, force_refetch=force_api)
    out.api_entries.sort(key=lambda e: e.start_time or 0, reverse=True)
    log.info("deadlock-api history for %s: %d entries (force=%s)", account_id, len(out.api_entries), force_api)
    if use_gc and gc.is_logged_in() and gc.find_helper() is not None:
        if gc.game_running():
            out.notes.append("Steam GC skipped: Deadlock is running")
        else:
            if progress:
                progress(0, 0, "Asking the Steam Game Coordinator for your match history")
            try:
                out.gc_entries = gc.fetch_history(account_id, max_pages=gc_pages)
                log.info("GC history for %s: %d entries", account_id, len(out.gc_entries))
            except gc.GcError as exc:
                out.notes.append(f"Steam GC: {exc}")
                log.warning("GC history for %s failed: %s", account_id, exc)
    return out


def apply_history(db: Database, fetch: HistoryFetch) -> tuple[int, int]:
    """Store a ``HistoryFetch``. Returns (api rows written, GC rows added that the API did not have)."""
    repo = HistoryRepo(db)
    n_api = repo.upsert_many(fetch.account_id, [e.to_dict() for e in fetch.api_entries], source="api")
    api_ids = {e.match_id for e in fetch.api_entries}
    extra = [e for e in fetch.gc_entries if int(e.get("match_id", 0)) not in api_ids]
    n_gc = repo.upsert_many(fetch.account_id, extra, source="gc", replace=False) if extra else 0
    if n_gc:
        log.info("GC added %d match(es) deadlock-api does not list: %s", n_gc,
                 ", ".join(str(e["match_id"]) for e in extra[:10]))
    return n_api, n_gc


def resolve_account_id(account_id: int | None = None) -> int:
    if account_id:
        return account_id
    from deaddemo.core.steam import locator
    from deaddemo.settings import Settings

    install = locator.detect(Settings.load())
    if not install.account:
        raise RuntimeError("No Steam account detected; set an account id in Settings")
    return install.account.account_id


def refresh_history(
    account_id: int | None = None,
    *,
    force_refetch: bool = False,
    db: Database | None = None,
    client: DeadlockApiClient | None = None,
    use_gc: bool = True,
) -> tuple[list[MatchHistoryEntry], int]:
    account_id = resolve_account_id(account_id)
    fetch = fetch_history_sources(account_id, client=client, force_api=force_refetch, use_gc=use_gc)
    own_db = db is None
    db = db or Database.open()
    try:
        apply_history(db, fetch)
    finally:
        if own_db:
            db.close()
    for note in fetch.notes:
        log.info("history note: %s", note)
    return fetch.api_entries, account_id


def _row_is_fresh(row: SaltsRow) -> bool:
    if row.replay_salt:
        return True
    try:
        fetched = datetime.fromisoformat(row.fetched_at).replace(tzinfo=UTC).timestamp()
    except ValueError:
        return False
    return time.time() - fetched < SALTS_RETRY_S


def _row_to_salts(row: SaltsRow) -> MatchSalts:
    return MatchSalts(row.match_id, row.cluster_id, row.metadata_salt, row.replay_salt, row.metadata_url,
                      row.demo_url)


def resolve_salts(match_id: int, *, db: Database, client: DeadlockApiClient | None = None,
                  force: bool = False) -> MatchSalts:
    """deadlock-api salts for a match, using the DB copy when it is usable."""
    repo = HistoryRepo(db)
    row = repo.salts(match_id)
    if row is not None and not force and _row_is_fresh(row):
        return _row_to_salts(row)
    client = client or default_client()
    if force:
        client.cache.invalidate(f"/v1/matches/{match_id}/salts")
    salts = client.match_salts(match_id)
    if row is not None and row.replay_salt and not salts.replay_salt:
        return _row_to_salts(row)  # never downgrade a salt we already know (e.g. from the GC)
    repo.upsert_salts(salts.to_dict())
    return salts


def resolve_salts_gc(match_ids: list[int], *, db: Database) -> dict[int, MatchSalts]:
    """Ask the Steam GC for salts (requires login). Stores what it learns. Raises GcError."""
    repo = HistoryRepo(db)
    out: dict[int, MatchSalts] = {}
    for r in gc.fetch_salts(match_ids):
        salts = MatchSalts(r.match_id, r.cluster_id, r.metadata_salt, r.replay_salt, r.metadata_url(),
                           r.demo_url())
        if r.ok or r.metadata_salt:
            repo.upsert_salts(salts.to_dict())
        out[r.match_id] = salts
    return out


def resolve_salts_any(
    match_ids: list[int], *, db: Database, client: DeadlockApiClient | None = None,
    use_gc: bool = True, progress: Callable[[int, int, str], None] | None = None,
) -> tuple[dict[int, MatchSalts], dict[int, str]]:
    """Resolve replay salts for many matches. Returns (found, reasons_for_missing)."""
    found: dict[int, MatchSalts] = {}
    missing: dict[int, str] = {}
    for i, mid in enumerate(match_ids):
        if progress:
            progress(i, len(match_ids), f"Resolving replay {mid}")
        try:
            s = resolve_salts(mid, db=db, client=client)
            if not s.demo_url:
                s = resolve_salts(mid, db=db, client=client, force=True)
        except Exception as exc:  # noqa: BLE001
            missing[mid] = f"deadlock-api: {exc}"
            continue
        if s.demo_url:
            found[mid] = s
        else:
            missing[mid] = "not on deadlock-api"
    if use_gc and missing and gc.is_logged_in():
        todo = list(missing)
        if progress:
            progress(len(match_ids), len(match_ids), f"Asking Steam for {len(todo)} replay salt(s)")
        try:
            got = resolve_salts_gc(todo, db=db)
        except gc.GcError as exc:
            for mid in todo:
                missing[mid] = f"{missing[mid]}; Steam GC: {exc}"
        else:
            for mid in todo:
                s = got.get(mid)
                if s and s.demo_url:
                    found[mid] = s
                    missing.pop(mid, None)
                elif s:
                    missing[mid] = f"Steam GC: {gc_result_text(s)}"
                else:
                    missing[mid] = "Steam GC: no answer (daily quota?)"
    return found, missing


def gc_result_text(s: MatchSalts) -> str:
    return "no replay salt (expired or not recorded)" if not s.replay_salt else "ok"


def demo_url_for(match_id: int, *, db: Database, client: DeadlockApiClient | None = None,
                 use_gc: bool = True) -> str:
    found, missing = resolve_salts_any([match_id], db=db, client=client, use_gc=use_gc)
    if match_id in found:
        return found[match_id].demo_url or ""
    raise ReplayUnavailable(f"No replay available for match {match_id} ({missing.get(match_id, 'unknown')})")
