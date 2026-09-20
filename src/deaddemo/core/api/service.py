"""Higher-level API operations that also persist into the local database.

Replay salt resolution order:
1. Local database row (fresh, or already holding a replay salt).
2. deadlock-api.com ``/v1/matches/{id}/salts``.
3. Steam Game Coordinator via the ``deaddemo-gc`` helper, when the user has logged in.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from deaddemo.core.api.client import DeadlockApiClient, ReplayUnavailable, default_client
from deaddemo.core.api.models import MatchHistoryEntry, MatchSalts
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import HistoryRepo, SaltsRow
from deaddemo.core.gc import provider as gc

SALTS_RETRY_S = 60 * 60


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
) -> tuple[list[MatchHistoryEntry], int]:
    account_id = resolve_account_id(account_id)
    client = client or default_client()
    entries = client.match_history(account_id, force_refetch=force_refetch)
    entries.sort(key=lambda e: e.start_time or 0, reverse=True)
    own_db = db is None
    db = db or Database.open()
    try:
        HistoryRepo(db).upsert_many(account_id, [e.to_dict() for e in entries])
    finally:
        if own_db:
            db.close()
    return entries, account_id


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
