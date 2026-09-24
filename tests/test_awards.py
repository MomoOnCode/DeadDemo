from deaddemo.core.api.awards import award_label, parse_awards
from deaddemo.core.db.database import SCHEMA_VERSION, Database
from deaddemo.core.db.repos import AwardsRepo

# trimmed from deadlock-api /v1/matches/106561831/metadata
METADATA = {
    "match_info": {
        "match_id": 106561831,
        "winning_team": 0,
        "players": [
            {"player_slot": 6, "team": 0, "hero_id": 14, "account_id": 111, "mvp_rank": 1, "accolades": [
                {"accolade_id": 9, "accolade_stat_value": 31, "accolade_threshold_achieved": 1},
                {"accolade_id": 16, "accolade_stat_value": 4, "accolade_threshold_achieved": 0},
                {"accolade_id": 4, "accolade_stat_value": 33255, "accolade_threshold_achieved": -1}]},
            {"player_slot": 3, "team": 0, "hero_id": 7, "account_id": 222, "mvp_rank": 2, "accolades": [
                {"accolade_id": 14, "accolade_stat_value": 4, "accolade_threshold_achieved": 2}]},
            {"player_slot": 8, "team": 1, "hero_id": 50, "account_id": 333, "mvp_rank": 3, "accolades": []},
            {"player_slot": 1, "team": 0, "hero_id": 20, "account_id": 444, "accolades": []},
            {"player_slot": 9, "team": 1, "hero_id": 19, "account_id": 555},
        ],
    }
}


def test_parse_awards_keeps_ranks_and_earned_accolades():
    rows = {r.hero_id: r for r in parse_awards(106561831, METADATA)}
    assert len(rows) == 5
    assert rows[14].mvp_rank == 1 and rows[14].label == "MVP"
    assert rows[14].accolades == [{"id": 9, "value": 31, "stars": 1}]  # 0 / -1 thresholds are not earned
    assert rows[7].label == "Key Player" and rows[7].accolades[0]["stars"] == 2
    assert rows[50].label == "Key Player" and rows[50].team == 1
    assert rows[20].mvp_rank is None and rows[20].label == ""
    assert rows[19].accolades == []


def test_award_label():
    assert award_label(None) == "" and award_label(0) == ""
    assert award_label(1) == "MVP" and award_label(2) == "Key Player" and award_label(3) == "Key Player"


def test_awards_repo_round_trip():
    db = Database.open_memory()
    assert db.schema_version() == SCHEMA_VERSION >= 4
    repo = AwardsRepo(db)
    rows = parse_awards(106561831, METADATA)
    assert not repo.has(106561831)
    assert repo.upsert(106561831, rows) == 5
    assert repo.has(106561831)
    by_hero = repo.for_match(106561831)
    assert by_hero[14].label == "MVP" and by_hero[14].accolades[0]["id"] == 9 and by_hero[14].account_id == 111
    by_match = repo.for_account(222)
    assert list(by_match) == [106561831] and by_match[106561831].label == "Key Player"
    # re-upsert replaces
    rows[0].mvp_rank = None
    repo.upsert(106561831, rows[:1])
    assert repo.for_match(106561831)[14].label == "" and len(repo.for_match(106561831)) == 1
