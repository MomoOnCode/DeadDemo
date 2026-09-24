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
| Matches | Your match history from deadlock-api.com, completed by the Steam Game Coordinator (the same feed the in-game history screen uses) when you are logged in, plus any match you analyzed locally that neither lists yet. Local/parsed/downloading state, MVP / Key Player marker, one-click download of replays that have a known URL, "download everything missing from the last N days". |
| Match detail | Scoreboard per team with the post-game MVP and Key Player awards (hover for the earned accolades), kill/objective/teamfight timeline, the in-game graph menu (souls, souls/min, kills, deaths, healing, lane stats, damage breakdown by ability/type/target, damage to/from players, healing by source) with Player/Team toggle plus extras (net worth lead, kill lead, rolling income, unspent souls, time dead), final builds with item icons and hover stats, purchase order, damage dealt/taken, chat, position/death/kill heatmaps, tags and comments, XLSX/JSON export. |
| Players | Cross-match aggregates for every player seen in parsed demos, and a per-player profile (double-click): win ratio from history and from demos, K/D, KDA, kill participation, souls and damage per minute, headshot %, damage split, multi-kills, first blood, teamfights won, tempo at 10/20 min, lanes, nemesis and favourite victims, teammates and opponents with win rates, most bought and opening items, ranked badge history, hero pool, per-match trend graphs, an aggregate position heatmap, and notes. |
| Viewer | 2D minimap playback with hero markers, health rings, movement trails, kill markers, objective markers, scrubbing, speed control, hero filters, jump-to-event. Manual calibration dialog if the default map transform is off. |
| Settings | Paths, account override, parallel parses, extra boon datasets, viewer sampling. |

## Video clips (experimental)

The Clips tab of an analyzed match builds *sequences* (a time range plus whom to watch) from kills,
multi-kills, teamfights, deaths, first blood, objectives or by hand, and **Record…** launches the
Deadlock client, drives the replay over Valve's VConsole remote console (`-vconsole`), seeks to each
sequence, locks the camera on the player, hides the HUD and captures it:

- **Window recorder** (default): Windows Graphics Capture of the game window, piped into ffmpeg
  (NVENC when available) at a constant frame rate. It captures the window's own surface, so you can
  keep using the PC while it records; just do not minimize the game. The engine ignores `-w/-h` and
  uses your saved video settings, so clips come out at your normal resolution. Game audio is captured
  from deadlock.exe alone through Windows' per-process loopback (Windows 10 build 20348+), so Discord
  or music never end up in a clip; `snd_mute_losefocus` is switched off for the session because the
  game sits behind your other windows while filming. Untick "Game audio" (or `--no-audio`) to skip it.
- **Screen recorder**: ffmpeg desktop duplication of the game window's screen area. Only sees what is
  on top, so the game must stay unobstructed. No audio.
- **Engine recorder** (`startmovie`, experimental, opt-in): would be frame-exact with audio, but the
  command is silently blocked in the retail client. The CS2 trick of setting `DefensiveConCommands 0` in
  `gameinfo.gi` does not work here: none of Deadlock's binaries even contain that key (verified on
  build 6698). `deaddemo video probe <id> --movie` tests it; it edits `gameinfo.gi` for a few seconds
  and restores the exact bytes, but note that Steam's "verify integrity" and mod managers also touch
  that file. Always launched with `-insecure`.

A filming launch makes the engine rewrite `game/citadel/cfg/video.txt` and `cfg/machine_convars.vcfg`
(window mode, `fps_max`, `engine_no_focus_sleep`); both are snapshotted before the launch and restored
once the game has exited, so your settings survive. Steam is started first if it is not running.

Clips land in the video output folder (Settings) and are listed on the Videos page. CLI:
`deaddemo video probe <id>`, `deaddemo video sequences <id> --player NAME --save`,
`deaddemo video record <id> --fps 60 --backend window`. Deadlock must be closed first, and
the demo must have been recorded on the currently installed build (older demos are refused by the game).

## Development

```
uv sync
uv run deaddemo info                 # detected Steam install, account, data dirs
uv run deaddemo scan                 # catalog local replays
uv run deaddemo parse 104589898      # parse a match (id from scan, or a .dem path)
uv run deaddemo history              # pull match history from deadlock-api.com (+ Steam GC when logged in)
uv run deaddemo awards <match_id>    # MVP / Key Player and accolades from the match metadata
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
  matches. For the rest the app can ask Valve's Game Coordinator directly through the `deaddemo-gc`
  helper (see below). Replays you download in game land in the replays folder and are picked up by
  the scanner either way.
- **Match history completeness.** deadlock-api's per-player history lags and misses matches (on one
  account, 67 of the 100 most recent were absent). The Matches page therefore also shows matches you
  analyzed locally, asks deadlock-api to re-pull from Valve when a local demo is newer than its newest
  entry (once per hour), and, when the GC helper is logged in and the game is closed, pulls your history
  straight from the Game Coordinator (`deaddemo gc history --store`). Refresh outcomes and errors are
  logged to `logs/app.log` in the data directory.

## Steam Game Coordinator helper (`gc/`)

A small Rust binary built on [steam-vent](https://codeberg.org/icewind/steam-vent) that logs into
Steam with *your* account and asks the Deadlock Game Coordinator for a match's replay salts (the same
request the game makes when you press "Download replay") or for your match history (`history
<account_id> [pages]`, 50 matches per page). Valve allows roughly 40 GC requests per account per day;
the app tracks the quota. Steam routes GC replies to a running game, so close Deadlock first.

```
cd gc
cargo build --release          # produces gc/target/release/deaddemo-gc(.exe); the app finds it there
cd ..
uv run deaddemo gc login       # once; prompts for password + Steam Guard, stores an encrypted token
uv run deaddemo gc status
```

Or use Settings → Steam login in the GUI. After that, "Download" on the Matches page falls back to
Steam automatically when deadlock-api has no salt.

