from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from deaddemo import paths
from deaddemo.gui.context import AppContext


class _PathField(QWidget):
    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        btn = QPushButton("…")
        btn.setFixedWidth(28)
        btn.clicked.connect(self._browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.edit.text() or str(Path.home()))
        if d:
            self.edit.setText(d)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, value: str | None) -> None:
        self.edit.setText(value or "")


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.steam_root = _PathField("auto-detect")
        self.deadlock_dir = _PathField("auto-detect")
        self.download_dir = _PathField(str(paths.default_download_dir()))
        self.extra_dirs = QLineEdit()
        self.extra_dirs.setPlaceholderText("extra replay folders, separated by ;")
        self.account_id = QLineEdit()
        self.account_id.setPlaceholderText("auto-detect (SteamID3 / account id)")
        self.parse_workers = QSpinBox()
        self.parse_workers.setRange(1, 8)
        self.viewer_step = QSpinBox()
        self.viewer_step.setRange(1, 64)
        self.extra_datasets = QLineEdit()
        self.extra_datasets.setPlaceholderText("comma-separated boon datasets stored as parquet")
        self.auto_parse = QCheckBox("Analyze downloads automatically")
        self.auto_history = QCheckBox("Refresh match history on startup")
        self.use_gc = QCheckBox("Ask Steam (Game Coordinator) for replay salts deadlock-api does not have")

        form.addRow("Steam folder", self.steam_root)
        form.addRow("Deadlock folder", self.deadlock_dir)
        form.addRow("Download folder", self.download_dir)
        form.addRow("Extra replay folders", self.extra_dirs)
        form.addRow("Account id override", self.account_id)
        form.addRow("Parallel analyses", self.parse_workers)
        form.addRow("Viewer tick step", self.viewer_step)
        form.addRow("Extra datasets", self.extra_datasets)
        form.addRow("", self.auto_parse)
        form.addRow("", self.auto_history)
        form.addRow("", self.use_gc)

        steam_row = QHBoxLayout()
        self.btn_steam_login = QPushButton("Steam login…")
        self.btn_steam_logout = QPushButton("Forget Steam token")
        self.btn_steam_status = QPushButton("Check")
        self.steam_status = QLabel("")
        self.steam_status.setWordWrap(True)
        steam_row.addWidget(self.btn_steam_login)
        steam_row.addWidget(self.btn_steam_logout)
        steam_row.addWidget(self.btn_steam_status)
        steam_row.addWidget(self.steam_status, 1)
        form.addRow("Steam GC", steam_row)
        self.btn_steam_login.clicked.connect(self._steam_login)
        self.btn_steam_logout.clicked.connect(self._steam_logout)
        self.btn_steam_status.clicked.connect(self._steam_check)

        secrets_note = QLabel(
            "Secrets are never stored in settings.json or the project folder. Set DEADLOCK_API_KEY, "
            "DEADDEMO_STEAM_USER / DEADDEMO_STEAM_PASSWORD in your environment or a git-ignored .env file "
            "(see .env.example). The Steam refresh token is kept encrypted in the app data folder."
        )
        secrets_note.setWordWrap(True)
        secrets_note.setStyleSheet("color: #aaaaaa")
        form.addRow("", secrets_note)

        self.detected = QLabel()
        self.detected.setWordWrap(True)
        layout.addWidget(self.detected)

        btns = QHBoxLayout()
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        btns.addWidget(save)
        btns.addStretch(1)
        layout.addLayout(btns)
        layout.addStretch(1)
        self.load()

    def load(self) -> None:
        s = self.ctx.settings
        self.steam_root.set_text(s.steam_root)
        self.deadlock_dir.set_text(s.deadlock_dir)
        self.download_dir.set_text(s.download_dir)
        self.extra_dirs.setText(";".join(s.extra_replay_dirs))
        self.account_id.setText(str(s.account_id) if s.account_id else "")
        self.parse_workers.setValue(s.parse_workers)
        self.viewer_step.setValue(s.viewer_tick_step)
        self.extra_datasets.setText(",".join(s.extra_datasets))
        self.auto_parse.setChecked(s.auto_parse_downloads)
        self.auto_history.setChecked(s.auto_refresh_history)
        self.use_gc.setChecked(s.use_steam_gc)
        self._refresh_detected()
        self._refresh_steam()

    def _refresh_steam(self) -> None:
        from deaddemo.core import secrets
        from deaddemo.core.gc import provider

        helper = provider.find_helper()
        if provider.is_logged_in():
            text = (f"logged in as {provider.logged_in_user()} · quota {provider.quota_used_today()}/"
                    f"{provider.DAILY_LIMIT} today")
        elif secrets.get(secrets.ENV_STEAM_REFRESH_TOKEN):
            text = "using DEADDEMO_STEAM_REFRESH_TOKEN from the environment"
        else:
            text = "not logged in"
        text += " · helper: " + (str(helper) if helper else "not built (run `cargo build --release` in gc\\)")
        self.steam_status.setText(text)
        api_key = secrets.api_key()
        self.steam_status.setToolTip("deadlock-api key: " + ("set" if api_key else "not set"))

    def _steam_login(self) -> None:
        from deaddemo.gui.widgets.steam_login_dialog import SteamLoginDialog

        dlg = SteamLoginDialog(self.ctx, self)
        dlg.exec()
        self._refresh_steam()

    def _steam_logout(self) -> None:
        from deaddemo.core.gc import provider

        provider.logout()
        self._refresh_steam()
        self.ctx.status("Steam token removed")

    def _steam_check(self) -> None:
        from deaddemo.core.gc import provider

        if not provider.is_logged_in():
            self.ctx.status("Not logged into Steam")
            return
        self.steam_status.setText("Checking…")

        def work(progress, cancel):
            return provider.status()

        def done(data):
            self._refresh_steam()
            ok = data.get("ok")
            self.ctx.status("Steam session OK" if ok else f"Steam check failed: {data.get('error')}", 10000)

        self.ctx.jobs.submit("steam-status", work, on_finished=done,
                             on_failed=lambda e: self.ctx.status(f"Steam check failed: {e.splitlines()[0]}", 10000))

    def _refresh_detected(self) -> None:
        i = self.ctx.install
        acct = f"{i.account.persona_name} ({i.account.account_id})" if i.account else "not found"
        self.detected.setText(
            f"Detected — Steam: {i.root or 'not found'} | Deadlock: {i.deadlock_dir or 'not found'} | "
            f"client build: {i.client_build or '?'} | account: {acct}\n"
            f"Replay folders: {', '.join(str(p) for _, p in i.replay_dirs) or 'none'}\n"
            f"Data: {paths.data_dir()}"
        )

    def save(self) -> None:
        s = self.ctx.settings
        s.steam_root = self.steam_root.text() or None
        s.deadlock_dir = self.deadlock_dir.text() or None
        s.download_dir = self.download_dir.text() or None
        s.extra_replay_dirs = [p.strip() for p in self.extra_dirs.text().split(";") if p.strip()]
        acct = self.account_id.text().strip()
        s.account_id = int(acct) if acct.isdigit() else None
        s.parse_workers = self.parse_workers.value()
        s.viewer_tick_step = self.viewer_step.value()
        s.extra_datasets = [d.strip() for d in self.extra_datasets.text().split(",") if d.strip()]
        s.auto_parse_downloads = self.auto_parse.isChecked()
        s.auto_refresh_history = self.auto_history.isChecked()
        s.use_steam_gc = self.use_gc.isChecked()
        s.save()
        self.ctx.redetect()
        self._refresh_detected()
        self.ctx.events.settings_changed.emit()
        self.ctx.status("Settings saved")
