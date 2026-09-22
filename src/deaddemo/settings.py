"""User settings persisted as JSON in the config directory."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from deaddemo import paths

DEFAULT_EXTRA_DATASETS: tuple[str, ...] = ("healing", "world_ticks", "teamfights", "chat")


@dataclass
class Settings:
    steam_root: str | None = None  # override; auto-detected when None
    deadlock_dir: str | None = None  # override; auto-detected when None
    extra_replay_dirs: list[str] = field(default_factory=list)
    download_dir: str | None = None  # defaults to paths.default_download_dir()
    account_id: int | None = None  # override; auto-detected when None
    parse_workers: int = 1
    extra_datasets: list[str] = field(default_factory=lambda: list(DEFAULT_EXTRA_DATASETS))
    minimap_images: dict[str, str] = field(default_factory=dict)  # map_name -> image path
    viewer_tick_step: int = 4
    auto_parse_downloads: bool = True
    auto_refresh_history: bool = True
    use_steam_gc: bool = True  # ask the Steam Game Coordinator for salts deadlock-api lacks (needs login)
    # video clips
    video_backend: str = "window"  # window (capture the game window, works while covered) | screen (desktop
    # duplication, needs the window on top) | engine (experimental: startmovie is silently blocked in retail) | auto
    video_width: int = 1920
    video_height: int = 1080
    video_fps: int = 60
    video_quality: str = "high"  # low | medium | high | max
    video_hide_hud: bool = True
    video_concat: bool = False
    video_output_dir: str | None = None
    video_lead_in_s: float = 6.0
    video_lead_out_s: float = 3.0
    vconsole_port: int = 29005
    video_launch_mode: str = "steam"  # steam (ask the Steam client to start the game) | direct (run deadlock.exe)

    def resolved_video_dir(self) -> Path:
        return Path(self.video_output_dir) if self.video_output_dir else paths.data_dir() / "videos"

    def resolved_download_dir(self) -> Path:
        return Path(self.download_dir) if self.download_dir else paths.default_download_dir()

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or paths.settings_path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or paths.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)
