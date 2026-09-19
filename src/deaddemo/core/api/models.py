"""Typed views over deadlock-api.com responses. Unknown keys are ignored so API additions never break us."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any


def _pick(cls, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class MatchHistoryEntry:
    account_id: int
    match_id: int
    hero_id: int | None = None
    hero_level: int | None = None
    start_time: int | None = None
    game_mode: int | None = None
    match_mode: int | None = None
    player_team: int | None = None
    player_kills: int | None = None
    player_deaths: int | None = None
    player_assists: int | None = None
    denies: int | None = None
    net_worth: int | None = None
    last_hits: int | None = None
    team_abandoned: bool | None = None
    abandoned_time_s: int | None = None
    match_duration_s: int | None = None
    match_result: int | None = None
    player_match_outcome: int | None = None
    ranked_display_badge: int | None = None
    ranked_delta: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchHistoryEntry:
        return cls(**_pick(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def won(self) -> bool | None:
        """``match_result`` is the winning team index (0/1); compare with ``player_team``."""
        if self.match_result is None or self.player_team is None:
            return None
        return self.match_result == self.player_team

    def start_iso(self) -> str:
        if not self.start_time:
            return ""
        return datetime.fromtimestamp(self.start_time).strftime("%Y-%m-%d %H:%M")

    def age_days(self) -> float | None:
        if not self.start_time:
            return None
        return (datetime.now(UTC).timestamp() - self.start_time) / 86400

    def kda(self) -> str:
        return f"{self.player_kills or 0}/{self.player_deaths or 0}/{self.player_assists or 0}"


@dataclass
class MatchSalts:
    match_id: int
    cluster_id: int | None = None
    metadata_salt: int | None = None
    replay_salt: int | None = None
    metadata_url: str | None = None
    demo_url: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchSalts:
        return cls(**_pick(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def available(self) -> bool:
        return bool(self.demo_url)


@dataclass
class Hero:
    id: int
    name: str
    class_name: str = ""
    player_selectable: bool = True
    disabled: bool = False
    in_development: bool = False
    images: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hero:
        d = _pick(cls, data)
        d["images"] = data.get("images") or {}
        return cls(**d)


@dataclass
class Item:
    id: int
    name: str
    class_name: str = ""
    type: str = ""
    image: str | None = None
    item_slot_type: str | None = None
    item_tier: int | None = None
    cost: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Item:
        d = _pick(cls, data)
        if d.get("cost") is not None:
            try:
                d["cost"] = int(d["cost"])
            except (TypeError, ValueError):
                d["cost"] = None
        return cls(**d)


@dataclass
class MapInfo:
    radius: float
    images: dict[str, str] = field(default_factory=dict)
    objective_positions: dict[str, dict[str, float]] = field(default_factory=dict)
    zipline_paths: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MapInfo:
        return cls(
            radius=float(data.get("radius") or 10752),
            images=data.get("images") or {},
            objective_positions=data.get("objective_positions") or {},
            zipline_paths=data.get("zipline_paths") or [],
        )
