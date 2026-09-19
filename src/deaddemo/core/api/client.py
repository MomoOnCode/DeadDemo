"""HTTP client for deadlock-api.com (free community API, no key required)."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from deaddemo import __version__, paths
from deaddemo.core.api.cache import JsonFileCache
from deaddemo.core.api.models import Hero, Item, MapInfo, MatchHistoryEntry, MatchSalts

BASE_URL = "https://api.deadlock-api.com"
USER_AGENT = f"DeadDemo/{__version__} (+https://github.com/deaddemo)"

HISTORY_TTL = 10 * 60
SALTS_MISSING_TTL = 60 * 60
ASSETS_TTL = 24 * 60 * 60


class ApiError(Exception):
    pass


class RateLimited(ApiError):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry after {retry_after:.0f}s")
        self.retry_after = retry_after


class ReplayUnavailable(ApiError):
    pass


class DeadlockApiClient:
    def __init__(
        self,
        cache: JsonFileCache | None = None,
        base_url: str = BASE_URL,
        min_interval_s: float = 0.3,
        timeout_s: float = 60.0,
    ):
        self.cache = cache or JsonFileCache(paths.api_cache_dir())
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout_s,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    # -- low level ------------------------------------------------------------------
    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get_json(self, path: str, params: dict[str, Any] | None = None, *, ttl_s: float | None = None,
                 cache_predicate=None) -> Any:
        key = path + ("?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())) if params else "")
        if ttl_s is not None:
            cached = self.cache.get(key, ttl_s)
            if cached is not None:
                return cached
        data = self._request(path, params)
        if ttl_s is not None and (cache_predicate is None or cache_predicate(data)):
            self.cache.set(key, data)
        return data

    def _request(self, path: str, params: dict[str, Any] | None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(4):
            self._throttle()
            try:
                resp = self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "5") or 5)
                if attempt == 3:
                    raise RateLimited(retry_after)
                time.sleep(min(retry_after, 30))
                continue
            if resp.status_code == 404:
                raise ApiError(f"not found: {path}")
            if resp.status_code >= 500:
                last_exc = ApiError(f"server error {resp.status_code} for {path}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise ApiError(f"HTTP {resp.status_code} for {path}: {resp.text[:200]}")
            return resp.json()
        raise ApiError(f"request failed for {path}: {last_exc}")

    # -- endpoints ------------------------------------------------------------------
    def match_history(self, account_id: int, *, force_refetch: bool = False) -> list[MatchHistoryEntry]:
        params = {"force_refetch": "true"} if force_refetch else None
        data = self.get_json(f"/v1/players/{account_id}/match-history", params,
                             ttl_s=None if force_refetch else HISTORY_TTL)
        if not isinstance(data, list):
            raise ApiError("unexpected match-history payload")
        return [MatchHistoryEntry.from_dict(d) for d in data]

    def match_salts(self, match_id: int) -> MatchSalts:
        data = self.get_json(f"/v1/matches/{match_id}/salts", ttl_s=SALTS_MISSING_TTL,
                             cache_predicate=lambda d: True)
        salts = MatchSalts.from_dict({**data, "match_id": match_id})
        return salts

    def match_metadata(self, match_id: int) -> dict[str, Any]:
        return self.get_json(f"/v1/matches/{match_id}/metadata", ttl_s=10 * 365 * 86400)

    def heroes(self) -> list[Hero]:
        data = self.get_json("/v1/assets/heroes", {"language": "english"}, ttl_s=ASSETS_TTL)
        return [Hero.from_dict(d) for d in data if "id" in d]

    def items(self) -> list[Item]:
        data = self.get_json("/v1/assets/items", {"language": "english"}, ttl_s=ASSETS_TTL)
        return [Item.from_dict(d) for d in data if "id" in d]

    def map_info(self) -> MapInfo:
        return MapInfo.from_dict(self.get_json("/v1/assets/map", ttl_s=ASSETS_TTL))

    # -- binary assets ----------------------------------------------------------------
    def fetch_image(self, url: str, dest_dir: Path | None = None) -> Path:
        dest_dir = dest_dir or paths.image_cache_dir()
        suffix = Path(url.split("?")[0]).suffix or ".png"
        dest = dest_dir / (hashlib.sha1(url.encode()).hexdigest() + suffix)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        self._throttle()
        resp = self._http.get(url)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code} fetching {url}")
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)
        return dest


_default_client: DeadlockApiClient | None = None


def default_client() -> DeadlockApiClient:
    global _default_client
    if _default_client is None:
        _default_client = DeadlockApiClient()
    return _default_client
