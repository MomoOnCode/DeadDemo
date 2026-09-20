"""Steam Game Coordinator salt provider, backed by the ``deaddemo-gc`` Rust helper.

Contract with the helper (all secrets travel through environment variables, never argv):

* ``deaddemo-gc login``   env: DEADDEMO_STEAM_USER, DEADDEMO_STEAM_PASSWORD, optional
  DEADDEMO_STEAM_GUARD_CODE. Prints one JSON object: {"refresh_token", "steam_id64", "account_id"}.
* ``deaddemo-gc salts <match_id>...``   env: DEADDEMO_STEAM_USER, DEADDEMO_STEAM_REFRESH_TOKEN.
  Prints one JSON object per line: {"match_id", "result", "replay_salt", "metadata_salt",
  "cluster_id", "replay_valid_through"}; ``result`` is "success" or a k_eResult_* name.
* ``deaddemo-gc status``  env as for salts. Prints {"ok": bool, "steam_id64", "error"}.

The refresh token is stored encrypted (DPAPI on Windows) in the app data dir by ``secrets``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from deaddemo import paths
from deaddemo.core import secrets

TOKEN_NAME = "steam_refresh"
USER_NAME = "steam_user"
HELPER_NAME = "deaddemo-gc.exe" if sys.platform == "win32" else "deaddemo-gc"
DAILY_LIMIT = 40
MIN_SPACING_S = 20.0
GAME_PROCESSES = ("project8.exe", "deadlock.exe", "project8")


class GcError(Exception):
    pass


class GcNotConfigured(GcError):
    pass


@dataclass
class GcSalts:
    match_id: int
    result: str
    replay_salt: int | None = None
    metadata_salt: int | None = None
    cluster_id: int | None = None
    replay_valid_through: int | None = None

    @property
    def ok(self) -> bool:
        return self.result == "success" and bool(self.replay_salt)

    def demo_url(self) -> str | None:
        if not self.ok:
            return None
        return f"http://replay{self.cluster_id}.valve.net/1422450/{self.match_id}_{self.replay_salt}.dem.bz2"

    def metadata_url(self) -> str | None:
        if not self.metadata_salt or not self.cluster_id:
            return None
        return f"http://replay{self.cluster_id}.valve.net/1422450/{self.match_id}_{self.metadata_salt}.meta.bz2"


def find_helper() -> Path | None:
    override = secrets.get(secrets.ENV_GC_BINARY)
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    exe_dir = Path(sys.executable).resolve().parent
    root = Path(__file__).resolve().parents[4]  # src/deaddemo/core/gc/provider.py -> project root
    candidates += [
        exe_dir / HELPER_NAME,  # PyInstaller onedir
        exe_dir / "gc" / HELPER_NAME,
        root / "gc" / "target" / "release" / HELPER_NAME,
        root / "gc" / "target" / "debug" / HELPER_NAME,
        paths.data_dir() / "bin" / HELPER_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    found = shutil.which("deaddemo-gc")
    return Path(found) if found else None


def is_logged_in() -> bool:
    return secrets.load_token(TOKEN_NAME) is not None and secrets.load_token(USER_NAME) is not None


def logged_in_user() -> str | None:
    return secrets.load_token(USER_NAME)


def logout() -> None:
    secrets.delete_token(TOKEN_NAME)
    secrets.delete_token(USER_NAME)


def _run(args: list[str], env: dict[str, str], timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    helper = find_helper()
    if helper is None:
        raise GcNotConfigured(
            "deaddemo-gc helper not found. Build it with `cargo build --release` in the gc/ folder, "
            "or set DEADDEMO_GC_BINARY."
        )
    full_env = {**os.environ, **env}
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
    try:
        # stdin is closed on purpose: without a guard code the helper falls through to the
        # mobile-app confirmation instead of blocking on a terminal prompt.
        return subprocess.run([str(helper), *args], env=full_env, capture_output=True, text=True, timeout=timeout,
                              creationflags=creation, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise GcError(f"deaddemo-gc {args[0]} timed out after {timeout:.0f}s") from exc


def _parse_json_line(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise GcError("helper produced no JSON output")


def login(username: str, password: str, guard_code: str | None = None) -> dict:
    """Interactive Steam login. Stores the refresh token and username; returns the helper's JSON."""
    env = {secrets.ENV_STEAM_USER: username, secrets.ENV_STEAM_PASSWORD: password}
    if guard_code:
        env[secrets.ENV_STEAM_GUARD_CODE] = guard_code
    proc = _run(["login"], env, timeout=180.0)
    if proc.returncode != 0:
        raise GcError(_error_text(proc))
    data = _parse_json_line(proc.stdout)
    token = data.get("refresh_token")
    if not token:
        raise GcError("login succeeded but no refresh token was returned")
    secrets.store_token(TOKEN_NAME, str(token))
    secrets.store_token(USER_NAME, username)
    data.pop("refresh_token", None)
    return data


def _auth_env() -> dict[str, str]:
    token = secrets.load_token(TOKEN_NAME)
    user = secrets.load_token(USER_NAME)
    if not token or not user:
        env_user = secrets.get(secrets.ENV_STEAM_USER)
        env_token = secrets.get(secrets.ENV_STEAM_REFRESH_TOKEN)
        if env_user and env_token:
            return {secrets.ENV_STEAM_USER: env_user, secrets.ENV_STEAM_REFRESH_TOKEN: env_token}
        raise GcNotConfigured("Not logged into Steam. Use Settings → Steam login (or `deaddemo gc login`).")
    return {secrets.ENV_STEAM_USER: user, secrets.ENV_STEAM_REFRESH_TOKEN: token}


def status() -> dict:
    proc = _run(["status"], _auth_env(), timeout=90.0)
    try:
        return _parse_json_line(proc.stdout)
    except GcError:
        return {"ok": False, "error": _error_text(proc)}


def _error_text(proc: subprocess.CompletedProcess[str]) -> str:
    err = (proc.stderr or "").strip().splitlines()
    out = (proc.stdout or "").strip().splitlines()
    text = err[-1] if err else (out[-1] if out else f"exit code {proc.returncode}")
    return text[:400]


# --------------------------------------------------------------------------- quota


def _quota_path() -> Path:
    return paths.data_dir() / "gc_quota.json"


def quota_used_today() -> int:
    try:
        data = json.loads(_quota_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    day = time.strftime("%Y-%m-%d")
    return int(data.get(day, 0))


def _bump_quota(n: int) -> None:
    day = time.strftime("%Y-%m-%d")
    try:
        data = json.loads(_quota_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = {day: int(data.get(day, 0)) + n}
    _quota_path().write_text(json.dumps(data), encoding="utf-8")


def game_running() -> bool:
    """Steam routes GC traffic to the running game, so lookups must wait until Deadlock is closed."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
                                 creationflags=subprocess.CREATE_NO_WINDOW).stdout.lower()  # type: ignore[attr-defined]
        else:
            out = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=10).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    if sys.platform == "win32":
        return any(f'"{name}"' in out for name in GAME_PROCESSES)  # exact image name in the CSV
    return any(line.strip().split("/")[-1] in GAME_PROCESSES for line in out.splitlines())


def fetch_salts(match_ids: list[int]) -> list[GcSalts]:
    """Ask the GC for salts. Respects the daily quota and reports per-match results."""
    if not match_ids:
        return []
    remaining = DAILY_LIMIT - quota_used_today()
    if remaining <= 0:
        raise GcError(f"Daily Steam GC quota ({DAILY_LIMIT}) reached; try again tomorrow")
    ids = match_ids[:remaining]
    env = _auth_env()
    if game_running():
        raise GcError("Deadlock is running; Steam sends Game Coordinator replies to the game. Close it and retry.")
    proc = _run(["salts", *[str(m) for m in ids]], env, timeout=90.0 + (MIN_SPACING_S + 45) * len(ids))
    _bump_quota(len(ids))
    results: list[GcSalts] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        results.append(GcSalts(
            match_id=int(d.get("match_id", 0)), result=str(d.get("result", "unknown")),
            replay_salt=d.get("replay_salt") or None, metadata_salt=d.get("metadata_salt") or None,
            cluster_id=d.get("cluster_id") or None, replay_valid_through=d.get("replay_valid_through") or None,
        ))
    if any(r.result == "k_eResult_RateLimited" for r in results):
        _bump_quota(DAILY_LIMIT)  # Steam itself throttled us: stop for the day
    if proc.returncode != 0 and not results:
        raise GcError(_error_text(proc))
    return results
