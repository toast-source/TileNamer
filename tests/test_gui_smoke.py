import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from tilenamer.ui import MainWindow


ROOT = Path(__file__).resolve().parents[1]


def test_main_window_load_and_assign(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (65, 64), (40, 80, 120, 128)).save(source)
    window = MainWindow(ROOT)
    window._load_source(source)
    window.toggle_tile(0, 0)
    window.canvas.grab()
    assert window.source_image.size == (65, 64)
    assert window.assignment_list.count() == 1
    assert "남는 영역" in window.warning.text()
    window.close()
    app.processEvents()
