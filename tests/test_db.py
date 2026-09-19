from deaddemo.core.db.database import SCHEMA_VERSION, Database
from deaddemo.core.db.repos import DemoRepo, HistoryRepo, MatchRepo, TagRepo


def test_migrate_sets_version():
    db = Database.open_memory()
    assert db.schema_version() == SCHEMA_VERSION


def test_open_on_disk_is_idempotent():
    db = Database.open()
    db.close()
    db = Database.open()
    assert db.schema_version() == SCHEMA_VERSION
    db.close()


def test_demo_upsert_and_missing():
    db = Database.open_memory()
    repo = DemoRepo(db)
    row = repo.upsert_found(path="C:/x/1.dem", source="game", size_bytes=10, mtime=1.0, match_id=1,
                            build=6686, map_name="dl_streets", tick_rate=64, total_ticks=100,
                            status="found", error=None)
    assert row.id and row.status == "found"
    repo.set_status(row.id, "parsed", parser_version="1")
    again = repo.upsert_found(path="C:/x/1.dem", source="game", size_bytes=10, mtime=1.0, match_id=1,
                              build=6686, map_name="dl_streets", tick_rate=64, total_ticks=100,
                              status="found", error=None)
    assert again.status == "parsed", "unchanged parsed demo keeps its status"
    assert repo.mark_missing({"C:/other.dem"}) == 1
    assert repo.by_path("C:/x/1.dem").status == "missing"


def test_store_parse_result_replaces_children():
    db = Database.open_memory()
    repo = MatchRepo(db)
    payload = dict(
        match_row={"match_id": 5, "map_name": "dl_streets", "winning_team": 2},
        players=[{"hero_id": 1, "team_num": 2, "kills": 3}, {"hero_id": 2, "team_num": 3, "kills": 1}],
        kills=[{"tick": 10, "victim_hero_id": 2, "attacker_hero_id": 1, "assister_hero_ids": [3]}],
        item_purchases=[{"tick": 5, "hero_id": 1, "ability_id": 99, "change": "purchase"}],
        objective_events=[{"tick": 500, "objective_type": "walker", "team_num": 3, "lane": 1}],
    )
    repo.store_parse_result(**payload)
    repo.store_parse_result(**payload)  # second store must not duplicate
    assert repo.get(5).winning_team == 2
    assert len(repo.players(5)) == 2
    assert len(repo.kills(5)) == 1
    assert len(repo.item_purchases(5)) == 1
    assert len(repo.objective_events(5)) == 1


def test_history_and_tags():
    db = Database.open_memory()
    hist = HistoryRepo(db)
    n = hist.upsert_many(42, [{"match_id": 1, "hero_id": 7, "start_time": 100, "team_abandoned": False}])
    assert n == 1 and hist.for_account(42)[0].hero_id == 7
    tags = TagRepo(db)
    t = tags.create("clutch", "#ff0000")
    tags.set_match_tags(1, [t.id])
    assert [x.name for x in tags.tags_for_match(1)] == ["clutch"]
    tags.set_comment(1, "hello")
    assert tags.comment(1) == "hello"
