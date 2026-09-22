"""FFmpeg helpers (binary bundled by imageio-ffmpeg)."""

from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path


def ffmpeg_path() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
    return subprocess.run([ffmpeg_path(), "-hide_banner", "-y", *args], capture_output=True, text=True,
                          timeout=timeout, creationflags=creation, encoding="utf-8", errors="replace")


@functools.lru_cache(maxsize=1)
def capabilities() -> dict[str, bool]:
    enc = _run(["-encoders"]).stdout
    dev = _run(["-devices"]).stdout
    return {
        "h264_nvenc": "h264_nvenc" in enc,
        "hevc_nvenc": "hevc_nvenc" in enc,
        "libx264": "libx264" in enc,
        "ddagrab": "ddagrab" in dev or "ddagrab" in _run(["-filters"]).stdout,
        "gdigrab": "gdigrab" in dev,
    }


def video_codec_args(quality: str = "high", prefer_nvenc: bool = True) -> list[str]:
    caps = capabilities()
    if prefer_nvenc and caps.get("h264_nvenc"):
        cq = {"low": "30", "medium": "24", "high": "19", "max": "15"}.get(quality, "19")
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", cq, "-b:v", "0", "-pix_fmt", "yuv420p"]
    crf = {"low": "28", "medium": "23", "high": "18", "max": "14"}.get(quality, "18")
    return ["-c:v", "libx264", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p"]


def encode_frames(frame_pattern: str, fps: int, out: Path, *, wav: Path | None = None, max_frames: int | None = None,
                  start_number: int = 0, quality: str = "high") -> None:
    """``frame_pattern`` like ``C:/x/clip%08d.jpg``."""
    args = ["-framerate", str(fps), "-start_number", str(start_number), "-i", frame_pattern]
    if wav and wav.exists():
        args += ["-i", str(wav)]
    if max_frames:
        args += ["-frames:v", str(max_frames)]
    args += video_codec_args(quality)
    if wav and wav.exists():
        args += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    args += ["-movflags", "+faststart", str(out)]
    r = _run(args, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed: {r.stderr[-800:]}")


def screen_capture_args(rect, fps: int, out: Path, *, quality: str = "high") -> list[str]:
    """Real-time capture of a window rectangle via Desktop Duplication (Windows)."""
    caps = capabilities()
    if caps.get("ddagrab"):
        inp = ["-f", "lavfi", "-i",
               f"ddagrab=framerate={fps}:offset_x={rect.left}:offset_y={rect.top}:video_size={rect.width}x{rect.height}"]
        if caps.get("h264_nvenc"):
            # frames stay on the GPU all the way into NVENC
            return [*inp, "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq",
                    {"low": "30", "medium": "24", "high": "19", "max": "15"}.get(quality, "19"), "-b:v", "0",
                    "-movflags", "+faststart", str(out)]
        return [*inp, "-vf", "hwdownload,format=bgra", *video_codec_args(quality, prefer_nvenc=False),
                "-movflags", "+faststart", str(out)]
    return ["-f", "gdigrab", "-framerate", str(fps), "-offset_x", str(rect.left), "-offset_y", str(rect.top),
            "-video_size", f"{rect.width}x{rect.height}", "-i", "desktop", *video_codec_args(quality),
            "-movflags", "+faststart", str(out)]


def concat(files: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
    r = _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)],
             timeout=3600)
    try:
        lst.unlink()
    except OSError:
        pass
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {r.stderr[-800:]}")


def probe_duration(path: Path) -> float:
    r = _run(["-i", str(path)])
    import re

    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)
