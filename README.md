# DeadDemo

A demo manager for Valve's Deadlock, in the spirit of CS Demo Manager: find the replays the game
has downloaded, archive them before Valve expires them, parse them into stats, and replay them on a
2D minimap.

- Python 3.13, PySide6 (native Qt UI, no web view)
- Parsing by [boon-deadlock](https://github.com/pnxenopoulos/boon)
- Match history, replay URLs and map/hero assets from [deadlock-api.com](https://deadlock-api.com)

## Features

| Page | What it does |
|---|---|
| Demos | Scans `game\citadel\replays` (and `addons\replays`, the download folder, extra folders), reads build/map/match id from each demo header, flags `.dem.partial` leftovers and demos older than the installed client, parses on demand, copies a `playdemo` command for the in-game console. |
| Matches | Your match history from deadlock-api.com with local/parsed/downloading state, one-click download of replays that have a known URL, "download everything missing from the last N days". |
| Match detail | Scoreboard per team, kill/objective/teamfight timeline, the in-game graph menu (souls, souls/min, kills, deaths, healing, lane stats, damage breakdown by ability/type/target, damage to/from players, healing by source) with Player/Team toggle plus extras (net worth lead, kill lead, rolling income, unspent souls, time dead), final builds with item icons and hover stats, purchase order, damage dealt/taken, chat, position/death/kill heatmaps, tags and comments, XLSX/JSON export. |
| Players | Cross-match aggregates (games, win rate, KDA, souls per minute, hero pool) for every player seen in parsed demos. |
| Viewer | 2D minimap playback with hero markers, health rings, movement trails, kill markers, objective markers, scrubbing, speed control, hero filters, jump-to-event. Manual calibration dialog if the default map transform is off. |
| Settings | Paths, account override, parallel parses, extra boon datasets, viewer sampling. |

## Development

```
uv sync
uv run deaddemo info                 # detected Steam install, account, data dirs
uv run deaddemo scan                 # catalog local replays
uv run deaddemo parse 104589898      # parse a match (id from scan, or a .dem path)
uv run deaddemo history              # pull match history from deadlock-api.com
uv run deaddemo download <match_id>  # fetch + decompress a replay (add --parse)
uv run deaddemo export <match_id> --xlsx out.xlsx
uv run deaddemo-gui                  # desktop app (or just `uv run deaddemo`)
uv run pytest
uv run ruff check src tests
```

Data lives under the per-user app data directory (see `deaddemo info`): a SQLite catalog plus one
Parquet folder per parsed match. Set `DEADDEMO_HOME` to relocate everything (tests do this).

Packaging: `uv run pyinstaller packaging/deaddemo.spec` produces `dist/DeadDemo/DeadDemo.exe`.

## Things worth knowing

- **Replay availability.** Valve only serves a replay if you know its `replay_salt`. deadlock-api.com
  knows the salt for matches its ingest tools have seen, which is a minority of a given player's own
  matches. Matches without a salt show as "no replay" here; downloading them requires a Steam Game
  Coordinator session (`GetMatchMetaData`), which is planned as an optional provider. Replays you
  download in game land in the replays folder and are picked up by the scanner either way.
- **Compression.** Replay URLs end in `.dem.bz2`, but Valve currently serves zstd frames. The
  downloader sniffs zstd, bzip2 and raw demos.
- **Version lock.** The game refuses to play demos recorded on an older build. The Demos page shows
  the demo build next to the installed client build; parsing and the 2D viewer still work.
- **boon and patch days.** Deadlock patches weekly and boon tracks it closely. The boon version is
  pinned to a minor release and shown in the status bar; "Reparse" re-runs a demo after upgrading.
- **Souls vs net worth.** Tables show net worth (`gold_net_worth`), which is what the in-game
  scoreboard and the API report; the raw `souls` column in the Parquet is unspent souls.
