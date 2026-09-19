"""Locate the Steam client, its library folders, the Deadlock install and the logged-in user."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from deaddemo.core.steam.vdf import parse_vdf

if TYPE_CHECKING:
    from deaddemo.settings import Settings

DEADLOCK_APP_ID = 1422450
STEAMID64_BASE = 76561197960265728


@dataclass(frozen=True)
class SteamAccount:
    steam_id64: int
    account_id: int
    persona_name: str
    account_name: str = ""

    @classmethod
    def from_steam_id64(cls, steam_id64: int, persona_name: str = "", account_name: str = "") -> SteamAccount:
        return cls(steam_id64, steam_id64 - STEAMID64_BASE, persona_name, account_name)


@dataclass
class SteamInstall:
    root: Path | None
    libraries: list[Path] = field(default_factory=list)
    deadlock_dir: Path | None = None
    client_build: int | None = None
    account: SteamAccount | None = None
    replay_dirs: list[tuple[str, Path]] = field(default_factory=list)


def find_steam_root(override: str | None = None) -> Path | None:
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    candidates: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
                candidates.append(Path(str(value)))
        except OSError:
            pass
        candidates += [Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")]
    else:
        home = Path.home()
        candidates += [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
            home / "Library" / "Application Support" / "Steam",
        ]
    for c in candidates:
        if (c / "config").is_dir() or (c / "steamapps").is_dir():
            return c
    return None


def read_library_folders(root: Path) -> list[Path]:
    libs: list[Path] = [root]
    vdf_path = root / "config" / "libraryfolders.vdf"
    if not vdf_path.exists():
        vdf_path = root / "steamapps" / "libraryfolders.vdf"
    if vdf_path.exists():
        try:
            data = parse_vdf(vdf_path.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            data = {}
        folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
        for value in folders.values():
            path_str = value.get("path") if isinstance(value, dict) else value
            if isinstance(path_str, str):
                p = Path(path_str)
                if p not in libs:
                    libs.append(p)
    return [p for p in libs if p.is_dir()]


def find_deadlock_dir(libraries: list[Path], override: str | None = None) -> Path | None:
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    for lib in libraries:
        manifest = lib / "steamapps" / f"appmanifest_{DEADLOCK_APP_ID}.acf"
        common = lib / "steamapps" / "common" / "Deadlock"
        if manifest.exists() and common.is_dir():
            return common
    for lib in libraries:  # fallback: folder without manifest (manually copied installs)
        common = lib / "steamapps" / "common" / "Deadlock"
        if (common / "game" / "citadel").is_dir():
            return common
    return None


def find_logged_in_account(root: Path) -> SteamAccount | None:
    vdf_path = root / "config" / "loginusers.vdf"
    if not vdf_path.exists():
        return None
    try:
        data = parse_vdf(vdf_path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return None
    users = data.get("users") or {}
    best: tuple[int, int, dict] | None = None  # (priority, timestamp, entry)
    for sid, entry in users.items():
        if not isinstance(entry, dict) or not sid.isdigit():
            continue
        priority = 0
        if str(entry.get("AutoLogin", "0")) == "1":
            priority = 2
        elif str(entry.get("MostRecent", "0")) == "1":
            priority = 1
        try:
            ts = int(entry.get("Timestamp", 0))
        except ValueError:
            ts = 0
        cand = (priority, ts, {**entry, "_sid": int(sid)})
        if best is None or cand[:2] > best[:2]:
            best = cand
    if best is None:
        return None
    entry = best[2]
    return SteamAccount.from_steam_id64(
        entry["_sid"], str(entry.get("PersonaName", "")), str(entry.get("AccountName", ""))
    )


def read_client_build(deadlock_dir: Path) -> int | None:
    inf = deadlock_dir / "game" / "citadel" / "steam.inf"
    if not inf.exists():
        return None
    for line in inf.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("ClientVersion="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def replay_dirs(deadlock_dir: Path) -> list[tuple[str, Path]]:
    """Existing replay folders in scan order. The game may not have created them yet."""
    citadel = deadlock_dir / "game" / "citadel"
    candidates = [("game", citadel / "replays"), ("addons", citadel / "addons" / "replays")]
    return [(name, p) for name, p in candidates if p.is_dir()]


def primary_replay_dir(deadlock_dir: Path) -> Path:
    """Where the game itself looks for replays (created on demand by the app if absent)."""
    return deadlock_dir / "game" / "citadel" / "replays"


def detect(settings: Settings | None = None) -> SteamInstall:
    root_override = settings.steam_root if settings else None
    dl_override = settings.deadlock_dir if settings else None
    root = find_steam_root(root_override)
    install = SteamInstall(root=root)
    if root:
        install.libraries = read_library_folders(root)
        install.account = find_logged_in_account(root)
    install.deadlock_dir = find_deadlock_dir(install.libraries, dl_override)
    if install.deadlock_dir:
        install.client_build = read_client_build(install.deadlock_dir)
        install.replay_dirs = replay_dirs(install.deadlock_dir)
    if settings and settings.account_id:
        install.account = SteamAccount(
            settings.account_id + STEAMID64_BASE, settings.account_id,
            install.account.persona_name if install.account and install.account.account_id == settings.account_id
            else "(override)",
        )
    return install
