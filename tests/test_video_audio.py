import ctypes
import struct
import sys
import threading
from pathlib import Path

import pytest

from deaddemo.core.video import audio
from deaddemo.core.video.audio import (
    AUDCLNT_BUFFERFLAGS_SILENT,
    AUDIOCLIENT_ACTIVATION_PARAMS,
    AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS,
    PROPVARIANT,
    WAVEFORMATEX,
    AudioCaptureError,
    ProcessAudioCapture,
    activation_params,
    is_supported,
    pump,
    wave_format,
)
from deaddemo.core.video.encode import mux_audio_args


def test_struct_layouts_match_the_sdk():
    assert ctypes.sizeof(AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS) == 8
    assert ctypes.sizeof(AUDIOCLIENT_ACTIVATION_PARAMS) == 12
    assert ctypes.sizeof(WAVEFORMATEX) == 18
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(PROPVARIANT) == 24


def test_activation_propvariant_points_at_the_params_blob():
    params, pv = activation_params(4242)
    assert pv.vt == 65 and pv.cbSize == 12 and pv.pBlobData == ctypes.addressof(params)
    assert ctypes.string_at(pv.pBlobData, 12) == struct.pack("<IIi", 1, 4242, 0)


def test_wave_format():
    f = wave_format()
    assert (f.wFormatTag, f.nChannels, f.nSamplesPerSec) == (1, 2, 48000)
    assert f.nBlockAlign == 4 and f.nAvgBytesPerSec == 192000 and f.wBitsPerSample == 16 and f.cbSize == 0


class _FakeStream:
    def __init__(self, stop: threading.Event):
        self.stop = stop
        self.calls = 0

    def wait(self, _ms):
        return True

    def packets(self):
        self.calls += 1
        if self.calls == 1:
            yield b"\x01" * 8, 2, 0
            yield b"", 3, AUDCLNT_BUFFERFLAGS_SILENT
        else:
            self.stop.set()


def test_pump_writes_pcm_and_zeros_for_silence(tmp_path: Path):
    stop = threading.Event()
    out = tmp_path / "a.pcm"
    with out.open("wb") as fh:
        frames = pump(_FakeStream(stop), fh, stop, frame_bytes=4, wait_ms=1)
    assert frames == 5 and out.read_bytes() == b"\x01" * 8 + b"\x00" * 12


def test_capture_reports_thread_errors(tmp_path: Path, monkeypatch):
    def failing_run(self):
        self.error = "AudioCaptureError: nope"
        self._ready.set()

    monkeypatch.setattr(ProcessAudioCapture, "_run", failing_run)
    cap = ProcessAudioCapture(1234, tmp_path / "x.pcm")
    with pytest.raises(AudioCaptureError):
        cap.start()
    assert cap.stop() is None and not (tmp_path / "x.pcm").exists()


def test_stop_computes_lead_and_track(tmp_path: Path):
    cap = ProcessAudioCapture(1, tmp_path / "x.pcm")
    cap.started_at, cap.frames = 100.0, 48000
    (tmp_path / "x.pcm").write_bytes(b"\x00" * 8)
    track = cap.stop(video_started_at=100.25)
    assert track is not None and track.lead_s == pytest.approx(0.25) and track.frames == 48000


def test_is_supported_checks_build_and_comtypes(monkeypatch):
    if sys.platform != "win32":
        assert is_supported()[0] is False
        return

    class V:
        build = 19041

    monkeypatch.setattr(sys, "getwindowsversion", lambda: V())
    ok, why = is_supported()
    assert not ok and "20348" in why
    V.build = 26200
    monkeypatch.setattr(audio.importlib.util, "find_spec", lambda name: None)
    ok, why = is_supported()
    assert not ok and "comtypes" in why


def test_mux_audio_args():
    v, a, o = Path("c.mp4"), Path("c.pcm"), Path("c.mux.mp4")
    args = mux_audio_args(v, a, o, sample_rate=48000, channels=2, lead_s=0.25)
    assert args[:2] == ["-i", "c.mp4"]
    i = args.index("-f")
    assert args[i:i + 8] == ["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "c.pcm"]
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-af") + 1] == "atrim=start=0.2500,asetpts=PTS-STARTPTS"
    assert "-shortest" in args and args[args.index("-b:a") + 1] == "192k" and args[-1] == "c.mux.mp4"
    late = mux_audio_args(v, a, o, sample_rate=48000, channels=2, lead_s=-0.25)
    assert late[late.index("-af") + 1] == "adelay=250:all=1"
    assert "-af" not in mux_audio_args(v, a, o, sample_rate=48000, channels=2, lead_s=0.0)
