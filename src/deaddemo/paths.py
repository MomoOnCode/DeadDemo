"""Application directories.

Everything lives under the per-user app data directories from platformdirs, unless
``DEADDEMO_HOME`` is set, in which case config, data and cache all live under that one folder
(handy for tests and portable installs).
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "DeadDemo"
_dirs = PlatformDirs(APP_NAME, APP_NAME, roaming=False)


def _home_override() -> Path | None:
    value = os.environ.get("DEADDEMO_HOME")
    return Path(value).expanduser() if value else None


def config_dir() -> Path:
    home = _home_override()
    path = home / "config" if home else Path(_dirs.user_config_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    home = _home_override()
    path = home / "data" if home else Path(_dirs.user_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    home = _home_override()
    path = home / "cache" if home else Path(_dirs.user_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return config_dir() / "settings.json"


def db_path() -> Path:
    return data_dir() / "deaddemo.sqlite3"


def parsed_root() -> Path:
    path = data_dir() / "parsed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parsed_dir(match_id: int) -> Path:
    return parsed_root() / str(match_id)


def api_cache_dir() -> Path:
    path = cache_dir() / "api"
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_cache_dir() -> Path:
    path = cache_dir() / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_download_dir() -> Path:
    return data_dir() / "downloads"
