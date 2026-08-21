from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from tilenamer.ui import MainWindow  # noqa: E402
from tilenamer.resources import application_icon, set_windows_app_user_model_id  # noqa: E402


def main() -> int:
    if "--check" in sys.argv[1:]:
        from tilenamer import __version__

        print(f"TileNamer v{__version__}: import/dependency check OK")
        return 0
    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    icon = application_icon(ROOT)
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow(ROOT)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
