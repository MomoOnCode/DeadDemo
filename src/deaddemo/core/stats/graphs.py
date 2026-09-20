"""Post-game graph series, mirroring the in-game "Graph Type" menu plus a few extras.

Everything is computed from the per-match Parquet files (``player_ticks``, ``damage``,
``healing``) sampled onto a fixed time grid so series line up and team sums are exact.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import polars as pl

from deaddemo.core.db.repos import MatchRow
from deaddemo.core.stats.match_stats import scan

DAMAGE_CATEGORY = {0: "Other", 1: "Bullet", 2: "Spirit", 3: "Melee", 4: "Environmental", 7: "Damage over time"}


@dataclass
class GraphSeries:
    key: str
    name: str
    xs: list[float]
    ys: list[float]
    team: int | None = None
    hero_id: int | None = None


@dataclass(frozen=True)
class GraphSpec:
    id: str
    label: str
    group: str  # 'General' | 'Player-Specific' | 'Extra'
    kind: str  # 'per_hero' (Player/Team toggle) | 'team_only' | 'single_hero'
    y_unit: str = ""  # '' | 'souls' | 'per_min' | 'count' | 'damage'
    description: str = ""


GRAPHS: list[GraphSpec] = [
    GraphSpec("souls_collected", "Souls Collected", "General", "per_hero", "souls", "Net worth over time"),
    GraphSpec("souls_per_minute", "Souls per Minute", "General", "per_hero", "per_min",
              "Net worth divided by minutes played"),
    GraphSpec("kills", "Kills", "General", "per_hero", "count"),
    GraphSpec("deaths", "Deaths", "General", "per_hero", "count"),
    GraphSpec("assists", "Assists", "General", "per_hero", "count"),
    GraphSpec("healing", "Healing", "General", "per_hero", "damage", "Healing done to heroes plus self healing"),
    GraphSpec("last_hits", "Lane Stats: Last Hits", "General", "per_hero", "count"),
    GraphSpec("denies", "Lane Stats: Denies", "General", "per_hero", "count"),
    GraphSpec("hero_damage", "Hero Damage", "General", "per_hero", "damage"),
    GraphSpec("objective_damage", "Objective Damage", "General", "per_hero", "damage"),
    GraphSpec("level", "Level", "General", "per_hero", "count"),
    GraphSpec("damage_breakdown", "Damage Breakdown", "Player-Specific", "single_hero", "damage",
              "Cumulative damage dealt, split by ability or item"),
    GraphSpec("damage_by_type", "Damage Dealt by Type", "Player-Specific", "single_hero", "damage",
              "Bullet / Spirit / Melee"),
    GraphSpec("damage_by_target", "Damage by Target", "Player-Specific", "single_hero", "damage",
              "Heroes vs everything else"),
    GraphSpec("healing_by_type", "Healing by Type", "Player-Specific", "single_hero", "damage",
              "Healing done, split by source ability or item"),
    GraphSpec("healing_received", "Healing Received by Source", "Player-Specific", "single_hero", "damage"),
    GraphSpec("damage_to_players", "Damage to Players", "Player-Specific", "single_hero", "damage"),
    GraphSpec("damage_from_players", "Damage from Players", "Player-Specific", "single_hero", "damage"),
    GraphSpec("damage_taken_by_type", "Damage Taken by Type", "Player-Specific", "single_hero", "damage"),
    GraphSpec("networth_lead", "Net Worth Lead", "Extra", "team_only", "souls",
              "Amber minus Sapphire; above zero means Amber is ahead"),
    GraphSpec("kill_lead", "Kill Lead", "Extra", "team_only", "count", "Amber kills minus Sapphire kills"),
    GraphSpec("rolling_souls", "Souls per Minute (rolling 2 min)", "Extra", "per_hero", "per_min",
              "Income rate over the previous two minutes"),
    GraphSpec("unspent_souls", "Unspent Souls", "Extra", "per_hero", "souls", "Souls sitting in the bank"),
    GraphSpec("time_dead", "Time Dead", "Extra", "per_hero", "count", "Cumulative seconds spent dead"),
]

GRAPHS_BY_ID = {g.id: g for g in GRAPHS}

PLAYER_TICK_STATS = {
    "souls_collected": "gold_net_worth",
    "kills": "kills",
    "deaths": "deaths",
    "assists": "assists",
    "last_hits": "last_hits",
    "denies": "denies",
    "hero_damage": "hero_damage",
    "objective_damage": "objective_damage",
    "level": "level",
    "unspent_souls": "souls",
}


# --------------------------------------------------------------------------- sampling helpers


def sampled_ticks(match: MatchRow, cols: list[str], every_s: float = 15.0) -> pl.DataFrame:
    """Per-hero values on a fixed grid: columns match_seconds, hero_id, *cols (forward-filled)."""
    lf = scan(match, "player_ticks")
    if lf is None:
        return pl.DataFrame()
    have = lf.collect_schema().names()
    cols = [c for c in cols if c in have]
    if not cols:
        return pl.DataFrame()
    sampled = (
        lf.select(["match_seconds", "hero_id", "tick", *cols])
        .filter(pl.col("match_seconds") >= 0)
        .with_columns(((pl.col("match_seconds") / every_s).floor() * every_s).alias("bucket"))
        .sort("tick")
        .group_by(["hero_id", "bucket"])
        .agg([pl.col(c).last() for c in cols])
        .collect()
    )
    if sampled.height == 0:
        return pl.DataFrame()
    heroes = sampled.select("hero_id").unique()
    buckets = sampled.select("bucket").unique()
    grid = heroes.join(buckets, how="cross")
    return (
        grid.join(sampled, on=["hero_id", "bucket"], how="left")
        .sort(["hero_id", "bucket"])
        .with_columns([pl.col(c).fill_null(strategy="forward").over("hero_id").fill_null(0) for c in cols])
        .rename({"bucket": "match_seconds"})
        .sort(["hero_id", "match_seconds"])
    )


def cumulative_events(
    lf: pl.LazyFrame,
    value_col: str,
    group_expr: pl.Expr,
    t_end: float,
    every_s: float = 15.0,
) -> pl.DataFrame:
    """Cumulative sum of ``value_col`` per group on a fixed grid: columns match_seconds, group, value."""
    ev = (
        lf.filter(pl.col("match_seconds") >= 0)
        .with_columns(group_expr.alias("group"))
        .with_columns(((pl.col("match_seconds") / every_s).floor() * every_s).alias("bucket"))
        .group_by(["group", "bucket"])
        .agg(pl.col(value_col).sum().alias("value"))
        .collect()
    )
    n = int(t_end // every_s) + 1
    buckets = pl.DataFrame({"bucket": [i * every_s for i in range(n)]})
    if ev.height == 0:
        return pl.DataFrame({"match_seconds": [], "group": [], "value": []})
    groups = ev.select("group").unique()
    grid = groups.join(buckets, how="cross")
    return (
        grid.join(ev, on=["group", "bucket"], how="left")
        .with_columns(pl.col("value").fill_null(0))
        .sort(["group", "bucket"])
        .with_columns(pl.col("value").cum_sum().over("group"))
        .rename({"bucket": "match_seconds"})
    )


def _fold_other(df: pl.DataFrame, max_groups: int = 7) -> pl.DataFrame:
    """Keep the biggest groups (by final value) and fold the rest into 'Other'."""
    finals = df.group_by("group").agg(pl.col("value").last()).sort("value", descending=True)
    if finals.height <= max_groups:
        return df
    keep = set(finals["group"].head(max_groups).to_list())
    return (
        df.with_columns(pl.when(pl.col("group").is_in(list(keep))).then(pl.col("group")).otherwise(pl.lit("Other")))
        .group_by(["group", "match_seconds"])
        .agg(pl.col("value").sum())
        .sort(["group", "match_seconds"])
    )


# --------------------------------------------------------------------------- series builders


@dataclass
class GraphContext:
    match: MatchRow
    team_of: dict[int, int]
    hero_name: Callable[[int | None], str]
    ability_name: Callable[[int | None], str]
    every_s: float = 15.0
    t_end: float = field(init=False)

    def __post_init__(self) -> None:
        self.t_end = float(self.match.regulation_seconds or 0) or 3600.0


def _per_hero_series(df: pl.DataFrame, col: str, ctx: GraphContext) -> list[GraphSeries]:
    out = []
    for hero_id, sub in df.group_by("hero_id", maintain_order=True):
        h = int(hero_id[0] if isinstance(hero_id, tuple) else hero_id)
        out.append(GraphSeries(f"hero:{h}", ctx.hero_name(h), sub["match_seconds"].to_list(), sub[col].to_list(),
                               ctx.team_of.get(h), h))
    out.sort(key=lambda s: (s.team or 0, s.hero_id or 0))
    return out


def _team_series(df: pl.DataFrame, col: str, ctx: GraphContext, agg: str = "sum") -> list[GraphSeries]:
    mapping = pl.DataFrame({"hero_id": list(ctx.team_of.keys()), "team_num": list(ctx.team_of.values())})
    joined = df.join(mapping, on="hero_id", how="inner")
    expr = pl.col(col).sum() if agg == "sum" else pl.col(col).mean()
    agg_df = joined.group_by(["team_num", "match_seconds"]).agg(expr.alias(col)).sort(["team_num", "match_seconds"])
    out = []
    for team, name in ((2, "Amber"), (3, "Sapphire")):
        sub = agg_df.filter(pl.col("team_num") == team)
        if sub.height:
            out.append(GraphSeries(f"team:{team}", name, sub["match_seconds"].to_list(), sub[col].to_list(), team))
    return out


def _stat_frame(spec_id: str, ctx: GraphContext) -> tuple[pl.DataFrame, str] | None:
    """Per-hero grid for the General graphs. Returns (frame, value column)."""
    if spec_id in PLAYER_TICK_STATS:
        col = PLAYER_TICK_STATS[spec_id]
        df = sampled_ticks(ctx.match, [col], ctx.every_s)
        return (df, col) if df.height else None
    if spec_id == "souls_per_minute":
        df = sampled_ticks(ctx.match, ["gold_net_worth"], ctx.every_s)
        if not df.height:
            return None
        # The first minute divides by a tiny elapsed time and only shows starting souls; skip it.
        df = df.filter(pl.col("match_seconds") >= 60.0)
        minutes = pl.col("match_seconds") / 60.0
        return df.with_columns((pl.col("gold_net_worth") / minutes).alias("spm")), "spm"
    if spec_id == "rolling_souls":
        df = sampled_ticks(ctx.match, ["gold_net_worth"], ctx.every_s)
        if not df.height:
            return None
        k = max(1, int(120 / ctx.every_s))
        df = df.with_columns(
            ((pl.col("gold_net_worth") - pl.col("gold_net_worth").shift(k).over("hero_id")) / 2.0)
            .fill_null(0).clip(lower_bound=0).alias("rate")
        )
        return df, "rate"
    if spec_id == "healing":
        df = sampled_ticks(ctx.match, ["hero_healing", "self_healing"], ctx.every_s)
        if not df.height:
            return None
        cols = [c for c in ("hero_healing", "self_healing") if c in df.columns]
        return df.with_columns(pl.sum_horizontal([pl.col(c) for c in cols]).alias("heal")), "heal"
    if spec_id == "time_dead":
        lf = scan(ctx.match, "player_ticks")
        if lf is None:
            return None
        step = ctx.every_s
        dead = (
            lf.filter(pl.col("match_seconds") >= 0)
            .with_columns(((pl.col("match_seconds") / step).floor() * step).alias("bucket"))
            .group_by(["hero_id", "bucket"])
            .agg((1 - pl.col("is_alive").cast(pl.Float64)).mean().alias("dead_frac"))
            .collect()
            .sort(["hero_id", "bucket"])
            .with_columns((pl.col("dead_frac") * step).cum_sum().over("hero_id").alias("dead_s"))
            .rename({"bucket": "match_seconds"})
        )
        return dead, "dead_s"
    return None


def general_series(spec_id: str, ctx: GraphContext, by_team: bool) -> list[GraphSeries]:
    got = _stat_frame(spec_id, ctx)
    if got is None:
        return []
    df, col = got
    if by_team:
        agg = "mean" if spec_id in ("level",) else "sum"
        return _team_series(df, col, ctx, agg)
    return _per_hero_series(df, col, ctx)


def team_only_series(spec_id: str, ctx: GraphContext) -> list[GraphSeries]:
    col = "gold_net_worth" if spec_id == "networth_lead" else "kills"
    df = sampled_ticks(ctx.match, [col], ctx.every_s)
    if not df.height:
        return []
    teams = _team_series(df, col, ctx)
    if len(teams) != 2:
        return []
    amber, sapphire = (teams[0], teams[1]) if teams[0].team == 2 else (teams[1], teams[0])
    n = min(len(amber.xs), len(sapphire.xs))
    ys = [a - b for a, b in zip(amber.ys[:n], sapphire.ys[:n], strict=False)]
    name = "Amber lead" if spec_id == "networth_lead" else "Amber kill lead"
    return [GraphSeries("lead", name, amber.xs[:n], ys, None)]


def _grouped_series(df: pl.DataFrame, name_of: Callable[[str], str]) -> list[GraphSeries]:
    out = []
    finals = df.group_by("group").agg(pl.col("value").last()).sort("value", descending=True)
    for g in finals["group"].to_list():
        sub = df.filter(pl.col("group") == g).sort("match_seconds")
        out.append(GraphSeries(f"g:{g}", name_of(g), sub["match_seconds"].to_list(), sub["value"].to_list()))
    return out


def single_hero_series(spec_id: str, ctx: GraphContext, hero_id: int) -> list[GraphSeries]:
    dmg = scan(ctx.match, "damage")
    heal = scan(ctx.match, "healing")
    to_str = pl.col("group").cast(pl.String)
    if spec_id in ("damage_breakdown", "damage_by_type", "damage_by_target", "damage_to_players"):
        if dmg is None:
            return []
        lf = dmg.filter(pl.col("attacker_hero_id") == hero_id)
        if spec_id == "damage_breakdown":
            df = cumulative_events(lf, "damage", pl.col("ability_id").cast(pl.String), ctx.t_end, ctx.every_s)
            df = _fold_other(df.with_columns(to_str))
            return _grouped_series(df, lambda g: ctx.ability_name(int(g)) if g.isdigit() else g)
        if spec_id == "damage_by_type":
            df = cumulative_events(lf, "damage", pl.col("citadel_type").cast(pl.String), ctx.t_end, ctx.every_s)
            return _grouped_series(df, lambda g: DAMAGE_CATEGORY.get(int(g), f"Type {g}"))
        if spec_id == "damage_by_target":
            expr = pl.when(pl.col("victim_hero_id").is_not_null()).then(pl.lit("Heroes")).otherwise(
                pl.lit("Troopers, objectives & NPCs"))
            df = cumulative_events(lf, "damage", expr, ctx.t_end, ctx.every_s)
            return _grouped_series(df, lambda g: g)
        lf = lf.filter(pl.col("victim_hero_id").is_not_null())
        df = cumulative_events(lf, "damage", pl.col("victim_hero_id").cast(pl.String), ctx.t_end, ctx.every_s)
        return _grouped_series(df, lambda g: ctx.hero_name(int(g)))
    if spec_id in ("damage_from_players", "damage_taken_by_type"):
        if dmg is None:
            return []
        lf = dmg.filter(pl.col("victim_hero_id") == hero_id)
        if spec_id == "damage_taken_by_type":
            df = cumulative_events(lf, "damage", pl.col("citadel_type").cast(pl.String), ctx.t_end, ctx.every_s)
            return _grouped_series(df, lambda g: DAMAGE_CATEGORY.get(int(g), f"Type {g}"))
        lf = lf.filter(pl.col("attacker_hero_id").is_not_null())
        df = cumulative_events(lf, "damage", pl.col("attacker_hero_id").cast(pl.String), ctx.t_end, ctx.every_s)
        return _grouped_series(df, lambda g: ctx.hero_name(int(g)))
    if spec_id in ("healing_by_type", "healing_received"):
        if heal is None:
            return []
        col = "source_hero_id" if spec_id == "healing_by_type" else "target_hero_id"
        lf = heal.filter(pl.col(col) == hero_id)
        df = cumulative_events(lf, "amount", pl.col("ability_id").cast(pl.String), ctx.t_end, ctx.every_s)
        df = _fold_other(df.with_columns(to_str))
        return _grouped_series(df, lambda g: ctx.ability_name(int(g)) if g.isdigit() else g)
    return []


def build_series(spec: GraphSpec, ctx: GraphContext, *, by_team: bool = False,
                 hero_id: int | None = None) -> list[GraphSeries]:
    if spec.kind == "per_hero":
        return general_series(spec.id, ctx, by_team)
    if spec.kind == "team_only":
        return team_only_series(spec.id, ctx)
    if spec.kind == "single_hero" and hero_id is not None:
        return single_hero_series(spec.id, ctx, hero_id)
    return []
