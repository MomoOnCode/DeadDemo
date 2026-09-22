"""Launch the Deadlock client for scripted demo playback and find its window.

Two things the engine persists that a filming launch must not leave behind:

* ``-w/-h/-windowed/-noborder`` are written to ``game/citadel/cfg/video.txt`` a few seconds after
  start ("Saved video settings config"), so the player's next normal launch comes up at our size.
* archived convars we set while filming (``fps_max``, ``engine_no_focus_sleep``) are written to
  ``game/citadel/cfg/machine_convars.vcfg`` on quit.

``ConfigGuard`` snapshots those files before the launch and puts them back once the process has
exited. Both are machine-local (not Steam-Cloud synced), so restoring them is safe.

The engine's movie recorder (``startmovie``) is hidden behind ``DefensiveConCommands 1`` in
``game/citadel/gameinfo.gi``. ``GameInfoUnlock`` flips it to 0 for one launch and restores the
original bytes right after the process has started (and again on exit). It is opt-in: retail builds
carry a ``PGIVersion`` signature for that file, Steam's "verify integrity" re-downloads it whenever it
differs from the depot, and mod managers (Deadlock Mod Manager) rewrite it on their own launches.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from deaddemo.core.steam.locator import SteamInstall

# No -dev (assertion spam/dialogs) and no -novid (Deadlock does not know it and echoes "Unknown command").
BASE_ARGS = ["-insecure", "-console", "-condebug"]
DEADLOCK_APP_ID = 1422450
GAME_PROCESS = "deadlock.exe"
STEAM_PROCESS = "steam.exe"
_DEFENSIVE_RE = re.compile(rb"(DefensiveConCommands[ \t]+)1")
# engine-written config files (relative to game/citadel) that a filming launch may alter
ENGINE_CONFIG_FILES = ("cfg/video.txt", "cfg/machine_convars.vcfg")


class LaunchError(Exception):
    pass


def find_deadlock_exe(install: SteamInstall) -> Path:
    if not install.deadlock_dir:
        raise LaunchError("Deadlock install folder not found")
    exe = install.deadlock_dir / "game" / "bin" / "win64" / "deadlock.exe"
    if not exe.exists():
        raise LaunchError(f"deadlock.exe not found at {exe}")
    return exe


def gameinfo_path(install: SteamInstall) -> Path:
    assert install.deadlock_dir
    return install.deadlock_dir / "game" / "citadel" / "gameinfo.gi"


def movie_dir(install: SteamInstall) -> Path:
    assert install.deadlock_dir
    return install.deadlock_dir / "game" / "citadel" / "movie"


def citadel_dir(install: SteamInstall) -> Path:
    assert install.deadlock_dir
    return install.deadlock_dir / "game" / "citadel"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class ConfigGuard:
    """Snapshot the engine's persisted config files and put them back after the game has exited.

    ``restore()`` returns the names of the files it had to rewrite (empty when the engine left them alone).
    """

    def __init__(self, root: Path, names: tuple[str, ...] = ENGINE_CONFIG_FILES):
        self.root = root
        self.names = names
        self.snapshots: dict[str, bytes | None] = {}

    def snapshot(self) -> ConfigGuard:
        for name in self.names:
            p = self.root / name
            try:
                self.snapshots[name] = p.read_bytes() if p.exists() else None
            except OSError:
                self.snapshots[name] = None
        return self

    def changed(self) -> list[str]:
        out = []
        for name, before in self.snapshots.items():
            p = self.root / name
            after = p.read_bytes() if p.exists() else None
            if after != before:
                out.append(name)
        return out

    def restore(self, attempts: int = 10, delay_s: float = 0.5) -> list[str]:
        restored: list[str] = []
        for name in self.changed():
            before = self.snapshots[name]
            p = self.root / name
            for i in range(attempts):
                try:
                    if before is None:
                        p.unlink(missing_ok=True)
                    else:
                        tmp = p.with_name(p.name + ".deaddemo-tmp")
                        tmp.write_bytes(before)
                        os.replace(tmp, p)
                    restored.append(name)
                    break
                except OSError:  # the game may still hold the file for a moment while shutting down
                    if i == attempts - 1:
                        raise
                    time.sleep(delay_s)
        return restored


class GameInfoUnlock:
    """Context manager that patches ``DefensiveConCommands 1 -> 0`` and always restores the file.

    Works on raw bytes so the file's (mixed CRLF/LF) line endings survive untouched."""

    def __init__(self, path: Path):
        self.path = path
        self.backup = path.with_name(path.name + ".deaddemo-bak")
        self.original_hash: str | None = None
        self.patched = False

    def __enter__(self) -> GameInfoUnlock:
        data = self.path.read_bytes()
        if b"DefensiveConCommands" not in data:
            raise LaunchError("gameinfo.gi has no DefensiveConCommands entry; refusing to patch")
        self.original_hash = _sha256(self.path)
        shutil.copy2(self.path, self.backup)
        new_data, n = _DEFENSIVE_RE.subn(rb"\g<1>0", data, count=1)
        if n:
            self.path.write_bytes(new_data)
            self.patched = True
        return self

    def restore(self) -> bool:
        """Put the original bytes back. Returns True when the file matches the original hash."""
        if self.backup.exists():
            shutil.copy2(self.backup, self.path)
            try:
                self.backup.unlink()
            except OSError:
                pass
        ok = self.original_hash is None or _sha256(self.path) == self.original_hash
        self.patched = False
        return ok

    def __exit__(self, *exc) -> None:
        self.restore()


@dataclass
class LaunchOptions:
    width: int = 1280
    height: int = 720
    windowed: bool = True
    borderless: bool = True  # -noborder makes the engine size the window to the desktop, ignoring -w/-h
    vcon_port: int = 29005
    netcon_port: int | None = None
    unlock_movie: bool = False
    extra: list[str] | None = None

    def args(self) -> list[str]:
        a = list(BASE_ARGS)
        a += ["-vconsole", "-vconport", str(self.vcon_port)]
        if self.netcon_port:
            a += ["-netconport", str(self.netcon_port)]
        if self.windowed:
            a += ["-windowed"] + (["-noborder"] if self.borderless else []) + ["-w", str(self.width), "-h",
                                                                                 str(self.height)]
        else:
            a += ["-fullscreen", "-w", str(self.width), "-h", str(self.height)]
        if self.extra:
            a += self.extra
        return a


def _pids(image: str) -> set[int]:
    """PIDs of running processes with this image name (Windows)."""
    if sys.platform != "win32":
        return set()
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image}"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout  # type: ignore[attr-defined]
    except (OSError, subprocess.SubprocessError):
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == image and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def game_pids() -> set[int]:
    return _pids(GAME_PROCESS)


def steam_running() -> bool:
    return bool(_pids(STEAM_PROCESS))


def steam_ready() -> bool:
    """Steam is up and past its bootstrap (the web helper only starts once the client is logged in)."""
    return steam_running() and bool(_pids("steamwebhelper.exe"))


def ensure_steam(steam: Path, log=None, timeout_s: float = 240.0, settle_s: float = 15.0) -> None:
    """Start the Steam client if needed and wait until it can take an -applaunch.

    A cold ``steam.exe -applaunch`` is unreliable: when the bootstrapper finds a pending client update it
    installs it, shuts down, and the launch request is lost. Starting Steam on its own first and waiting
    for the web helper (plus a settle period for login/cloud sync) avoids that."""
    if steam_ready():
        return
    if not steam_running():
        if log:
            log("Steam is not running; starting it (this can take a while)")
        creation = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0  # type: ignore[attr-defined]
        subprocess.Popen([str(steam), "-silent"], creationflags=creation, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if steam_ready():
            time.sleep(settle_s)
            if steam_ready():  # still up after the settle period (no update-restart in between)
                return
            deadline = time.monotonic() + timeout_s  # it restarted (client update); wait again
        time.sleep(1.0)
    raise LaunchError("Steam did not become ready (is it logged in?)")


class GameProcess:
    """Popen-like handle for a game process we may not have started ourselves (Steam launched it)."""

    def __init__(self, pid: int, popen: subprocess.Popen | None = None):
        self.pid = pid
        self._popen = popen

    def poll(self) -> int | None:
        if self._popen is not None:
            rc = self._popen.poll()
            if rc is not None:
                return rc
        return None if self.pid in game_pids() else 0

    def terminate(self) -> None:
        if self._popen is not None:
            self._popen.terminate()
        elif sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(self.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)  # type: ignore[attr-defined]

    def kill(self) -> None:
        if self._popen is not None:
            self._popen.kill()
        elif sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(self.pid), "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)  # type: ignore[attr-defined]


def steam_exe(install: SteamInstall) -> Path | None:
    if not install.root:
        return None
    p = install.root / "steam.exe"
    return p if p.exists() else None


def launch(
    install: SteamInstall, options: LaunchOptions, gameinfo: Path | None = None, *, mode: str = "steam",
    log=None,
) -> tuple[GameProcess, GameInfoUnlock | None]:
    """Start the game. ``mode='steam'`` asks the Steam client to launch it with our extra arguments
    (the game then has its normal Steam context); ``mode='direct'`` runs deadlock.exe ourselves.
    When ``options.unlock_movie`` the gameinfo patch is applied for the launch and restored a few
    seconds after the process is alive (the engine reads it once at startup)."""
    unlock: GameInfoUnlock | None = None
    if options.unlock_movie:
        if gameinfo is None:
            raise LaunchError("gameinfo path required for movie unlock")
        unlock = GameInfoUnlock(gameinfo)
        unlock.__enter__()
    before = game_pids()
    try:
        creation = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0  # type: ignore[attr-defined]
        popen: subprocess.Popen | None = None
        steam = steam_exe(install) if mode == "steam" else None
        wait_s = 90.0
        if steam is not None:
            cmd = [str(steam), "-applaunch", str(DEADLOCK_APP_ID), *options.args()]
            ensure_steam(steam, log)
            if log:
                log("via Steam: " + " ".join(cmd[1:]))
            subprocess.Popen(cmd, creationflags=creation, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            exe = find_deadlock_exe(install)
            env = dict(os.environ)
            env.setdefault("SteamAppId", str(DEADLOCK_APP_ID))
            env.setdefault("SteamGameId", str(DEADLOCK_APP_ID))
            if log:
                log("direct: " + " ".join([exe.name, *options.args()]))
            popen = subprocess.Popen([str(exe), *options.args()], cwd=str(exe.parent), creationflags=creation, env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # wait for a (new) deadlock.exe to appear
        deadline = time.monotonic() + wait_s
        pid: int | None = None
        while time.monotonic() < deadline:
            if popen is not None and popen.poll() is not None and popen.returncode not in (None, 0):
                raise LaunchError(f"deadlock.exe exited immediately with code {popen.returncode}")
            new = game_pids() - before
            if new:
                pid = max(new)
                break
            time.sleep(0.5)
        if pid is None:
            raise LaunchError("the game process never appeared (is Steam running and logged in?)")
        return GameProcess(pid, popen if popen is not None and popen.pid == pid else None), unlock
    except Exception:
        if unlock:
            unlock.restore()
        raise


def terminate(proc: GameProcess, grace_s: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        deadline = time.monotonic() + grace_s
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass


def console_log_path(install: SteamInstall) -> Path:
    assert install.deadlock_dir
    return install.deadlock_dir / "game" / "citadel" / "console.log"


def console_log_tail(install: SteamInstall, n: int = 40) -> list[str]:
    try:
        p = console_log_path(install)
        if not p.exists():
            return []
        data = p.read_bytes()[-200_000:]
        return data.decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


# --------------------------------------------------------------------------- window lookup (Windows)


@dataclass
class WindowRect:
    left: int  # client area origin on the virtual desktop
    top: int
    width: int  # client area size
    height: int
    hwnd: int
    frame_left: int = 0  # client area offset inside the whole window (borders/title bar)
    frame_top: int = 0


def find_window_for_pid(pid: int) -> WindowRect | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    found: list[WindowRect] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd, _lparam):
        owner_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        rect = (ctypes.c_long * 4)()
        if user32.GetClientRect(hwnd, ctypes.byref(rect)):
            pt = (ctypes.c_long * 2)(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            win = (ctypes.c_long * 4)()
            user32.GetWindowRect(hwnd, ctypes.byref(win))
            if w > 100 and h > 100:
                found.append(WindowRect(pt[0], pt[1], w, h, int(hwnd), pt[0] - win[0], pt[1] - win[1]))
        return True

    user32.EnumWindows(enum_proc, 0)
    found.sort(key=lambda r: -(r.width * r.height))
    return found[0] if found else None


def bring_to_front(hwnd: int) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetForegroundWindow(hwnd)  # type: ignore[attr-defined]
        except OSError:
            pass


