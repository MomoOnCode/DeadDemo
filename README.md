# DeadDemo

I am a real developer I promise, but please know this was an AI driven proof of concept that worked out quite well. Eventually this disclaimer will be removed as I comb through and extensively test and fix the slop that was provided implement the features. It utilizes your steam login and understand you are trusting your steam credentials with vibe-coded software.  


# Cue Claude Generated README I never asked it to make :)

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
| Players | Cross-match aggregates for every player seen in parsed demos, and a per-player profile (double-click): win ratio from history and from demos, K/D, KDA, kill participation, souls and damage per minute, headshot %, damage split, multi-kills, first blood, teamfights won, tempo at 10/20 min, lanes, nemesis and favourite victims, teammates and opponents with win rates, most bought and opening items, ranked badge history, hero pool, per-match trend graphs, an aggregate position heatmap, and notes. |
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


## Steam Game Coordinator helper (`gc/`)

A small Rust binary built on [steam-vent](https://codeberg.org/icewind/steam-vent) that logs into
Steam with *your* account and asks the Deadlock Game Coordinator for a match's replay salts, the same
request the game makes when you press "Download replay". Valve allows roughly 40 of these per
account per day; the app tracks the quota.

```
cd gc
cargo build --release          # produces gc/target/release/deaddemo-gc(.exe); the app finds it there
cd ..
uv run deaddemo gc login       # once; prompts for password + Steam Guard, stores an encrypted token
uv run deaddemo gc status
```

Or use Settings → Steam login in the GUI. After that, "Download" on the Matches page falls back to
Steam automatically when deadlock-api has no salt.

