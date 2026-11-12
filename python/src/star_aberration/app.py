"""Starts the program: python -m star_aberration [image ...]"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import TITLE


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName(TITLE)
    from .controller.main_controller import MainController
    from .model.settings import SettingsStore
    from .view.main_window import MainWindow

    window = MainWindow()
    controller = MainController(window, SettingsStore())
    window.show()
    window._controller = controller  # kept alive with the window
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
