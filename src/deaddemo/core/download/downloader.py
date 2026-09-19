"""Stream a ``.dem.bz2`` replay from Valve's servers straight into a decompressed ``.dem``."""

from __future__ import annotations

import bz2
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from deaddemo.core.replays.scanner import DEMO_MAGIC

CHUNK = 1 << 20
PART_SUFFIX = ".deaddemo-part"


class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


@dataclass
class DownloadProgress:
    bytes_downloaded: int
    bytes_total: int | None
    bytes_written: int


ProgressFn = Callable[[DownloadProgress], None]


def download_demo(
    url: str,
    dest: Path,
    *,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    client: httpx.Client | None = None,
) -> Path:
    """Download ``url`` and write the decompressed demo to ``dest`` atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + PART_SUFFIX)
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, read=120.0), follow_redirects=True,
                                    headers={"User-Agent": "DeadDemo"})
    downloaded = written = 0
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code == 404:
                raise DownloadError(f"replay not found on server (404): {url}")
            if resp.status_code != 200:
                raise DownloadError(f"HTTP {resp.status_code} for {url}")
            total = int(resp.headers.get("Content-Length") or 0) or None
            decomp: bz2.BZ2Decompressor | None = None
            first = True
            with part.open("wb") as out:
                for chunk in resp.iter_bytes(CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    if not chunk:
                        continue
                    if first:
                        first = False
                        if chunk[:3] == b"BZh":
                            decomp = bz2.BZ2Decompressor()
                        elif chunk[: len(DEMO_MAGIC)] != DEMO_MAGIC:
                            raise DownloadError("server response is neither bzip2 nor a Source 2 demo")
                    downloaded += len(chunk)
                    data = decomp.decompress(chunk) if decomp else chunk
                    if data:
                        out.write(data)
                        written += len(data)
                    if progress:
                        progress(DownloadProgress(downloaded, total, written))
                out.flush()
                os.fsync(out.fileno())
        with part.open("rb") as fh:
            if fh.read(len(DEMO_MAGIC)) != DEMO_MAGIC:
                raise DownloadError("downloaded file is not a valid demo (bad magic)")
        os.replace(part, dest)
        return dest
    except BaseException:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        raise
    finally:
        if own_client:
            client.close()
