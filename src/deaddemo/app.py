"""GUI entry point."""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()
    from deaddemo.core import secrets

    secrets.load_dotenv()
    from deaddemo.core.log import setup_logging

    setup_logging()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from deaddemo.gui.main_window import MainWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("DeadDemo")
    app.setOrganizationName("DeadDemo")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
