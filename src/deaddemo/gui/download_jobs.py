"""Queue replay downloads from the GUI."""

from __future__ import annotations

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
    try:
        url = demo_url_for(match_id, db=ctx.db, use_gc=ctx.settings.use_steam_gc)
    except ReplayUnavailable as exc:
        ctx.status(str(exc), 12000)
        return False
    except Exception as exc:  # noqa: BLE001
        ctx.status(f"Could not resolve replay URL for {match_id}: {exc}", 12000)
        return False
    row = ctx.downloads.create(match_id, url, str(dest))
    ctx.downloads.set_status(row.id, "running")
    ctx.events.downloads_changed.emit()

    def work(progress, cancel):
        last = [0]

        def on_progress(p: DownloadProgress) -> None:
            if p.bytes_downloaded - last[0] > 4 << 20 or (p.bytes_total and p.bytes_downloaded == p.bytes_total):
                last[0] = p.bytes_downloaded
                progress(p.bytes_downloaded, p.bytes_total or 0, f"{p.bytes_downloaded / 1e6:.0f} MB")

        return download_demo(url, dest, progress=on_progress, cancel=cancel)

    def on_progress(done: int, total: int, _msg: str) -> None:
        ctx.downloads.update_progress(row.id, done, total or None)
        ctx.events.downloads_changed.emit()

    def on_finished(path: Path) -> None:
        ctx.downloads.set_status(row.id, "done")
        ctx.events.downloads_changed.emit()
        ctx.status(f"Downloaded match {match_id}")
        _register_and_parse(ctx, match_id, path)

    def on_failed(err: str) -> None:
        first = err.splitlines()[0]
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
