"""Command-line entry point. ``deaddemo`` with no arguments launches the GUI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from deaddemo import __version__, paths


def _cmd_info(args: argparse.Namespace) -> int:
    from deaddemo.core.steam import locator
    from deaddemo.settings import Settings

    settings = Settings.load()
    install = locator.detect(settings)
    info = {
        "version": __version__,
        "steam_root": str(install.root) if install.root else None,
        "libraries": [str(p) for p in install.libraries],
        "deadlock_dir": str(install.deadlock_dir) if install.deadlock_dir else None,
        "client_build": install.client_build,
        "replay_dirs": [{"source": s, "path": str(p)} for s, p in install.replay_dirs],
        "account": (
            {
                "steam_id64": install.account.steam_id64,
                "account_id": install.account.account_id,
                "persona_name": install.account.persona_name,
            }
            if install.account
            else None
        ),
        "config_dir": str(paths.config_dir()),
        "data_dir": str(paths.data_dir()),
        "cache_dir": str(paths.cache_dir()),
        "db_path": str(paths.db_path()),
        "download_dir": str(settings.resolved_download_dir()),
    }
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        for k, v in info.items():
            print(f"{k}: {v}")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    from deaddemo.core.db.database import Database
    from deaddemo.core.db.repos import DemoRepo
    from deaddemo.core.replays import scanner
    from deaddemo.core.steam import locator
    from deaddemo.settings import Settings

    settings = Settings.load()
    install = locator.detect(settings)
    extra = [Path(p) for p in (args.dir or [])] + [Path(p) for p in settings.extra_replay_dirs]
    found = scanner.scan(install.replay_dirs, extra, download_dir=settings.resolved_download_dir())
    db = Database.open()
    report = scanner.sync_to_db(DemoRepo(db), found)
    if args.json:
        print(json.dumps([f.to_dict() for f in found], indent=2))
    else:
        for f in found:
            flag = "PARTIAL" if f.is_partial else (f.error or "ok")
            print(f"{f.match_id or '?':>10}  build={f.build or '?':<6} map={f.map_name or '?':<12} "
                  f"{f.size_bytes / 1e6:8.1f} MB  [{f.source}] {flag}  {f.path}")
        print(f"\n{len(found)} files, {report.added} new, {report.updated} updated, {report.missing} missing")
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    from deaddemo.core.parse.runner import parse_and_store

    for target in args.target:
        result = parse_and_store(target, force=args.force,
                                 datasets=tuple(args.datasets.split(",")) if args.datasets else None,
                                 progress=lambda msg: print(f"  {msg}"))
        print(f"parsed match {result.match_id}: {len(result.players)} players, {len(result.kills)} kills,"
              f" parquet -> {result.parquet_dir}")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    from deaddemo.core.api.service import refresh_history

    entries, account_id = refresh_history(account_id=args.account_id, force_refetch=args.force_refetch,
                                          use_gc=not args.no_gc)
    if args.json:
        print(json.dumps([e.to_dict() for e in entries], indent=2))
    else:
        print(f"account {account_id}: {len(entries)} matches")
        for e in entries[:50]:
            print(f"{e.match_id:>10}  {e.start_iso()}  hero={e.hero_id:<3} {e.kda()}  "
                  f"{'WIN ' if e.won else 'LOSS'} {e.match_duration_s // 60}m")
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    from deaddemo.core.download.service import download_match

    def progress(done: int, total: int | None) -> None:
        if total:
            print(f"\r  {done / 1e6:8.1f} / {total / 1e6:.1f} MB", end="", flush=True)
        else:
            print(f"\r  {done / 1e6:8.1f} MB", end="", flush=True)

    for match_id in args.match_id:
        dest = download_match(int(match_id), dest_dir=Path(args.dest) if args.dest else None, progress=progress)
        print(f"\ndownloaded {match_id} -> {dest}")
        if args.parse:
            from deaddemo.core.parse.runner import parse_and_store

            parse_and_store(str(dest), progress=lambda msg: print(f"  {msg}"))
    return 0


def _cmd_awards(args: argparse.Namespace) -> int:
    """MVP / Key Player and accolades for a match, fetched from deadlock-api and stored."""
    from deaddemo.core.api.awards import store_awards
    from deaddemo.core.assets.catalog import catalog
    from deaddemo.core.db.database import Database

    db = Database.open()
    cat = catalog()
    cat.load_accolades()
    for match_id in args.match_id:
        rows = store_awards(db, int(match_id))
        if not rows:
            print(f"match {match_id}: no metadata on deadlock-api (yet)")
            continue
        if args.json:
            print(json.dumps([r.__dict__ for r in rows], indent=1))
            continue
        print(f"match {match_id}:")
        for r in sorted(rows, key=lambda r: ((r.team or 0), r.mvp_rank or 9, r.player_slot or 0)):
            acc = ", ".join(f"{cat.accolade_name(a['id'])} ({a['value']})" for a in r.accolades[:4])
            print(f"  team {r.team} {cat.hero_name(r.hero_id):<14} {r.label:<10} {acc}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from deaddemo.core.export.exporter import export_match_json, export_match_xlsx

    if args.xlsx:
        export_match_xlsx(int(args.match_id), Path(args.xlsx))
        print(f"wrote {args.xlsx}")
    if args.json_out:
        export_match_json(int(args.match_id), Path(args.json_out))
        print(f"wrote {args.json_out}")
    return 0


def _cmd_gc(args: argparse.Namespace) -> int:
    import getpass

    from deaddemo.core import secrets
    from deaddemo.core.gc import provider

    if args.gc_command == "login":
        user = secrets.get(secrets.ENV_STEAM_USER) or input("Steam username: ").strip()
        password = secrets.get(secrets.ENV_STEAM_PASSWORD) or getpass.getpass("Steam password: ")
        code = secrets.get(secrets.ENV_STEAM_GUARD_CODE) or (args.guard_code or "")
        if not code and not args.no_guard:
            code = input("Steam Guard code (blank to approve in the mobile app): ").strip()
        data = provider.login(user, password, code or None)
        print(f"logged in as {user} (steam id {data.get('steam_id64')}); token stored in "
              f"{secrets.token_path(provider.TOKEN_NAME).parent}")
        return 0
    if args.gc_command == "logout":
        provider.logout()
        print("Steam token removed")
        return 0
    if args.gc_command == "status":
        helper = provider.find_helper()
        print(f"helper: {helper or 'NOT FOUND'}")
        print(f"logged in: {provider.is_logged_in()} ({provider.logged_in_user() or '-'})")
        print(f"quota used today: {provider.quota_used_today()}/{provider.DAILY_LIMIT}")
        if provider.is_logged_in() and helper:
            print("steam says:", json.dumps(provider.status()))
        return 0
    if args.gc_command == "salts":
        for r in provider.fetch_salts([int(m) for m in args.match_id]):
            print(json.dumps(r.__dict__))
        return 0
    if args.gc_command == "history":
        from deaddemo.core.api.service import resolve_account_id

        account_id = resolve_account_id(args.account_id)
        entries = provider.fetch_history(account_id, max_pages=args.pages)
        for e in entries:
            print(json.dumps(e))
        print(f"{len(entries)} matches from the Game Coordinator", file=sys.stderr)
        if args.store and entries:
            from deaddemo.core.db.database import Database
            from deaddemo.core.db.repos import HistoryRepo

            db = Database.open()
            repo = HistoryRepo(db)
            known = repo.match_ids(account_id)
            new = [e for e in entries if int(e["match_id"]) not in known]
            n = repo.upsert_many(account_id, new, source="gc", replace=False)
            print(f"stored {n} match(es) the local history did not have", file=sys.stderr)
        return 0
    return 1


def _cmd_video(args: argparse.Namespace) -> int:
    from deaddemo.core.assets.catalog import catalog
    from deaddemo.core.db.database import Database
    from deaddemo.core.db.repos import MatchRepo
    from deaddemo.core.steam import locator
    from deaddemo.core.video import director
    from deaddemo.core.video.sequences import GenerateOptions, SequenceRepo, generate
    from deaddemo.settings import Settings

    settings = Settings.load()
    install = locator.detect(settings)
    db = Database.open()
    log = lambda msg: print(msg, flush=True)  # noqa: E731
    match_id = int(args.match_id)
    if args.video_command == "probe":
        caps = director.probe(install, db, match_id, log, try_movie=args.movie,
                              launch_mode=args.launch or settings.video_launch_mode)
        print(json.dumps(caps.__dict__, indent=1))
        return 0
    match = MatchRepo(db).get(match_id)
    if match is None:
        print(f"match {match_id} is not analyzed", file=sys.stderr)
        return 1
    players = MatchRepo(db).players(match_id)
    if args.video_command == "sequences":
        hero = _pick_hero(players, args.player)
        opts = GenerateOptions(kinds=tuple(args.kinds.split(",")), lead_in_s=settings.video_lead_in_s,
                               lead_out_s=settings.video_lead_out_s, chain_kills_s=settings.video_chain_kills_s)
        seqs = generate(db, match, hero, opts, catalog().hero_name)
        if args.save:
            SequenceRepo(db).replace_all(match_id, seqs)
        for s in seqs:
            print(f"{s.start_s:7.1f}s – {s.end_s:7.1f}s  {s.label}")
        print(f"{len(seqs)} sequence(s)" + (" saved" if args.save else ""))
        return 0
    if args.video_command == "record":
        seqs = SequenceRepo(db).for_match(match_id)
        if not seqs and args.player:
            hero = _pick_hero(players, args.player)
            seqs = generate(db, match, hero, GenerateOptions(), catalog().hero_name)
        if not seqs:
            print("no sequences; run `deaddemo video sequences <id> --player NAME --save` first", file=sys.stderr)
            return 1
        rs = director.RecordSettings(
            width=args.width or settings.video_width, height=args.height or settings.video_height,
            fps=args.fps or settings.video_fps, quality=settings.video_quality,
            backend=args.backend or settings.video_backend, hide_hud=settings.video_hide_hud,
            concat=args.concat, output_dir=settings.resolved_video_dir(), vcon_port=settings.vconsole_port,
            audio=settings.video_audio and not args.no_audio, auto_hdr_off=settings.video_auto_hdr_off,
            preroll_s=settings.video_preroll_s,
            launch_mode=args.launch or settings.video_launch_mode,
        )
        results = director.record(install, db, match_id, seqs[: args.limit] if args.limit else seqs, rs, log)
        for r in results:
            print(f"{'OK ' if r.path else 'ERR'} {r.sequence.label}: {r.path or r.error}")
        return 0
    return 1


def _pick_hero(players, name: str | None) -> int:
    if not name:
        raise SystemExit("--player NAME is required")
    lname = name.lower()
    for p in players:
        if (p.player_name or "").lower() == lname:
            return p.hero_id
    for p in players:
        if lname in (p.player_name or "").lower():
            return p.hero_id
    raise SystemExit(f"no player named {name!r} in this match; players: "
                     + ", ".join(p.player_name or "?" for p in players))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deaddemo", description="Deadlock demo manager")
    p.add_argument("--version", action="version", version=f"deaddemo {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("info", help="show detected Steam install, account and data directories")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_info)

    s = sub.add_parser("scan", help="catalog local replay files")
    s.add_argument("--json", action="store_true")
    s.add_argument("--dir", action="append", help="extra directory to scan (repeatable)")
    s.set_defaults(func=_cmd_scan)

    s = sub.add_parser("parse", help="parse a demo into the database")
    s.add_argument("target", nargs="+", help="match id or path to .dem")
    s.add_argument("--force", action="store_true", help="reparse even if already parsed")
    s.add_argument("--datasets", help="comma-separated extra boon datasets to store")
    s.set_defaults(func=_cmd_parse)

    s = sub.add_parser("history", help="fetch match history from deadlock-api.com")
    s.add_argument("--account-id", type=int)
    s.add_argument("--force-refetch", action="store_true")
    s.add_argument("--no-gc", action="store_true", help="do not also ask the Steam Game Coordinator")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_history)

    s = sub.add_parser("download", help="download a match replay")
    s.add_argument("match_id", nargs="+")
    s.add_argument("--dest", help="destination directory")
    s.add_argument("--parse", action="store_true", help="parse after download")
    s.set_defaults(func=_cmd_download)

    s = sub.add_parser("export", help="export a parsed match")
    s.add_argument("match_id")
    s.add_argument("--xlsx")
    s.add_argument("--json", dest="json_out")
    s.set_defaults(func=_cmd_export)

    s = sub.add_parser("awards", help="MVP / Key Player and accolades for matches (from deadlock-api metadata)")
    s.add_argument("match_id", nargs="+")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_awards)

    g = sub.add_parser("gc", help="Steam Game Coordinator helper (replay salts for your own matches)")
    gs = g.add_subparsers(dest="gc_command", required=True)
    gl = gs.add_parser("login", help="log into Steam once; stores an encrypted refresh token")
    gl.add_argument("--guard-code", help="Steam Guard code (or set DEADDEMO_STEAM_GUARD_CODE)")
    gl.add_argument("--no-guard", action="store_true", help="do not prompt for a code")
    gs.add_parser("logout", help="delete the stored Steam token")
    gs.add_parser("status", help="show helper, login and quota state")
    gsl = gs.add_parser("salts", help="fetch replay salts for match ids")
    gsl.add_argument("match_id", nargs="+")
    gh = gs.add_parser("history", help="fetch your match history from the Game Coordinator (game must be closed)")
    gh.add_argument("--account-id", type=int)
    gh.add_argument("--pages", type=int, default=3)
    gh.add_argument("--store", action="store_true", help="add matches deadlock-api lacks to the local history")
    g.set_defaults(func=_cmd_gc)

    v = sub.add_parser("video", help="film clips from a demo by driving the Deadlock client")
    vs = v.add_subparsers(dest="video_command", required=True)
    vp = vs.add_parser("probe", help="launch the game once and test console control, seeking and the recorder")
    vp.add_argument("match_id")
    vp.add_argument("--movie", action="store_true",
                    help="also test the engine recorder (relaunches once with gameinfo.gi patched for a few seconds)")
    vp.add_argument("--launch", choices=["steam", "direct"], help="how to start the game (default: via Steam)")
    vq = vs.add_parser("sequences", help="auto-generate clip sequences for a player")
    vq.add_argument("match_id")
    vq.add_argument("--player", required=True)
    vq.add_argument("--kinds", default="kills,bursts,multikills,teamfights")
    vq.add_argument("--save", action="store_true")
    vr = vs.add_parser("record", help="record the saved sequences of a match")
    vr.add_argument("match_id")
    vr.add_argument("--player", help="generate default sequences for this player if none are saved")
    vr.add_argument("--width", type=int)
    vr.add_argument("--height", type=int)
    vr.add_argument("--fps", type=int)
    vr.add_argument("--backend", choices=["auto", "engine", "screen", "window"])
    vr.add_argument("--concat", action="store_true")
    vr.add_argument("--no-audio", action="store_true", help="do not capture the game's audio (window recorder)")
    vr.add_argument("--limit", type=int, help="record only the first N sequences")
    vr.add_argument("--launch", choices=["steam", "direct"])
    v.set_defaults(func=_cmd_video)
    return p


def main(argv: list[str] | None = None) -> int:
    from deaddemo.core import secrets

    secrets.load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        from deaddemo.app import main as gui_main

        return gui_main()
    from deaddemo.core.log import setup_logging

    setup_logging(console=True)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI surface
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
