from __future__ import annotations

import sys

from fracrtm.messages import configure_cli_language
from fracrtm.messages import message as msg


def main() -> int:
    configure_cli_language(sys.argv[1:])
    try:
        from PyQt5 import QtWidgets

        from fracrtm.ui.localization import configure_startup_locale
        from fracrtm.ui.main_window import MainWindow
    except ModuleNotFoundError as exc:
        print(msg("app.dependencies_missing", module=exc.name or msg("state.unknown")))
        return 1
    try:
        argv = configure_startup_locale(sys.argv)
    except ValueError:
        print(msg("app.invalid_language"))
        return 2
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv)
    application.setApplicationName("FracRTM-GUI")
    window = MainWindow()
    window.show()
    return application.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
