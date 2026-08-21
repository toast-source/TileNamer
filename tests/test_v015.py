from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, QSettings
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QStyle

from tilenamer.config import CategoryRule
from tilenamer.exporter import effective_output_directory, output_asset_count
from tilenamer.model import AssignmentModel
from tilenamer.preferences import Preferences
from tilenamer.project import TileProject
from tilenamer.ui import MainWindow, MiddleElideLabel, UiTokens


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(path: Path) -> Preferences:
    return Preferences(QSettings(str(path), QSettings.Format.IniFormat))


def rendered_contrast(widget) -> tuple[tuple[int, int, int], float]:
    image = widget.grab().toImage()
    colors = []
    for y in range(4, image.height() - 4):
        for x in range(4, image.width() - 4):
            color = image.pixelColor(x, y)
            colors.append((color.red(), color.green(), color.blue()))
    background = Counter(colors).most_common(1)[0][0]
    distance = max(
        sum((component - base) ** 2 for component, base in zip(color, background)) ** 0.5
        for color in colors
    )
    return background, distance


def test_project_v9_export_destination_round_trip_and_v1_v8_migration(tmp_path: Path) -> None:
    destination = tmp_path / "game" / "chapter2"
    project_path = tmp_path / "chapter2.tilenamer.json"
    TileProject(
        "sheet.png", 32, AssignmentModel(),
        export_base_directory=str(destination),
    ).save(project_path)

    payload = json.loads(project_path.read_text(encoding="utf-8"))
    restored = TileProject.load(project_path)
    assert payload["format_version"] == 9
    assert restored.export_base_directory == str(destination.resolve())

    for version in range(1, 9):
        old_path = tmp_path / f"legacy-v{version}.json"
        old_path.write_text(json.dumps({
            "format_version": version,
            "source_file": "sheet.png",
            "tile_size": 32,
            "assignments": {},
        }), encoding="utf-8")
        assert TileProject.load(old_path).export_base_directory is None


def test_relative_export_destination_resolves_from_project_folder(tmp_path: Path) -> None:
    project_path = tmp_path / "projects" / "work.tilenamer.json"
    project_path.parent.mkdir()
    project_path.write_text(json.dumps({
        "format_version": 9,
        "source_file": "sheet.png",
        "tile_size": 32,
        "assignments": {},
        "export_base_directory": "../game/chapter2",
    }), encoding="utf-8")
    restored = TileProject.load(project_path)
    assert Path(restored.export_base_directory) == (tmp_path / "game" / "chapter2").resolve()


def test_effective_tileimages_path_and_output_count(tmp_path: Path) -> None:
    rules = [CategoryRule("A", "A", 0, 2, "TileImages")]
    model = AssignmentModel({"A": [(0, 0), (1, 0)]})
    assert effective_output_directory(tmp_path, rules) == tmp_path / "TileImages"
    assert output_asset_count(model) == 2


def test_right_output_section_empty_populated_dirty_and_assignment_preview(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (64, 64), (20, 40, 60, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window.output_path_label.fullText() == "출력 위치가 지정되지 않았습니다."
    assert not window.open_output_action.isEnabled()
    assert not window.project_dirty

    window._load_source(source)
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    window.assign_region(0, 0, 1, 1)
    destination = tmp_path / "game" / "chapter2"
    window.set_export_base_directory(destination)

    assert window.effective_output_path() == destination / "TileImages"
    assert window.output_path_label.fullText() == str(destination / "TileImages")
    assert window.output_summary_label.text().startswith("1 PNG")
    assert window.open_output_action.isEnabled()
    assert window.project_dirty
    assert f"Source: {source.resolve()}" in window.output_section.toolTip()
    assert f"Output: {destination / 'TileImages'}" in window.output_section.toolTip()
    assert str(destination / "TileImages" / "Platform_Center_00.png") in (
        window.assignment_list.item(0).toolTip()
    )
    window.close()
    qt.processEvents()


def test_one_off_export_keeps_primary_destination(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (32, 32), (1, 2, 3, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window._load_source(source)
    primary = tmp_path / "primary"
    one_off = tmp_path / "one-off"
    window.set_export_base_directory(primary, mark_dirty=False)
    called: list[tuple[str | None, Path]] = []
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *args, **kwargs: str(one_off))
    )
    monkeypatch.setattr(window, "_run_export", lambda category, output: called.append((category, output)))

    window.export_other_location()
    assert called == [(None, one_off)]
    assert window.export_base_directory == primary
    window.close()
    qt.processEvents()


def test_primary_destination_restores_and_save_clears_dirty(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(source)
    destination = tmp_path / "game" / "chapter2"
    project_path = tmp_path / "work.tilenamer.json"
    TileProject(
        str(source), 32, AssignmentModel(),
        export_base_directory=str(destination),
    ).save(project_path)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(project_path), "")),
    )
    window.load_project()
    assert window.project_path == project_path.resolve()
    assert window.export_base_directory == destination.resolve()
    assert window.effective_output_path() == destination / "TileImages"
    assert not window.project_dirty

    changed = tmp_path / "changed"
    window.set_export_base_directory(changed)
    assert window.project_dirty
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(project_path), "")),
    )
    window.save_project()
    assert not window.project_dirty
    assert TileProject.load(project_path).export_base_directory == str(changed.resolve())
    window.close()
    qt.processEvents()


def test_first_primary_export_selects_and_remembers_destination(tmp_path: Path, monkeypatch) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(source)
    destination = tmp_path / "primary"
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window._load_source(source)
    called: list[tuple[str | None, Path]] = []
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(destination)),
    )
    monkeypatch.setattr(window, "_run_export", lambda category, output: called.append((category, output)))
    window.export_all()
    assert window.export_base_directory == destination.resolve()
    assert called == [(None, destination.resolve())]
    assert window.project_dirty
    window.close()
    qt.processEvents()


def test_output_actions_are_shared_and_selection_is_segmented(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    export_menu = window.menuBar().findChild(QMenu, "exportMenu")
    assert export_menu is not None
    assert window.change_output_action in export_menu.actions()
    assert window.open_output_action in export_menu.actions()
    assert window.rectangle_mode_button.isCheckable()
    assert window.paint_mode_button.isCheckable()
    assert window.rectangle_mode_button.isEnabled()
    assert window.paint_mode_button.isEnabled()
    assert window.rectangle_mode_button.isChecked()
    assert not window.paint_mode_button.isChecked()
    window.paint_mode_button.click()
    assert window.canvas.selection_mode == "paint"
    assert window.paint_mode_button.isChecked()
    window.close()
    qt.processEvents()


def test_selection_control_is_outside_viewport_and_upper_right(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window.resize(1100, 680)
    window.show()
    qt.processEvents()

    selection_rect = window.selection_control.rect().translated(
        window.selection_control.mapToGlobal(QPoint(0, 0))
    )
    viewport_rect = window.viewport_stack.rect().translated(
        window.viewport_stack.mapToGlobal(QPoint(0, 0))
    )
    tool_row_rect = window.center_tool_row.rect().translated(
        window.center_tool_row.mapToGlobal(QPoint(0, 0))
    )
    assert not selection_rect.intersects(viewport_rect)
    assert selection_rect.bottom() < viewport_rect.top()
    assert 0 <= tool_row_rect.right() - selection_rect.right() <= UiTokens.SPACE_MD
    assert window.selection_control.parentWidget() is window.center_tool_row
    window.close()
    qt.processEvents()


def test_dark_zoom_and_history_rendered_contrast(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window._set_theme("dark")
    window.show()
    qt.processEvents()

    assert window.zoom_label.text() == "100%"
    assert window.zoom_label.isEnabled()
    assert {button.height() for button in (
        window.zoom_out_button, window.zoom_label, window.zoom_in_button,
    )} == {window.zoom_label.height()}
    for button in (window.zoom_out_button, window.zoom_label, window.zoom_in_button):
        background, contrast = rendered_contrast(button)
        assert max(background) < 100
        assert contrast > 120

    assert not window.undo_button.isEnabled() and not window.redo_button.isEnabled()
    for button in (window.undo_button, window.redo_button):
        background, contrast = rendered_contrast(button)
        assert max(background) < 100
        assert contrast > 100

    window.undo_stack.push(QUndoCommand("contrast test"))
    qt.processEvents()
    assert window.undo_button.isEnabled()
    assert rendered_contrast(window.undo_button)[1] > 180
    window.undo_stack.undo()
    qt.processEvents()
    assert window.redo_button.isEnabled()
    assert rendered_contrast(window.redo_button)[1] > 180
    window.close()
    qt.processEvents()


def test_light_theme_uses_clear_one_pixel_boundaries(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window._set_theme("light")
    window.resize(1100, 680)
    window.show()
    qt.processEvents()
    stylesheet = window.styleSheet()
    assert "border: 1px solid #9da5ae" in stylesheet
    assert "border: 1px solid #929ba4" in stylesheet
    assert "QSplitter::handle { background: #969fa8; width: 2px; }" in stylesheet
    assert "#centerToolRow { background: #e9ecef; border-bottom: 1px solid #9da5ae; }" in stylesheet
    assert window.category_search.style().pixelMetric(
        QStyle.PixelMetric.PM_DefaultFrameWidth
    ) >= 1
    window.close()
    qt.processEvents()


def test_long_path_middle_elide_and_responsive_theme(tmp_path: Path) -> None:
    qt = app()
    label = MiddleElideLabel()
    full = r"C:\Users\SOUTHPAW GAMES\GameProject\Resources\Chapter2\TileImages"
    label.setFullText(full)
    label.resize(150, 30)
    label.show()
    qt.processEvents()
    assert label.toolTip() == full
    assert label.text() != full and "…" in label.text()

    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window.minimumWidth() == UiTokens.WINDOW_MIN_WIDTH
    window.resize(window.minimumSize())
    window.show()
    qt.processEvents()
    assert window.main_splitter.widget(0).width() >= UiTokens.PANEL_MIN_LEFT
    assert window.main_splitter.widget(2).width() >= UiTokens.PANEL_MIN_RIGHT
    assert window.centralWidget().layout().count() == 2
    assert not hasattr(window, "output_bar")
    assert window.main_splitter.widget(2).isAncestorOf(window.output_section)
    assert window.output_section.sizeHint().height() <= 130
    assert window.selection_control.width() < window.viewport_stack.width()
    for button in (*window.right_control_buttons, window.change_output_button, window.open_output_button):
        assert button.width() >= button.minimumSizeHint().width(), button.text()
    window._set_theme("dark")
    assert "#outputSection" in window.styleSheet()
    assert "#selectionControl" in window.styleSheet()
    window._set_theme("light")
    assert "#outputSection" in window.styleSheet()
    label.close()
    window.close()
    qt.processEvents()
