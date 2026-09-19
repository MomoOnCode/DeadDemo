"""Read demo header metadata via boon without loading any dataset."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DemoHeader:
    match_id: int | None
    build: int | None
    map_name: str | None
    tick_rate: int | None
    total_ticks: int | None
    game_mode: int | None


def _opt_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def read_header(path: Path) -> DemoHeader:
    from boon import Demo

    demo = Demo(str(path))
    return DemoHeader(
        match_id=_opt_int(getattr(demo, "match_id", None)),
        build=_opt_int(getattr(demo, "build", None)),
        map_name=getattr(demo, "map_name", None),
        tick_rate=_opt_int(getattr(demo, "tick_rate", None)),
        total_ticks=_opt_int(getattr(demo, "total_ticks", None)),
        game_mode=_opt_int(getattr(demo, "game_mode", None)),
    )
