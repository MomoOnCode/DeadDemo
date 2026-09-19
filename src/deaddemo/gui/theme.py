"""Colors and formatting helpers shared by GUI pages."""

from __future__ import annotations

from PySide6.QtGui import QColor

# Deadlock team numbers: 2 = Amber Hand, 3 = Sapphire Flame.
TEAM_NAMES = {2: "Amber", 3: "Sapphire"}
TEAM_COLORS = {2: QColor("#e0a03c"), 3: QColor("#4a90e2")}
TEAM_COLORS_DIM = {2: QColor("#7a5a24"), 3: QColor("#2b5382")}

STATUS_COLORS = {
    "found": QColor("#3c3c3c"),
    "parsed": QColor("#245c2e"),
    "partial": QColor("#7a5a24"),
    "error": QColor("#7a2424"),
    "missing": QColor("#555555"),
}


def team_name(team_num: int | None) -> str:
    return TEAM_NAMES.get(team_num or -1, f"Team {team_num}")


def team_color(team_num: int | None) -> QColor:
    return TEAM_COLORS.get(team_num or -1, QColor("#888888"))


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return ""
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"


def fmt_clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(round(seconds))
    sign = "-" if s < 0 else ""
    s = abs(s)
    return f"{sign}{s // 60}:{s % 60:02d}"


def fmt_souls(n: int | None) -> str:
    if n is None:
        return ""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
