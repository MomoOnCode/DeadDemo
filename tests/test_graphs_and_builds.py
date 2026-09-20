import polars as pl

from deaddemo.core.api.models import Item
from deaddemo.core.assets.catalog import Catalog
from deaddemo.core.assets.tooltips import item_tooltip_html
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import MatchRepo
from deaddemo.core.stats.builds import final_builds
from deaddemo.core.stats.graphs import cumulative_events


def _catalog_with(items: list[dict]) -> Catalog:
    cat = Catalog.__new__(Catalog)
    cat._client = None
    cat._hero_names = {}
    cat._heroes = {}
    cat._map = None
    cat._api_loaded = True
    cat._ability_class = {}
    cat._ability_display = {}
    parsed = [Item.from_dict(i) for i in items]
    cat._items = {i.id: i for i in parsed}
    cat._items_by_class = {i.class_name: i for i in parsed}
    return cat


def test_final_build_consumes_components_and_sold_items():
    cat = _catalog_with([
        {"id": 1, "class_name": "upgrade_a", "name": "A", "item_slot_type": "weapon", "item_tier": 1, "cost": 500},
        {"id": 2, "class_name": "upgrade_b", "name": "B", "item_slot_type": "weapon", "item_tier": 2, "cost": 1250,
         "component_items": ["upgrade_a"]},
        {"id": 3, "class_name": "upgrade_c", "name": "C", "item_slot_type": "spirit", "item_tier": 1, "cost": 500},
    ])
    db = Database.open_memory()
    MatchRepo(db).store_parse_result(
        match_row={"match_id": 9}, players=[{"hero_id": 7, "team_num": 2}], kills=[], objective_events=[],
        item_purchases=[
            {"tick": 10, "match_seconds": 1.0, "hero_id": 7, "ability_id": 1, "change": "purchased"},
            {"tick": 20, "match_seconds": 2.0, "hero_id": 7, "ability_id": 3, "change": "purchased"},
            {"tick": 30, "match_seconds": 3.0, "hero_id": 7, "ability_id": 2, "change": "purchased"},  # eats A
            {"tick": 40, "match_seconds": 4.0, "hero_id": 7, "ability_id": 3, "change": "sold"},
        ],
    )
    builds = final_builds(db, 9, cat)
    assert [i.item_id for i in builds[7]] == [2]
    assert builds[7][0].slot == "weapon" and builds[7][0].tier == 2


def test_cumulative_events_grid_and_cumsum():
    lf = pl.DataFrame({
        "match_seconds": [1.0, 2.0, 20.0, 40.0], "damage": [5, 5, 10, 1],
        "victim": ["a", "a", "b", "a"],
    }).lazy()
    df = cumulative_events(lf, "damage", pl.col("victim"), t_end=45.0, every_s=15.0)
    a = df.filter(pl.col("group") == "a").sort("match_seconds")
    assert a["match_seconds"].to_list() == [0.0, 15.0, 30.0, 45.0]
    assert a["value"].to_list() == [10, 10, 11, 11]
    b = df.filter(pl.col("group") == "b").sort("match_seconds")
    assert b["value"].to_list() == [0, 10, 10, 10]


def test_item_tooltip_uses_game_sections():
    raw = {
        "name": "Extra Health", "item_slot_type": "vitality", "item_tier": 1, "cost": 800,
        "properties": {"BonusHealth": {"value": "115", "label": "Health", "postfix": ""},
                       "AbilityCooldown": {"value": "0", "label": "Cooldown", "postfix": "s"},
                       "BonusSprintSpeed": {"value": "2.0m", "label": "Sprint Speed", "postfix": "m"}},
        "tooltip_sections": [{"section_type": "innate", "section_attributes": [
            {"properties": ["AbilityCooldown", "BonusSprintSpeed"], "elevated_properties": ["BonusHealth"]}]}],
        "description": {"desc": "Gives <span class=\"highlight\">health</span>."},
    }
    html = item_tooltip_html(raw, "3:20")
    assert "Extra Health" in html and "Tier 1" in html and "800 souls" in html and "bought 3:20" in html
    assert "<b>115</b> Health" in html
    assert "<b>2.0m</b> Sprint Speed" in html, "unit already in the value is not doubled"
    assert "Cooldown" not in html, "zero-valued properties are hidden like in the game"
