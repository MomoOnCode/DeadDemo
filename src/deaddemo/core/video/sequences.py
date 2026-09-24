"""Clip sequences: which ticks of a demo to film, and whom to watch."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import MatchRepo, MatchRow
from deaddemo.core.stats.extras import MULTI_KILL_WINDOW_S
from deaddemo.core.stats.match_stats import scan

KINDS = ("kills", "bursts", "deaths", "multikills", "first_blood", "teamfights", "objectives", "midboss")
CAMERAS = ("chase", "in_eye", "free")


@dataclass
class Sequence:
    match_id: int
    start_s: float
    end_s: float
    label: str = ""
    focus_hero_id: int | None = None
    focus_name: str | None = None
    focus_account_id: int | None = None
    camera: str = "chase"
    hud: bool = False
    timescale: float = 1.0
    id: int | None = None
    kind: str = "manual"

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def start_tick(self, match: MatchRow) -> int:
        return int((match.game_start_tick or 0) + self.start_s * (match.tick_rate or 64))

    def end_tick(self, match: MatchRow) -> int:
        return int((match.game_start_tick or 0) + self.end_s * (match.tick_rate or 64))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GenerateOptions:
    kinds: tuple[str, ...] = ("kills", "multikills", "teamfights")
    lead_in_s: float = 6.0
    lead_out_s: float = 3.0
    merge_gap_s: float = 2.0
    chain_kills_s: float = 10.0  # a kill this soon after the previous one extends the same clip
    burst_min_events: int = 2  # "bursts": at least this many of my kills + assists ...
    burst_window_s: float = 10.0  # ... each within this many seconds of the previous one
    min_teamfight_kills: int = 2
    max_sequences: int = 40
    camera: str = "chase"
    hud: bool = False


def chain_events(events: list[dict], window_s: float) -> list[list[dict]]:
    """Group time-sorted events so that each one within ``window_s`` of the previous joins its chain."""
    chains: list[list[dict]] = []
    for e in sorted(events, key=lambda k: float(k["match_seconds"] or 0)):
        t = float(e["match_seconds"] or 0)
        if chains and t - float(chains[-1][-1]["match_seconds"] or 0) <= window_s:
            chains[-1].append(e)
        else:
            chains.append([e])
    return chains


def _merge(ranges: list[tuple[float, float, str]], gap: float) -> list[tuple[float, float, str]]:
    if not ranges:
        return []
    ranges.sort()
    out = [list(ranges[0])]
    for s, e, label in ranges[1:]:
        if s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
            if label not in out[-1][2]:
                out[-1][2] = f"{out[-1][2]} + {label}"
        else:
            out.append([s, e, label])
    return [(s, e, lbl) for s, e, lbl in out]


def generate(db: Database, match: MatchRow, hero_id: int, opts: GenerateOptions, hero_name) -> list[Sequence]:
    repo = MatchRepo(db)
    players = {p.hero_id: p for p in repo.players(match.match_id)}
    me = players.get(hero_id)
    my_team = me.team_num if me else None
    regulation = float(match.regulation_seconds or 0)
    kills = [dict(r) for r in repo.kills(match.match_id)]
    ranges: list[tuple[float, float, str]] = []

    def add(t: float, label: str, before: float | None = None, after: float | None = None) -> None:
        s = max(0.0, t - (opts.lead_in_s if before is None else before))
        e = t + (opts.lead_out_s if after is None else after)
        if regulation:
            e = min(e, regulation)
        ranges.append((s, e, label))

    my_kills = [k for k in kills if k["attacker_hero_id"] == hero_id]
    if "kills" in opts.kinds:
        for chain in chain_events(my_kills, opts.chain_kills_s):
            victims = [hero_name(k["victim_hero_id"]) for k in chain]
            first, last = float(chain[0]["match_seconds"] or 0), float(chain[-1]["match_seconds"] or 0)
            label = f"Kill on {victims[0]}" if len(chain) == 1 else "Kills on " + ", ".join(victims)
            add(first, label, after=(last - first) + opts.lead_out_s)
    if "bursts" in opts.kinds:
        # moments of heavy involvement: my kills and my assists, two or more in quick succession
        def assisted(k: dict) -> bool:
            ids = k.get("assister_hero_ids") or "[]"
            return hero_id in (json.loads(ids) if isinstance(ids, str) else ids)

        involved = [dict(k, mine=(k["attacker_hero_id"] == hero_id)) for k in kills
                    if k["attacker_hero_id"] == hero_id or assisted(k)]
        for chain in chain_events(involved, opts.burst_window_s):
            if len(chain) < opts.burst_min_events:
                continue
            n_kills = sum(1 for k in chain if k["mine"])
            n_assists = len(chain) - n_kills
            parts = [f"{n_kills} kill{'s' if n_kills != 1 else ''}"] if n_kills else []
            parts += [f"{n_assists} assist{'s' if n_assists != 1 else ''}"] if n_assists else []
            first, last = float(chain[0]["match_seconds"] or 0), float(chain[-1]["match_seconds"] or 0)
            add(first, "Burst: " + " + ".join(parts), after=(last - first) + opts.lead_out_s)
    if "deaths" in opts.kinds:
        for k in kills:
            if k["victim_hero_id"] == hero_id:
                add(float(k["match_seconds"] or 0), f"Death to {hero_name(k['attacker_hero_id'])}")
    if "multikills" in opts.kinds and my_kills:
        times = sorted(float(k["match_seconds"] or 0) for k in my_kills)
        cluster = [times[0]]
        for t in times[1:]:
            if t - cluster[-1] <= MULTI_KILL_WINDOW_S:
                cluster.append(t)
            else:
                if len(cluster) >= 2:
                    ranges.append((max(0.0, cluster[0] - opts.lead_in_s), cluster[-1] + opts.lead_out_s,
                                   f"{len(cluster)}K"))
                cluster = [t]
        if len(cluster) >= 2:
            ranges.append((max(0.0, cluster[0] - opts.lead_in_s), cluster[-1] + opts.lead_out_s, f"{len(cluster)}K"))
    if "first_blood" in opts.kinds and kills:
        fb = min(kills, key=lambda k: k["tick"])
        if hero_id in (fb["attacker_hero_id"], fb["victim_hero_id"]):
            add(float(fb["match_seconds"] or 0), "First blood")
    if "teamfights" in opts.kinds:
        lf = scan(match, "teamfights")
        if lf is not None:
            tick_rate = match.tick_rate or 64
            start_tick = match.game_start_tick or 0
            for f in lf.collect().to_dicts():
                parts = [int(p) for p in (f.get("participants") or [])]
                if hero_id not in parts or int(f.get("kills") or 0) < opts.min_teamfight_kills:
                    continue
                s = (int(f["start_tick"]) - start_tick) / tick_rate
                e = (int(f["end_tick"]) - start_tick) / tick_rate
                ranges.append((max(0.0, s - 3.0), e + opts.lead_out_s, f"Teamfight ({f.get('kills', 0)} kills)"))
    if "objectives" in opts.kinds or "midboss" in opts.kinds:
        for o in repo.objective_events(match.match_id):
            is_mid = o["objective_type"] == "mid_boss"
            if is_mid and "midboss" not in opts.kinds:
                continue
            if not is_mid and "objectives" not in opts.kinds:
                continue
            if not is_mid and my_team is not None and o["team_num"] == my_team:
                continue  # our own structure falling is not a highlight
            add(float(o["match_seconds"] or 0), "Mid boss" if is_mid else f"{o['objective_type'].title()} destroyed",
                before=10.0)
    merged = _merge(ranges, opts.merge_gap_s)[: opts.max_sequences]
    return [
        Sequence(match.match_id, s, e, label, hero_id, me.player_name if me else None,
                 (me.steam_id - 76561197960265728) if me and me.steam_id else None, opts.camera, opts.hud,
                 kind="auto")
        for s, e, label in merged
    ]


# --------------------------------------------------------------------------- persistence


class SequenceRepo:
    def __init__(self, db: Database):
        self.db = db

    def for_match(self, match_id: int) -> list[Sequence]:
        rows = self.db.conn.execute(
            "SELECT * FROM sequences WHERE match_id=? ORDER BY sort, start_s", (match_id,)
        ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r) -> Sequence:
        return Sequence(r["match_id"], r["start_s"], r["end_s"], r["label"] or "", r["focus_hero_id"],
                        r["focus_name"], r["focus_account_id"], r["camera"] or "chase", bool(r["hud"]),
                        float(r["timescale"] or 1.0), r["id"], r["kind"] or "manual")

    def replace_all(self, match_id: int, seqs: list[Sequence]) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sequences WHERE match_id=?", (match_id,))
            conn.executemany(
                "INSERT INTO sequences(match_id, label, start_s, end_s, focus_hero_id, focus_name, focus_account_id,"
                " camera, hud, timescale, sort, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(match_id, s.label, s.start_s, s.end_s, s.focus_hero_id, s.focus_name, s.focus_account_id,
                  s.camera, int(s.hud), s.timescale, i, s.kind) for i, s in enumerate(seqs)],
            )

    def add(self, seq: Sequence) -> Sequence:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO sequences(match_id, label, start_s, end_s, focus_hero_id, focus_name, focus_account_id,"
                " camera, hud, timescale, sort, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (seq.match_id, seq.label, seq.start_s, seq.end_s, seq.focus_hero_id, seq.focus_name,
                 seq.focus_account_id, seq.camera, int(seq.hud), seq.timescale, 9999, seq.kind),
            )
            seq.id = int(cur.lastrowid)
        return seq

    def update(self, seq: Sequence) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE sequences SET label=?, start_s=?, end_s=?, focus_hero_id=?, focus_name=?, focus_account_id=?,"
                " camera=?, hud=?, timescale=? WHERE id=?",
                (seq.label, seq.start_s, seq.end_s, seq.focus_hero_id, seq.focus_name, seq.focus_account_id,
                 seq.camera, int(seq.hud), seq.timescale, seq.id),
            )

    def delete(self, seq_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sequences WHERE id=?", (seq_id,))


@dataclass
class VideoRow:
    id: int
    match_id: int
    path: str
    sequence_ids: list[int]
    width: int
    height: int
    fps: int
    backend: str
    duration_s: float
    created_at: str
    error: str | None = None
    label: str = ""
    extra: dict = field(default_factory=dict)


class VideoRepo:
    def __init__(self, db: Database):
        self.db = db

    def all(self) -> list[VideoRow]:
        rows = self.db.conn.execute("SELECT * FROM videos ORDER BY id DESC").fetchall()
        return [VideoRow(r["id"], r["match_id"], r["path"], json.loads(r["sequence_ids"] or "[]"), r["width"],
                         r["height"], r["fps"], r["backend"], float(r["duration_s"] or 0), r["created_at"],
                         r["error"], r["label"] or "") for r in rows]

    def add(self, match_id: int, path: str, sequence_ids: list[int], width: int, height: int, fps: int,
            backend: str, duration_s: float, label: str = "", error: str | None = None) -> int:
        from deaddemo.core.db.database import utcnow_iso

        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO videos(match_id, path, sequence_ids, width, height, fps, backend, duration_s,"
                " created_at, error, label) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (match_id, path, json.dumps(sequence_ids), width, height, fps, backend, duration_s, utcnow_iso(),
                 error, label),
            )
            return int(cur.lastrowid)

    def delete(self, video_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
