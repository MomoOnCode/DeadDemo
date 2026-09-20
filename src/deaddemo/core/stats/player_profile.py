"""Cross-match player profile: everything the Player page shows, from SQLite only."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import HistoryRepo, HistoryRow
from deaddemo.core.stats.extras import EXTRA_COLUMNS

STEAMID64_BASE = 76561197960265728
LANE_NAMES = {1: "Left", 4: "Mid", 6: "Right"}


def _div(a: float, b: float) -> float:
    return a / b if b else 0.0


@dataclass
class HeroStat:
    hero_id: int
    games: int
    wins: int
    kills: float
    deaths: float
    assists: float
    souls_per_min: float

    @property
    def winrate(self) -> float:
        return _div(self.wins, self.games)

    @property
    def kda(self) -> float:
        return _div(self.kills + self.assists, max(self.deaths, 1.0))


@dataclass
class PersonStat:
    steam_id: int
    name: str
    hero_id: int | None
    count: int
    wins: int = 0

    @property
    def winrate(self) -> float:
        return _div(self.wins, self.count)


@dataclass
class RecentMatch:
    match_id: int
    start_time: int | None
    hero_id: int | None
    won: bool | None
    parsed: bool
    duration_s: int | None
    kda: str


@dataclass
class PlayerProfile:
    steam_id: int
    account_id: int
    name: str
    # history-based (all matches deadlock-api knows), None when unavailable
    hist_matches: int | None = None
    hist_wins: int | None = None
    hist_kda: tuple[float, float, float] | None = None
    hist_souls_per_min: float | None = None
    # parsed-demo based
    games: int = 0
    wins: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    kp: float = 0.0
    souls_per_min: float = 0.0
    hero_dmg_per_min: float = 0.0
    lh_per_min: float = 0.0
    denies_per_min: float = 0.0
    time_dead_pct: float = 0.0
    avg_level: float = 0.0
    minutes_played: float = 0.0
    extras: dict[str, int] = field(default_factory=dict)  # summed EXTRA_COLUMNS
    extras_games: int = 0
    heroes: list[HeroStat] = field(default_factory=list)
    lanes: dict[str, tuple[int, int]] = field(default_factory=dict)  # lane -> (games, wins)
    nemesis: list[tuple[int, int]] = field(default_factory=list)  # (hero_id, deaths to)
    victims: list[tuple[int, int]] = field(default_factory=list)  # (hero_id, kills of)
    teammates: list[PersonStat] = field(default_factory=list)
    opponents: list[PersonStat] = field(default_factory=list)
    top_items: list[tuple[int, int]] = field(default_factory=list)  # (item_id, count)
    first_buys: list[tuple[int, int]] = field(default_factory=list)
    items_by_hero: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    first_buys_by_hero: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    ranks: list[tuple[int, int]] = field(default_factory=list)  # (start_time, badge)
    recent: list[RecentMatch] = field(default_factory=list)
    per_match: list[dict] = field(default_factory=list)  # chronological rows for trend graphs

    @property
    def winrate(self) -> float:
        return _div(self.wins, self.games)

    @property
    def kd(self) -> float:
        return _div(self.kills, max(self.deaths, 1))

    @property
    def kda(self) -> float:
        return _div(self.kills + self.assists, max(self.deaths, 1))

    @property
    def headshot_pct(self) -> float | None:
        hits = self.extras.get("bullet_hits", 0)
        return _div(self.extras.get("headshot_hits", 0), hits) if hits else None

    def per_game(self, key: str) -> float:
        return _div(self.extras.get(key, 0), self.extras_games)


def build_profile(db: Database, steam_id: int, *, history_account_id: int | None = None) -> PlayerProfile:
    conn = db.conn
    account_id = steam_id - STEAMID64_BASE
    rows = conn.execute(
        """
        SELECT mp.*, m.regulation_seconds, m.parsed_at, m.winning_team, m.match_id AS mid
        FROM match_players mp JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.steam_id = ? ORDER BY m.parsed_at ASC
        """,
        (steam_id,),
    ).fetchall()
    name = rows[-1]["player_name"] if rows else ""
    p = PlayerProfile(steam_id=steam_id, account_id=account_id, name=name or str(steam_id))

    # -- parsed summaries -----------------------------------------------------------------
    p.games = len(rows)
    minutes = 0.0
    kp_sum, kp_n, dead_s, level_sum = 0.0, 0, 0.0, 0.0
    lh = den = dmg = 0
    heroes: dict[int, HeroStat] = {}
    lanes: dict[str, list[int]] = {}
    for r in rows:
        mins = (r["regulation_seconds"] or 0) / 60.0
        minutes += mins
        won = bool(r["won"])
        p.wins += int(won)
        p.kills += r["kills"] or 0
        p.deaths += r["deaths"] or 0
        p.assists += r["assists"] or 0
        lh += r["last_hits"] or 0
        den += r["denies"] or 0
        dmg += r["hero_damage"] or 0
        if r["kill_participation"] is not None:
            kp_sum += r["kill_participation"]
            kp_n += 1
        dead_s += r["time_dead_s"] or 0
        level_sum += r["level"] or 0
        h = heroes.setdefault(r["hero_id"], HeroStat(r["hero_id"], 0, 0, 0, 0, 0, 0))
        h.games += 1
        h.wins += int(won)
        h.kills += r["kills"] or 0
        h.deaths += r["deaths"] or 0
        h.assists += r["assists"] or 0
        h.souls_per_min += _div(r["souls"] or 0, mins) if mins else 0
        lane = LANE_NAMES.get(r["start_lane"], f"Lane {r['start_lane']}")
        lanes.setdefault(lane, [0, 0])
        lanes[lane][0] += 1
        lanes[lane][1] += int(won)
        p.per_match.append({
            "match_id": r["mid"], "parsed_at": r["parsed_at"], "hero_id": r["hero_id"], "won": won,
            "kills": r["kills"] or 0, "deaths": r["deaths"] or 0, "assists": r["assists"] or 0,
            "souls_per_min": _div(r["souls"] or 0, mins), "dmg_per_min": _div(r["hero_damage"] or 0, mins),
            "kp": r["kill_participation"] or 0.0, "minutes": mins,
        })
    p.minutes_played = minutes
    p.souls_per_min = _div(sum(r["souls"] or 0 for r in rows), minutes)
    p.hero_dmg_per_min = _div(dmg, minutes)
    p.lh_per_min = _div(lh, minutes)
    p.denies_per_min = _div(den, minutes)
    p.kp = _div(kp_sum, kp_n)
    p.time_dead_pct = _div(dead_s, minutes * 60)
    p.avg_level = _div(level_sum, p.games)
    for h in heroes.values():
        h.kills /= h.games
        h.deaths /= h.games
        h.assists /= h.games
        h.souls_per_min /= h.games
    p.heroes = sorted(heroes.values(), key=lambda h: (-h.games, -h.winrate))
    p.lanes = {k: (v[0], v[1]) for k, v in sorted(lanes.items(), key=lambda kv: -kv[1][0])}

    # -- extras ------------------------------------------------------------------------
    ex = conn.execute(
        """
        SELECT e.* FROM match_player_extras e JOIN match_players mp
          ON mp.match_id = e.match_id AND mp.hero_id = e.hero_id
        WHERE mp.steam_id = ?
        """,
        (steam_id,),
    ).fetchall()
    p.extras_games = len(ex)
    p.extras = {c: sum(int(r[c] or 0) for r in ex) for c in EXTRA_COLUMNS}
    p.extras["first_blood_attacker"] = sum(1 for r in ex if (r["first_blood"] or 0) == 1)
    p.extras["first_blood_victim"] = sum(1 for r in ex if (r["first_blood"] or 0) == -1)
    fb_ids = {r["match_id"] for r in ex if r["first_blood"]}
    p.extras["first_blood_wins"] = sum(1 for r in rows if r["mid"] in fb_ids and r["won"])

    # -- people: kills/deaths vs heroes, teammates/opponents ----------------------------
    mids = [r["mid"] for r in rows]
    if mids:
        my_hero = {r["mid"]: r["hero_id"] for r in rows}
        my_team = {r["mid"]: r["team_num"] for r in rows}
        my_won = {r["mid"]: bool(r["won"]) for r in rows}
        q = ",".join("?" for _ in mids)
        nemesis: Counter[int] = Counter()
        victims: Counter[int] = Counter()
        for k in conn.execute(f"SELECT match_id, attacker_hero_id, victim_hero_id FROM kills WHERE match_id IN ({q})",
                              mids).fetchall():
            h = my_hero[k["match_id"]]
            if k["victim_hero_id"] == h and k["attacker_hero_id"] is not None:
                nemesis[k["attacker_hero_id"]] += 1
            if k["attacker_hero_id"] == h and k["victim_hero_id"] is not None:
                victims[k["victim_hero_id"]] += 1
        p.nemesis = nemesis.most_common(5)
        p.victims = victims.most_common(5)
        mates: dict[int, PersonStat] = {}
        opps: dict[int, PersonStat] = {}
        for o in conn.execute(
            f"SELECT match_id, steam_id, player_name, hero_id, team_num FROM match_players "
            f"WHERE match_id IN ({q}) AND steam_id IS NOT NULL AND steam_id != ?", [*mids, steam_id]
        ).fetchall():
            bucket = mates if o["team_num"] == my_team[o["match_id"]] else opps
            ps = bucket.setdefault(o["steam_id"], PersonStat(o["steam_id"], o["player_name"] or "", o["hero_id"], 0))
            ps.count += 1
            ps.wins += int(my_won[o["match_id"]])
        p.teammates = sorted(mates.values(), key=lambda s: -s.count)[:8]
        p.opponents = sorted(opps.values(), key=lambda s: -s.count)[:8]
        items: Counter[int] = Counter()
        firsts: Counter[int] = Counter()
        items_by_hero: dict[int, Counter[int]] = {}
        firsts_by_hero: dict[int, Counter[int]] = {}
        per_match_first: dict[int, list[int]] = {}
        seen: set[tuple[int, int]] = set()  # (match, item): count an item once per game even if rebought
        for it in conn.execute(
            f"SELECT match_id, hero_id, ability_id, change, tick FROM item_purchases WHERE match_id IN ({q}) "
            f"AND change='purchased' ORDER BY tick", mids
        ).fetchall():
            hero = my_hero[it["match_id"]]
            if it["hero_id"] != hero or it["ability_id"] is None:
                continue
            if (it["match_id"], it["ability_id"]) not in seen:
                seen.add((it["match_id"], it["ability_id"]))
                items[it["ability_id"]] += 1
                items_by_hero.setdefault(hero, Counter())[it["ability_id"]] += 1
            lst = per_match_first.setdefault(it["match_id"], [])
            if len(lst) < 3:
                lst.append(it["ability_id"])
                firsts[it["ability_id"]] += 1
                firsts_by_hero.setdefault(hero, Counter())[it["ability_id"]] += 1
        p.top_items = items.most_common(10)
        p.first_buys = firsts.most_common(5)
        p.items_by_hero = {h: c.most_common(10) for h, c in items_by_hero.items()}
        p.first_buys_by_hero = {h: c.most_common(5) for h, c in firsts_by_hero.items()}

    # -- history (deadlock-api) --------------------------------------------------------
    hist_id = history_account_id if history_account_id is not None else account_id
    hist: list[HistoryRow] = HistoryRepo(db).for_account(hist_id)
    parsed_ids = {r["mid"] for r in rows}
    if hist:
        p.hist_matches = len(hist)
        wins = sum(1 for h in hist if h.match_result is not None and h.match_result == h.player_team)
        p.hist_wins = wins
        n = len(hist)
        p.hist_kda = (_div(sum(h.player_kills or 0 for h in hist), n), _div(sum(h.player_deaths or 0 for h in hist), n),
                      _div(sum(h.player_assists or 0 for h in hist), n))
        total_min = sum((h.match_duration_s or 0) for h in hist) / 60.0
        p.hist_souls_per_min = _div(sum(h.net_worth or 0 for h in hist), total_min)
        p.ranks = [(h.start_time or 0, h.ranked_display_badge) for h in hist if h.ranked_display_badge]
        p.ranks.sort()
        for h in hist[:12]:
            won = h.match_result == h.player_team if h.match_result is not None and h.player_team is not None else None
            p.recent.append(RecentMatch(h.match_id, h.start_time, h.hero_id, won, h.match_id in parsed_ids,
                                        h.match_duration_s, f"{h.player_kills or 0}/{h.player_deaths or 0}/"
                                        f"{h.player_assists or 0}"))
    else:
        for r in reversed(rows[-12:]):
            p.recent.append(RecentMatch(r["mid"], None, r["hero_id"], bool(r["won"]), True,
                                        int(r["regulation_seconds"] or 0),
                                        f"{r['kills'] or 0}/{r['deaths'] or 0}/{r['assists'] or 0}"))
    return p


def profile_to_rows(p: PlayerProfile) -> list[tuple[str, str]]:
    """Flat (label, value) pairs for export."""
    rows = [
        ("Player", p.name), ("SteamID64", str(p.steam_id)), ("Parsed games", str(p.games)),
        ("Win rate (parsed)", f"{p.winrate * 100:.1f}%"), ("K/D", f"{p.kd:.2f}"), ("KDA", f"{p.kda:.2f}"),
        ("Kill participation", f"{p.kp * 100:.0f}%"), ("Souls/min", f"{p.souls_per_min:.0f}"),
        ("Hero dmg/min", f"{p.hero_dmg_per_min:.0f}"), ("Last hits/min", f"{p.lh_per_min:.2f}"),
        ("Denies/min", f"{p.denies_per_min:.2f}"), ("Time dead", f"{p.time_dead_pct * 100:.1f}%"),
        ("Headshot %", f"{(p.headshot_pct or 0) * 100:.1f}%"),
    ]
    for c in EXTRA_COLUMNS:
        rows.append((c, str(p.extras.get(c, 0))))
    if p.hist_matches is not None:
        rows.append(("History matches", str(p.hist_matches)))
        rows.append(("History wins", str(p.hist_wins)))
    return rows


def dumps(p: PlayerProfile) -> str:
    return json.dumps({k: v for k, v in profile_to_rows(p)}, indent=1)
