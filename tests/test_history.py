import sqlite3

from deaddemo.core.api.service import HistoryFetch, apply_history, should_force_refetch
from deaddemo.core.db.database import SCHEMA_VERSION, Database
from deaddemo.core.db.repos import HistoryRepo, HistoryRow, MatchRepo

STEAM = 76561198314737511
ACCOUNT = 354471783


def test_should_force_refetch():
    now = 1_000_000.0
    assert should_force_refetch(100, 5000, 0.0, now)  # local demo newer than the API's newest match
    assert not should_force_refetch(5000, 5000, 0.0, now)  # nothing newer locally
    assert not should_force_refetch(100, 5000, now - 600, now)  # forced 10 minutes ago: budget spent
    assert should_force_refetch(None, 5000, 0.0, now)  # API knows nothing at all
    assert not should_force_refetch(None, None, 0.0, now)  # no local evidence


def _entry(match_id: int, **kw) -> dict:
    d = {"match_id": match_id, "hero_id": 1, "start_time": 1000 + match_id, "match_result": 0, "player_team": 0,
         "player_kills": 3, "ranked_display_badge": 55}
    d.update(kw)
    return d


def test_history_sources_merge_without_overwriting_api_rows():
    db = Database.open_memory()
    repo = HistoryRepo(db)
    assert repo.upsert_many(ACCOUNT, [_entry(1), _entry(2)], source="api") == 2
    # the GC feed lacks ranked fields: it must only add unknown matches
    assert repo.upsert_many(ACCOUNT, [_entry(2, ranked_display_badge=None), _entry(3, ranked_display_badge=None)],
                            source="gc", replace=False) == 1
    rows = {r.match_id: r for r in repo.for_account(ACCOUNT)}
    assert rows[2].source == "api" and rows[2].ranked_display_badge == 55
    assert rows[3].source == "gc" and rows[3].ranked_display_badge is None
    assert repo.match_ids(ACCOUNT) == {1, 2, 3} and repo.newest_start_time(ACCOUNT) == 1003


def test_apply_history_counts_gc_extras():
    from deaddemo.core.api.models import MatchHistoryEntry

    db = Database.open_memory()
    fetch = HistoryFetch(ACCOUNT, api_entries=[MatchHistoryEntry(ACCOUNT, 10, start_time=10)],
                         gc_entries=[_entry(10), _entry(11)])
    assert apply_history(db, fetch) == (1, 1)
    assert HistoryRepo(db).match_ids(ACCOUNT) == {10, 11}


def test_history_row_from_parsed_match():
    db = Database.open_memory()
    repo = MatchRepo(db)
    repo.store_parse_result(
        match_row={"match_id": 77, "tick_rate": 64, "game_start_tick": 0, "regulation_seconds": 1500.5,
                   "winning_team": 3, "game_mode": 1},
        players=[{"hero_id": 5, "steam_id": STEAM, "player_name": "me", "team_num": 3, "kills": 4, "deaths": 2,
                  "assists": 9, "souls": 30000, "level": 20}],
        kills=[], item_purchases=[], objective_events=[],
    )
    played = repo.played_by(STEAM)
    assert len(played) == 1
    row = HistoryRow.from_parsed(played[0][0], played[0][1], 123456)
    assert row.account_id == ACCOUNT and row.match_id == 77 and row.hero_id == 5
    assert row.player_team == 1 and row.match_result == 1 and row.source == "parsed"
    assert row.match_duration_s == 1500 and row.net_worth == 30000 and row.start_time == 123456


def test_migration_adds_source_column_to_old_history_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('schema_version', '3')")
    conn.execute("""CREATE TABLE api_match_history (account_id INTEGER NOT NULL, match_id INTEGER NOT NULL,
                    hero_id INTEGER, start_time INTEGER, match_duration_s INTEGER, game_mode INTEGER,
                    match_mode INTEGER, player_team INTEGER, match_result INTEGER, player_match_outcome INTEGER,
                    player_kills INTEGER, player_deaths INTEGER, player_assists INTEGER, denies INTEGER,
                    last_hits INTEGER, net_worth INTEGER, hero_level INTEGER, ranked_display_badge INTEGER,
                    ranked_delta INTEGER, team_abandoned INTEGER, fetched_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, match_id))""")
    conn.commit()
    db = Database(conn, None)
    db._configure()
    db.migrate()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_match_history)").fetchall()}
    assert "source" in cols and db.schema_version() == SCHEMA_VERSION
    assert HistoryRepo(db).upsert_many(ACCOUNT, [_entry(1)], source="gc", replace=False) == 1


def test_replay_salt_known_bulk_lookup():
    db = Database.open_memory()
    repo = HistoryRepo(db)
    repo.upsert_salts({"match_id": 5, "cluster_id": 1, "replay_salt": 123})
    repo.upsert_salts({"match_id": 6, "cluster_id": 1, "replay_salt": None})
    assert repo.replay_salt_known() == {5: True, 6: False}
