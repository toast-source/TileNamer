from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from tilenamer.ui import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(ROOT)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
