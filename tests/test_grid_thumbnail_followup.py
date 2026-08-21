import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox

from tilenamer.config import CategoryRule
from tilenamer.exporter import build_export_plan, export_tiles
from tilenamer.grid import GridReference
from tilenamer.image_loader import AsepriteLayer, LoadedSource
from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.thumbnail import build_assignment_thumbnail
from tilenamer.ui import MainWindow


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_nonzero_grid_transform_is_shared_by_selection_and_export(tmp_path: Path) -> None:
    reference = GridReference(32, 32, 2, 1, "layer", "terrain")
    asset = AssetAssignment("Solid_Top", 1, 2, 2, 1)
    assert reference.pixel_rect(asset) == (34, 65, 98, 97)
    assert reference.cell_at(35, 66, 128, 128) == (1, 2)
    assert reference.contains(asset, 128, 128)
    source = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    for y in range(65, 97):
        for x in range(34, 98):
            source.putpixel((x, y), (5, 60, 200, 255))
    model = AssignmentModel({"Solid_Top": [asset]})
    plan = build_export_plan(tmp_path, model, [CategoryRule("Solid_Top", "Solid_Top")])
    written = export_tiles(source, plan, grid=reference)
    with Image.open(written[0]) as result:
        assert result.size == (64, 32)
        assert result.getpixel((0, 0)) == (5, 60, 200, 255)


def test_thumbnail_fit_transparency_and_current_source_refresh() -> None:
    grid = GridReference()
    transparent = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    transparent.paste((255, 0, 0, 128), (0, 0, 64, 32))
    one = build_assignment_thumbnail(transparent, AssetAssignment("A", 0, 0), grid)
    wide = build_assignment_thumbnail(transparent, AssetAssignment("A", 0, 0, 2, 1), grid)
    square = build_assignment_thumbnail(transparent, AssetAssignment("A", 0, 0, 2, 2), grid)
    assert one.size == wide.size == square.size == (48, 48)
    assert one.getpixel((24, 24)) != (255, 0, 0, 128)
    assert wide.getpixel((24, 24))[0] > wide.getpixel((24, 24))[2]
    replacement = Image.new("RGBA", (128, 128), (0, 255, 0, 255))
    refreshed = build_assignment_thumbnail(replacement, AssetAssignment("A", 0, 0, 2, 1), grid)
    assert refreshed.getpixel((24, 24))[:3] == (0, 255, 0)


def test_canvas_nonzero_origin_selection_mapping(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (128, 128), (10, 20, 30, 255)).save(source)
    window = MainWindow(ROOT)
    window._load_source(source)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.grid_reference = GridReference(32, 32, 2, 1, "layer", "fixture")
    window.canvas.set_grid_reference(window.grid_reference)
    window.canvas.set_zoom(2.0)
    QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(70, 68))
    qt.processEvents()
    assert window.model.assets("Platform_Center")[0].origin == (1, 1)
    window.close()


def test_grid_reference_change_with_assignments_requires_confirmation(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (128, 128), (1, 2, 3, 255)).save(source)
    window = MainWindow(ROOT)
    window._load_source(source)
    window.document_grid = GridReference(32, 32, 2, 1, "document")
    window._populate_grid_references()
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(0, 0, 1, 1)
    window.assignment_list.setCurrentRow(0)
    document_index = next(
        index for index in range(window.grid_reference_combo.count())
        if window.grid_reference_combo.itemData(index) == ("document", None)
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    window.grid_reference_combo.setCurrentIndex(document_index)
    assert window.grid_reference == GridReference()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.grid_reference_combo.setCurrentIndex(document_index)
    assert (window.grid_reference.origin_x, window.grid_reference.origin_y) == (2, 1)
    assert window.undo_stack.count() == 0
    window.close()
    qt.processEvents()


def test_layer_order_repeated_visibility_and_signal_safety(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source_path = tmp_path / "layers.aseprite"
    source_path.write_bytes(b"fixture")

    def layers_for(visibility):
        state = {"1": True, "2": True, "2/1": True, "2/2": True, "3": True}
        state.update(visibility or {})
        return (
            AsepriteLayer("1", "Bottom", "image", state["1"]),
            AsepriteLayer("2", "Middle", "group", state["2"], (
                AsepriteLayer("2/1", "Child Bottom", "image", state["2/1"]),
                AsepriteLayer("2/2", "Child Top", "image", state["2/2"]),
            )),
            AsepriteLayer("3", "Top", "image", state["3"]),
        )

    def fake_load(path, visibility=None, alignment_offsets=None):
        current = {layer.identity: layer.visible for layer in layers_for(visibility)}
        current.update({"2/1": (visibility or {}).get("2/1", True),
                        "2/2": (visibility or {}).get("2/2", True)})
        if not current["2"]:
            color = (20, 230, 20, 255)
        else:
            color = (230, 20, 20, 255) if current["3"] else (20, 20, 230, 255)
        return LoadedSource(Image.new("RGBA", (128, 128), color), layers_for(visibility), current)

    monkeypatch.setattr("tilenamer.ui.load_source_document", fake_load)
    window = MainWindow(ROOT)
    window._load_source(source_path)
    assert [window.layer_tree.topLevelItem(i).text(0) for i in range(3)] == [
        "Top", "Middle", "Bottom"
    ]
    group = window.layer_tree.topLevelItem(1)
    assert [group.child(i).text(0) for i in range(2)] == ["Child Top", "Child Bottom"]
    top_item = window.layer_tree.topLevelItem(0)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(0, 0, 1, 1)
    window.assignment_list.setCurrentRow(0)
    red = window.assignment_list.item(0).icon().pixmap(48, 48).toImage().pixelColor(24, 24)
    assert red.red() > red.blue()
    top_item.setCheckState(0, Qt.CheckState.Unchecked)
    qt.processEvents()
    blue = window.assignment_list.item(0).icon().pixmap(48, 48).toImage().pixelColor(24, 24)
    assert blue.blue() > blue.red()
    assert window.assignment_list.currentRow() == 0
    top_item.setCheckState(0, Qt.CheckState.Checked)
    for _ in range(5):
        top_item.setCheckState(0, Qt.CheckState.Unchecked)
        qt.processEvents()
        assert not window.layer_visibility["3"]
        top_item.setCheckState(0, Qt.CheckState.Checked)
        qt.processEvents()
        assert window.layer_visibility["3"]
    group.setCheckState(0, Qt.CheckState.Unchecked)
    qt.processEvents()
    assert not window.layer_visibility["2"] and window.layer_visibility["2/1"]
    assert window.source_image.getpixel((0, 0))[1] == 230
    window.undo_stack.undo()
    assert window.layer_visibility["2"]
    assert window.source_image.getpixel((0, 0))[0] == 230
    window.undo_stack.redo()
    assert not window.layer_visibility["2"]
    assert window.source_image.getpixel((0, 0))[1] == 230
    window.close()


def test_alignment_mismatch_manual_offsets_and_grid_choice(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source_path = tmp_path / "tilemaps.aseprite"
    source_path.write_bytes(b"fixture")
    layers = (
        AsepriteLayer("1", "Base", "tilemap", True, grid_origin_x=0, grid_origin_y=0,
                      grid_width=32, grid_height=32),
        AsepriteLayer("2", "Terrain", "tilemap", True, grid_origin_x=2, grid_origin_y=-1,
                      grid_width=32, grid_height=32),
    )

    def fake_load(path, visibility=None, alignment_offsets=None):
        color = (0, 200, 0, 255) if alignment_offsets else (200, 0, 0, 255)
        return LoadedSource(
            Image.new("RGBA", (128, 128), color), layers, {"1": True, "2": True},
            GridReference(32, 32, 0, 0, "document"),
        )

    monkeypatch.setattr("tilenamer.ui.load_source_document", fake_load)
    monkeypatch.setattr(MainWindow, "_show_alignment_mismatch_if_needed", lambda self: None)
    window = MainWindow(ROOT)
    window._load_source(source_path)
    assert window.layer_grid_origins == {"1": (0, 0), "2": (2, -1)}
    assert window.layer_alignment_offsets == {}
    assert window.alignment_mismatches() == {"2": (2, -1)}
    terrain = window.layer_tree.topLevelItem(0)
    assert terrain.text(0) == "Terrain"
    window.layer_tree.setCurrentItem(terrain)
    window.layer_offset_x.setValue(3)
    window.layer_offset_y.setValue(-2)
    assert window.layer_alignment_offsets["2"] == (3, -2)
    window._alignment_edit_finished()
    layer_index = next(
        index for index in range(window.grid_reference_combo.count())
        if window.grid_reference_combo.itemData(index) == ("layer", "2")
    )
    window.grid_reference_combo.setCurrentIndex(layer_index)
    assert (window.grid_reference.origin_x, window.grid_reference.origin_y) == (2, -1)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(0, 1, 1, 1)
    assert (window.grid_reference.origin_x, window.grid_reference.origin_y) == (2, -1)
    assert window.source_image.getpixel((0, 0))[:3] == (0, 200, 0)
    after_icon = window.assignment_list.item(0).icon().pixmap(48, 48).toImage().pixelColor(24, 24)
    assert after_icon.green() > after_icon.red()
    window.layer_offset_reset.click()
    assert window.layer_alignment_offsets["2"] == (0, 0)
    window.close()


def test_source_swap_preserves_matching_grid_identity_and_resets_mismatch(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    first = tmp_path / "first.aseprite"
    variant = tmp_path / "variant.aseprite"
    other = tmp_path / "other.aseprite"
    for path in (first, variant, other):
        path.write_bytes(path.name.encode())

    def fake_load(path, visibility=None, alignment_offsets=None):
        identity = "9" if Path(path).stem == "other" else "1"
        layers = (AsepriteLayer(identity, "Terrain", "image", True),)
        return LoadedSource(
            Image.new("RGBA", (128, 128), (20, 30, 40, 255)), layers,
            {identity: (visibility or {}).get(identity, True)},
            GridReference(32, 32, 0, 0, "document"),
        )

    monkeypatch.setattr("tilenamer.ui.load_source_document", fake_load)
    monkeypatch.setattr(MainWindow, "_show_alignment_mismatch_if_needed", lambda self: None)
    window = MainWindow(ROOT)
    window._load_source(first)
    window.layer_grid_origins = {"1": (2, 1)}
    window.layer_grid_manual_overrides = {"1"}
    window.layer_alignment_offsets = {"1": (2, 1)}
    window.grid_reference = GridReference(32, 32, 2, 1, "layer", "1")
    window.canvas.set_grid_reference(window.grid_reference)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(0, 0, 1, 1)
    monkeypatch.setattr(window, "_choose_source_mode", lambda path: "keep")
    assert window._request_source(variant)
    assert window.layer_alignment_offsets == {"1": (2, 1)}
    assert window.layer_grid_origins == {"1": (2, 1)}
    assert window.grid_reference.layer_identity == "1"
    assert window._request_source(other)
    assert window.layer_alignment_offsets == {}
    assert window.layer_grid_origins == {"9": (0, 0)}
    assert window.grid_reference == GridReference()
    assert len(window.model.assets("Platform_Center")) == 1
    window.close()
    qt.processEvents()
