"""Post-game awards (MVP, Key Player, accolades) from deadlock-api's match metadata.

The metadata is Valve's ``CMsgMatchMetaDataContents``: every player carries ``mvp_rank`` (1 = MVP,
2 = the winning side's Key Player, 3 = the losing side's Key Player, absent otherwise) and a list of
accolades with ``accolade_threshold_achieved > 0`` for the tiles the post-game screen shows.
"""

from __future__ import annotations

from typing import Any

from deaddemo.core.api.client import ApiError, DeadlockApiClient, default_client
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import AwardRow, AwardsRepo, award_label

__all__ = ["award_label", "fetch_match_awards", "parse_awards", "store_awards"]


def parse_awards(match_id: int, metadata: dict[str, Any]) -> list[AwardRow]:
    info = metadata.get("match_info") or metadata
    out: list[AwardRow] = []
    for p in info.get("players") or []:
        hero_id = p.get("hero_id")
        if hero_id is None:
            continue
        earned = [
            {"id": int(a.get("accolade_id") or 0), "value": a.get("accolade_stat_value"),
             "stars": int(a.get("accolade_threshold_achieved") or 0)}
            for a in (p.get("accolades") or []) if int(a.get("accolade_threshold_achieved") or 0) > 0
        ]
        earned.sort(key=lambda a: (-a["stars"], a["id"]))
        out.append(AwardRow(match_id, int(hero_id), p.get("account_id"), p.get("team"), p.get("player_slot"),
                            p.get("mvp_rank"), earned))
    return out


def fetch_match_awards(match_id: int, client: DeadlockApiClient | None = None) -> list[AwardRow] | None:
    """None when the API has no metadata for the match (yet); raises nothing but network-level errors."""
    client = client or default_client()
    try:
        data = client.match_metadata(match_id)
    except ApiError:
        return None
    if not isinstance(data, dict) or "match_info" not in data:
        return None
    return parse_awards(match_id, data)


def store_awards(db: Database, match_id: int, client: DeadlockApiClient | None = None) -> list[AwardRow] | None:
    rows = fetch_match_awards(match_id, client)
    if rows:
        AwardsRepo(db).upsert(match_id, rows)
    return rows
