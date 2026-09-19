# DeadDemo

A demo manager for Valve's Deadlock, in the spirit of CS Demo Manager: find the replays the game
has downloaded, archive them before Valve expires them, parse them into stats, and replay them on a
2D minimap.

- Python 3.13, PySide6 (native Qt UI, no web view)
- Parsing by [boon-deadlock](https://github.com/pnxenopoulos/boon)
- Match history and replay URLs from [deadlock-api.com](https://deadlock-api.com)

## Development

```
uv sync
uv run deaddemo info        # detected Steam install, account, data dirs
uv run deaddemo scan        # catalog local replays
uv run deaddemo-gui         # launch the desktop app
uv run pytest
```

Data lives under the per-user app data directory (see `deaddemo info`). Set `DEADDEMO_HOME` to
relocate everything (used by tests).
