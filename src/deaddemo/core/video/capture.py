"""Capture backends: the engine's own movie recorder, or real-time screen capture with ffmpeg."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deaddemo.core.video import encode
from deaddemo.core.video.audio import AudioCaptureError, AudioTrack, ProcessAudioCapture, is_supported
from deaddemo.core.video.console import ConsoleBase
from deaddemo.core.video.launcher import WindowRect


@dataclass
class ClipFiles:
    backend: str
    video: Path | None = None  # finished mp4 (screen backend)
    frames_dir: Path | None = None  # engine backend
    frame_pattern: str | None = None
    wav: Path | None = None
    frame_count: int = 0
    audio: AudioTrack | None = None  # window backend: raw PCM to mux into ``video``


class EngineRecorder:
    """``startmovie`` + ``host_framerate``: offline, frame-exact, includes audio (wav)."""

    name = "engine"

    def __init__(self, console: ConsoleBase, movie_root: Path, fps: int, width: int, height: int,
                 fmt: str = "jpg", jpeg_quality: int = 95):
        self.console = console
        self.movie_root = movie_root
        self.fps, self.width, self.height = fps, width, height
        self.fmt = fmt
        self.jpeg_quality = jpeg_quality
        self._name: str | None = None
        self._started_at = 0.0

    def start(self, clip_name: str) -> None:
        self._name = clip_name
        self.movie_root.mkdir(parents=True, exist_ok=True)
        opts = f"{self.fmt} wav framerate {self.fps} width {self.width} height {self.height} no_unique_dir"
        if self.fmt == "jpg":
            opts += f" jpeg_quality {self.jpeg_quality}"
        self.console.send(f"host_framerate {self.fps}")
        self.console.send(f"startmovie {clip_name} {opts}")
        self._started_at = time.monotonic()

    def stop(self) -> ClipFiles:
        self.console.send("endmovie")
        self.console.send("host_framerate 0")
        time.sleep(1.0)
        return self.locate(self._name or "")

    def locate(self, clip_name: str) -> ClipFiles:
        frames = sorted(self.movie_root.rglob(f"{clip_name}*.{self.fmt}"))
        wavs = sorted(self.movie_root.rglob(f"{clip_name}*.wav"))
        cf = ClipFiles(self.name, frames_dir=frames[0].parent if frames else None, frame_count=len(frames),
                       wav=wavs[0] if wavs else None)
        if frames:
            first = frames[0].name
            digits = len(first[len(clip_name):-len(self.fmt) - 1])
            cf.frame_pattern = str(frames[0].parent / f"{clip_name}%0{digits}d.{self.fmt}")
        return cf

    def frames_written(self, clip_name: str) -> int:
        return sum(1 for _ in self.movie_root.rglob(f"{clip_name}*.{self.fmt}"))


class ScreenRecorder:
    """ffmpeg desktop duplication of the game window, in real time."""

    name = "screen"

    def __init__(self, rect: WindowRect, fps: int, quality: str = "high"):
        self.rect = rect
        self.fps = fps
        self.quality = quality
        self.proc: subprocess.Popen | None = None
        self.out: Path | None = None

    def start(self, out: Path) -> None:
        self.out = out
        out.parent.mkdir(parents=True, exist_ok=True)
        args = [encode.ffmpeg_path(), "-hide_banner", "-y", *encode.screen_capture_args(self.rect, self.fps, out,
                                                                                            quality=self.quality)]
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=creation)

    def stop(self) -> ClipFiles:
        if self.proc is None:
            return ClipFiles(self.name)
        try:
            self.proc.stdin.write(b"q")  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass
        try:
            _, err = self.proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            _, err = self.proc.communicate()
        if self.proc.returncode not in (0, 255) and not (self.out and self.out.exists()):
            raise RuntimeError(f"screen capture failed: {err.decode('utf-8', 'replace')[-600:]}")
        return ClipFiles(self.name, video=self.out)


class WindowRecorder:
    """Windows Graphics Capture of one window, piped into ffmpeg as raw BGRA at a constant frame rate.

    Unlike desktop duplication this captures the window's own surface, so it keeps working while other
    windows cover the game (the player can keep using the PC). WGC delivers a frame only when the content
    changes, so a pacing thread re-sends the latest frame at exactly ``fps`` to keep real-time speed.
    The window must not be minimized.

    With ``audio_pid`` the process's own audio is captured alongside (WASAPI process loopback) and returned
    as ``ClipFiles.audio`` for the director to mux; any audio failure only logs a warning."""

    name = "window"

    def __init__(self, rect: WindowRect, fps: int, quality: str = "high", *, audio_pid: int | None = None,
                 log: Callable[[str], None] | None = None):
        self.rect = rect
        self.fps = fps
        self.quality = quality
        self.audio_pid = audio_pid
        self.log = log or (lambda _msg: None)
        self.proc: subprocess.Popen | None = None
        self.out: Path | None = None
        self.frames_in = 0
        self.frames_out = 0
        self.first_write_at: float | None = None
        self.audio: ProcessAudioCapture | None = None
        self._latest: bytes | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = threading.Event()
        self._first = threading.Event()
        self._control = None
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def _start_audio(self, out: Path) -> None:
        if self.audio_pid is None:
            return
        ok, why = is_supported()
        if not ok:
            self.log(f"warning: no game audio: {why}")
            return
        try:
            cap = ProcessAudioCapture(self.audio_pid, out.with_suffix(".pcm"))
            cap.start()
        except AudioCaptureError as exc:
            self.log(f"warning: no game audio: {exc}")
        except Exception as exc:  # noqa: BLE001 - audio must never break the video capture
            self.log(f"warning: no game audio: {type(exc).__name__}: {exc}")
        else:
            self.audio = cap

    def start(self, out: Path, first_frame_timeout: float = 10.0) -> None:
        from windows_capture import WindowsCapture

        self.out = out
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = self.rect.width, self.rect.height
        cx, cy = self.rect.frame_left, self.rect.frame_top
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=self.rect.hwnd)

        @cap.event
        def on_frame_arrived(frame, _control):  # runs on the capture thread; the buffer is only valid here
            buf = frame.frame_buffer
            if buf.shape[0] < cy + h or buf.shape[1] < cx + w:
                return  # window shrank; keep the last good frame
            data = buf[cy:cy + h, cx:cx + w].tobytes()
            with self._lock:
                self._latest = data
                self.frames_in += 1
            self._first.set()

        @cap.event
        def on_closed():
            self._closed.set()

        self._control = cap.start_free_threaded()
        if not self._first.wait(first_frame_timeout):
            self._control.stop()
            raise RuntimeError("window capture delivered no frame (is the game window minimized?)")
        self._start_audio(out)  # before ffmpeg spawns, so the audio normally leads the first video frame
        args = [encode.ffmpeg_path(), "-hide_banner", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}",
                "-framerate", str(self.fps), "-i", "-", *encode.video_codec_args(self.quality), "-movflags",
                "+faststart", str(out)]
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=creation)
        self._thread = threading.Thread(target=self._pace, name="window-capture-pacer", daemon=True)
        self._thread.start()

    def _pace(self) -> None:
        assert self.proc and self.proc.stdin
        period = 1.0 / self.fps
        next_at = time.monotonic()
        try:
            while not self._stop.is_set():
                with self._lock:
                    data = self._latest
                if data is not None:
                    if self.first_write_at is None:
                        self.first_write_at = time.perf_counter()  # video t=0, for audio alignment
                    self.proc.stdin.write(data)
                    self.frames_out += 1
                next_at += period
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -1.0:  # fell far behind (encoder stall); resync instead of bursting
                    next_at = time.monotonic()
        except (OSError, ValueError) as exc:
            self.error = f"pipe to ffmpeg broke: {exc}"

    def stop(self) -> ClipFiles:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        track: AudioTrack | None = None
        try:
            if self._control is not None:
                try:
                    self._control.stop()
                except Exception:  # noqa: BLE001 - the capture may already be gone with the window
                    pass
        finally:
            if self.audio is not None:
                track = self.audio.stop(self.first_write_at)
                if track is None:
                    self.log(f"warning: game audio dropped: {self.audio.error}")
                for w in self.audio.warnings:
                    self.log("warning: " + w)
        if self.proc is None:
            return ClipFiles(self.name, audio=track)
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        try:
            _, err = self.proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            _, err = self.proc.communicate()
        if self.error:
            raise RuntimeError(self.error + ": " + err.decode("utf-8", "replace")[-400:])
        if self.proc.returncode != 0 and not (self.out and self.out.exists()):
            raise RuntimeError(f"window capture encode failed: {err.decode('utf-8', 'replace')[-600:]}")
        return ClipFiles(self.name, video=self.out, frame_count=self.frames_out, audio=track)
