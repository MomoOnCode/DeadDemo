"""Hero / item / map static data: boon's built-in names first, deadlock-api assets for the rest."""

from __future__ import annotations

from pathlib import Path

from deaddemo.core.api.client import ApiError, DeadlockApiClient, default_client
from deaddemo.core.api.models import Hero, Item, MapInfo


class Catalog:
    def __init__(self, client: DeadlockApiClient | None = None):
        self._client = client
        self._hero_names: dict[int, str] = {}
        self._heroes: dict[int, Hero] = {}
        self._items: dict[int, Item] = {}
        self._items_by_class: dict[str, Item] = {}
        self._map: MapInfo | None = None
        self._api_loaded = False
        self._ability_class: dict[int, str] = {}
        self._ability_display: dict[str, str] = {}
        try:
            import boon

            self._hero_names = {int(k): str(v) for k, v in boon.hero_names().items()}
            self._ability_class = {int(k): str(v) for k, v in boon.ability_names().items()}
            self._ability_display = {str(k): str(v) for k, v in boon.ability_display_names().items()}
        except Exception:  # noqa: BLE001 - boon missing or API changed; API fallback still works
            pass

    @property
    def client(self) -> DeadlockApiClient:
        if self._client is None:
            self._client = default_client()
        return self._client

    # -- loading -------------------------------------------------------------------
    def load_api(self) -> bool:
        """Fetch hero/item/map data from the API (cached on disk). Safe to call from a worker."""
        if self._api_loaded:
            return True
        try:
            heroes = self.client.heroes()
            items = self.client.items()
            map_info = self.client.map_info()
        except ApiError:
            return False
        self._heroes = {h.id: h for h in heroes}
        self._items = {i.id: i for i in items}
        self._items_by_class = {i.class_name: i for i in items if i.class_name}
        self._map = map_info
        for h in heroes:
            self._hero_names.setdefault(h.id, h.name)
        self._api_loaded = True
        return True

    @property
    def api_loaded(self) -> bool:
        return self._api_loaded

    # -- lookups -------------------------------------------------------------------
    def hero_name(self, hero_id: int | None) -> str:
        if hero_id is None:
            return ""
        return self._hero_names.get(int(hero_id), f"Hero {hero_id}")

    def hero(self, hero_id: int) -> Hero | None:
        return self._heroes.get(hero_id)

    def item(self, item_id: int | None) -> Item | None:
        return self._items.get(int(item_id)) if item_id is not None else None

    def item_by_class(self, class_name: str) -> Item | None:
        return self._items_by_class.get(class_name)

    def item_name(self, item_id: int | None) -> str:
        if item_id is None:
            return ""
        it = self._items.get(int(item_id))
        if it:
            return it.name
        return self.ability_name(item_id)

    def ability_name(self, ability_id: int | None) -> str:
        """Human name for an ability / item / weapon id from a damage or healing event."""
        if ability_id is None:
            return ""
        aid = int(ability_id)
        if aid == 0:
            return "Unattributed"
        it = self._items.get(aid)
        cls = self._ability_class.get(aid) or (it.class_name if it else "")
        if cls in self._ability_display:
            return self._ability_display[cls]
        if it and it.name and it.name != it.class_name:
            return it.name
        if cls.startswith("citadel_weapon_"):
            return "Weapon"
        if cls.startswith("citadel_ability_melee") or cls.startswith("ability_melee"):
            return "Melee"
        if cls:
            return cls.removeprefix("citadel_ability_").removeprefix("ability_").removeprefix("upgrade_") \
                .replace("_", " ").title()
        return f"Ability {aid}"

    def map_info(self) -> MapInfo | None:
        return self._map

    def hero_image(self, hero_id: int, kind: str = "icon_image_small") -> Path | None:
        h = self._heroes.get(hero_id)
        if not h:
            return None
        url = h.images.get(kind)
        if not url:
            return None
        try:
            return self.client.fetch_image(url)
        except ApiError:
            return None

    def item_image(self, item_id: int) -> Path | None:
        it = self._items.get(item_id)
        if not it or not it.icon_url:
            return None
        try:
            return self.client.fetch_image(it.icon_url)
        except ApiError:
            return None

    def minimap_image(self, kind: str = "minimap") -> Path | None:
        if not self._map:
            return None
        url = self._map.images.get(kind)
        if not url:
            return None
        try:
            return self.client.fetch_image(url)
        except ApiError:
            return None


_catalog: Catalog | None = None


def catalog() -> Catalog:
    global _catalog
    if _catalog is None:
        _catalog = Catalog()
    return _catalog
