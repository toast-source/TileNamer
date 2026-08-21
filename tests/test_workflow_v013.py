import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tilenamer.exporter import build_export_plan, export_tiles
from tilenamer.image_loader import AsepriteLayer, LoadedSource, render_aseprite_document
from tilenamer.ui import MainWindow, SourceDropStack


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_sheet(path: Path, size: tuple[int, int], color=(20, 40, 60, 255)) -> Path:
    Image.new("RGBA", size, color).save(path)
    return path


def test_drop_validation_accepts_supported_single_file_only() -> None:
    assert SourceDropStack.validate_paths([Path("sheet.aseprite")]) == (True, "")
    assert SourceDropStack.validate_paths([Path("sheet.TIFF")]) == (True, "")
    assert not SourceDropStack.validate_paths([Path("a.png"), Path("b.png")])[0]
    assert "한 번에 하나" in SourceDropStack.validate_paths([Path("a.png"), Path("b.png")])[1]
    assert not SourceDropStack.validate_paths([Path("notes.txt")])[0]


def test_source_swap_keep_new_cancel_and_transactional_rollback(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    first = make_sheet(tmp_path / "first.png", (128, 128), (255, 0, 0, 255))
    second = make_sheet(tmp_path / "second.png", (128, 128), (0, 255, 0, 255))
    smaller = make_sheet(tmp_path / "smaller.png", (96, 96), (0, 0, 255, 255))
    larger = make_sheet(tmp_path / "larger.png", (160, 160), (255, 255, 0, 255))
    too_small = make_sheet(tmp_path / "small.png", (32, 32), (0, 0, 255, 255))
    window = MainWindow(ROOT)
    assert window._request_source(first)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(1, 1, 2, 2)
    monkeypatch.setattr(window, "_choose_source_mode", lambda path: "keep")
    assert window._request_source(second)
    assert window.source_path == second.resolve()
    assert len(window.model.assets("Platform_Center")) == 1
    thumbnail_pixel = (
        window.assignment_list.item(0).icon().pixmap(48, 48).toImage().pixelColor(24, 24)
    )
    assert (thumbnail_pixel.red(), thumbnail_pixel.green(), thumbnail_pixel.blue()) == (0, 255, 0)
    exported = export_tiles(
        window.source_image,
        build_export_plan(tmp_path / "export", window.model, window.rules),
    )
    with Image.open(exported[0]) as tile:
        assert tile.getpixel((0, 0)) == (0, 255, 0, 255)
        assert tile.size == (64, 64)
    assert exported[0].name == "Platform_Center_00.png"
    assert window._request_source(smaller)
    assert window.source_image.size == (96, 96)
    assert len(window.model.assets("Platform_Center")) == 1
    assert window._request_source(larger)
    assert window.source_image.size == (160, 160)
    assert len(window.model.assets("Platform_Center")) == 1
    previous_image = window.source_image
    previous_path = window.source_path
    monkeypatch.setattr("tilenamer.ui.QMessageBox.warning", lambda *args, **kwargs: None)
    assert not window._request_source(too_small)
    assert window.source_path == previous_path
    assert window.source_image is previous_image
    monkeypatch.setattr(window, "_choose_source_mode", lambda path: "cancel")
    assert not window._request_source(first)
    assert window.source_path == previous_path
    monkeypatch.setattr(window, "_choose_source_mode", lambda path: "new")
    assert window._request_source(first)
    assert not window._has_assignments()
    window.close()
    qt.processEvents()


def test_same_path_reload_keeps_assignments_and_load_failure_rolls_back(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = make_sheet(tmp_path / "sheet.png", (64, 64))
    window = MainWindow(ROOT)
    window._load_source(source)
    window.category_tree.setCurrentItem(window.category_items["Solid_Top"])
    window.assign_region(0, 0, 1, 1)
    assert window._request_source(source)
    assert len(window.model.assets("Solid_Top")) == 1
    old_path, old_image = window.source_path, window.source_image
    monkeypatch.setattr("tilenamer.ui.load_source_document", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad")))
    monkeypatch.setattr("tilenamer.ui.QMessageBox.critical", lambda *args, **kwargs: None)
    assert not window._request_source(tmp_path / "bad.png")
    assert window.source_path == old_path and window.source_image is old_image
    window.close()
    qt.processEvents()


def test_assignment_undo_redo_move_reorder_and_remove(tmp_path: Path) -> None:
    qt = app()
    source = make_sheet(tmp_path / "sheet.png", (128, 128))
    window = MainWindow(ROOT)
    window._load_source(source)
    window.category_tree.setCurrentItem(window.category_items["Solid_Top"])
    window.assign_region(0, 0, 1, 1)
    window.assign_region(1, 0, 1, 1)
    assert window.undo_stack.count() == 2
    window.assignment_list.setCurrentRow(1)
    window.reorder(-1)
    assert window.model.assets("Solid_Top")[0].x_cell == 1
    window.undo_stack.undo()
    assert window.model.assets("Solid_Top")[0].x_cell == 0
    window.category_tree.setCurrentItem(window.category_items["Wall_Top"])
    window.assign_region(0, 0, 1, 1)
    assert len(window.model.assets("Wall_Top")) == 1
    window.undo_stack.undo()
    assert not window.model.assets("Wall_Top")
    window.undo_stack.redo()
    assert len(window.model.assets("Wall_Top")) == 1
    window.assignment_list.setCurrentRow(0)
    window.remove_selected()
    assert not window.model.assets("Wall_Top")
    window.undo_stack.undo()
    assert len(window.model.assets("Wall_Top")) == 1
    window.undo_stack.undo()
    window.category_tree.setCurrentItem(window.category_items["Solid_Bottom"])
    window.assign_region(2, 3, 2, 1)
    assert not window.undo_stack.canRedo()
    assert window.model.assets("Solid_Bottom")[0].width_cells == 2
    window._request_source(source)
    assert window.undo_stack.count() == 0
    window.close()
    qt.processEvents()


def test_top_sequence_uses_normal_multicell_history_order_and_planning(tmp_path: Path) -> None:
    qt = app()
    source = make_sheet(tmp_path / "sheet.png", (160, 160))
    window = MainWindow(ROOT)
    window._load_source(source)
    category = "Solid_TopSequence_Repeat_01"
    window.category_tree.setCurrentItem(window.category_items[category])
    window.assign_region(0, 0, 2, 3)
    window.assign_region(2, 0, 1, 1)
    window.assignment_list.setCurrentRow(1)
    window.reorder(-1)
    assert window.model.assets(category)[0].origin == (2, 0)
    window.undo_stack.undo()
    assert window.model.assets(category)[0].origin == (0, 0)
    window.undo_stack.redo()
    current = build_export_plan(tmp_path / "current", window.model, window.rules, category)
    complete = build_export_plan(tmp_path / "all", window.model, window.rules)
    assert [item.output_path.name for item in current] == [
        f"{category}_00.png", f"{category}_01.png"
    ]
    assert [item.output_path.name for item in complete] == [
        f"{category}_00.png", f"{category}_01.png"
    ]
    assert current[1].assignment.output_height_px == 96
    window.close()
    qt.processEvents()


def test_layer_visibility_render_and_undo_preserve_assignments(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source_path = tmp_path / "layers.aseprite"
    source_path.write_bytes(b"fixture")
    layers = (
        AsepriteLayer("1", "Group", "group", True, (
            AsepriteLayer("1/1", "Ink", "image", True),
        )),
    )

    def fake_load(path, visibility=None, alignment_offsets=None):
        resolved = {"1": True, "1/1": True}
        resolved.update(visibility or {})
        current_layers = (
            AsepriteLayer("1", "Group", "group", resolved["1"], (
                AsepriteLayer("1/1", "Ink", "image", resolved["1/1"]),
            )),
        )
        color = (255, 0, 0, 255) if resolved["1/1"] else (0, 0, 0, 0)
        return LoadedSource(Image.new("RGBA", (512, 512), color), current_layers, resolved)

    monkeypatch.setattr("tilenamer.ui.load_source_document", fake_load)
    window = MainWindow(ROOT)
    assert window._load_source(source_path)
    window.category_tree.setCurrentItem(window.category_items["Solid_Top"])
    window.assign_region(0, 0, 1, 1)
    window.show()
    qt.processEvents()
    window._set_zoom(2.0)
    window.viewport_scroll.horizontalScrollBar().setValue(120)
    window.viewport_scroll.verticalScrollBar().setValue(90)
    layer_item = window.layer_tree.topLevelItem(0).child(0)
    layer_item.setCheckState(0, Qt.CheckState.Unchecked)
    qt.processEvents()
    assert not window.layer_visibility["1/1"]
    assert len(window.model.assets("Solid_Top")) == 1
    assert window.canvas.zoom == 2.0
    assert window.viewport_scroll.horizontalScrollBar().value() == 120
    assert window.viewport_scroll.verticalScrollBar().value() == 90
    window.undo_stack.undo()
    assert window.layer_visibility["1/1"]
    assert len(window.model.assets("Solid_Top")) == 1
    window.undo_stack.redo()
    assert not window.layer_visibility["1/1"]
    window.undo_stack.undo()
    group_item = window.layer_tree.topLevelItem(0)
    group_item.setCheckState(0, Qt.CheckState.Unchecked)
    qt.processEvents()
    assert not window.layer_visibility["1"]
    assert window.layer_visibility["1/1"]
    window.close()


def test_aseprite_cli_metadata_pipeline_does_not_modify_source(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.aseprite"
    source.write_bytes(b"original-document")
    executable = tmp_path / "Aseprite.exe"
    executable.write_bytes(b"")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        output = Path(next(value.split("=", 1)[1] for value in command if value.startswith("output=")))
        metadata = Path(next(value.split("=", 1)[1] for value in command if value.startswith("metadata=")))
        Image.new("RGBA", (32, 32), (1, 2, 3, 4)).save(output)
        metadata.write_text(json.dumps({
            "canvas": {"width": 32, "height": 32},
            "document_grid": {"x": 4, "y": 0, "width": 32, "height": 32},
            "layers": [{
                "identity": "1", "name": "Ink", "kind": "tilemap", "visible": False,
                "uuid": "fixture-uuid", "cel_x": 2, "cel_y": -1,
                "cels": [{"frame_index": 0, "x": 2, "y": -1,
                          "width": 16, "height": 16, "opacity": 255}],
                "grid_origin_x": 0, "grid_origin_y": 0,
                "grid_width": 32, "grid_height": 32, "children": [],
            }],
        }), encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr("tilenamer.image_loader.subprocess.run", fake_run)
    loaded = render_aseprite_document(source, {"1": False}, executable)
    assert source.read_bytes() == b"original-document"
    assert loaded.image.size == (32, 32)
    assert loaded.layers[0].identity == "1"
    assert loaded.layers[0].uuid == "fixture-uuid"
    assert (loaded.layers[0].cel_x, loaded.layers[0].cel_y) == (2, -1)
    assert (loaded.layers[0].grid_origin_x, loaded.layers[0].grid_origin_y) == (2, -1)
    assert loaded.layer_visibility == {"1": False}
    assert loaded.document_grid.origin_x == 4
    assert "visibility=1=0" in captured["command"]


def test_ctrl_wheel_zoom_and_space_pan_do_not_edit_assignments(tmp_path: Path) -> None:
    qt = app()
    source = make_sheet(tmp_path / "large.png", (1024, 1024))
    window = MainWindow(ROOT)
    window._load_source(source)
    window.show()
    qt.processEvents()
    event = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50), QPoint(), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(window.canvas, event)
    assert event.isAccepted()
    assert window.canvas.zoom == 2.0
    event_out = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50), QPoint(), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(window.canvas, event_out)
    assert event_out.isAccepted()
    assert window.canvas.zoom == 1.0
    window._set_zoom(2.0)
    before = window.model.as_json()
    horizontal = window.viewport_scroll.horizontalScrollBar()
    horizontal.setValue(200)
    QTest.keyPress(window.canvas, Qt.Key.Key_Space)
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
    QTest.mouseMove(window.canvas, QPoint(350, 300), delay=1)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(350, 300))
    QTest.keyRelease(window.canvas, Qt.Key.Key_Space)
    assert horizontal.value() < 200
    assert window.model.as_json() == before
    assert window.undo_stack.count() == 0
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(208, 208))
    qt.processEvents()
    assert window.model.assets("Platform_Center")[0].origin == (3, 3)
    window.close()
    qt.processEvents()
