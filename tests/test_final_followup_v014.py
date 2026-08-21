from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from tilenamer.config import load_categories
from tilenamer.exporter import build_export_plan, export_tiles
from tilenamer.grid import GridReference
from tilenamer.image_loader import AsepriteLayer
from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.preferences import Preferences
from tilenamer.resources import WINDOWS_APP_USER_MODEL_ID, set_windows_app_user_model_id
from tilenamer.thumbnail import build_assignment_thumbnail
from tilenamer.ui import MainWindow, TileCanvas


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(tmp_path: Path) -> Preferences:
    return Preferences(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))


def test_layer_grid_origin_sources_and_correction_independence(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path))
    window.layers = (
        AsepriteLayer("a", "Layer A", "image", True),
        AsepriteLayer("b", "Layer B", "image", True),
        AsepriteLayer("c", "Layer C", "image", True),
        AsepriteLayer("tile", "Terrain", "tilemap", True, grid_origin_x=4,
                       grid_origin_y=1, grid_width=32, grid_height=32),
    )
    window.layer_grid_origins = {"a": (0, 0), "b": (3, 2), "c": (-1, 4), "tile": (4, 1)}
    window.layer_alignment_offsets = {"a": (0, 0), "b": (-1, 2), "c": (5, 0), "tile": (9, 9)}
    assert window._reference_from_choice("image", None).origin_x == 0
    assert (window._reference_from_choice("layer", "a").origin_x,
            window._reference_from_choice("layer", "a").origin_y) == (0, 0)
    assert (window._reference_from_choice("layer", "b").origin_x,
            window._reference_from_choice("layer", "b").origin_y) == (3, 2)
    assert (window._reference_from_choice("layer", "c").origin_x,
            window._reference_from_choice("layer", "c").origin_y) == (-1, 4)
    tile_reference = window._reference_from_choice("layer", "tile")
    assert (tile_reference.origin_x, tile_reference.origin_y) == (4, 1)
    assert window._reference_from_choice("layer", "b") == GridReference(32, 32, 3, 2, "layer", "b")
    assert window._reference_from_choice("layer", "tile") == tile_reference
    assert window._active_alignment_corrections()["b"] == (-1, 2)
    assert window._active_alignment_corrections()["a"] == (0, 0)
    window.close()
    qt.processEvents()


def test_layer_dropdown_and_grid_editor_move_overlay_without_changing_alignment(
    tmp_path: Path, monkeypatch,
) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (128, 128), "white").save(source)
    window = MainWindow(ROOT, preferences(tmp_path))
    window._load_source(source)
    window.layers = (AsepriteLayer("b", "Decoration", "image", True),)
    window.layer_visibility = {"b": True}
    window.layer_grid_origins = {"b": (3, 2)}
    window.layer_alignment_offsets = {"b": (-1, 0)}
    window._populate_layer_tree()
    window._populate_grid_references()
    layer_index = next(
        index for index in range(window.grid_reference_combo.count())
        if window.grid_reference_combo.itemData(index) == ("layer", "b")
    )
    assert "(+3, +2)" in window.grid_reference_combo.itemText(layer_index)
    window.grid_reference_combo.setCurrentIndex(layer_index)
    assert window.grid_reference == GridReference(32, 32, 3, 2, "layer", "b")
    assert window.canvas.grid_reference == window.grid_reference
    assert "Grid 32×32 @ (+3, +2)" in window.warning.text()
    window.layer_tree.setCurrentItem(window.layer_tree.topLevelItem(0))
    revision = window.source_revision
    rerenders: list[bool] = []
    monkeypatch.setattr(window, "_rerender_alignment", lambda: rerenders.append(True) or True)
    window.layer_grid_x.setValue(5)
    assert (window.grid_reference.origin_x, window.grid_reference.origin_y) == (5, 2)
    assert window.canvas.grid_reference == window.grid_reference
    assert window.layer_alignment_offsets["b"] == (-1, 0)
    assert window.source_revision == revision
    assert rerenders == []
    window.layer_offset_x.setValue(7)
    assert (window.grid_reference.origin_x, window.grid_reference.origin_y) == (5, 2)
    assert window.layer_grid_origins["b"] == (5, 2)
    window.close()
    qt.processEvents()


def test_shifted_grid_is_shared_by_paint_cell_thumbnail_and_export(tmp_path: Path) -> None:
    qt = app()
    source = Image.new("RGBA", (96, 96))
    for y in range(96):
        for x in range(96):
            source.putpixel((x, y), (x, y, (x + y) % 256, 255))
    reference = GridReference(32, 32, 3, 2, "layer", "b")
    assignment = AssetAssignment("Platform_Center", 1, 1)
    assert reference.pixel_rect(assignment) == (35, 34, 67, 66)
    assert reference.cell_at(35, 34, 96, 96) == (1, 1)

    canvas = TileCanvas()
    canvas.set_content(source, AssignmentModel())
    plain = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    canvas.render(plain)
    canvas.set_grid_reference(reference)
    shifted = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    canvas.render(shifted)
    assert plain.pixelColor(32, 10) != shifted.pixelColor(32, 10)
    assert shifted.pixelColor(35, 10) != plain.pixelColor(35, 10)

    canvas.set_zoom(4.0)
    canvas.set_grid_reference(GridReference())
    plain_400 = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    canvas.render(plain_400)
    canvas.set_grid_reference(reference)
    shifted_400 = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    canvas.render(shifted_400)
    assert plain_400.pixelColor(128, 40) != shifted_400.pixelColor(128, 40)
    assert shifted_400.pixelColor(140, 40) != plain_400.pixelColor(140, 40)
    assert 140 - 128 == 3 * 4

    thumbnail = build_assignment_thumbnail(source, assignment, reference, 32)
    assert thumbnail.getpixel((0, 0)) == source.getpixel((35, 34))
    model = AssignmentModel({"Platform_Center": [assignment]})
    plan = build_export_plan(tmp_path / "export", model, load_categories(ROOT / "tile_names.json"))
    written = export_tiles(source, plan, grid=reference)
    with Image.open(written[0]) as exported:
        assert exported.getpixel((0, 0)) == source.getpixel((35, 34))
    canvas.close()
    qt.processEvents()


def test_windows_app_id_is_stable_and_non_windows_is_noop() -> None:
    calls: list[str] = []

    class Shell32:
        def SetCurrentProcessExplicitAppUserModelID(self, value: str) -> None:
            calls.append(value)

    assert set_windows_app_user_model_id("win32", Shell32())
    assert calls == [WINDOWS_APP_USER_MODEL_ID]
    assert WINDOWS_APP_USER_MODEL_ID == "toast-source.TileNamer"
    assert not set_windows_app_user_model_id("linux", Shell32())
    assert calls == [WINDOWS_APP_USER_MODEL_ID]


def test_fixed_ui_text_fits_in_light_and_dark_at_minimum_window_size(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path))
    window.resize(window.minimumSize())
    window.show()
    qt.processEvents()

    def assert_fits() -> None:
        widgets = (
            *window.toolbar_primary_buttons,
                *window.temporary_tag_buttons,
                window.layer_grid_reset,
                window.auto_alignment_button,
            *window.right_control_buttons,
        )
        for widget in widgets:
            assert widget.width() >= widget.minimumSizeHint().width(), widget.text()

    assert_fits()
    window._set_theme("dark")
    qt.processEvents()
    assert_fits()
    window.close()
    qt.processEvents()
