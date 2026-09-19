from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtGui import QColor

from deaddemo.core.db.repos import HistoryRow
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import fmt_clock, fmt_souls


@dataclass
class MatchListRow:
    history: HistoryRow
    hero_name: str
    local: bool
    parsed: bool
    downloading: bool
    salts_known: bool | None  # None = not checked yet

    @property
    def match_id(self) -> int:
        return self.history.match_id

    @property
    def won(self) -> bool | None:
        h = self.history
        if h.match_result is None or h.player_team is None:
            return None
        return h.match_result == h.player_team

    @property
    def state(self) -> str:
        if self.parsed:
            return "parsed"
        if self.local:
            return "local"
        if self.downloading:
            return "downloading"
        if self.salts_known is False:
            return "no replay"
        return ""


def _fmt_time(v: int | None) -> str:
    return datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M") if v else ""


def _fmt_age(v: int | None) -> str:
    if not v:
        return ""
    days = (datetime.now().timestamp() - v) / 86400
    return f"{days:.0f} d" if days >= 1 else f"{days * 24:.0f} h"


class MatchesModel(RowTableModel):
    def __init__(self, parent=None):
        super().__init__(
            [
                Column("Date", lambda r: r.history.start_time, _fmt_time),
                Column("Age", lambda r: r.history.start_time, _fmt_age, align_right=True,
                       sort_key=lambda r: -(r.history.start_time or 0)),
                Column("Match", lambda r: r.match_id, align_right=True),
                Column("Hero", lambda r: r.hero_name),
                Column("Result", lambda r: r.won, lambda v: "" if v is None else ("Win" if v else "Loss")),
                Column("K/D/A", lambda r: r.history.player_kills or 0,
                       lambda v: "", align_right=True),
                Column("Souls", lambda r: r.history.net_worth, fmt_souls, align_right=True),
                Column("Duration", lambda r: r.history.match_duration_s, fmt_clock, align_right=True),
                Column("Mode", lambda r: r.history.game_mode, lambda v: {1: "Normal", 4: "Brawl"}.get(v, str(v))),
                Column("Status", lambda r: r.state),
            ],
            parent,
        )
        # K/D/A needs the whole row; patch the formatter via a closure over the getter.
        self.columns[5] = Column("K/D/A", lambda r: r, lambda r: _kda(r), align_right=True,
                                 sort_key=lambda r: r.history.player_kills or 0)

    def row_background(self, row: MatchListRow) -> QColor | None:
        if row.parsed:
            return QColor("#245c2e")
        if row.local:
            return QColor("#2f4f6f")
        if row.downloading:
            return QColor("#5c4a24")
        if row.salts_known is False:
            return QColor("#4a2a2a")
        return None

    def row_foreground(self, row: MatchListRow) -> QColor | None:
        if row.won is True:
            return QColor("#c8f0c8")
        if row.won is False:
            return QColor("#f0c8c8")
        return None


def _kda(r: MatchListRow) -> str:
    h = r.history
    return f"{h.player_kills or 0}/{h.player_deaths or 0}/{h.player_assists or 0}"
