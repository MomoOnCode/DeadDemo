"""Discover replay files on disk and keep the ``demos`` table in sync."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from deaddemo.core.db.repos import DemoRepo

DEMO_MAGIC = b"PBDEMS2\0"
_MATCH_ID_RE = re.compile(r"(\d{6,})")


@dataclass
class ReplayFile:
    path: Path
    source: str
    size_bytes: int
    mtime: float
    is_partial: bool
    match_id: int | None = None
    build: int | None = None
    map_name: str | None = None
    tick_rate: int | None = None
    total_ticks: int | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        if self.is_partial:
            return "partial"
        if self.error:
            return "error"
        return "found"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path"] = str(self.path)
        d["status"] = self.status
        return d


@dataclass
class ScanReport:
    added: int = 0
    updated: int = 0
    missing: int = 0
    sniffed: int = 0


def match_id_from_name(path: Path) -> int | None:
    m = _MATCH_ID_RE.search(path.name)
    return int(m.group(1)) if m else None


def has_demo_magic(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(len(DEMO_MAGIC)) == DEMO_MAGIC
    except OSError:
        return False


def sniff_header(path: Path, source: str) -> ReplayFile:
    """Cheap per-file inspection: magic check, then boon's header read for build/map/match id."""
    stat = path.stat()
    is_partial = path.name.endswith(".partial")
    rf = ReplayFile(path=path, source=source, size_bytes=stat.st_size, mtime=stat.st_mtime,
                    is_partial=is_partial, match_id=match_id_from_name(path))
    if is_partial:
        return rf
    if not has_demo_magic(path):
        rf.error = "not a Source 2 demo (bad magic)"
        return rf
    try:
        from deaddemo.core.parse.header import read_header

        hdr = read_header(path)
    except Exception as exc:  # noqa: BLE001 - any parser failure is reported, never raised
        rf.error = f"header: {type(exc).__name__}: {exc}"[:300]
        return rf
    rf.match_id = hdr.match_id or rf.match_id
    rf.build = hdr.build
    rf.map_name = hdr.map_name
    rf.tick_rate = hdr.tick_rate
    rf.total_ticks = hdr.total_ticks
    return rf


def list_candidates(
    replay_dirs: list[tuple[str, Path]],
    extra_dirs: list[Path] = (),
    download_dir: Path | None = None,
) -> list[tuple[str, Path]]:
    seen: set[Path] = set()
    out: list[tuple[str, Path]] = []
    dirs: list[tuple[str, Path]] = list(replay_dirs)
    if download_dir and download_dir.is_dir():
        dirs.append(("download", download_dir))
    dirs += [("manual", d) for d in extra_dirs if d.is_dir()]
    for source, d in dirs:
        for p in sorted(d.glob("*.dem")) + sorted(d.glob("*.dem.partial")):
            rp = p.resolve()
            if rp in seen or not p.is_file():
                continue
            seen.add(rp)
            out.append((source, p))
    return out


def scan(
    replay_dirs: list[tuple[str, Path]],
    extra_dirs: list[Path] = (),
    download_dir: Path | None = None,
    known: dict[str, tuple[int, float]] | None = None,
    progress=None,
) -> list[ReplayFile]:
    """Scan folders. ``known`` maps path -> (size, mtime) for files whose header we may skip."""
    found: list[ReplayFile] = []
    candidates = list_candidates(replay_dirs, extra_dirs, download_dir)
    for i, (source, p) in enumerate(candidates):
        if progress:
            progress(i, len(candidates), p.name)
        found.append(sniff_header(p, source))
    return found


def sync_to_db(repo: DemoRepo, found: list[ReplayFile]) -> ScanReport:
    report = ScanReport()
    present: set[str] = set()
    for rf in found:
        key = str(rf.path)
        present.add(key)
        existing = repo.by_path(key)
        if existing is None:
            report.added += 1
        elif existing.size_bytes != rf.size_bytes or existing.mtime != rf.mtime or existing.status == "missing":
            report.updated += 1
        repo.upsert_found(
            path=key, source=rf.source, size_bytes=rf.size_bytes, mtime=rf.mtime, match_id=rf.match_id,
            build=rf.build, map_name=rf.map_name, tick_rate=rf.tick_rate, total_ticks=rf.total_ticks,
            status=rf.status, error=rf.error,
        )
    report.missing = repo.mark_missing(present)
    return report


def scan_and_sync(repo: DemoRepo, replay_dirs, extra_dirs=(), download_dir=None, progress=None) -> ScanReport:
    """Scan, skipping the header read for unchanged files already in the database."""
    candidates = list_candidates(replay_dirs, extra_dirs, download_dir)
    found: list[ReplayFile] = []
    report = ScanReport()
    for i, (source, p) in enumerate(candidates):
        if progress:
            progress(i, len(candidates), p.name)
        stat = p.stat()
        existing = repo.by_path(str(p))
        if (existing is not None and existing.size_bytes == stat.st_size and existing.mtime == stat.st_mtime
                and existing.status not in ("missing", "error")):
            rf = ReplayFile(path=p, source=source, size_bytes=stat.st_size, mtime=stat.st_mtime,
                            is_partial=existing.status == "partial", match_id=existing.match_id,
                            build=existing.build, map_name=existing.map_name, tick_rate=existing.tick_rate,
                            total_ticks=existing.total_ticks)
        else:
            rf = sniff_header(p, source)
            report.sniffed += 1
        found.append(rf)
    sub = sync_to_db(repo, found)
    report.added, report.updated, report.missing = sub.added, sub.updated, sub.missing
    return report
