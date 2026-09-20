"""Read demo header metadata via boon without loading any dataset.

``build`` is the game version in the same numbering as ``steam.inf`` ``ClientVersion``. Valve
stores it in the header's ``game_directory`` (``.../citadel_v6686/citadel``); boon's ``build``
property is a different counter, so it is only a fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_GAME_DIR_RE = re.compile(rb"citadel_v(\d{3,6})")


@dataclass(frozen=True)
class DemoHeader:
    match_id: int | None
    build: int | None
    map_name: str | None
    tick_rate: int | None
    total_ticks: int | None
    game_mode: int | None
    boon_build: int | None = None


def _opt_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def client_version_from_header(path: Path) -> int | None:
    """Scan the first few KB of the demo for the ``citadel_v<version>`` game directory."""
    try:
        with path.open("rb") as fh:
            head = fh.read(8192)
    except OSError:
        return None
    m = _GAME_DIR_RE.search(head)
    return int(m.group(1)) if m else None


def read_header(path: Path) -> DemoHeader:
    from boon import Demo

    demo = Demo(str(path))
    boon_build = _opt_int(getattr(demo, "build", None))
    return DemoHeader(
        match_id=_opt_int(getattr(demo, "match_id", None)),
        build=client_version_from_header(path) or boon_build,
        map_name=getattr(demo, "map_name", None),
        tick_rate=_opt_int(getattr(demo, "tick_rate", None)),
        total_ticks=_opt_int(getattr(demo, "total_ticks", None)),
        game_mode=_opt_int(getattr(demo, "game_mode", None)),
        boon_build=boon_build,
    )
