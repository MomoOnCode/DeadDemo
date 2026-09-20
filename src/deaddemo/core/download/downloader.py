"""Stream a replay from Valve's servers straight into a decompressed ``.dem``.

Valve names the files ``.dem.bz2`` but (as of September 2026) serves **zstd** frames; older
files were genuinely bzip2. The container is sniffed from the first bytes, so all of zstd,
bzip2 and raw demos are accepted.
"""

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
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
BZIP2_MAGIC = b"BZh"


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


class _Passthrough:
    def decompress(self, data: bytes) -> bytes:
        return data


def make_decompressor(head: bytes):
    """Pick a streaming decompressor from the first bytes of the payload."""
    if head.startswith(ZSTD_MAGIC):
        import zstandard

        return zstandard.ZstdDecompressor().decompressobj()
    if head.startswith(BZIP2_MAGIC):
        return bz2.BZ2Decompressor()
    if head.startswith(DEMO_MAGIC):
        return _Passthrough()
    raise DownloadError(f"server response is not zstd, bzip2 or a Source 2 demo (starts with {head[:8]!r})")


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
            decomp = None
            with part.open("wb") as out:
                for chunk in resp.iter_bytes(CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    if not chunk:
                        continue
                    if decomp is None:
                        decomp = make_decompressor(chunk)
                    downloaded += len(chunk)
                    data = decomp.decompress(chunk)
                    if data:
                        out.write(data)
                        written += len(data)
                    if progress:
                        progress(DownloadProgress(downloaded, total, written))
                flush = getattr(decomp, "flush", None)
                if callable(flush):
                    tail = flush()
                    if tail:
                        out.write(tail)
                        written += len(tail)
                out.flush()
                os.fsync(out.fileno())
        with part.open("rb") as fh:
            if fh.read(len(DEMO_MAGIC)) != DEMO_MAGIC:
                raise DownloadError("downloaded file is not a valid demo (bad magic after decompression)")
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
