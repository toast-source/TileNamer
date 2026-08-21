import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHeaderView

from tilenamer.image_loader import AsepriteLayer, LoadedSource
from tilenamer.ui import MainWindow


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def icon_border_rgb(window: MainWindow, row: int) -> tuple[int, int, int]:
    color = window.assignment_list.item(row).icon().pixmap(48, 48).toImage().pixelColor(0, 0)
    return color.red(), color.green(), color.blue()


def test_assignment_row_canvas_and_thumbnail_share_selection_state(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "transparent.png"
    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(source)
    window = MainWindow(ROOT)
    window._load_source(source)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(0, 0, 1, 1)
    window.assign_region(1, 0, 2, 1)
    assert window.canvas.assignment_visual_state("Platform_Center", 0) == "current"
    assert icon_border_rgb(window, 0) != (70, 225, 245)

    window.assignment_list.setCurrentRow(0)
    qt.processEvents()
    assert window.canvas.selected_assignment_index == 0
    assert window.canvas.assignment_visual_state("Platform_Center", 0) == "selected"
    assert window.canvas.assignment_visual_state("Platform_Center", 1) == "current-dimmed"
    assert icon_border_rgb(window, 0) == (70, 225, 245)
    assert icon_border_rgb(window, 1) != (70, 225, 245)

    window.assignment_list.setCurrentRow(1)
    assert window.canvas.selected_assignment_index == 1
    assert icon_border_rgb(window, 1) == (70, 225, 245)
    selected_origin = window.model.assets("Platform_Center")[1].origin
    window.reorder(-1)
    assert window.assignment_list.currentRow() == 0
    assert window.model.assets("Platform_Center")[0].origin == selected_origin
    window.undo_stack.undo()
    assert window.assignment_list.currentRow() == 1
    assert window.model.assets("Platform_Center")[1].origin == selected_origin

    window.remove_selected()
    assert window.assignment_list.currentRow() == -1
    assert window.canvas.selected_assignment_index == -1
    window.undo_stack.undo()
    assert window.canvas.selected_assignment_index == -1
    window.close()
    qt.processEvents()


def test_layer_panel_readability_alignment_section_and_history_buttons(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = tmp_path / "layers.aseprite"
    source.write_bytes(b"fixture")
    layers = (
        AsepriteLayer("1", "Background", "image", True),
        AsepriteLayer("2", "Ch2. Terrain", "group", True, (
            AsepriteLayer("2/1", "Rocks Foreground Decoration", "image", True),
        )),
    )

    def fake_load(path, visibility=None, alignment_offsets=None):
        return LoadedSource(
            Image.new("RGBA", (128, 128), (30, 50, 70, 255)), layers,
            {"1": True, "2": True, "2/1": True},
        )

    monkeypatch.setattr("tilenamer.ui.load_source_document", fake_load)
    window = MainWindow(ROOT)
    window._load_source(source)
    header = window.layer_tree.header()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Fixed
    assert header.sectionSize(1) == 82
    assert window.layer_tree.headerItem().text(1) == "Grid"
    assert window.layer_tree.columnCount() == 2
    assert not window.advanced_layer_settings.isChecked()
    assert not window.layer_offset_x.isEnabled()
    assert window.selected_layer_name.text() == "레이어를 선택하세요"

    group = window.layer_tree.topLevelItem(0)
    leaf = group.child(0)
    assert "Ch2. Terrain / Rocks Foreground Decoration" in leaf.toolTip(0)
    window.layer_tree.setCurrentItem(leaf)
    assert window.selected_layer_name.text() == "Rocks Foreground Decoration"
    assert window.selected_grid_layer_name.text().startswith("Rocks Foreground Decoration")
    assert window.selected_layer_name.toolTip() == leaf.toolTip(0)
    assert window.layer_offset_x.isEnabled() and window.layer_offset_y.isEnabled()
    assert window.layer_grid_x.isEnabled() and window.layer_grid_y.isEnabled()
    assert not hasattr(window, "alignment_correction_check")
    assert window.auto_alignment_button.text() == "기준 격자에 자동 맞춤"

    assert window.undo_button.size().width() == 40
    assert window.redo_button.size().width() == 40
    assert window.undo_button.toolTip() == "실행 취소 (Ctrl+Z)"
    assert window.redo_button.toolTip() == "다시 실행 (Ctrl+Y)"
    assert not window.undo_button.isEnabled() and not window.redo_button.isEnabled()
    window.category_tree.setCurrentItem(window.category_items["Solid_Top"])
    window.assign_region(0, 0, 1, 1)
    assert window.undo_button.isEnabled()
    window.close()
    qt.processEvents()
