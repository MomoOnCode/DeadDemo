"""Higher-level API operations that also persist into the local database."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from deaddemo.core.api.client import DeadlockApiClient, ReplayUnavailable, default_client
from deaddemo.core.api.models import MatchHistoryEntry, MatchSalts
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import HistoryRepo, SaltsRow

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


def resolve_salts(match_id: int, *, db: Database, client: DeadlockApiClient | None = None,
                  force: bool = False) -> MatchSalts:
    """Return the replay salts for a match, using the DB copy when it is usable."""
    repo = HistoryRepo(db)
    row = repo.salts(match_id)
    if row is not None and not force and _row_is_fresh(row):
        return MatchSalts(match_id, row.cluster_id, row.metadata_salt, row.replay_salt, row.metadata_url,
                          row.demo_url)
    client = client or default_client()
    if force:
        client.cache.invalidate(f"/v1/matches/{match_id}/salts")
    salts = client.match_salts(match_id)
    repo.upsert_salts(salts.to_dict())
    return salts


def demo_url_for(match_id: int, *, db: Database, client: DeadlockApiClient | None = None) -> str:
    salts = resolve_salts(match_id, db=db, client=client)
    if not salts.demo_url:
        salts = resolve_salts(match_id, db=db, client=client, force=True)
    if not salts.demo_url:
        raise ReplayUnavailable(
            f"deadlock-api.com has no replay salt for match {match_id} yet (replay may be expired, "
            "not ingested, or not public)"
        )
    return salts.demo_url
