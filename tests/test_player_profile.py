import polars as pl

from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import MatchRepo
from deaddemo.core.stats.extras import compute_extras, multi_kill_counts
from deaddemo.core.stats.player_profile import build_profile


def test_multi_kill_clustering():
    assert multi_kill_counts([]) == {}
    assert multi_kill_counts([0, 5, 9, 100, 104, 300]) == {3: 1, 2: 1}
    assert multi_kill_counts([0, 1, 2, 3, 4, 5, 6]) == {6: 1}, "6+ folds into multi6"


def test_compute_extras_synthetic():
    ticks = pl.DataFrame({
        "tick": [64, 64000, 64, 64000], "hero_id": [1, 1, 2, 2], "match_seconds": [1.0, 1000.0, 1.0, 1000.0],
        "hero_healing": [0, 500, 0, 0], "self_healing": [0, 50, 0, 0], "objective_damage": [0, 9000, 0, 100],
        "kill_streak": [0, 3, 0, 1], "gold_net_worth": [500, 20000, 500, 15000], "level": [1, 20, 1, 18],
    })
    damage = pl.DataFrame({
        "attacker_hero_id": [1, 1, 1, 2], "victim_hero_id": [2, 2, 2, 1], "citadel_type": [1, 1, 2, 3],
        "hitgroup_id": [1, 0, -1, -1], "damage": [50, 30, 200, 40],
    })
    kills = [
        {"tick": 640, "match_seconds": 10.0, "attacker_hero_id": 1, "victim_hero_id": 2, "assister_hero_ids": []},
        {"tick": 1000, "match_seconds": 15.6, "attacker_hero_id": 1, "victim_hero_id": 2, "assister_hero_ids": [3]},
        {"tick": 5000, "match_seconds": 78.0, "attacker_hero_id": 2, "victim_hero_id": 1, "assister_hero_ids": "[]"},
    ]
    fights = pl.DataFrame({"start_tick": [600, 4900], "end_tick": [1100, 5100], "participants": [[1, 2], [1, 2]]})
    rows = compute_extras(hero_ids=[1, 2], team_of={1: 2, 2: 3}, ticks=ticks, damage=damage, kills=kills,
                          teamfights=fights)
    h1, h2 = rows
    assert h1["bullet_hits"] == 2 and h1["headshot_hits"] == 1
    assert h1["bullet_dmg"] == 80 and h1["spirit_dmg"] == 200 and h2["melee_dmg"] == 40
    assert h1["first_blood"] == 1 and h2["first_blood"] == -1
    assert h1["multi2"] == 1 and h1["solo_kills"] == 1 and h2["solo_kills"] == 1
    assert h1["teamfights"] == 2 and h1["teamfights_won"] == 1 and h2["teamfights_won"] == 1
    assert h1["max_kill_streak"] == 3 and h1["hero_healing"] == 500
    assert h1["souls_10m"] == 0, "no sample near the 10 minute mark in this synthetic data"


def test_build_profile_aggregates():
    db = Database.open_memory()
    repo = MatchRepo(db)
    for mid, won in ((1, 1), (2, 0)):
        repo.store_parse_result(
            match_row={"match_id": mid, "regulation_seconds": 1800.0, "winning_team": 2 if won else 3},
            players=[
                {"hero_id": 7, "steam_id": 100, "player_name": "me", "team_num": 2, "start_lane": 4, "kills": 10,
                 "deaths": 5, "assists": 8, "souls": 30000, "hero_damage": 20000, "level": 30,
                 "kill_participation": 0.6, "time_dead_s": 60, "won": won},
                {"hero_id": 9, "steam_id": 200, "player_name": "mate", "team_num": 2, "won": won},
                {"hero_id": 11, "steam_id": 300, "player_name": "enemy", "team_num": 3, "won": 1 - won},
            ],
            kills=[{"tick": 100, "match_seconds": 1.5, "attacker_hero_id": 11, "victim_hero_id": 7,
                    "assister_hero_ids": []}],
            item_purchases=[{"tick": 50, "match_seconds": 1.0, "hero_id": 7, "ability_id": 555, "change": "purchased"}],
            objective_events=[],
            player_extras=[{"hero_id": 7, "headshot_hits": 4, "bullet_hits": 10, "first_blood": -1}],
        )
    p = build_profile(db, 100)
    assert p.games == 2 and p.wins == 1 and p.kills == 20
    assert abs(p.souls_per_min - 1000) < 1e-6
    assert p.headshot_pct == 0.4
    assert p.extras["first_blood_victim"] == 2
    assert p.heroes[0].hero_id == 7 and p.heroes[0].games == 2
    assert p.lanes == {"Mid": (2, 1)}
    assert p.nemesis == [(11, 2)]
    assert p.teammates[0].steam_id == 200 and p.teammates[0].wins == 1
    assert p.opponents[0].steam_id == 300
    assert p.top_items == [(555, 2)] and p.first_buys == [(555, 2)]
    assert p.items_by_hero == {7: [(555, 2)]} and p.first_buys_by_hero == {7: [(555, 2)]}
    assert len(p.recent) == 2 and p.hist_matches is None
