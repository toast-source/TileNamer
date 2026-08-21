from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from tilenamer.exporter import build_export_plan, export_tiles
from tilenamer.grid import GridReference
from tilenamer.image_loader import AsepriteLayer, LoadedSource
from tilenamer.model import AssignmentModel
from tilenamer.preferences import Preferences
from tilenamer.project import TileProject
from tilenamer.ui import MainWindow, ROLE_CATEGORY


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(path: Path) -> Preferences:
    return Preferences(QSettings(str(path), QSettings.Format.IniFormat))


def find_item(root: QTreeWidgetItem, text: str) -> QTreeWidgetItem | None:
    if root.text(0) == text:
        return root
    for index in range(root.childCount()):
        found = find_item(root.child(index), text)
        if found is not None:
            return found
    return None


def test_theme_alpha_preferences_are_global_and_isolated(tmp_path: Path) -> None:
    qt = app()
    settings_path = tmp_path / "preferences.ini"
    prefs = preferences(settings_path)
    window = MainWindow(ROOT, prefs)
    assert prefs.theme == "light"
    window.toggle_theme()
    prefs.alpha_background = "light"
    window._apply_alpha_background()
    prefs.custom_alpha_color = "#123456"
    prefs.auto_reload_aseprite = False
    prefs.settings.sync()

    restored = preferences(settings_path)
    assert restored.theme == "dark"
    assert restored.alpha_background == "light"
    assert restored.custom_alpha_color == "#123456"
    assert restored.auto_reload_aseprite is False
    assert "#25282c" in window.styleSheet()
    window.close()
    qt.processEvents()


def test_alpha_background_refreshes_thumbnail_without_touching_source_or_export(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "transparent.png"
    Image.new("RGBA", (32, 32), (9, 8, 7, 0)).save(source)
    prefs = preferences(tmp_path / "preferences.ini")
    window = MainWindow(ROOT, prefs)
    window._load_source(source)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(0, 0, 1, 1)
    before = window.assignment_list.item(0).icon().pixmap(48, 48).toImage().pixelColor(24, 24)
    prefs.alpha_background = "light"
    window._apply_alpha_background()
    after = window.assignment_list.item(0).icon().pixmap(48, 48).toImage().pixelColor(24, 24)
    assert before != after
    assert window.source_image.getpixel((0, 0)) == (9, 8, 7, 0)
    written = export_tiles(
        window.source_image,
        build_export_plan(tmp_path / "export", window.model, window.rules),
    )
    with Image.open(written[0]) as result:
        assert result.getpixel((0, 0)) == (9, 8, 7, 0)
    window.close()
    qt.processEvents()


def test_group_nodes_are_not_selectable_and_do_not_change_category(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path / "preferences.ini"))
    leaf = window.category_items["Platform_Center"]
    window.category_tree.setCurrentItem(leaf)
    before = window.current_category()
    labels = {"Platform", "Solid", "Wall", "Bridge", "Top Sequence", "Type 00", "Type 01"}
    found: dict[str, QTreeWidgetItem] = {}
    for index in range(window.category_tree.topLevelItemCount()):
        root = window.category_tree.topLevelItem(index)
        for label in labels:
            item = find_item(root, label)
            if item is not None:
                found[label] = item
    assert set(found) == labels
    for item in found.values():
        assert not item.flags() & Qt.ItemFlag.ItemIsSelectable
        assert not item.data(0, ROLE_CATEGORY)
        window._category_changed(item)
        assert window.current_category() == before
    assert leaf.flags() & Qt.ItemFlag.ItemIsSelectable
    window.close()
    qt.processEvents()


def test_temporary_tag_lifecycle_export_and_undo(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (96, 64), (12, 34, 56, 200)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "preferences.ini"))
    window._load_source(source)
    window.add_temporary_tag("Special_Cliff")
    window.assign_region(0, 0, 1, 1)
    window.assign_region(1, 0, 1, 1)
    window.reorder(-1)
    with pytest.raises(ValueError):
        window.add_temporary_tag("special_cliff")
    for invalid in ("", "bad/name", "CON", "trail."):
        with pytest.raises(ValueError):
            window.add_temporary_tag(invalid)

    window.rename_temporary_tag("Special_Cliff", "Special_Cliff_Red")
    plan = build_export_plan(tmp_path / "output", window.model, window.rules)
    assert [item.output_path.name for item in plan] == [
        "Special_Cliff_Red_00.png", "Special_Cliff_Red_01.png",
    ]
    written = export_tiles(window.source_image, plan)
    assert written[0].parent.name == "TileImages"
    window.undo_stack.undo()
    assert "Special_Cliff" in window.temporary_tags
    window.undo_stack.redo()
    assert "Special_Cliff_Red" in window.temporary_tags
    assert window.remove_temporary_tag("Special_Cliff_Red", confirmed=True)
    assert "Special_Cliff_Red" not in window.temporary_tags
    window.undo_stack.undo()
    assert len(window.model.assets("Special_Cliff_Red")) == 2
    window.close()
    qt.processEvents()


def test_temporary_tags_project_v5_and_old_migration(tmp_path: Path) -> None:
    project_path = tmp_path / "tags.tilenamer.json"
    project = TileProject("sheet.png", 32, AssignmentModel(), temporary_tags=["BossRoom_Pillar"])
    project.save(project_path)
    restored = TileProject.load(project_path)
    assert restored.temporary_tags == ["BossRoom_Pillar"]
    assert json.loads(project_path.read_text(encoding="utf-8"))["format_version"] == 8

    old_path = tmp_path / "old.tilenamer.json"
    payload = json.loads(project_path.read_text(encoding="utf-8"))
    payload["format_version"] = 5
    payload.pop("temporary_tags")
    payload.pop("layer_grid_origins")
    old_path.write_text(json.dumps(payload), encoding="utf-8")
    assert TileProject.load(old_path).temporary_tags == []


def test_export_other_folder_preserves_working_state(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (64, 64), (70, 80, 90, 123)).save(source)
    prefs = preferences(tmp_path / "preferences.ini")
    window = MainWindow(ROOT, prefs)
    window._load_source(source)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(0, 0, 1, 1)
    before = (window.source_path, window.model.as_json(), window.grid_reference, dict(window.layer_visibility))
    destination = tmp_path / "copy"
    monkeypatch.setattr("tilenamer.ui.QFileDialog.getExistingDirectory", lambda *args: str(destination))
    monkeypatch.setattr("tilenamer.ui.QMessageBox.information", lambda *args: None)
    window.export_other_location()
    target = destination / "TileImages" / "Platform_Center_00.png"
    assert target.exists()
    with Image.open(target) as exported:
        assert exported.getpixel((0, 0)) == (70, 80, 90, 123)
    assert before == (window.source_path, window.model.as_json(), window.grid_reference, dict(window.layer_visibility))
    assert prefs.last_export_directory == str(destination)
    window.close()
    qt.processEvents()


def test_resource_replace_is_transactional_and_preserves_tags(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    small = tmp_path / "small.png"
    Image.new("RGBA", (64, 64), "red").save(first)
    Image.new("RGBA", (96, 96), "green").save(second)
    Image.new("RGBA", (32, 32), "blue").save(small)
    window = MainWindow(ROOT, preferences(tmp_path / "preferences.ini"))
    window._load_source(first)
    window.add_temporary_tag("Local_Test")
    window.assign_region(1, 1, 1, 1)
    revision = window.source_revision
    assert window._replace_resource_path(second)
    assert window.source_path == second.resolve()
    assert window.source_revision > revision
    assert window.temporary_tags == ["Local_Test"]
    assert len(window.model.assets("Local_Test")) == 1
    state = (window.source_path, window.source_image, window.model.as_json(), window.source_revision)
    monkeypatch.setattr("tilenamer.ui.QMessageBox.warning", lambda *args: None)
    assert not window._replace_resource_path(small)
    assert state == (window.source_path, window.source_image, window.model.as_json(), window.source_revision)
    monkeypatch.setattr("tilenamer.ui.load_source_document", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("decode")))
    monkeypatch.setattr("tilenamer.ui.QMessageBox.critical", lambda *args: None)
    assert not window._replace_resource_path(tmp_path / "bad.png")
    assert state == (window.source_path, window.source_image, window.model.as_json(), window.source_revision)
    window.close()
    qt.processEvents()


def test_auto_reload_preserves_state_and_rolls_back_on_failure(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = tmp_path / "live.aseprite"
    source.write_bytes(b"fixture")
    prefs = preferences(tmp_path / "preferences.ini")
    live_layer = AsepriteLayer("live", "Live Layer", "image", True)
    first = LoadedSource(Image.new("RGBA", (96, 96), "red"), (live_layer,), {"live": True})
    window = MainWindow(ROOT, prefs)
    monkeypatch.setattr("tilenamer.ui.load_source_document", lambda *args, **kwargs: first)
    window._apply_source(source, first, keep_assignments=False, notify_mismatch=False)
    window.add_temporary_tag("Live_Tag")
    window.assign_region(0, 0, 1, 1)
    window.layer_grid_origins["live"] = (3, 2)
    window.layer_grid_manual_overrides.add("live")
    window.layer_alignment_offsets["live"] = (-1, 0)
    window.canvas.set_zoom(2.0)
    revision = window.source_revision
    second = LoadedSource(Image.new("RGBA", (96, 96), "green"), (live_layer,), {"live": True})
    monkeypatch.setattr("tilenamer.ui.load_source_document", lambda *args, **kwargs: second)
    window._auto_reload_source()
    assert window.source_revision == revision + 1
    assert window.canvas.zoom == 2.0
    assert len(window.model.assets("Live_Tag")) == 1
    assert window.layer_grid_origins["live"] == (3, 2)
    assert window.layer_alignment_offsets["live"] == (-1, 0)
    assert window.file_watcher.files() == [str(source.resolve())]
    assert window.source_image.getpixel((0, 0))[:3] == (0, 128, 0)

    stable = (window.source_image, window.source_revision, window.model.as_json())
    monkeypatch.setattr("tilenamer.ui.load_source_document", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("partial")))
    window._auto_reload_retry_count = 2
    window._auto_reload_source()
    assert stable == (window.source_image, window.source_revision, window.model.as_json())
    prefs.auto_reload_aseprite = False
    window._configure_source_watcher()
    assert window.file_watcher.files() == []
    window.close()
    qt.processEvents()


def test_auto_alignment_uses_only_reliable_metadata_and_is_one_undo(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = tmp_path / "fixture.aseprite"
    source.write_bytes(b"unchanged")
    tilemap = AsepriteLayer("tile", "Terrain", "tilemap", True, grid_origin_x=4,
                            grid_origin_y=2, grid_width=32, grid_height=32)
    image = AsepriteLayer("image", "Decoration", "image", True, cel_x=7, cel_y=9)
    window = MainWindow(ROOT, preferences(tmp_path / "preferences.ini"))
    window.source_path = source
    window.layers = (tilemap, image)
    window.layer_grid_origins = {"tile": (4, 2), "image": (7, -3)}
    window.layer_alignment_offsets = {"image": (3, 3)}
    monkeypatch.setattr(window, "_rerender_alignment", lambda: True)
    reference = GridReference(32, 32, 1, 1, "document")
    plan, skipped = window.auto_alignment_plan(reference)
    assert plan == {"tile": (3, 1)}
    assert skipped == ["Decoration"]
    original_bytes = source.read_bytes()
    assert window.apply_auto_alignment(reference)
    assert window.layer_alignment_offsets == {"image": (3, 3), "tile": (3, 1)}
    assert window.layer_grid_origins == {"tile": (4, 2), "image": (7, -3)}
    assert window.undo_stack.count() == 1
    window.undo_stack.undo()
    assert window.layer_alignment_offsets == {"image": (3, 3)}
    assert window.layer_grid_origins == {"tile": (4, 2), "image": (7, -3)}
    assert not hasattr(window, "alignment_correction_enabled")
    assert not hasattr(window, "alignment_correction_check")
    assert source.read_bytes() == original_bytes
    window.close()
    qt.processEvents()


def test_live_alignment_debounces_and_commits_one_history_entry(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    layer = AsepriteLayer("image", "Decoration", "image", True)
    window = MainWindow(ROOT, preferences(tmp_path / "preferences.ini"))
    window.layers = (layer,)
    window.layer_visibility = {"image": True}
    window._populate_layer_tree()
    item = window.layer_tree.topLevelItem(0)
    window.layer_tree.setCurrentItem(item)
    renders: list[dict[str, tuple[int, int]]] = []
    monkeypatch.setattr(window, "_rerender_alignment", lambda: renders.append(dict(window.layer_alignment_offsets)) or True)
    window.layer_offset_x.setValue(1)
    window.layer_offset_x.setValue(2)
    window.layer_offset_y.setValue(3)
    assert window.alignment_preview_timer.isActive()
    assert renders == []
    window._alignment_edit_finished()
    assert renders[-1] == {"image": (2, 3)}
    assert window.undo_stack.count() == 1
    window.undo_stack.undo()
    assert window.layer_alignment_offsets == {}
    window.close()
    qt.processEvents()


def test_failed_live_alignment_preview_restores_last_good(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    layer = AsepriteLayer("image", "Decoration", "image", True)
    window = MainWindow(ROOT, preferences(tmp_path / "preferences.ini"))
    window.layers = (layer,)
    window.layer_visibility = {"image": True}
    window.layer_alignment_offsets = {"image": (1, 1)}
    window._alignment_last_good_offsets = {"image": (1, 1)}
    window._populate_layer_tree()
    window.layer_tree.setCurrentItem(window.layer_tree.topLevelItem(0))
    monkeypatch.setattr(window, "_rerender_alignment", lambda: False)
    window.layer_offset_x.setValue(5)
    window._render_alignment_preview()
    assert window.layer_alignment_offsets == {"image": (1, 1)}
    assert window.undo_stack.count() == 0
    window.close()
    qt.processEvents()
