"""Cross-match player aggregates computed with SQL over ``match_players``."""

from __future__ import annotations

from dataclasses import dataclass

from deaddemo.core.db.database import Database


@dataclass
class PlayerAgg:
    steam_id: int
    player_name: str
    games: int
    wins: int
    kills: float
    deaths: float
    assists: float
    souls_per_min: float
    top_heroes: list[int]
    last_seen: str | None

    @property
    def winrate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    @property
    def kda(self) -> float:
        return (self.kills + self.assists) / max(self.deaths, 1.0)


def player_overview(db: Database, min_games: int = 1) -> list[PlayerAgg]:
    rows = db.conn.execute(
        """
        SELECT mp.steam_id,
               COUNT(*) AS games,
               SUM(COALESCE(mp.won, 0)) AS wins,
               AVG(mp.kills) AS kills, AVG(mp.deaths) AS deaths, AVG(mp.assists) AS assists,
               AVG(CASE WHEN m.regulation_seconds > 0 THEN mp.souls * 60.0 / m.regulation_seconds END) AS spm,
               MAX(m.parsed_at) AS last_seen
        FROM match_players mp JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.steam_id IS NOT NULL
        GROUP BY mp.steam_id HAVING COUNT(*) >= ?
        ORDER BY games DESC
        """,
        (min_games,),
    ).fetchall()
    names = {
        r["steam_id"]: r["player_name"]
        for r in db.conn.execute(
            "SELECT mp.steam_id, mp.player_name FROM match_players mp JOIN matches m ON m.match_id=mp.match_id "
            "WHERE mp.steam_id IS NOT NULL ORDER BY m.parsed_at ASC"
        ).fetchall()
    }
    heroes: dict[int, list[int]] = {}
    for r in db.conn.execute(
        "SELECT steam_id, hero_id, COUNT(*) AS n FROM match_players WHERE steam_id IS NOT NULL "
        "GROUP BY steam_id, hero_id ORDER BY n DESC"
    ).fetchall():
        heroes.setdefault(int(r["steam_id"]), []).append(int(r["hero_id"]))
    return [
        PlayerAgg(
            steam_id=int(r["steam_id"]),
            player_name=names.get(r["steam_id"]) or "",
            games=int(r["games"]),
            wins=int(r["wins"] or 0),
            kills=float(r["kills"] or 0),
            deaths=float(r["deaths"] or 0),
            assists=float(r["assists"] or 0),
            souls_per_min=float(r["spm"] or 0),
            top_heroes=heroes.get(int(r["steam_id"]), [])[:3],
            last_seen=r["last_seen"],
        )
        for r in rows
    ]


def matches_for_player(db: Database, steam_id: int) -> list[dict]:
    rows = db.conn.execute(
        """
        SELECT m.match_id, m.map_name, m.regulation_seconds, m.parsed_at, mp.hero_id, mp.team_num,
               mp.kills, mp.deaths, mp.assists, mp.souls, mp.won
        FROM match_players mp JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.steam_id = ? ORDER BY m.parsed_at DESC
        """,
        (steam_id,),
    ).fetchall()
    return [dict(r) for r in rows]
