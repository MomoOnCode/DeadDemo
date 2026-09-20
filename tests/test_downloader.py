import bz2
import threading

import httpx
import pytest
import zstandard

from deaddemo.core.download.downloader import DownloadCancelled, DownloadError, download_demo
from deaddemo.core.replays.scanner import DEMO_MAGIC

PAYLOAD = DEMO_MAGIC + bytes(range(256)) * 4000  # ~1 MB fake demo


def _client(body: bytes, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers={"Content-Length": str(len(body))})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "body",
    [zstandard.ZstdCompressor().compress(PAYLOAD), bz2.compress(PAYLOAD), PAYLOAD],
    ids=["zstd", "bzip2", "raw"],
)
def test_download_decompresses_any_container(tmp_path, body):
    dest = tmp_path / "1.dem"
    seen = []
    out = download_demo("http://x/1.dem.bz2", dest, client=_client(body), progress=seen.append)
    assert out == dest and dest.read_bytes() == PAYLOAD
    assert seen and seen[-1].bytes_written == len(PAYLOAD)
    assert not list(tmp_path.glob("*part*"))


def test_download_rejects_unknown_container(tmp_path):
    with pytest.raises(DownloadError):
        download_demo("http://x/1.dem.bz2", tmp_path / "1.dem", client=_client(b"<html>nope</html>"))
    assert not list(tmp_path.iterdir())


def test_download_404(tmp_path):
    with pytest.raises(DownloadError):
        download_demo("http://x/1.dem.bz2", tmp_path / "1.dem", client=_client(b"", 404))


def test_download_cancel(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        download_demo("http://x/1.dem.bz2", tmp_path / "1.dem", client=_client(PAYLOAD), cancel=cancel)
    assert not list(tmp_path.iterdir())
