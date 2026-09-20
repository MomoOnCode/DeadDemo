"""Secrets: environment variables first, encrypted-at-rest token store second, never the repo.

Sources, in order:
1. Process environment (``DEADDEMO_*`` / ``DEADLOCK_API_KEY``).
2. A ``.env`` file in the current working directory or the project root (git-ignored), loaded once
   at startup without overriding real environment variables.
3. Long-lived tokens the app itself obtains (the Steam refresh token) live in the per-user data
   directory, encrypted with Windows DPAPI when available, in a 0600 file elsewhere.

Nothing here ever writes into the project tree.
"""

from __future__ import annotations

import base64
import os
import stat
import sys
from pathlib import Path

from deaddemo import paths

ENV_STEAM_USER = "DEADDEMO_STEAM_USER"
ENV_STEAM_PASSWORD = "DEADDEMO_STEAM_PASSWORD"
ENV_STEAM_GUARD_CODE = "DEADDEMO_STEAM_GUARD_CODE"
ENV_STEAM_REFRESH_TOKEN = "DEADDEMO_STEAM_REFRESH_TOKEN"
ENV_API_KEY = "DEADLOCK_API_KEY"
ENV_GC_BINARY = "DEADDEMO_GC_BINARY"

_loaded_dotenv = False


def load_dotenv(extra: Path | None = None) -> list[Path]:
    """Load ``.env`` files (KEY=VALUE, # comments) without overriding existing variables."""
    global _loaded_dotenv
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env"]
    if extra:
        candidates.insert(0, extra)
    loaded = []
    for p in candidates:
        if not p.is_file() or p in loaded:
            continue
        try:
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().removeprefix("export ").strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
            loaded.append(p)
        except OSError:
            continue
    _loaded_dotenv = True
    return loaded


def get(name: str) -> str | None:
    if not _loaded_dotenv:
        load_dotenv()
    value = os.environ.get(name)
    return value if value else None


def api_key() -> str | None:
    return get(ENV_API_KEY)


# --------------------------------------------------------------------------- token store


def _token_path(name: str) -> Path:
    d = paths.data_dir() / "secrets"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.token"


def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):  # noqa: N801
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(blob_in), "DeadDemo", None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):  # noqa: N801
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData failed (token was saved by another user or machine)")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def store_token(name: str, value: str) -> Path:
    p = _token_path(name)
    raw = value.encode("utf-8")
    if sys.platform == "win32":
        payload = b"DPAPI1:" + base64.b64encode(_dpapi_protect(raw))
    else:
        payload = b"PLAIN1:" + base64.b64encode(raw)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(payload)
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    tmp.replace(p)
    return p


def load_token(name: str) -> str | None:
    p = _token_path(name)
    if not p.exists():
        return None
    try:
        payload = p.read_bytes()
        kind, _, body = payload.partition(b":")
        data = base64.b64decode(body)
        if kind == b"DPAPI1":
            data = _dpapi_unprotect(data)
        return data.decode("utf-8")
    except (OSError, ValueError):
        return None


def delete_token(name: str) -> None:
    p = _token_path(name)
    if p.exists():
        p.unlink()


def token_path(name: str) -> Path:
    return _token_path(name)
