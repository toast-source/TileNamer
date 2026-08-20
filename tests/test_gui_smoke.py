import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
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


def test_tree_search_top_sequence_notice_and_counts(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (128, 128), (40, 80, 120, 128)).save(source)
    window = MainWindow(ROOT)
    window._load_source(source)
    assert {"Platform_Center", "Solid_LeftBridge", "Solid_TopSequence_Start_00"} <= set(window.category_items)
    assert window.category_items["Solid_TopSequence_Start_00"].parent().parent().text(0) == "Top Sequence"
    window.category_search.setText("bridge")
    app.processEvents()
    assert not window.category_items["Solid_LeftBridge"].isHidden()
    assert window.category_items["Solid_Bottom"].isHidden()
    assert window.category_items["Wall_Top"].isHidden()
    window.category_search.clear()
    window.category_tree.setCurrentItem(window.category_items["Solid_TopSequence_Start_00"])
    app.processEvents()
    assert "아직 편집을 지원하지 않습니다" in window.warning.text()
    assert not window.canvas.editing_enabled
    window.category_tree.setCurrentItem(window.category_items["Solid_Top"])
    window.assign_region(0, 0, 2, 1)
    assert "(1)" in window.category_items["Solid_Top"].text(0)
    window.close()


def test_canvas_drag_preview_and_zoom_accuracy(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (128, 128), (10, 20, 30, 255)).save(source)
    window = MainWindow(ROOT)
    window._load_source(source)
    window.category_tree.setCurrentItem(window.category_items["Solid_Top"])
    window.canvas.set_zoom(2.0)
    window.show()
    app.processEvents()
    emitted: list[tuple[int, int, int, int]] = []
    window.canvas.region_selected.connect(lambda *args: emitted.append(args))
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(16, 16))
    assert window.canvas.drag_start == (0, 0)
    QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
    assert window.canvas.drag_start is None
    assert emitted == []
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(16, 16))
    QTest.mouseMove(window.canvas, QPoint(112, 48), delay=1)
    app.processEvents()
    assert window.canvas.drag_start == (0, 0)
    assert window.canvas.drag_end == (1, 0)
    assert not window.canvas.grab().isNull()
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(112, 48))
    app.processEvents()
    assert emitted == [(0, 0, 2, 1)]
    assert window.model.assets("Solid_Top")[0].output_width_px == 64
    QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(208, 208))
    app.processEvents()
    assert emitted[-1] == (3, 3, 1, 1)
    assert len(window.model.assets("Solid_Top")) == 2
    window.close()
