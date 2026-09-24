"""Queue replay downloads from the GUI."""

from __future__ import annotations

import time
from pathlib import Path

from deaddemo.core.api.client import ReplayUnavailable
from deaddemo.core.api.service import demo_url_for
from deaddemo.core.download.downloader import DownloadCancelled, DownloadProgress, download_demo
from deaddemo.core.download.service import demo_filename
from deaddemo.gui.context import AppContext


def start_download(ctx: AppContext, match_id: int) -> bool:
    name = f"download:{match_id}"
    if ctx.jobs.is_running(name) or match_id in ctx.downloads.active_match_ids():
        return False
    dest_dir = ctx.settings.resolved_download_dir()
    dest = dest_dir / demo_filename(match_id)
    if dest.exists():
        ctx.status(f"Match {match_id} is already in {dest_dir}")
        return False
    # The URL is resolved inside the worker: it can mean a deadlock-api call or a Steam GC round trip
    # (45 s handshake + 20 s per match), which used to freeze the GUI when done here.
    row = ctx.downloads.create(match_id, "", str(dest))
    ctx.downloads.set_status(row.id, "running")
    ctx.events.downloads_changed.emit()
    resolved: dict[str, str] = {}
    db = ctx.db
    use_gc = ctx.settings.use_steam_gc

    def work(progress, cancel):
        progress(0, 0, "Resolving replay URL")
        url = demo_url_for(match_id, db=db, use_gc=use_gc)
        resolved["url"] = url
        last_bytes, last_at = [0], [0.0]

        def on_progress(p: DownloadProgress) -> None:
            now = time.monotonic()
            finished = bool(p.bytes_total) and p.bytes_downloaded == p.bytes_total
            if finished or (p.bytes_downloaded - last_bytes[0] > 4 << 20 and now - last_at[0] >= 0.5):
                last_bytes[0], last_at[0] = p.bytes_downloaded, now
                progress(p.bytes_downloaded, p.bytes_total or 0, f"{p.bytes_downloaded / 1e6:.0f} MB")

        return download_demo(url, dest, progress=on_progress, cancel=cancel)

    def on_progress(done: int, total: int, _msg: str) -> None:
        if not done and not total:
            return  # "resolving" notice, nothing to draw yet
        ctx.downloads.update_progress(row.id, done, total or None)
        ctx.events.download_progress.emit(row.id, done, total)  # the Downloads page repaints one row

    def on_finished(path: Path) -> None:
        if resolved.get("url"):
            ctx.downloads.set_url(row.id, resolved["url"])
        ctx.downloads.set_status(row.id, "done")
        ctx.events.downloads_changed.emit()
        ctx.status(f"Downloaded match {match_id}")
        _register_and_parse(ctx, match_id, path)

    def on_failed(err: str) -> None:
        first = err.splitlines()[0]
        if resolved.get("url"):
            ctx.downloads.set_url(row.id, resolved["url"])
        if ReplayUnavailable.__name__ in first:
            status = "failed"
            first = first.split(":", 1)[1].strip() if ":" in first else first
        else:
            status = "cancelled" if "cancelled" in first.lower() or DownloadCancelled.__name__ in first else "failed"
        ctx.downloads.set_status(row.id, status, error=first[:400])
        ctx.events.downloads_changed.emit()
        ctx.status(f"Download {status} for {match_id}: {first}", 12000)

    ctx.jobs.submit(name, work, on_finished=on_finished, on_failed=on_failed, on_progress=on_progress)
    ctx.status(f"Downloading match {match_id} …")
    return True


def cancel_download(ctx: AppContext, match_id: int) -> None:
    job = ctx.jobs.active.get(f"download:{match_id}")
    if job:
        job.cancel()


def _register_and_parse(ctx: AppContext, match_id: int, path: Path) -> None:
    from deaddemo.core.replays.scanner import sniff_header

    rf = sniff_header(path, "download")
    row = ctx.demos.upsert_found(
        path=str(path), source="download", size_bytes=rf.size_bytes, mtime=rf.mtime, match_id=rf.match_id or match_id,
        build=rf.build, map_name=rf.map_name, tick_rate=rf.tick_rate, total_ticks=rf.total_ticks,
        status=rf.status, error=rf.error,
    )
    ctx.events.demos_changed.emit()
    if ctx.settings.auto_parse_downloads and row.status == "found":
        from deaddemo.gui.parse_jobs import start_parse

        start_parse(ctx, row)
