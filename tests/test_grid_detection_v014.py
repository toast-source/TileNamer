from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PIL.ImageQt import ImageQt
from PySide6.QtGui import QImage
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from tilenamer.config import load_categories
from tilenamer.exporter import build_export_plan, export_tiles
from tilenamer.grid import GridReference
from tilenamer.grid_detection import CelMetadata, detect_layer_grid
from tilenamer.image_loader import AsepriteLayer, LoadedSource
from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.preferences import Preferences
from tilenamer.thumbnail import build_assignment_thumbnail
from tilenamer.ui import MainWindow, TileCanvas


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(tmp_path: Path) -> Preferences:
    return Preferences(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


def periodic_fixture(origin: tuple[int, int] = (3, 1)) -> Image.Image:
    image = Image.new("RGBA", (160, 160))
    draw = ImageDraw.Draw(image)
    for row in range(5):
        for column in range(5):
            draw.rectangle(
                (origin[0] + column * 32, origin[1] + row * 32,
                 origin[0] + column * 32 + 31, origin[1] + row * 32 + 31),
                fill=((column % 3) * 100, (row % 3) * 100, 50, 255),
            )
    return image


def test_cel_position_phase_and_multiple_frame_consistency() -> None:
    one = detect_layer_grid(
        "image", (CelMetadata(0, 3, 1, 20, 20),), (128, 128),
        document_origin=(0, 0),
    )
    assert (one.origin, one.confidence, one.method) == ((3, 1), "high", "cel-position")
    multiple = detect_layer_grid(
        "image",
        (CelMetadata(0, 3, 1, 20, 20), CelMetadata(1, 35, 33, 20, 20)),
        (128, 128), document_origin=(0, 0),
    )
    assert (multiple.origin, multiple.confidence) == ((3, 1), "high")


def test_inconsistent_cel_phase_is_not_silently_accepted() -> None:
    result = detect_layer_grid(
        "image",
        (CelMetadata(0, 3, 1, 20, 20), CelMetadata(1, 4, 1, 20, 20)),
        (128, 128), document_origin=(0, 0),
    )
    assert result.origin == (0, 0)
    assert result.confidence == "low"
    assert result.method == "inconsistent-cel-phase"


def test_full_canvas_pixel_periodicity_searches_the_layer_layout() -> None:
    image = periodic_fixture((3, 1))
    result = detect_layer_grid(
        "image", (CelMetadata(0, 0, 0, 160, 160),), (160, 160),
        layer_images=(image,), document_origin=(0, 0),
    )
    assert result.origin == (3, 1)
    assert result.confidence == "high"
    assert result.method == "pixel-periodicity"


def test_uniform_full_canvas_uses_explicit_low_confidence_fallback() -> None:
    image = Image.new("RGBA", (128, 128), (20, 30, 40, 255))
    result = detect_layer_grid(
        "image", (CelMetadata(0, 0, 0, 128, 128),), (128, 128),
        layer_images=(image,), document_origin=(2, 1),
    )
    assert result.origin == (2, 1)
    assert result.confidence == "low"
    assert result.method == "document-grid-fallback"


def test_tilemap_combines_canvas_cel_position_with_native_tileset_origin() -> None:
    result = detect_layer_grid(
        "tilemap", (CelMetadata(0, 416, 34, 352, 448),), (800, 1119),
        tileset_origin=(0, 0), document_origin=(0, 0),
    )
    assert result.origin == (0, 2)
    assert result.confidence == "high"
    assert result.method == "tilemap-cel+tileset"


def detected_layer(identity: str, origin: tuple[int, int]) -> AsepriteLayer:
    return AsepriteLayer(
        identity, "Detected", "image", True,
        grid_origin_x=origin[0], grid_origin_y=origin[1],
        grid_confidence="high", grid_detection_method="cel-position",
        grid_detection_score=1.0,
    )


def test_reload_redetects_automatic_values_but_preserves_manual_override(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path))
    image = Image.new("RGBA", (96, 96))
    path = tmp_path / "source.aseprite"
    window._apply_source(path, LoadedSource(image, (detected_layer("a", (3, 1)),), {"a": True}), False)
    assert window.layer_grid_origins == {"a": (3, 1)}

    window._apply_source(
        path, LoadedSource(image, (detected_layer("a", (5, 2)),), {"a": True}),
        True, preserve_grid_settings=True,
    )
    assert window.layer_grid_origins == {"a": (5, 2)}

    window.layer_grid_origins["a"] = (7, 4)
    window.layer_grid_manual_overrides.add("a")
    window._apply_source(
        tmp_path / "replacement.aseprite",
        LoadedSource(image, (detected_layer("a", (9, 6)),), {"a": True}),
        True, preserve_grid_settings=True,
    )
    assert window.layer_grid_origins == {"a": (7, 4)}
    assert window.layer_grid_manual_overrides == {"a"}
    window.close()
    qt.processEvents()


def test_automatic_detection_flows_to_paint_selection_thumbnail_and_export_coordinates(
    tmp_path: Path,
) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path))
    source = Image.new("RGBA", (96, 96))
    for y in range(96):
        for x in range(96):
            source.putpixel((x, y), (x, y, 0, 255))
    window._apply_source(
        tmp_path / "detected.aseprite",
        LoadedSource(source, (detected_layer("a", (3, 1)),), {"a": True}), False,
    )
    index = next(
        value for value in range(window.grid_reference_combo.count())
        if window.grid_reference_combo.itemData(value) == ("layer", "a")
    )
    window.grid_reference_combo.setCurrentIndex(index)
    reference = window.grid_reference
    assert reference == GridReference(32, 32, 3, 1, "layer", "a")
    assert reference.cell_at(35, 33, 96, 96) == (1, 1)

    assignment = AssetAssignment("Platform_Center", 1, 1)
    thumbnail = build_assignment_thumbnail(source, assignment, reference, 32)
    assert thumbnail.getpixel((0, 0)) == source.getpixel((35, 33))
    assert reference.pixel_rect(assignment) == (35, 33, 67, 65)
    model = AssignmentModel({"Platform_Center": [assignment]})
    plan = build_export_plan(tmp_path / "export", model, load_categories(ROOT / "tile_names.json"))
    written = export_tiles(source, plan, grid=reference)
    with Image.open(written[0]) as exported:
        assert exported.getpixel((0, 0)) == source.getpixel((35, 33))

    canvas = TileCanvas()
    canvas.set_qimage_content(ImageQt(source).copy(), AssignmentModel())
    canvas.set_grid_reference(reference)
    painted = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    canvas.render(painted)
    assert painted.pixelColor(35, 10) != painted.pixelColor(34, 10)
    canvas.set_zoom(4.0)
    painted_400 = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    canvas.render(painted_400)
    assert painted_400.pixelColor(140, 40) != painted_400.pixelColor(139, 40)
    canvas.close()
    window.close()
    qt.processEvents()
