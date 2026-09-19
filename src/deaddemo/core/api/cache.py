"""Tiny JSON file cache keyed by URL."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class JsonFileCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def get(self, key: str, ttl_s: float | None) -> Any | None:
        p = self._path(key)
        if not p.exists():
            return None
        if ttl_s is not None and time.time() - p.stat().st_mtime > ttl_s:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: Any) -> None:
        p = self._path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        tmp.replace(p)

    def invalidate(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()
