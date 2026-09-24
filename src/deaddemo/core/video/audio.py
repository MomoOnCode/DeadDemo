"""Per-process game audio via WASAPI process loopback (Windows 10 build 20348+).

Only what the target process (deadlock.exe) renders is captured; Discord, music and notifications stay
out of the clip. The COM plumbing lives in ``audio_com`` and is imported on the capture thread only, so
importing this module never touches COM (and tests can exercise the structs and the pump without it).

Output is raw interleaved signed 16-bit PCM at ``SAMPLE_RATE``/``CHANNELS`` (see ``AudioTrack``); the
recorder muxes it into the clip with ffmpeg afterwards.
"""

from __future__ import annotations

import ctypes
import importlib.util
import sys
import threading
import time
from ctypes import c_int, c_ulong, c_ushort, c_void_p
from dataclasses import dataclass
from pathlib import Path

MIN_BUILD = 20348
SAMPLE_RATE, CHANNELS, BITS = 48000, 2, 16
FRAME_BYTES = CHANNELS * BITS // 8

AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
VT_BLOB = 65
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
AUDCLNT_E_UNSUPPORTED_FORMAT = 0x88890008
WAVE_FORMAT_PCM = 1
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"


class AudioCaptureError(RuntimeError):
    pass


# --------------------------------------------------------------------------- structures


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", c_ushort), ("nChannels", c_ushort), ("nSamplesPerSec", c_ulong),
        ("nAvgBytesPerSec", c_ulong), ("nBlockAlign", c_ushort), ("wBitsPerSample", c_ushort), ("cbSize", c_ushort),
    ]


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [("TargetProcessId", c_ulong), ("ProcessLoopbackMode", c_int)]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", c_int), ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]


class PROPVARIANT(ctypes.Structure):
    """Only the VT_BLOB shape is modelled: vt, 3 reserved words, then BLOB {cbSize, pBlobData}."""

    _fields_ = [("vt", c_ushort), ("wReserved1", c_ushort), ("wReserved2", c_ushort), ("wReserved3", c_ushort),
                ("cbSize", c_ulong), ("pBlobData", c_void_p)]


def wave_format(rate: int = SAMPLE_RATE, channels: int = CHANNELS, bits: int = BITS) -> WAVEFORMATEX:
    block = channels * bits // 8
    return WAVEFORMATEX(WAVE_FORMAT_PCM, channels, rate, rate * block, block, bits, 0)


def activation_params(pid: int) -> tuple[AUDIOCLIENT_ACTIVATION_PARAMS, PROPVARIANT]:
    """Both objects are returned because the PROPVARIANT points into the params; keep them alive together."""
    params = AUDIOCLIENT_ACTIVATION_PARAMS()
    params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
    params.ProcessLoopbackParams.TargetProcessId = pid
    params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
    pv = PROPVARIANT()
    pv.vt = VT_BLOB
    pv.cbSize = ctypes.sizeof(params)
    pv.pBlobData = ctypes.addressof(params)
    return params, pv


def is_supported() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "process audio capture needs Windows"
    build = getattr(sys.getwindowsversion(), "build", 0)
    if build < MIN_BUILD:
        return False, f"process audio capture needs Windows build {MIN_BUILD}+ (this is {build})"
    if importlib.util.find_spec("comtypes") is None:
        return False, "the comtypes package is not installed"
    return True, ""


# --------------------------------------------------------------------------- capture


@dataclass
class AudioTrack:
    path: Path  # raw s16le interleaved PCM
    sample_rate: int
    channels: int
    frames: int  # PCM frames written
    lead_s: float  # audio seconds captured before video t=0 (>0: trim; <0: audio started late, pad)


def pump(stream, fh, stop: threading.Event, frame_bytes: int = FRAME_BYTES, wait_ms: int = 200) -> int:
    """Drain ``stream`` into ``fh`` until ``stop`` is set. The stream yields (data, frames, flags) packets;
    SILENT packets carry no data and are written as zeros so the timeline stays continuous."""
    written = 0
    while not stop.is_set():
        stream.wait(wait_ms)
        for data, frames, flags in stream.packets():
            if flags & AUDCLNT_BUFFERFLAGS_SILENT or not data:
                fh.write(b"\x00" * (frames * frame_bytes))
            else:
                fh.write(data)
            written += frames
    return written


class ProcessAudioCapture:
    """Record one process's audio to a raw PCM file on a dedicated thread.

    ``start()`` returns once the WASAPI stream is running (``started_at`` is stamped right after
    ``IAudioClient.Start()``); ``stop(video_started_at)`` ends it and reports the lead time relative to
    the video's first frame. Any failure surfaces as ``AudioCaptureError`` from ``start()`` or as
    ``error`` after ``stop()``; callers degrade to a silent clip."""

    def __init__(self, pid: int, out: Path, *, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.pid = pid
        self.out = out
        self.sample_rate = sample_rate
        self.channels = channels
        self.frames = 0
        self.started_at: float | None = None
        self.error: str | None = None
        self.warnings: list[str] = []
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name="process-audio", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout + 5.0):
            self._stop.set()
            raise AudioCaptureError("audio capture did not start in time")
        if self.error:
            raise AudioCaptureError(self.error)

    def _run(self) -> None:
        stream = None
        com = None
        try:
            from deaddemo.core.video import audio_com as com  # noqa: PLC0415 - COM init happens on this thread

            com.initialize_thread()
            stream = com.LoopbackStream(self.pid, self.sample_rate, self.channels)
            stream.open()
            self.sample_rate = stream.sample_rate  # may have fallen back to 44100
            self.started_at = time.perf_counter()
            self._ready.set()
            self.out.parent.mkdir(parents=True, exist_ok=True)
            with self.out.open("wb", buffering=1 << 16) as fh:
                self.frames = pump(stream, fh, self._stop, self.channels * BITS // 8)
        except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised on this thread
            self.error = f"{type(exc).__name__}: {exc}"
            self._ready.set()
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            if com is not None:
                com.uninitialize_thread()

    def stop(self, video_started_at: float | None = None) -> AudioTrack | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self.error or self.frames == 0 or self.started_at is None:
            if not self.error:
                self.error = "no audio frames were captured"
            self.out.unlink(missing_ok=True)
            return None
        elapsed = time.perf_counter() - self.started_at
        expected = elapsed * self.sample_rate
        if expected > 0 and abs(self.frames - expected) / expected > 0.02:
            self.warnings.append(f"audio delivered {self.frames / self.sample_rate:.1f}s for {elapsed:.1f}s of wall "
                                 "time")
        lead = (video_started_at - self.started_at) if video_started_at is not None else 0.0
        return AudioTrack(self.out, self.sample_rate, self.channels, self.frames, lead)
