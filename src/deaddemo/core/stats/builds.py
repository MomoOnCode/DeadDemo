"""Reconstruct each hero's final item build from the purchase log.

Deadlock consumes component items when their upgrade is bought, so a naive "purchased minus
sold" set would show both the component and the upgrade. The catalog's ``component_items``
links resolve that.
"""

from __future__ import annotations

from dataclasses import dataclass

from deaddemo.core.assets.catalog import Catalog
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import MatchRepo

SLOT_ORDER = {"weapon": 0, "vitality": 1, "spirit": 2, "None": 3, None: 3}


@dataclass
class BuildItem:
    item_id: int
    purchased_s: float
    slot: str
    tier: int | None
    name: str


def final_builds(db: Database, match_id: int, cat: Catalog) -> dict[int, list[BuildItem]]:
    """hero_id -> items still owned at the end of the match, in purchase order."""
    rows = MatchRepo(db).item_purchases(match_id)
    owned: dict[int, list[BuildItem]] = {}
    for r in rows:
        hero = int(r["hero_id"])
        item_id = int(r["ability_id"]) if r["ability_id"] is not None else None
        if item_id is None:
            continue
        items = owned.setdefault(hero, [])
        change = r["change"]
        if change == "purchased":
            info = cat.item(item_id)
            if info is not None:
                for comp_class in info.component_items:
                    comp = cat.item_by_class(comp_class)
                    if comp is not None:
                        _remove_first(items, comp.id)
            items.append(BuildItem(
                item_id, float(r["match_seconds"] or 0), (info.item_slot_type if info else None) or "None",
                info.item_tier if info else None, info.name if info else f"Item {item_id}",
            ))
        elif change == "sold":
            _remove_first(items, item_id)
    return owned


def _remove_first(items: list[BuildItem], item_id: int) -> None:
    for i, it in enumerate(items):
        if it.item_id == item_id:
            del items[i]
            return


def grouped_by_slot(items: list[BuildItem]) -> dict[str, list[BuildItem]]:
    out: dict[str, list[BuildItem]] = {"weapon": [], "vitality": [], "spirit": []}
    for it in items:
        out.setdefault(it.slot if it.slot in out else "spirit", []).append(it)
    return out
