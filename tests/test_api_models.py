from deaddemo.core.api.models import MatchHistoryEntry, MatchSalts


def test_history_entry_ignores_unknown_keys_and_computes_win():
    e = MatchHistoryEntry.from_dict({
        "account_id": 1, "match_id": 2, "player_team": 0, "match_result": 0, "brand_new_field": 42,
        "player_kills": 3, "player_deaths": 1, "player_assists": 7,
    })
    assert e.won is True and e.kda() == "3/1/7"
    loss = MatchHistoryEntry.from_dict({"account_id": 1, "match_id": 2, "player_team": 1, "match_result": 0})
    assert loss.won is False


def test_salts_availability():
    assert not MatchSalts.from_dict({"match_id": 1, "demo_url": None}).available
    assert MatchSalts.from_dict({"match_id": 1, "demo_url": "http://replay1.valve.net/x.dem.bz2"}).available
