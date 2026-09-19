"""Download a match replay by id: resolve salts, stream to the download folder, record it."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from deaddemo.core.api.client import DeadlockApiClient
from deaddemo.core.api.service import demo_url_for
from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import DownloadRepo
from deaddemo.core.download.downloader import DownloadProgress, download_demo


def demo_filename(match_id: int) -> str:
    return f"{match_id}.dem"


def download_match(
    match_id: int,
    *,
    dest_dir: Path | None = None,
    progress: Callable[[int, int | None], None] | None = None,
    cancel: threading.Event | None = None,
    db: Database | None = None,
    client: DeadlockApiClient | None = None,
) -> Path:
    from deaddemo.settings import Settings

    own_db = db is None
    db = db or Database.open()
    try:
        dest_dir = dest_dir or Settings.load().resolved_download_dir()
        dest = dest_dir / demo_filename(match_id)
        url = demo_url_for(match_id, db=db, client=client)
        repo = DownloadRepo(db)
        row = repo.create(match_id, url, str(dest))
        repo.set_status(row.id, "running")

        def _progress(p: DownloadProgress) -> None:
            if progress:
                progress(p.bytes_downloaded, p.bytes_total)

        try:
            download_demo(url, dest, progress=_progress, cancel=cancel)
        except Exception as exc:
            repo.set_status(row.id, "failed", error=str(exc)[:500])
            raise
        repo.set_status(row.id, "done")
        return dest
    finally:
        if own_db:
            db.close()
