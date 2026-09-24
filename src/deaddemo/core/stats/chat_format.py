"""Render a match's chat rows as team-colored HTML (Qt-free so it can be unit tested)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class ChatPlayer:
    name: str
    team_num: int | None


def _clock(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def chat_html(rows: list[dict], players: Mapping[int, ChatPlayer], hero_name: Callable[[int | None], str],
              colors: Mapping[int, str], dim_colors: Mapping[int, str], team_names: Mapping[int, str]) -> str:
    """``rows`` are chat.parquet records (match_seconds, hero_id, chat_type, text). Player name and team
    come from ``players`` keyed by hero id; unknown heroes fall back to the hero name in grey."""
    legend = " &nbsp; ".join(f"<b style='color:{colors[t]}'>{escape(team_names.get(t, str(t)))}</b>"
                             for t in sorted(colors))
    out = [f"<div style='color:#999; margin-bottom:6px'>{legend} &nbsp; "
           "<span style='color:#777'>[TEAM] = team chat</span></div>"]
    for r in rows:
        hero_id = r.get("hero_id")
        p = players.get(hero_id) if hero_id is not None else None
        team = p.team_num if p else None
        name = (p.name if p and p.name else hero_name(hero_id)) or "?"
        team_chat = r.get("chat_type") == "team"
        palette = dim_colors if team_chat else colors
        color = palette.get(team or -1, "#888888")
        tag = "<span style='color:#777'>[TEAM]</span> " if team_chat else ""
        out.append(f"<div><span style='color:#777'>[{_clock(r.get('match_seconds'))}]</span> {tag}"
                   f"<b style='color:{color}'>{escape(name)}</b>: "
                   f"<span style='color:{'#bbb' if team_chat else '#eee'}'>{escape(str(r.get('text') or ''))}"
                   "</span></div>")
    return "\n".join(out)
