"""Drive the Deadlock client through a demo and record clip sequences.

Flow: preflight → launch deadlock.exe → connect remote console → playdemo → for each sequence:
pause, seek, spectate, HUD, capture, resume until the end tick, stop → quit → encode.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from deaddemo import paths
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import DemoRepo, MatchRepo
from deaddemo.core.gc.provider import game_running
from deaddemo.core.steam.locator import SteamInstall
from deaddemo.core.video import encode, launcher
from deaddemo.core.video.capture import ClipFiles, EngineRecorder, ScreenRecorder, WindowRecorder
from deaddemo.core.video.console import ConsoleBase, ConsoleError, NetConClient, VConsoleClient
from deaddemo.core.video.sequences import Sequence

PLAYING_RE = re.compile(r"Currently playing (\d+) of (\d+) ticks")
FATAL_RE = re.compile(r"FATAL ERROR|Engine Error")  # dev assertions ("Assertion Failed") are not fatal
MENU_ACTIVE_RE = re.compile(r"Host activate: Idle")
LOOP_DONE_RE = re.compile(r"OnSwitchLoopModeFinished\( game : success \)")
DEMO_ACTIVE_RE = re.compile(r"Host activate: Playing Demo")
Progress = Callable[[str], None]


class VideoError(Exception):
    pass


class Cancelled(VideoError):
    pass


@dataclass
class RecordSettings:
    width: int = 1920
    height: int = 1080
    fps: int = 60
    quality: str = "high"
    backend: str = "window"  # window (WGC of the game window) | screen (desktop duplication) | engine | auto
    hide_hud: bool = True
    concat: bool = False
    output_dir: Path = field(default_factory=lambda: paths.data_dir() / "videos")
    vcon_port: int = 29005
    windowed: bool = True
    keep_frames: bool = False
    launch_mode: str = "steam"  # steam | direct


@dataclass
class Capabilities:
    transport: str | None = None  # vconsole | netcon
    demo_loads: bool = False
    seek_works: bool = False
    spec_target_ok: bool | None = None
    spec_player_ok: bool | None = None
    startmovie_works: bool | None = None
    checked_at: str = ""
    notes: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> Capabilities | None:
        p = paths.data_dir() / "video_capabilities.json"
        if not p.exists():
            return None
        try:
            return cls(**json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            return None

    def save(self) -> None:
        from deaddemo.core.db.database import utcnow_iso

        self.checked_at = utcnow_iso()
        (paths.data_dir() / "video_capabilities.json").write_text(json.dumps(self.__dict__, indent=1),
                                                                   encoding="utf-8")


# --------------------------------------------------------------------------- session


class GameSession:
    """One launched game process plus its console."""

    def __init__(self, install: SteamInstall, options: launcher.LaunchOptions, log: Progress,
                 cancel: threading.Event | None = None, launch_mode: str = "steam"):
        self.install = install
        self.options = options
        self.log = log
        self.launch_mode = launch_mode
        self.cancel = cancel or threading.Event()
        self.proc = None
        self.unlock: launcher.GameInfoUnlock | None = None
        self.guard: launcher.ConfigGuard | None = None
        self.console: ConsoleBase | None = None
        self.transport: str | None = None

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("cancelled")

    def __enter__(self) -> GameSession:
        if game_running():
            raise VideoError("Deadlock is already running. Close it first.")
        launcher.find_deadlock_exe(self.install)  # validates the install
        # the engine saves our -w/-h/-windowed into cfg/video.txt and archived convars into
        # cfg/machine_convars.vcfg; snapshot them now, restore after the process is gone
        self.guard = launcher.ConfigGuard(launcher.citadel_dir(self.install)).snapshot()
        self.log("Launching Deadlock …")
        self.proc, self.unlock = launcher.launch(self.install, self.options, launcher.gameinfo_path(self.install),
                                                 mode=self.launch_mode, log=self.log)
        self.log(f"Game process pid {self.proc.pid}")
        try:
            if self.unlock:
                # the engine reads gameinfo.gi during startup; give it a moment, then put the file back
                time.sleep(8.0)
                ok = self.unlock.restore()
                self.log("gameinfo.gi restored" + ("" if ok else " (hash mismatch!)"))
            self._connect()
        except Exception as exc:
            tail = launcher.console_log_tail(self.install, 25)
            if tail:
                self.log("--- last console.log lines ---")
                for line in tail:
                    self.log("  | " + line[:200])
            self.close()
            if self.proc is not None and self.proc.poll() is not None:
                raise VideoError(f"the game exited during startup ({exc})") from exc
            raise
        return self

    def _connect(self) -> None:
        vcon = VConsoleClient(port=self.options.vcon_port)
        try:
            self.log(f"Connecting to VConsole on port {self.options.vcon_port} …")
            vcon.connect(timeout=90.0)
            self.console, self.transport = vcon, "vconsole"
        except ConsoleError as exc:
            self.log(f"VConsole: {exc}")
            if self.options.netcon_port:
                net = NetConClient(port=self.options.netcon_port)
                self.log(f"Connecting to netcon on port {self.options.netcon_port} …")
                net.connect(timeout=30.0)
                self.console, self.transport = net, "netcon"
            else:
                raise VideoError("could not connect to the game console") from exc
        self.fatal: str | None = None
        self.launched_at = time.monotonic()

        def on_line(line: str) -> None:
            self.log("  > " + line[:200])
            if FATAL_RE.search(line) and self.fatal is None:
                self.fatal = line.strip()

        self.console.buffer.listeners.append(on_line)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self._check_cancel()
            try:
                if self.console.roundtrip(timeout=3.0):
                    self.log(f"Console ready ({self.transport})")
                    self._wait_for_menu()
                    return
            except ConsoleError as exc:
                raise VideoError(str(exc)) from exc
            if self.proc is not None and self.proc.poll() is not None:
                raise VideoError("game exited during startup")
        raise VideoError("console connected but the game never answered an echo")

    def _check_fatal(self) -> None:
        if self.fatal:
            msg = self.fatal
            self.log("Game reported a fatal error; closing it")
            if self.proc is not None:
                self.proc.kill()
            raise VideoError(f"game fatal error: {msg}")

    def _wait_quiet(self, quiet_s: float, max_s: float) -> None:
        """Wait until the console has printed nothing for ``quiet_s`` seconds (the game is idle)."""
        assert self.console
        deadline = time.monotonic() + max_s
        last_cursor = self.console.cursor
        last_change = time.monotonic()
        while time.monotonic() < deadline:
            self._check_cancel()
            self._check_fatal()
            time.sleep(0.5)
            cur = self.console.cursor
            if cur != last_cursor:
                last_cursor, last_change = cur, time.monotonic()
            elif time.monotonic() - last_change >= quiet_s:
                return

    def _wait_for_menu(self) -> None:
        """Loading a demo while the client is still booting crashes it (CopyNewEntity out of range 0)."""
        assert self.console
        since = 0
        if self.console.wait_for(MENU_ACTIVE_RE, timeout=90.0, since=since):
            self.log("Main menu activated; waiting for the hideout session …")
        else:
            self.log("No main-menu activation seen; waiting anyway")
        # the menu runs a local "hideout" game loop; wait for it to finish switching, then go quiet
        if self.console.wait_for(LOOP_DONE_RE, timeout=60.0, since=since):
            self.log("Hideout game loop finished loading")
        self._wait_quiet(quiet_s=5.0, max_s=60.0)
        min_uptime = 20.0
        remaining = min_uptime - (time.monotonic() - self.launched_at)
        if remaining > 0:
            time.sleep(remaining)
        self._check_fatal()
        self.log("Client ready")

    # -- demo control ------------------------------------------------------------------
    def playing(self) -> tuple[int, int] | None:
        m = self.console.query("demo_goto", PLAYING_RE, timeout=2.0) if self.console else None
        return (int(m.group(1)), int(m.group(2))) if m else None

    def play_demo(self, demo_name: str, timeout: float = 240.0) -> tuple[int, int]:
        """Load the demo and wait for the engine to activate it. No console polling while it loads."""
        assert self.console
        self.log(f"playdemo {demo_name}")
        since = self.console.cursor
        self.console.send(f"playdemo {demo_name}")
        deadline = time.monotonic() + timeout
        activated = False
        while time.monotonic() < deadline and not activated:
            self._check_cancel()
            self._check_fatal()
            if self.proc is not None and self.proc.poll() is not None:
                raise VideoError("game exited while loading the demo")
            activated = self.console.wait_for(DEMO_ACTIVE_RE, timeout=2.0, since=since) is not None
        if not activated:
            raise VideoError("demo never activated (build mismatch? see the console log)")
        self.log("Demo activated; letting it settle …")
        self._wait_quiet(quiet_s=3.0, max_s=45.0)
        self._check_fatal()
        for _ in range(10):
            p = self.playing()
            if p and p[1] > 0:
                self.log(f"Demo playing: tick {p[0]} of {p[1]}")
                return p
            time.sleep(1.0)
            self._check_fatal()
        raise VideoError("demo activated but reports no ticks")

    def seek(self, tick: int, pause: bool = True) -> None:
        assert self.console
        self.console.send(f"demo_goto {tick} 0 {1 if pause else 0}")
        self._wait_quiet(quiet_s=1.5, max_s=30.0)

    def wait_until_tick(self, tick: int, timeout: float, on_tick: Callable[[int], None] | None = None) -> int:
        deadline = time.monotonic() + timeout
        last = -1
        while time.monotonic() < deadline:
            self._check_cancel()
            self._check_fatal()
            p = self.playing()
            if p:
                last = p[0]
                if on_tick:
                    on_tick(last)
                if last >= tick:
                    return last
            time.sleep(0.25)
        return last

    def spectate(self, seq: Sequence, caps: Capabilities | None) -> None:
        assert self.console
        name = seq.focus_name
        if name:
            quoted = name.replace('"', "")
            if caps is None or caps.spec_player_ok is not False:
                self.console.send(f'spec_player "{quoted}"')
            self.console.send(f'spec_target "{quoted}"')
        if seq.camera == "in_eye":
            self.console.send("spec_in_eye")
        elif seq.camera == "chase":
            self.console.send("spec_chase")

    def set_hud(self, visible: bool) -> None:
        assert self.console
        self.console.send(f"citadel_hud_visible {1 if visible else 0}")
        self.console.send(f"citadel_hide_replay_hud {0 if visible else 1}")

    def get_convar(self, name: str, timeout: float = 2.0) -> str | None:
        """Current value of a convar (the engine echoes ``name = value`` when the name is sent bare)."""
        assert self.console
        m = self.console.query(name, re.compile(rf'^\s*"?{re.escape(name)}"?\s*=\s*"?([^\s"]+)'), timeout=timeout)
        return m.group(1) if m else None

    def set_convars(self, values: dict[str, str]) -> dict[str, str | None]:
        """Set convars and return their previous values (for ``restore_convars``)."""
        previous = {name: self.get_convar(name) for name in values}
        for name, value in values.items():
            self.console.send(f"{name} {value}")  # type: ignore[union-attr]
        return previous

    def restore_convars(self, previous: dict[str, str | None]) -> None:
        if self.console is None:
            return
        for name, value in previous.items():
            if value is not None:
                try:
                    self.console.send(f"{name} {value}")
                except ConsoleError:
                    return

    def close(self) -> None:
        try:
            if self.console is not None:
                try:
                    self.console.send("quit")
                except ConsoleError:
                    pass
                self.console.close()
        finally:
            if self.proc is not None:
                deadline = time.monotonic() + 10
                while self.proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.2)
                launcher.terminate(self.proc)
            if self.unlock is not None:
                self.unlock.restore()
            if self.guard is not None:
                try:
                    restored = self.guard.restore()
                except OSError as exc:
                    self.log(f"WARNING: could not restore engine config files: {exc}")
                else:
                    if restored:
                        self.log("Restored engine config the game had rewritten: " + ", ".join(restored))
                self.guard = None

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------- probe


def demo_console_name(demo_path: str) -> str:
    """The name the game expects: ``replays/<stem>`` for demos in its own folder, else the full path."""
    p = Path(demo_path)
    if p.parent.name == "replays":
        return f"replays/{p.stem}"
    return str(p.with_suffix("")).replace("\\", "/")


_NOISE_RE = re.compile(r"\[Animation 2\]|\[SteamNetSockets\]|\[MeshSystem\]|Cannot apply Ragdoll|\[Localization")


def _interesting(lines: list[str], limit: int = 12) -> list[str]:
    """Console lines worth quoting back: drop known engine spam."""
    out = [ln.strip() for ln in lines if ln.strip() and not _NOISE_RE.search(ln)]
    return out[:limit]


def open_probe_log() -> tuple[Path, Callable[[str], None]]:
    """Every probe/record run writes a full transcript under data_dir/logs."""
    logs = paths.data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"video_{time.strftime('%Y%m%d_%H%M%S')}.log"
    fh = path.open("a", encoding="utf-8")

    def write(msg: str) -> None:
        fh.write(time.strftime("%H:%M:%S ") + msg + "\n")
        fh.flush()

    return path, write


def probe(install: SteamInstall, db: Database, match_id: int, log: Progress, *, try_movie: bool = False,
          cancel: threading.Event | None = None, launch_mode: str = "steam") -> Capabilities:
    """Launch the game, test console control, seeking and spectating. ``try_movie`` relaunches once with the
    gameinfo.gi unlock to test ``startmovie`` (opt-in: it edits a signed game file for a few seconds)."""
    log_path, file_log = open_probe_log()

    def both(msg: str) -> None:
        file_log(msg)
        log(msg)

    both(f"Probe log: {log_path}")
    caps = Capabilities()
    caps.notes.append(f"log: {log_path}")
    match, demo = _resolve(db, match_id, install)
    name = demo_console_name(demo.path)
    tick_rate = match.tick_rate or 64
    target = (match.game_start_tick or 0) + 300 * tick_rate
    opts = launcher.LaunchOptions(width=1280, height=720, netcon_port=29006)
    with GameSession(install, opts, both, cancel, launch_mode) as s:
        caps.transport = s.transport
        cur, total = s.play_demo(name)
        caps.demo_loads = total > 0
        s.seek(target, pause=True)
        reached = s.wait_until_tick(target - 2 * tick_rate, timeout=90)
        caps.seek_works = reached >= 0 and abs(reached - target) < 10 * tick_rate
        both(f"seek to {target}: now at {reached} -> {'ok' if caps.seek_works else 'FAILED'}")
        players = MatchRepo(db).players(match_id)
        if players:
            nm = players[0].player_name or ""
            since = s.console.cursor
            s.console.send(f'spec_player "{nm}"')
            time.sleep(1.5)
            out = " ".join(s.console.lines_since(since)).lower()
            caps.spec_player_ok = "unknown command" not in out and "can't" not in out
            since = s.console.cursor
            s.console.send(f'spec_target "{nm}"')
            time.sleep(1.5)
            out = " ".join(s.console.lines_since(since)).lower()
            caps.spec_target_ok = "unknown command" not in out
        s.set_hud(False)
        s.console.send("demo_resume")
        t0 = s.playing()
        time.sleep(3.0)
        t1 = s.playing()
        both(f"playback advancing: {t0} -> {t1}")
        s.console.send("demo_pause")
        s.set_hud(True)
    if try_movie:
        both("Relaunching with the movie recorder unlocked …")
        opts = launcher.LaunchOptions(width=1280, height=720, unlock_movie=True)
        rec_ok = False
        with GameSession(install, opts, both, cancel, launch_mode) as s:
            s.play_demo(name)
            s.seek(target, pause=True)
            s.wait_until_tick(target - 2 * tick_rate, timeout=90)
            time.sleep(1.0)
            # 1) what does the engine say about the command itself?
            since = s.console.cursor
            s.console.send("startmovie")
            time.sleep(1.5)
            reply = _interesting(s.console.lines_since(since))
            caps.notes.append("startmovie reply: " + (" | ".join(reply) if reply else "(silence)"))
            both("startmovie reply: " + (" | ".join(reply) if reply else "(silence)"))
            # 2) record a few seconds
            rec = EngineRecorder(s.console, launcher.movie_dir(install), 30, 1280, 720)
            since = s.console.cursor
            rec.start("deaddemo_probe")
            s.console.send("demo_resume")
            time.sleep(4.0)
            files = rec.stop()
            s.console.send("demo_pause")
            reply = _interesting(s.console.lines_since(since))
            both("recording reply: " + (" | ".join(reply) if reply else "(silence)"))
            found = _find_movie_output(install, "deaddemo_probe")
            rec_ok = files.frame_count > 0 or bool(found)
            caps.notes.append(f"startmovie frames={files.frame_count} wav={'yes' if files.wav else 'no'}"
                              + (f" other output: {found[:3]}" if found else ""))
            if not rec_ok and reply:
                caps.notes.append("recording reply: " + " | ".join(reply)[:400])
            if files.frames_dir and files.frames_dir.exists():
                shutil.rmtree(files.frames_dir, ignore_errors=True)
            for f in found:
                try:
                    Path(f).unlink()
                except OSError:
                    pass
        caps.startmovie_works = rec_ok
        both(f"engine recorder: {'works' if rec_ok else 'not available'}")
    else:
        previous = Capabilities.load()
        if previous is not None:
            caps.startmovie_works = previous.startmovie_works  # keep the last full test's verdict
    caps.save()
    both("Capabilities saved: " + json.dumps(caps.__dict__))
    return caps


def _find_movie_output(install: SteamInstall, stem: str) -> list[str]:
    """startmovie output may land in several places depending on the engine's cwd."""
    roots = []
    if install.deadlock_dir:
        d = install.deadlock_dir
        roots += [d / "game" / "citadel" / "movie", d / "game" / "citadel", d / "game" / "bin" / "win64" / "movie",
                  d / "game" / "bin" / "win64", d / "movie", d]
    hits: list[str] = []
    for r in roots:
        if not r.exists():
            continue
        try:
            for p in r.rglob(f"{stem}*"):
                if p.is_file() and p.suffix.lower() in (".jpg", ".png", ".tga", ".wav", ".mp4", ".avi", ".webm"):
                    hits.append(str(p))
        except OSError:
            continue
    return hits


def _resolve(db: Database, match_id: int, install: SteamInstall):
    match = MatchRepo(db).get(match_id)
    if match is None:
        raise VideoError(f"match {match_id} is not analyzed")
    demos = [d for d in DemoRepo(db).by_match(match_id) if d.status in ("found", "parsed")]
    if not demos:
        raise VideoError(f"no local demo for match {match_id}")
    demo = demos[0]
    if demo.build and install.client_build and demo.build < install.client_build:
        raise VideoError(f"demo build {demo.build} is older than the installed client {install.client_build}; "
                         "Deadlock refuses to play it")
    return match, demo


# --------------------------------------------------------------------------- record


@dataclass
class ClipResult:
    sequence: Sequence
    path: Path | None
    duration_s: float
    error: str | None = None


def record(install: SteamInstall, db: Database, match_id: int, sequences: list[Sequence], settings: RecordSettings,
           log: Progress, cancel: threading.Event | None = None,
           on_progress: Callable[[int, int, str], None] | None = None) -> list[ClipResult]:
    if not sequences:
        raise VideoError("no sequences to record")
    log_path, file_log = open_probe_log()
    ui_log = log

    def log(msg: str) -> None:  # noqa: F811 - shadow on purpose: tee to file + UI
        file_log(msg)
        ui_log(msg)

    log(f"Recording log: {log_path}")
    match, demo = _resolve(db, match_id, install)
    caps = Capabilities.load()
    backend = settings.backend
    if backend == "auto":
        backend = "engine" if caps and caps.startmovie_works else "window"
    if backend == "engine" and caps and caps.startmovie_works is False:
        log("Engine recorder was reported unavailable by the probe; trying anyway")
    out_dir = settings.output_dir / str(match_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = demo_console_name(demo.path)
    # window capture wants a bordered window: -noborder makes the engine ignore -w/-h and span the desktop
    opts = launcher.LaunchOptions(width=settings.width, height=settings.height, windowed=settings.windowed,
                                  borderless=(backend != "window"), vcon_port=settings.vcon_port,
                                  unlock_movie=(backend == "engine"))
    results: list[ClipResult] = []
    with GameSession(install, opts, log, cancel, settings.launch_mode) as s:
        s.play_demo(name)
        # both are archived convars: remember the player's values and put them back before quitting
        previous = s.set_convars({"engine_no_focus_sleep": "0", "fps_max": "0"})
        log("Convars before filming: " + ", ".join(f"{k}={v}" for k, v in previous.items()))
        rect = launcher.find_window_for_pid(s.proc.pid) if backend in ("screen", "window") else None
        if backend in ("screen", "window") and rect is None:
            raise VideoError("could not find the game window for capture")
        if rect is not None:
            log(f"Game window client area at ({rect.left},{rect.top}) {rect.width}x{rect.height}, "
                f"frame offset ({rect.frame_left},{rect.frame_top})")
        try:
            _record_sequences(s, sequences, match, demo, settings, backend, caps, rect, out_dir, results, log,
                              cancel, on_progress)
        finally:
            s.restore_convars(previous)
            s.set_hud(True)
    ok = [r.path for r in results if r.path]
    if settings.concat and len(ok) > 1:
        final = out_dir / f"match_{match_id}_{int(time.time())}.mp4"
        log("Concatenating clips …")
        encode.concat(ok, final)
        results.append(ClipResult(Sequence(match_id, 0, sum(r.duration_s for r in results), "All clips"), final,
                                  encode.probe_duration(final)))
    return results


def _record_sequences(s: GameSession, sequences: list[Sequence], match, demo, settings: RecordSettings, backend: str,
                      caps: Capabilities | None, rect, out_dir: Path, results: list[ClipResult], log: Progress,
                      cancel: threading.Event | None, on_progress) -> None:
    install = s.install
    tick_rate = match.tick_rate or 64
    for i, seq in enumerate(sequences):
        if on_progress:
            on_progress(i, len(sequences), seq.label or f"clip {i + 1}")
        log(f"--- Sequence {i + 1}/{len(sequences)}: {seq.label} [{seq.start_s:.0f}s – {seq.end_s:.0f}s]")
        clip_name = f"clip{i + 1:02d}"
        start_tick, end_tick = seq.start_tick(match), seq.end_tick(match)
        try:
            s.console.send("demo_pause")
            s.seek(start_tick, pause=True)
            reached = s.wait_until_tick(start_tick - 2 * tick_rate, timeout=120)
            if reached < 0 or abs(reached - start_tick) > 10 * tick_rate:
                log(f"warning: seek landed at tick {reached}, wanted {start_tick}")
            time.sleep(1.5)
            s.spectate(seq, caps)
            s.set_hud(not settings.hide_hud and seq.hud)
            s.console.send(f"demo_timescale {seq.timescale}")
            time.sleep(0.5)
            if backend == "engine":
                rec = EngineRecorder(s.console, launcher.movie_dir(install), settings.fps, settings.width,
                                     settings.height)
                rec.start(clip_name)
                s.console.send("demo_resume")
                expected = int(seq.duration_s * settings.fps / max(seq.timescale, 0.01))
                deadline = time.monotonic() + seq.duration_s * 20 + 60
                while rec.frames_written(clip_name) < expected and time.monotonic() < deadline:
                    if cancel and cancel.is_set():
                        raise Cancelled("cancelled")
                    time.sleep(0.5)
                files = rec.stop()
                s.console.send("demo_pause")
                path = _encode_engine_clip(files, clip_name, out_dir, settings, expected)
            elif backend == "window":
                wrec = WindowRecorder(rect, settings.fps, settings.quality)  # type: ignore[arg-type]
                wrec.start(out_dir / f"{clip_name}.mp4")
                s.console.send("demo_resume")
                s.wait_until_tick(end_tick, timeout=seq.duration_s / max(seq.timescale, 0.01) + 30)
                s.console.send("demo_pause")
                files = wrec.stop()
                log(f"window capture: {wrec.frames_in} frames from the game, {wrec.frames_out} written")
                path = files.video
            else:
                srec = ScreenRecorder(rect, settings.fps, settings.quality)  # type: ignore[arg-type]
                launcher.bring_to_front(rect.hwnd)  # type: ignore[union-attr]
                srec.start(out_dir / f"{clip_name}.mp4")
                s.console.send("demo_resume")
                s.wait_until_tick(end_tick, timeout=seq.duration_s / max(seq.timescale, 0.01) + 30)
                s.console.send("demo_pause")
                files = srec.stop()
                path = files.video
            dur = encode.probe_duration(path) if path and path.exists() else 0.0
            results.append(ClipResult(seq, path, dur))
            log(f"Clip written: {path} ({dur:.1f}s)")
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            log(f"Sequence failed: {exc}")
            results.append(ClipResult(seq, None, 0.0, str(exc)))


def _encode_engine_clip(files: ClipFiles, clip_name: str, out_dir: Path, settings: RecordSettings,
                        expected_frames: int) -> Path:
    if not files.frame_pattern or files.frame_count == 0:
        raise VideoError("engine recorder produced no frames")
    out = out_dir / f"{clip_name}.mp4"
    encode.encode_frames(files.frame_pattern, settings.fps, out, wav=files.wav,
                         max_frames=max(1, min(expected_frames, files.frame_count)), quality=settings.quality)
    if not settings.keep_frames and files.frames_dir:
        for f in files.frames_dir.glob(f"{clip_name}*"):
            try:
                f.unlink()
            except OSError:
                pass
    return out
