from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tilenamer.config import CategoryRule, load_categories
from tilenamer.exporter import build_export_plan, export_tiles, extract_assignment_image
from tilenamer.grid import GridReference
from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.preferences import Preferences
from tilenamer.project import TileProject
from tilenamer.thumbnail import build_assignment_thumbnail
from tilenamer.ui import MainWindow, TileCanvas


ROOT = Path(__file__).resolve().parents[1]
L_CELLS = ((0, 0), (0, 1), (0, 2), (1, 2), (2, 2))
U_CELLS = ((0, 0), (1, 0), (2, 0), (0, 1), (0, 2), (1, 2), (2, 2))
STAIR_CELLS = ((0, 0), (0, 1), (1, 1), (1, 2), (2, 2))


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(tmp_path: Path) -> Preferences:
    return Preferences(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


def colored_cells(origin: tuple[int, int] = (0, 0)) -> Image.Image:
    image = Image.new("RGBA", (100, 100))
    colors = [
        (255, 0, 0, 180), (0, 255, 0, 190), (0, 0, 255, 200),
        (255, 255, 0, 210), (255, 0, 255, 220), (0, 255, 255, 230),
        (120, 30, 20, 240), (30, 120, 20, 250), (20, 30, 120, 255),
    ]
    for row in range(3):
        for column in range(3):
            color = colors[row * 3 + column]
            left, top = origin[0] + column * 32, origin[1] + row * 32
            tile = Image.new("RGBA", (32, 32), color)
            image.alpha_composite(tile, (left, top))
    return image


def test_shape_model_normalizes_duplicates_order_and_bounds() -> None:
    one = AssetAssignment.from_cells("A", [(4, 7), (4, 7)])
    assert one.selected_cells == ((4, 7),)
    assert one.cell_count == 1 and one.is_rectangular

    rectangle = AssetAssignment.from_cells("A", [(2, 3), (3, 4), (3, 3), (2, 4)])
    assert (rectangle.x_cell, rectangle.y_cell, rectangle.width_cells, rectangle.height_cells) == (2, 3, 2, 2)
    assert rectangle.is_rectangular

    for cells in (L_CELLS, U_CELLS, STAIR_CELLS):
        shape = AssetAssignment.from_cells("A", reversed(cells))
        assert shape.cell_count == len(set(cells))
        assert not shape.is_rectangular
        assert (shape.width_cells, shape.height_cells) == (3, 3)
        assert shape.same_region(AssetAssignment.from_cells("B", cells))


def test_shape_collision_toggle_move_reorder_and_round_trip() -> None:
    model = AssignmentModel()
    assert model.assign_cells("A", L_CELLS).status == "added"
    assert model.assign_cells("A", reversed(L_CELLS)).status == "removed"
    model.assign_cells("A", L_CELLS)
    assert model.assign_cells("B", reversed(L_CELLS)).status == "moved"
    conflict = model.assign_cells("A", [(2, 2), (3, 2)])
    assert conflict.status == "conflict"
    assert conflict.conflict is not None
    model.assign_cells("B", [(5, 5)])
    assert model.move("B", 1, -1) == 0
    restored = AssignmentModel.from_json(model.as_json())
    assert restored.as_json() == model.as_json()


def test_irregular_export_has_transparent_holes_and_preserves_alpha(tmp_path: Path) -> None:
    source = colored_cells()
    asset = AssetAssignment.from_cells("Platform_Center", L_CELLS)
    extracted = extract_assignment_image(source, asset, GridReference())
    assert extracted.size == (96, 96)
    for column, row in L_CELLS:
        assert extracted.getpixel((column * 32 + 16, row * 32 + 16)) == source.getpixel(
            (column * 32 + 16, row * 32 + 16)
        )
    for column, row in ((1, 0), (2, 0), (1, 1), (2, 1)):
        assert extracted.getpixel((column * 32 + 16, row * 32 + 16))[3] == 0

    model = AssignmentModel({"Platform_Center": [asset]})
    plan = build_export_plan(tmp_path, model, load_categories(ROOT / "tile_names.json"))
    written = export_tiles(source, plan)
    with Image.open(written[0]) as exported:
        assert exported.size == (96, 96)
        assert exported.getpixel((48, 16))[3] == 0
        assert exported.getpixel((16, 16))[3] == 180


def test_rectangle_fast_path_and_nonzero_grid_origin() -> None:
    source = colored_cells((3, 2))
    grid = GridReference(32, 32, 3, 2, "layer", "fixture")
    rectangle = AssetAssignment("A", 0, 0, 2, 2)
    assert extract_assignment_image(source, rectangle, grid).tobytes() == source.crop((3, 2, 67, 66)).convert("RGBA").tobytes()
    irregular = AssetAssignment.from_cells("A", L_CELLS)
    extracted = extract_assignment_image(source, irregular, grid)
    assert extracted.getpixel((16, 16)) == source.getpixel((19, 18))
    assert extracted.getpixel((48, 16))[3] == 0


def test_irregular_temporary_tag_and_top_sequence_keep_filename_policy(tmp_path: Path) -> None:
    source = colored_cells()
    top_name = "Solid_TopSequence_Start_00"
    top_rule = next(rule for rule in load_categories(ROOT / "tile_names.json") if rule.name == top_name)
    temporary_model = AssignmentModel({
        "Special_LCorner": [AssetAssignment.from_cells("Special_LCorner", L_CELLS)],
    })
    top_model = AssignmentModel({top_name: [AssetAssignment.from_cells(top_name, U_CELLS)]})
    temporary_plan = build_export_plan(
        tmp_path / "temporary", temporary_model, [CategoryRule("Special_LCorner", "Special_LCorner")],
    )
    top_plan = build_export_plan(tmp_path / "top", top_model, [top_rule])
    written = export_tiles(source, temporary_plan) + export_tiles(source, top_plan)
    assert {path.name for path in written} == {
        "Special_LCorner_00.png", top_rule.filename(0),
    }
    with Image.open(next(path for path in written if path.name == "Special_LCorner_00.png")) as image:
        assert image.getpixel((48, 16))[3] == 0


def test_v8_project_shape_and_v1_through_v7_rectangle_migration(tmp_path: Path) -> None:
    path = tmp_path / "shape.tilenamer.json"
    project = TileProject(
        "sheet.png", 32,
        AssignmentModel({"Special_LCorner": [
            AssetAssignment.from_cells("Special_LCorner", L_CELLS),
            AssetAssignment.from_cells(
                "Special_LCorner", ((x + 4, y) for x, y in STAIR_CELLS),
            ),
        ]}),
        temporary_tags=["Special_LCorner"],
    )
    project.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format_version"] == 8
    restored = TileProject.load(path)
    assert restored.temporary_tags == ["Special_LCorner"]
    assert restored.model.assets("Special_LCorner")[0].selected_cells == L_CELLS
    assert restored.model.assets("Special_LCorner")[1].origin == (4, 0)

    for version in range(1, 8):
        old = tmp_path / f"v{version}.json"
        assignments = {"A": [[2, 3]]} if version == 1 else {
            "A": [{"x_cell": 2, "y_cell": 3, "width_cells": 2, "height_cells": 2}]
        }
        old.write_text(json.dumps({
            "format_version": version, "source_file": "sheet.png", "tile_size": 32,
            "assignments": assignments,
        }), encoding="utf-8")
        asset = TileProject.load(old).model.assets("A")[0]
        expected = {(2, 3)} if version == 1 else {(2, 3), (3, 3), (2, 4), (3, 4)}
        assert asset.occupied_cells() == expected


def test_thumbnail_hole_and_canvas_selected_shape_only() -> None:
    qt = app()
    source = colored_cells()
    asset = AssetAssignment.from_cells("A", L_CELLS)
    thumbnail = build_assignment_thumbnail(source, asset, GridReference(), 96)
    assert thumbnail.getpixel((16, 16))[0] > thumbnail.getpixel((48, 16))[0]
    assert thumbnail.getpixel((48, 16)) in ((122, 122, 122, 255), (96, 96, 96, 255))

    canvas = TileCanvas()
    model = AssignmentModel({"A": [asset]})
    canvas.set_content(source, model)
    canvas.set_category("A")
    canvas.set_selected_assignment(0)
    rendered = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    canvas.render(rendered)
    selected = rendered.pixelColor(16, 16)
    hole = rendered.pixelColor(48, 16)
    assert selected != hole
    assert hole.green() > hole.red()  # original green B cell is not cyan-filled.
    canvas.close()
    qt.processEvents()


def test_cell_paint_drag_add_duplicate_remove_and_nonzero_grid() -> None:
    qt = app()
    canvas = TileCanvas()
    canvas.set_content(Image.new("RGBA", (100, 100), "white"), AssignmentModel())
    canvas.set_category("A")
    canvas.set_selection_mode("paint")
    canvas.set_grid_reference(GridReference(32, 32, 3, 2, "layer", "fixture"))
    emitted = []
    canvas.cells_selected.connect(emitted.append)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(19, 18))
    QTest.mouseMove(canvas, QPoint(19, 50), delay=1)
    QTest.mouseMove(canvas, QPoint(19, 82), delay=1)
    QTest.mouseMove(canvas, QPoint(51, 82), delay=1)
    QTest.mouseMove(canvas, QPoint(83, 82), delay=1)
    QTest.mouseMove(canvas, QPoint(51, 82), delay=1)  # duplicate revisit
    QTest.keyPress(canvas, Qt.Key.Key_Control)
    QTest.mouseMove(canvas, QPoint(19, 50), delay=1)  # remove middle-left cell
    QTest.keyRelease(canvas, Qt.Key.Key_Control)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(19, 50))
    assert len(emitted) == 1
    assert set(emitted[0]) == {(0, 0), (0, 2), (1, 2), (2, 2)}
    canvas.close()
    qt.processEvents()


def test_irregular_assignment_undo_redo_and_alignment_ui_is_single_state(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path))
    window.source_image = colored_cells()
    window.canvas.set_content(window.source_image, window.model)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_cells(L_CELLS)
    assert window.model.assets("Platform_Center")[0].selected_cells == L_CELLS
    window.undo_stack.undo()
    assert not window.model.assets("Platform_Center")
    window.undo_stack.redo()
    assert window.model.assets("Platform_Center")[0].selected_cells == L_CELLS
    window.assign_cells(reversed(L_CELLS))
    assert not window.model.assets("Platform_Center")
    window.undo_stack.undo()
    assert window.model.assets("Platform_Center")[0].selected_cells == L_CELLS
    window.category_tree.setCurrentItem(window.category_items["Solid_Top"])
    window.assign_cells(L_CELLS)
    assert not window.model.assets("Platform_Center")
    assert window.model.assets("Solid_Top")[0].selected_cells == L_CELLS
    window.undo_stack.undo()
    assert window.model.assets("Platform_Center")[0].selected_cells == L_CELLS
    window.undo_stack.redo()
    shifted = tuple((x + 4, y) for x, y in STAIR_CELLS)
    window.assign_cells(shifted)
    window.assignment_list.setCurrentRow(1)
    window.reorder(-1)
    assert window.model.assets("Solid_Top")[0].selected_cells == tuple(
        sorted(shifted, key=lambda value: (value[1], value[0]))
    )
    window.undo_stack.undo()
    assert window.model.assets("Solid_Top")[0].selected_cells == L_CELLS
    window.undo_stack.redo()
    assert not hasattr(window, "alignment_correction_check")
    assert window.auto_alignment_button.text() == "기준 격자에 자동 맞춤"
    assert window.selection_mode_combo.itemData(0) == "rectangle"
    assert window.selection_mode_combo.itemData(1) == "paint"
    window.close()
    qt.processEvents()


def test_cell_paint_zoom_mapping_and_space_drag_does_not_select(tmp_path: Path) -> None:
    qt = app()
    canvas = TileCanvas()
    canvas.set_content(Image.new("RGBA", (100, 100), "white"), AssignmentModel())
    canvas.set_category("A")
    canvas.set_selection_mode("paint")
    canvas.set_grid_reference(GridReference(32, 32, 3, 2, "layer", "fixture"))
    canvas.set_zoom(2.0)
    emitted = []
    canvas.cells_selected.connect(emitted.append)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(38, 36))
    QTest.mouseMove(canvas, QPoint(102, 36), delay=1)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(102, 36))
    assert set(emitted[0]) == {(0, 0), (1, 0)}
    canvas.close()

    window = MainWindow(ROOT, preferences(tmp_path))
    window.source_image = Image.new("RGBA", (256, 256), "white")
    window.canvas.set_content(window.source_image, window.model)
    window.canvas.set_selection_mode("paint")
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    QTest.keyPress(window.canvas, Qt.Key.Key_Space)
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
    QTest.mouseMove(window.canvas, QPoint(80, 40), delay=1)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(80, 40))
    QTest.keyRelease(window.canvas, Qt.Key.Key_Space)
    assert not window.model.assets("Platform_Center")
    window.close()
    qt.processEvents()
