"""Shared application state handed to every page."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import (
    CalibrationRepo,
    DemoRepo,
    DownloadRepo,
    HistoryRepo,
    MatchRepo,
    PlayerNotesRepo,
    TagRepo,
)
from deaddemo.core.steam import locator
from deaddemo.core.steam.locator import SteamInstall
from deaddemo.gui.jobs import JobManager
from deaddemo.settings import Settings


class AppEvents(QObject):
    demos_changed = Signal()
    matches_changed = Signal()
    history_changed = Signal()
    downloads_changed = Signal()
    settings_changed = Signal()
    status_message = Signal(str, int)  # message, timeout ms
    open_match = Signal(int)  # match_id
    open_viewer = Signal(int)  # match_id
    open_player = Signal(object)  # steam_id64 (too large for Qt's 32-bit int signal payload)


@dataclass
class AppContext:
    settings: Settings
    db: Database
    jobs: JobManager
    events: AppEvents
    install: SteamInstall
    demos: DemoRepo = field(init=False)
    matches: MatchRepo = field(init=False)
    history: HistoryRepo = field(init=False)
    downloads: DownloadRepo = field(init=False)
    tags: TagRepo = field(init=False)
    calibrations: CalibrationRepo = field(init=False)
    player_notes: PlayerNotesRepo = field(init=False)

    def __post_init__(self) -> None:
        self.demos = DemoRepo(self.db)
        self.matches = MatchRepo(self.db)
        self.history = HistoryRepo(self.db)
        self.downloads = DownloadRepo(self.db)
        self.tags = TagRepo(self.db)
        self.calibrations = CalibrationRepo(self.db)
        self.player_notes = PlayerNotesRepo(self.db)

    @classmethod
    def create(cls) -> AppContext:
        settings = Settings.load()
        db = Database.open()
        jobs = JobManager(parse_workers=settings.parse_workers)
        install = locator.detect(settings)
        return cls(settings=settings, db=db, jobs=jobs, events=AppEvents(), install=install)

    def redetect(self) -> None:
        self.install = locator.detect(self.settings)

    def status(self, message: str, timeout_ms: int = 5000) -> None:
        self.events.status_message.emit(message, timeout_ms)
