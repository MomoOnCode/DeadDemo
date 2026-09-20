"""Steam login for the Game Coordinator helper. Credentials go to the helper via its environment
and are discarded afterwards; only the refresh token is kept (encrypted, outside the repo)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from deaddemo.core import secrets
from deaddemo.core.gc import provider
from deaddemo.gui.context import AppContext


class SteamLoginDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Steam login for replay salts")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Logs into Steam once through the deaddemo-gc helper so the app can ask Valve's Game Coordinator "
            "for replay salts of your own matches (about 40 lookups per day).\n\n"
            "Your password is passed to the helper in memory only. A refresh token is stored encrypted in "
            f"{secrets.token_path(provider.TOKEN_NAME).parent}. Nothing is written into the project folder."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.user = QLineEdit(secrets.get(secrets.ENV_STEAM_USER) or provider.logged_in_user() or "")
        self.password = QLineEdit(secrets.get(secrets.ENV_STEAM_PASSWORD) or "")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.guard = QLineEdit(secrets.get(secrets.ENV_STEAM_GUARD_CODE) or "")
        self.guard.setPlaceholderText("Steam Guard code (leave empty to approve on your phone)")
        form.addRow("Username", self.user)
        form.addRow("Password", self.password)
        form.addRow("Guard code", self.guard)
        layout.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Log in")
        self.buttons.accepted.connect(self._login)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        helper = provider.find_helper()
        if helper is None:
            self.status.setText("deaddemo-gc helper not found. Build it with `cargo build --release` in gc\\ "
                                "or set DEADDEMO_GC_BINARY.")
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def _login(self) -> None:
        user = self.user.text().strip()
        password = self.password.text()
        code = self.guard.text().strip() or None
        if not user or not password:
            self.status.setText("Username and password are required.")
            return
        self.buttons.setEnabled(False)
        self.status.setText("Logging in… approve the request in the Steam mobile app if prompted.")

        def work(progress, cancel):
            return provider.login(user, password, code)

        def done(data):
            self.password.clear()
            self.guard.clear()
            self.status.setText(f"Logged in (steam id {data.get('steam_id64')}). Token stored.")
            self.ctx.status("Steam login successful")
            self.accept()

        def failed(err: str):
            self.buttons.setEnabled(True)
            self.status.setText(f"Login failed: {err.splitlines()[0]}")

        self.ctx.jobs.submit("steam-login", work, on_finished=done, on_failed=failed)
