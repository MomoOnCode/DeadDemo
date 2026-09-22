from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QProgressBar,
    QStackedWidget,
    QWidget,
)

from deaddemo import __version__
from deaddemo.gui.context import AppContext

PAGES = ["Demos", "Matches", "Players", "Downloads", "Viewer", "Videos", "Settings"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ctx = AppContext.create()
        self.setWindowTitle(f"DeadDemo {__version__}")
        self.resize(1400, 850)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar = QListWidget()
        self.sidebar.addItems(PAGES)
        self.sidebar.setFixedWidth(150)
        self.stack = QStackedWidget()
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._build_pages()
        self.sidebar.currentRowChanged.connect(self._switch_page)
        self.sidebar.setCurrentRow(0)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setVisible(False)
        self.job_label = QLabel("")
        self.statusBar().addPermanentWidget(self.job_label)
        self.statusBar().addPermanentWidget(self.progress)
        self._update_version_label()

        ev = self.ctx.events
        ev.status_message.connect(lambda m, t: self.statusBar().showMessage(m, t))
        ev.open_match.connect(self.open_match)
        ev.open_viewer.connect(self.open_viewer)
        ev.open_player.connect(self.open_player)
        ev.settings_changed.connect(self._update_version_label)
        self.ctx.jobs.job_started.connect(self._jobs_changed)
        self.ctx.jobs.job_finished.connect(self._jobs_changed)

        if self.ctx.install.replay_dirs or self.ctx.settings.extra_replay_dirs:
            self.pages["Demos"].rescan()
        if self.ctx.settings.auto_refresh_history and self.ctx.install.account:
            self.pages["Matches"].refresh_history(force=False)

    def _build_pages(self) -> None:
        from deaddemo.gui.pages.demos_page import DemosPage
        from deaddemo.gui.pages.downloads_page import DownloadsPage
        from deaddemo.gui.pages.match_detail_page import MatchDetailPage
        from deaddemo.gui.pages.matches_page import MatchesPage
        from deaddemo.gui.pages.players_page import PlayersPage
        from deaddemo.gui.pages.settings_page import SettingsPage
        from deaddemo.gui.pages.videos_page import VideosPage
        from deaddemo.gui.pages.viewer_page import ViewerPage

        self.pages: dict[str, QWidget] = {
            "Demos": DemosPage(self.ctx),
            "Matches": MatchesPage(self.ctx),
            "Players": PlayersPage(self.ctx),
            "Downloads": DownloadsPage(self.ctx),
            "Viewer": ViewerPage(self.ctx),
            "Videos": VideosPage(self.ctx),
            "Settings": SettingsPage(self.ctx),
        }
        for name in PAGES:
            self.stack.addWidget(self.pages[name])
        self.match_detail = MatchDetailPage(self.ctx)
        self.match_detail.back_requested.connect(lambda: self.sidebar.setCurrentRow(self._last_row))
        self.stack.addWidget(self.match_detail)
        from deaddemo.gui.pages.player_page import PlayerPage

        self.player_page = PlayerPage(self.ctx)
        self.player_page.back_requested.connect(lambda: self.sidebar.setCurrentRow(self._last_row))
        self.stack.addWidget(self.player_page)
        self._last_row = 0

    def _switch_page(self, row: int) -> None:
        if row < 0:
            return
        self._last_row = row
        self.stack.setCurrentWidget(self.pages[PAGES[row]])

    def open_match(self, match_id: int) -> None:
        self.match_detail.load_match(match_id)
        self.sidebar.blockSignals(True)
        self.sidebar.clearSelection()
        self.sidebar.blockSignals(False)
        self.stack.setCurrentWidget(self.match_detail)

    def open_player(self, steam_id) -> None:
        self.player_page.load_player(int(steam_id))
        self.sidebar.blockSignals(True)
        self.sidebar.clearSelection()
        self.sidebar.blockSignals(False)
        self.stack.setCurrentWidget(self.player_page)

    def open_viewer(self, match_id: int) -> None:
        self.pages["Viewer"].load_match(match_id)
        self.sidebar.setCurrentRow(PAGES.index("Viewer"))

    def _jobs_changed(self, _name: str) -> None:
        active = list(self.ctx.jobs.active.keys())
        self.progress.setVisible(bool(active))
        if active:
            self.progress.setRange(0, 0)
            self.job_label.setText(f"{len(active)} job(s): {', '.join(active[:3])}")
        else:
            self.job_label.setText("")

    def _update_version_label(self) -> None:
        from deaddemo.core.parse.pipeline import boon_version

        build = self.ctx.install.client_build
        self.statusBar().showMessage(
            f"boon {boon_version()} | "
            f"Deadlock client build {build or '?'} | account "
            f"{self.ctx.install.account.persona_name if self.ctx.install.account else 'unknown'}",
            0,
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        self.ctx.jobs.shutdown()
        self.ctx.db.close()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.stack.currentWidget() in (self.match_detail, self.player_page):
            self.sidebar.setCurrentRow(self._last_row)
        super().keyPressEvent(event)
