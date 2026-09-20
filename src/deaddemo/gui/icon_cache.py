"""Download-once, in-memory QPixmap cache for hero and item icons."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from deaddemo.core.assets.catalog import catalog
from deaddemo.gui.context import AppContext


class IconCache:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._items: dict[int, QPixmap] = {}
        self._heroes: dict[int, QPixmap] = {}

    def item(self, item_id: int, size: int = 44) -> QPixmap | None:
        pm = self._items.get(item_id)
        return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation) if pm else None

    def hero(self, hero_id: int, size: int = 40) -> QPixmap | None:
        pm = self._heroes.get(hero_id)
        return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation) if pm else None

    def ensure(self, item_ids: set[int], hero_ids: set[int], on_done: Callable[[], None]) -> None:
        """Fetch any missing icons on a worker thread, then call ``on_done`` on the main thread."""
        missing_items = [i for i in item_ids if i not in self._items]
        missing_heroes = [h for h in hero_ids if h not in self._heroes]
        if not missing_items and not missing_heroes:
            on_done()
            return
        cat = catalog()

        def work(progress, cancel):
            cat.load_api()
            paths_items, paths_heroes = {}, {}
            total = len(missing_items) + len(missing_heroes)
            for n, i in enumerate(missing_items):
                if cancel.is_set():
                    break
                progress(n, total, "Fetching item icons")
                p = cat.item_image(i)
                if p:
                    paths_items[i] = str(p)
            for n, h in enumerate(missing_heroes):
                if cancel.is_set():
                    break
                progress(len(missing_items) + n, total, "Fetching hero icons")
                p = cat.hero_image(h)
                if p:
                    paths_heroes[h] = str(p)
            return paths_items, paths_heroes

        def done(result):
            items, heroes = result
            for i, p in items.items():
                pm = QPixmap(p)
                if not pm.isNull():
                    self._items[i] = pm
            for h, p in heroes.items():
                pm = QPixmap(p)
                if not pm.isNull():
                    self._heroes[h] = pm
            on_done()

        name = f"icons:{len(missing_items)}:{len(missing_heroes)}"
        if not self.ctx.jobs.is_running(name):
            self.ctx.jobs.submit(name, work, on_finished=done,
                                 on_failed=lambda e: self.ctx.status(f"Icon fetch failed: {e.splitlines()[0]}", 8000))
