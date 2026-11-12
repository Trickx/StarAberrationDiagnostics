"""Starts the app: star-aberration [image | frames ...]"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import TITLE


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName(TITLE)
    app.setOrganizationName("Tricx")
    from .controller.main_controller import MainController
    from .model.settings import SettingsStore
    from .view import theme

    theme.apply(app, "auto")

    controller = MainController(SettingsStore())
    controller.v.show()
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        controller.files_dropped(files)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
