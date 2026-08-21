from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.placement import (
    SAMPLE_PATTERNS, available_families, available_patterns, build_placement_result,
    deterministic_candidate_index, resolve_platform_connections, resolve_platform_role,
    resolve_terrain_role,
)
from tilenamer.preferences import Preferences
from tilenamer.project import TileProject
from tilenamer.ui import MainWindow
from tilenamer.placement_window import PlacementCanvas, PlacementPreviewSource
from tilenamer.grid import GridReference


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(path: Path) -> Preferences:
    return Preferences(QSettings(str(path), QSettings.Format.IniFormat))


def wait_for_preview() -> None:
    QTest.qWait(90)
    app().processEvents()


def move_mouse(widget, position: QPointF) -> None:
    event = QMouseEvent(
        QEvent.Type.MouseMove, position, widget.mapToGlobal(position.toPoint()),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)
    app().processEvents()


def grabbed_image_contains(widget, color: QColor) -> bool:
    image = widget.grab().toImage()
    target = color.rgba()
    return any(
        image.pixelColor(x, y).rgba() == target
        for y in range(image.height())
        for x in range(image.width())
    )


def test_pure_reference_neighbor_roles_and_deterministic_candidates() -> None:
    all_neighbors = {
        (x, y) for y in range(3) for x in range(3)
    }
    assert resolve_terrain_role(all_neighbors, (1, 1), "Solid") == "Inner"
    assert resolve_terrain_role(all_neighbors - {(0, 0)}, (1, 1), "Solid") == "InnerRightBottom"
    assert resolve_terrain_role(
        all_neighbors - {(0, 0), (2, 2)}, (1, 1), "Wall"
    ) == "InnerSlash"
    assert resolve_terrain_role(
        all_neighbors - {(2, 0), (0, 2)}, (1, 1), "Wall"
    ) == "InnerBackslash"
    assert resolve_platform_role({(0, 0), (1, 0), (2, 0)}, (0, 0)) == "LeftEnd"
    assert resolve_platform_role({(0, 0), (1, 0), (2, 0)}, (1, 0)) == "Center"
    assert resolve_platform_role({(0, 0), (1, 0), (2, 0)}, (2, 0)) == "RightEnd"
    first = deterministic_candidate_index("Solid_Top", 4, 7, 5)
    assert first == deterministic_candidate_index("Solid_Top", 4, 7, 5)
    assert first is not None and 0 <= first < 5
    assert deterministic_candidate_index("Solid_Top", 4, 7, 0) is None
    assert resolve_platform_connections(False, True) == "LeftEnd"
    assert resolve_platform_connections(True, False) == "RightEnd"
    assert resolve_platform_connections(False, False) == "Center"
    assert resolve_platform_connections(True, True) == "Center"
    assert resolve_platform_connections(False, True, has_left_bridge=True) == "Center"
    assert resolve_platform_connections(True, False, has_right_bridge=True) == "Center"


def test_result_reports_ready_and_missing_roles() -> None:
    result = build_placement_result(
        "Platform", AssignmentModel({"Platform_Center": [(0, 0)]})
    )
    assert result.required_roles == {
        "Platform_LeftEnd", "Platform_Center", "Platform_RightEnd",
    }
    assert result.ready_roles == {"Platform_Center"}
    assert dict(result.missing_counts) == {
        "Platform_LeftEnd": 1, "Platform_RightEnd": 1,
    }


def test_reference_solid_pattern_delegates_platform_and_resolves_all_bridges() -> None:
    result = build_placement_result("Solid", AssignmentModel(), "종합")
    semantic = {(cell.x, cell.y): f"{cell.family}_{cell.role}" for cell in result.cells}
    expected = {
        (1, 0): "Solid_RightTopBridge",
        (2, 0): "Platform_Center",
        (3, 0): "Platform_Center",
        (4, 0): "Platform_Center",
        (5, 0): "Solid_LeftTopBridge",
        (1, 2): "Solid_RightBridge",
        (2, 2): "Platform_Center",
        (3, 2): "Platform_Center",
        (4, 2): "Platform_Center",
        (5, 2): "Solid_LeftBridge",
        (1, 4): "Solid_RightBottomBridge",
        (2, 4): "Platform_Center",
        (3, 4): "Platform_Center",
        (4, 4): "Platform_Center",
        (5, 4): "Solid_LeftBottomBridge",
    }
    assert {position: semantic[position] for position in expected} == expected
    assert set(expected.values()).issubset(result.required_roles)
    bridge_positions = {
        position for position, role in semantic.items() if "Bridge" in role
    }
    assert bridge_positions == {
        (1, 0), (5, 0), (1, 2), (5, 2), (1, 4), (5, 4),
    }
    assert {position for position, role in semantic.items() if role.startswith("Platform_")} == {
        (x, y) for y in (0, 2, 4) for x in (2, 3, 4)
    }


def test_bridge_multicell_orientation_companion_hit_and_fallback() -> None:
    app()
    assignments = AssignmentModel({
        "Solid_RightTopBridge": [AssetAssignment.from_cells(
            "Solid_RightTopBridge", ((0, 0), (1, 0)),
        )],
        "Solid_LeftTopBridge": [AssetAssignment.from_cells(
            "Solid_LeftTopBridge", ((2, 0), (3, 0)),
        )],
        "Platform_Center": [(4, 0)],
    })
    result = build_placement_result("Solid", assignments, "종합")
    right = next(cell for cell in result.cells if (cell.x, cell.y) == (1, 0))
    left = next(cell for cell in result.cells if (cell.x, cell.y) == (5, 0))
    assert set(right.occupied_cells) == {(1, 0), (2, 0)}
    assert set(left.occupied_cells) == {(4, 0), (5, 0)}
    assert right.assignment_identity is not None
    assert left.assignment_identity is not None

    canvas = PlacementCanvas()
    canvas.resize(900, 500)
    canvas.set_preview(result, PlacementPreviewSource(
        assignments, None, GridReference(),
        ((138, 143, 150, 255), (107, 112, 119, 255)),
    ))
    canvas.fit_view()
    assert canvas.hit_test(canvas.logical_cell_center((2, 0))) is right
    assert canvas.hit_test(canvas.logical_cell_center((4, 0))) is left

    fallback = build_placement_result(
        "Solid", AssignmentModel({"Solid_RightTop": [(0, 0)]}), "종합",
    )
    bridge = next(cell for cell in fallback.cells if (cell.x, cell.y) == (1, 0))
    assert bridge.role == "RightTopBridge"
    assert bridge.category == "Solid_RightTop"
    assert bridge.assignment_identity is not None

    one_cell = build_placement_result(
        "Solid", AssignmentModel({"Solid_RightTopBridge": [(0, 0)]}), "종합",
    )
    one_cell_bridge = next(cell for cell in one_cell.cells if (cell.x, cell.y) == (1, 0))
    assert one_cell_bridge.category == "Solid_RightTopBridge"
    assert one_cell_bridge.occupied_cells == ((1, 0),)
    assert one_cell_bridge.assignment_identity is not None


def test_platform_standalone_ends_and_wall_never_uses_bridge_roles() -> None:
    platform = build_placement_result("Platform", AssignmentModel(), "종합")
    roles = {(cell.x, cell.y): cell.role for cell in platform.cells}
    assert roles[(0, 0)] == "LeftEnd"
    assert roles[(2, 0)] == "Center"
    assert roles[(4, 0)] == "RightEnd"
    wall = build_placement_result("Wall", AssignmentModel(), "종합")
    assert all("Bridge" not in cell.role for cell in wall.cells)


def test_wall_reference_patterns_diagonals_coverage_and_red_inner_fallback() -> None:
    assert SAMPLE_PATTERNS["Wall"] == {
        "종합": (
            "###########.",
            "############",
            "##.######.##",
            "############",
            "###########.",
        ),
        "외곽": ("#######", "#.....#", "#######"),
        "대각선": ("###.", "####", "#.##", "####", "###."),
    }
    comprehensive = build_placement_result("Wall", AssignmentModel(), "종합")
    roles = {(cell.x, cell.y): cell.role for cell in comprehensive.cells}
    assert roles[(10, 1)] == "InnerBackslash"  # RT/LB empty: ╲
    assert roles[(10, 3)] == "InnerSlash"  # LT/RB empty: ╱
    assert len(comprehensive.required_roles) == 15
    assert all(cell.family == "Wall" and "Bridge" not in cell.role
               for cell in comprehensive.cells)

    diagonal = build_placement_result("Wall", AssignmentModel(), "대각선")
    assert {(cell.x, cell.y): cell.role for cell in diagonal.cells
            if cell.role in {"InnerSlash", "InnerBackslash"}} == {
        (2, 1): "InnerBackslash",
        (2, 3): "InnerSlash",
    }
    outer = build_placement_result("Wall", AssignmentModel(), "외곽")
    assert not any("InnerSlash" in cell.role or "InnerBackslash" in cell.role
                   for cell in outer.cells)

    diagonal_categories = {"Wall_InnerSlash", "Wall_InnerBackslash"}
    ordinary_categories = sorted(comprehensive.required_roles - diagonal_categories)
    assignments = AssignmentModel({
        category: [(index, 0)] for index, category in enumerate(ordinary_categories)
    })
    fallback = build_placement_result("Wall", assignments, "종합")
    assert len(fallback.ready_roles) == 13
    assert dict(fallback.missing_counts) == {
        "Wall_InnerBackslash": 1,
        "Wall_InnerSlash": 1,
    }
    fallback_cells = [cell for cell in fallback.cells if cell.fallback_for is not None]
    assert {cell.fallback_for for cell in fallback_cells} == diagonal_categories
    assert all(cell.category == "Wall_Inner" and cell.is_warning and not cell.is_missing
               for cell in fallback_cells)


def test_top_sequence_reference_patterns_roles_missing_and_type_isolation() -> None:
    assert available_families() == (
        "Solid", "Wall", "Platform", "Top Sequence 00", "Top Sequence 01",
    )
    for family in ("Top Sequence 00", "Top Sequence 01"):
        assert available_patterns(family) == ("최소 시퀀스", "긴 시퀀스")

    minimum_00 = build_placement_result(
        "Top Sequence 00", AssignmentModel(), "최소 시퀀스",
    )
    assert (minimum_00.width, minimum_00.height) == (4, 3)
    assert [cell.role for cell in minimum_00.cells] == ["Start", "Repeat", "End"]
    assert minimum_00.required_roles == {
        "Solid_TopSequence_Start_00",
        "Solid_TopSequence_Repeat_00",
        "Solid_TopSequence_End_00",
    }
    assert dict(minimum_00.missing_counts) == {
        "Solid_TopSequence_Start_00": 1,
        "Solid_TopSequence_Repeat_00": 1,
        "Solid_TopSequence_End_00": 1,
    }

    long_00 = build_placement_result(
        "Top Sequence 00", AssignmentModel(), "긴 시퀀스",
    )
    assert (long_00.width, long_00.height) == (8, 3)
    assert [cell.role for cell in long_00.cells] == [
        "Start", "Repeat", "Repeat", "Repeat", "End",
    ]
    assert dict(long_00.missing_counts)["Solid_TopSequence_Repeat_00"] == 3

    minimum_01 = build_placement_result(
        "Top Sequence 01", AssignmentModel(), "최소 시퀀스",
    )
    assert (minimum_01.width, minimum_01.height) == (4, 2)
    assert all(cell.category.endswith("_01") for cell in minimum_01.cells)
    assert not minimum_01.required_roles.intersection(minimum_00.required_roles)


def test_top_sequence_multicell_and_deterministic_candidate_selection() -> None:
    model = AssignmentModel({
        "Solid_TopSequence_Start_00": [
            AssetAssignment("Solid_TopSequence_Start_00", x, 0, 1, 3)
            for x in (0, 1, 2)
        ],
        "Solid_TopSequence_Repeat_00": [
            AssetAssignment("Solid_TopSequence_Repeat_00", x, 0, 2, 3)
            for x in (3, 5, 7)
        ],
        "Solid_TopSequence_End_00": [
            AssetAssignment("Solid_TopSequence_End_00", x, 0, 1, 3)
            for x in (9, 10, 11)
        ],
    })
    first = build_placement_result("Top Sequence 00", model, "최소 시퀀스")
    second = build_placement_result("Top Sequence 00", model, "최소 시퀀스")
    assert first == second
    assert (first.width, first.height) == (4, 3)
    assert [cell.width_cells for cell in first.cells] == [1, 2, 1]
    assert [len(cell.occupied_cells) for cell in first.cells] == [3, 6, 3]
    for cell in first.cells:
        expected = deterministic_candidate_index(cell.category, cell.x, cell.y, 3)
        assert cell.candidate_index == expected
        assert cell.filename == f"{cell.category}_{expected:02d}.png"
    assert first.ready_roles == first.required_roles
    assert not first.missing_counts


def test_top_sequence_each_missing_part_and_opposite_pool_are_isolated() -> None:
    categories_00 = {
        "Start": "Solid_TopSequence_Start_00",
        "Repeat": "Solid_TopSequence_Repeat_00",
        "End": "Solid_TopSequence_End_00",
    }
    for omitted in categories_00:
        values = {}
        source_x = 0
        for part, category in categories_00.items():
            if part == omitted:
                continue
            width = 2 if part == "Repeat" else 1
            values[category] = [AssetAssignment(category, source_x, 0, width, 3)]
            source_x += width
        result = build_placement_result(
            "Top Sequence 00", AssignmentModel(values), "최소 시퀀스",
        )
        assert dict(result.missing_counts) == {categories_00[omitted]: 1}
        assert categories_00[omitted] not in result.ready_roles

    type_01_only = AssignmentModel({
        "Solid_TopSequence_Start_01": [
            AssetAssignment("Solid_TopSequence_Start_01", 0, 0, 1, 2),
        ],
        "Solid_TopSequence_Repeat_01": [
            AssetAssignment("Solid_TopSequence_Repeat_01", 1, 0, 2, 2),
        ],
        "Solid_TopSequence_End_01": [
            AssetAssignment("Solid_TopSequence_End_01", 3, 0, 1, 2),
        ],
    })
    result_00 = build_placement_result("Top Sequence 00", type_01_only)
    assert not result_00.ready_roles
    assert result_00.missing_cell_count == 3


def test_preview_is_modeless_single_reusable_empty_themed_and_owned(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window.show()
    window.placement_preview_action.trigger()
    qt.processEvents()
    preview = window.placement_preview_window
    assert preview is not None and preview.isVisible()
    assert preview.isWindow()
    assert preview.windowModality() == Qt.WindowModality.NonModal
    assert window.isEnabled()
    assert preview.parentWidget() is window
    assert preview.windowTitle() == "TileNamer — 배치 미리보기"
    assert preview.windowIcon().cacheKey() == window.windowIcon().cacheKey()
    assert preview.empty_label.isVisible()
    assert "등록된 타일이 없습니다" in preview.empty_label.text()

    window.placement_preview_action.trigger()
    qt.processEvents()
    assert window.placement_preview_window is preview
    window._set_theme("dark")
    assert "#25282c" in preview.styleSheet()
    window._set_theme("light")
    assert "#eef0f2" in preview.styleSheet()

    preview.close()
    qt.processEvents()
    assert not preview.isVisible()
    window.placement_preview_action.trigger()
    qt.processEvents()
    assert window.placement_preview_window is preview and preview.isVisible()
    window.close()
    qt.processEvents()
    assert not preview.isVisible()


def test_assignment_add_remove_and_reorder_live_refresh(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (128, 128), (50, 100, 150, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window._load_source(source)
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    window.category_tree.setCurrentItem(window.category_items["Solid_LeftTop"])
    baseline = preview.refresh_count

    window.assign_region(0, 0, 1, 1)
    wait_for_preview()
    assert preview.refresh_count == baseline + 1
    assert any(
        cell.category == "Solid_LeftTop" and cell.candidate_index == 0
        for cell in preview.canvas.result.cells
    )

    window.assign_region(1, 0, 1, 1)
    wait_for_preview()
    before_reorder = preview.refresh_count
    window.assignment_list.setCurrentRow(1)
    window.reorder(-1)
    wait_for_preview()
    assert preview.refresh_count == before_reorder + 1
    assert window.model.assets("Solid_LeftTop")[0].origin == (1, 0)

    before_remove = preview.refresh_count
    window.assignment_list.setCurrentRow(0)
    window.remove_selected()
    wait_for_preview()
    assert preview.refresh_count == before_remove + 1
    assert len(window.model.assets("Solid_LeftTop")) == 1

    before_move = preview.refresh_count
    window.category_tree.setCurrentItem(window.category_items["Solid_RightTop"])
    window.assign_region(0, 0, 1, 1)
    wait_for_preview()
    assert preview.refresh_count == before_move + 1
    assert not window.model.assets("Solid_LeftTop")
    assert len(window.model.assets("Solid_RightTop")) == 1
    window.close()
    qt.processEvents()


def test_project_load_and_source_reload_refresh_open_preview(
    tmp_path: Path, monkeypatch,
) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    replacement = tmp_path / "replacement.png"
    resource = tmp_path / "resource.png"
    Image.new("RGBA", (64, 64), (100, 20, 20, 255)).save(source)
    Image.new("RGBA", (64, 64), (20, 100, 20, 255)).save(replacement)
    Image.new("RGBA", (64, 64), (20, 20, 100, 255)).save(resource)
    project_path = tmp_path / "wall.tilenamer.json"
    TileProject(
        str(source), 32, AssignmentModel({"Wall_Top": [(0, 0)]}),
    ).save(project_path)
    assert "\"format_version\": 9" in project_path.read_text(encoding="utf-8")

    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    preview.family_combo.setCurrentText("Wall")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(project_path), "")),
    )
    before_load = preview.refresh_count
    window.load_project()
    wait_for_preview()
    assert preview.refresh_count > before_load
    assert preview.canvas.result is not None
    assert any(
        cell.category == "Wall_Top" and cell.candidate_index == 0
        for cell in preview.canvas.result.cells
    )

    before_reload = preview.refresh_count
    assert window._load_source(replacement, keep_assignments=True)
    wait_for_preview()
    assert preview.refresh_count == before_reload + 1
    assert window.source_path == replacement.resolve()

    before_replace = preview.refresh_count
    assert window._replace_resource_path(resource)
    wait_for_preview()
    assert preview.refresh_count == before_replace + 1
    assert window.source_path == resource.resolve()
    window.close()
    qt.processEvents()


def test_preview_click_locates_exact_reordered_candidate_scrolls_and_flashes(
    tmp_path: Path,
) -> None:
    qt = app()
    source = tmp_path / "wide.png"
    Image.new("RGBA", (640, 64), (80, 120, 160, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({
        "Solid_LeftTop": [(index, 0) for index in range(20)],
    })
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    preview.refresh_preview()
    target = next(cell for cell in preview.canvas.result.cells
                  if cell.category == "Solid_LeftTop")
    assert target.candidate_index == 14
    dirty_before = window.isWindowModified()
    preview.canvas.set_zoom(2.0)
    preview.canvas.pan_offset = QPointF(17, 11)
    point = preview.canvas.logical_cell_center(target.occupied_cells[0]).toPoint()
    QTest.mouseClick(preview.canvas, Qt.MouseButton.LeftButton, pos=point)
    qt.processEvents()
    assert window.current_category() == "Solid_LeftTop"
    assert window.assignment_list.currentRow() == 14
    assert "Solid_LeftTop_14.png" in window.assignment_list.currentItem().text()
    assert window.assignment_list.visualItemRect(
        window.assignment_list.currentItem()
    ).intersects(window.assignment_list.viewport().rect())
    assert "이 타일입니다!" in window.statusBar().currentMessage()
    assert window.assignment_list.currentItem().data(Qt.ItemDataRole.UserRole + 4)
    assert window.isWindowModified() == dirty_before

    identity = target.assignment_identity
    assets = window.model.assignments["Solid_LeftTop"]
    moved = assets.pop(14)
    assets.insert(2, moved)
    window.refresh_assignments()
    window._locate_preview_assignment(identity)
    assert window.assignment_list.currentRow() == 2
    QTest.qWait(750)
    assert not window.assignment_list.currentItem().data(Qt.ItemDataRole.UserRole + 4)
    assert window.assignment_list.currentRow() == 2
    window.close()
    qt.processEvents()


def test_solid_preview_bridge_and_delegated_platform_locate_exact_categories(
    tmp_path: Path,
) -> None:
    qt = app()
    source = tmp_path / "linked.png"
    Image.new("RGBA", (96, 32), (75, 105, 135, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({
        "Platform_Center": [(0, 0)],
        "Solid_RightTopBridge": [(1, 0)],
        "Solid_LeftTopBridge": [(2, 0)],
    })
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    wait_for_preview()

    delegated = next(cell for cell in preview.canvas.result.cells
                     if (cell.x, cell.y) == (3, 0))
    assert delegated.category == "Platform_Center"
    preview._canvas_tile_clicked(delegated)
    qt.processEvents()
    assert window.current_category() == "Platform_Center"
    assert "Platform_Center_00.png" in window.assignment_list.currentItem().text()

    bridge = next(cell for cell in preview.canvas.result.cells
                  if (cell.x, cell.y) == (1, 0))
    assert bridge.category == "Solid_RightTopBridge"
    preview._canvas_tile_clicked(bridge)
    qt.processEvents()
    assert window.current_category() == "Solid_RightTopBridge"
    assert "Solid_RightTopBridge_00.png" in window.assignment_list.currentItem().text()
    window.close()
    qt.processEvents()


def test_exact_candidate_three_fixture_selects_number_two(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "three.png"
    Image.new("RGBA", (96, 32), (60, 100, 140, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({"Platform_LeftEnd": [(0, 0), (1, 0), (2, 0)]})
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    preview.family_combo.setCurrentText("Platform")
    preview.refresh_preview()
    wait_for_preview()
    assert preview.canvas.fit_mode and preview.canvas._view_initialized
    target = next(cell for cell in preview.canvas.result.cells
                  if cell.category == "Platform_LeftEnd")
    assert target.candidate_index == 2
    move_mouse(preview.canvas, preview.canvas.logical_cell_center(target.occupied_cells[0]))
    assert preview.canvas.hovered_cell is target
    assert preview.canvas.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseClick(
        preview.canvas, Qt.MouseButton.LeftButton,
        pos=preview.canvas.logical_cell_center(target.occupied_cells[0]).toPoint(),
    )
    qt.processEvents()
    assert window.assignment_list.currentRow() == 2
    assert "Platform_LeftEnd_02.png" in window.assignment_list.currentItem().text()
    assert preview.isVisible()
    window.close()
    qt.processEvents()


def test_multicell_irregular_hit_testing_and_space_pan_suppresses_click() -> None:
    qt = app()
    rectangular = AssetAssignment("Solid_LeftTop", 0, 0, 2, 2)
    rectangular_model = AssignmentModel({"Solid_LeftTop": [rectangular]})
    rectangular_result = build_placement_result("Solid", rectangular_model)
    rectangular_target = next(
        cell for cell in rectangular_result.cells if cell.assignment_identity is not None
    )
    rectangular_canvas = PlacementCanvas()
    rectangular_canvas.resize(760, 420)
    rectangular_canvas.set_preview(rectangular_result, PlacementPreviewSource(
        rectangular_model, Image.new("RGBA", (64, 64), (255, 255, 255, 255)),
        GridReference(), ((138, 143, 150, 255), (107, 112, 119, 255)),
    ))
    rectangular_canvas.fit_view()
    assert len(rectangular_target.occupied_cells) == 4
    for coord in rectangular_target.occupied_cells:
        assert rectangular_canvas.hit_test(
            rectangular_canvas.logical_cell_center(coord)
        ) is rectangular_target
        move_mouse(rectangular_canvas, rectangular_canvas.logical_cell_center(coord))
        assert rectangular_canvas.hovered_cell is rectangular_target

    assignment = AssetAssignment.from_cells(
        "Solid_LeftTop", ((0, 0), (1, 0), (0, 1)),
    )
    model = AssignmentModel({"Solid_LeftTop": [assignment]})
    result = build_placement_result("Solid", model)
    target = next(cell for cell in result.cells if cell.assignment_identity is not None)
    canvas = PlacementCanvas()
    canvas.resize(760, 420)
    canvas.set_preview(result, PlacementPreviewSource(
        model, Image.new("RGBA", (64, 64), (255, 255, 255, 255)),
        GridReference(), ((138, 143, 150, 255), (107, 112, 119, 255)),
    ))
    canvas.show()
    qt.processEvents()
    canvas.fit_view()
    for coord in target.occupied_cells:
        assert canvas.hit_test(canvas.logical_cell_center(coord)) is target
        move_mouse(canvas, canvas.logical_cell_center(coord))
        assert canvas.hovered_cell is target
    hole = (target.x + 1, target.y + 1)
    assert hole not in target.occupied_cells
    assert canvas.hit_test(canvas.logical_cell_center(hole)) is None
    move_mouse(canvas, canvas.logical_cell_center(hole))
    assert canvas.hovered_cell is None

    canvas.set_zoom(4.0)
    canvas.pan_offset = QPointF(23, -19)
    move_mouse(canvas, canvas.logical_cell_center(target.occupied_cells[0]))
    assert canvas.hovered_cell is target
    canvas.fit_view()
    canvas.set_grid_visible(False)
    qt.processEvents()
    hole_pixel = canvas.grab().toImage().pixelColor(
        canvas.logical_cell_center(hole).toPoint(),
    )
    assert hole_pixel == canvas.field_color
    move_mouse(canvas, canvas.logical_cell_center(target.occupied_cells[0]))
    assert grabbed_image_contains(canvas, QColor("#55efff"))

    spy = QSignalSpy(canvas.tile_clicked)
    start = canvas.logical_cell_center(target.occupied_cells[0]).toPoint()
    QTest.keyPress(canvas, Qt.Key.Key_Space)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, start + QPoint(30, 20), delay=10)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=start + QPoint(30, 20))
    QTest.keyRelease(canvas, Qt.Key.Key_Space)
    assert spy.count() == 0
    assert canvas.pan_offset != QPointF()
    canvas.close()


def test_missing_preview_click_selects_category_without_dirtying(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "sheet.png"
    Image.new("RGBA", (64, 64), (90, 90, 90, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({"Solid_Inner": [(0, 0)]})
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    preview.refresh_preview()
    missing = next(cell for cell in preview.canvas.result.cells if cell.is_missing)
    dirty_before = window.isWindowModified()
    move_mouse(preview.canvas, preview.canvas.logical_cell_center(missing.occupied_cells[0]))
    assert preview.canvas.hovered_cell is missing and preview.canvas.hovered_cell.is_missing
    preview.canvas.set_grid_visible(False)
    assert grabbed_image_contains(preview.canvas, QColor("#ff9a76"))
    QTest.mouseClick(
        preview.canvas, Qt.MouseButton.LeftButton,
        pos=preview.canvas.logical_cell_center(missing.occupied_cells[0]).toPoint(),
    )
    qt.processEvents()
    assert window.current_category() == missing.category
    assert window.assignment_list.currentRow() == -1
    assert window.assignment_list.count() == 0
    assert window.statusBar().currentMessage() == f"이 타일이 필요합니다: {missing.category}"
    assert window.isWindowModified() == dirty_before
    window.close()
    qt.processEvents()


def test_wall_diagonal_fallback_hover_and_click_navigate_missing_role(
    tmp_path: Path,
) -> None:
    qt = app()
    empty_result = build_placement_result("Wall", AssignmentModel(), "종합")
    diagonal_categories = {"Wall_InnerSlash", "Wall_InnerBackslash"}
    ordinary = sorted(empty_result.required_roles - diagonal_categories)
    source = tmp_path / "wall.png"
    Image.new("RGBA", (len(ordinary) * 32, 32), (90, 105, 120, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({
        category: [(index, 0)] for index, category in enumerate(ordinary)
    })
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    preview.family_combo.setCurrentText("Wall")
    wait_for_preview()
    assert preview.summary_label.text() == "준비 13 / 15 · 누락 2"
    fallback = next(cell for cell in preview.canvas.result.cells
                    if cell.fallback_for == "Wall_InnerBackslash")
    move_mouse(preview.canvas, preview.canvas.logical_cell_center(fallback.occupied_cells[0]))
    assert preview.canvas.hovered_cell is fallback
    preview.canvas.set_grid_visible(False)
    assert grabbed_image_contains(preview.canvas, QColor("#ff9a76"))
    preview._canvas_tile_clicked(fallback)
    qt.processEvents()
    assert window.current_category() == "Wall_InnerBackslash"
    assert window.assignment_list.count() == 0
    assert window.statusBar().currentMessage() == (
        "이 타일이 필요합니다: Wall_InnerBackslash"
    )
    window.close()
    qt.processEvents()


def _assert_preview_centered(canvas: PlacementCanvas, tolerance: float = 2.0) -> None:
    bounds = canvas.content_screen_rect()
    viewport = canvas.rect().center()
    assert abs(bounds.center().x() - viewport.x()) <= tolerance
    assert abs(bounds.center().y() - viewport.y()) <= tolerance
    assert bounds.left() >= 18
    assert bounds.top() >= 18
    assert bounds.right() <= canvas.width() - 18
    assert bounds.bottom() <= canvas.height() - 18


def test_initial_fit_lifecycle_patterns_resize_reopen_and_manual_preservation(
    tmp_path: Path,
) -> None:
    qt = app()
    source = tmp_path / "fit.png"
    Image.new("RGBA", (96, 32), (40, 90, 130, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({"Platform_LeftEnd": [(0, 0), (1, 0), (2, 0)]})
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    preview.family_combo.setCurrentText("Platform")
    wait_for_preview()
    assert preview.canvas.fit_mode
    assert preview.canvas._view_initialized
    expected = min(
        (preview.canvas.width() - 40) / (preview.canvas.result.width * 32),
        (preview.canvas.height() - 40) / (preview.canvas.result.height * 32),
        8.0,
    )
    assert abs(preview.canvas.zoom - expected) < 0.001
    assert preview.zoom_label.text() == f"{round(expected * 100)}%"
    _assert_preview_centered(preview.canvas)

    expected_patterns = {
        "Solid": ["종합", "외곽", "내부 코너", "브릿지"],
        "Platform": ["종합", "단일", "짧은 연결"],
        "Wall": ["종합", "외곽", "대각선"],
        "Top Sequence 00": ["최소 시퀀스", "긴 시퀀스"],
        "Top Sequence 01": ["최소 시퀀스", "긴 시퀀스"],
    }
    for family, expected_items in expected_patterns.items():
        preview.family_combo.setCurrentText(family)
        qt.processEvents()
        assert [preview.pattern_combo.itemText(index)
                for index in range(preview.pattern_combo.count())] == expected_items
        wait_for_preview()
        assert preview.canvas.fit_mode
        _assert_preview_centered(preview.canvas)

    preview.canvas.set_zoom(4.0)
    preview.canvas.pan_offset = QPointF(31, -17)
    manual_zoom, manual_pan = preview.canvas.zoom, QPointF(preview.canvas.pan_offset)
    preview.refresh_preview()
    qt.processEvents()
    assert not preview.canvas.fit_mode
    assert preview.canvas.zoom == manual_zoom
    assert preview.canvas.pan_offset == manual_pan

    preview.pattern_combo.setCurrentIndex(1)
    wait_for_preview()
    assert preview.canvas.result.pattern_name == preview.pattern_combo.currentText()
    assert preview.canvas.fit_mode
    _assert_preview_centered(preview.canvas)

    old_zoom = preview.canvas.zoom
    preview.resize(preview.width() + 140, preview.height() + 90)
    wait_for_preview()
    assert preview.canvas.fit_mode
    assert preview.canvas.zoom != old_zoom
    _assert_preview_centered(preview.canvas)

    preview.canvas.set_zoom(4.0)
    preview.canvas.pan_offset = QPointF(19, 23)
    preview.resize(preview.width() + 40, preview.height() + 30)
    wait_for_preview()
    assert preview.canvas.zoom == 4.0
    assert preview.canvas.pan_offset == QPointF(19, 23)
    preview.close()
    qt.processEvents()
    preview.show()
    wait_for_preview()
    assert preview.canvas.fit_mode
    assert preview.canvas.zoom != 4.0
    _assert_preview_centered(preview.canvas)
    assert "roles" not in preview.summary_label.text()
    window.close()
    qt.processEvents()


def test_tall_fit_uses_height_and_dark_light_text_tokens() -> None:
    qt = app()
    canvas = PlacementCanvas()
    canvas.resize(1000, 600)
    tall = build_placement_result("Platform", AssignmentModel(), "단일")
    tall = type(tall)(
        family=tall.family, pattern_name="tall", width=2, height=20,
        cells=tall.cells, required_roles=tall.required_roles,
        ready_roles=tall.ready_roles, missing_counts=tall.missing_counts,
    )
    canvas.set_preview(tall, PlacementPreviewSource(
        AssignmentModel(), None, GridReference(),
        ((138, 143, 150, 255), (107, 112, 119, 255)),
    ))
    canvas.show()
    canvas.fit_view()
    qt.processEvents()
    assert abs(canvas.zoom - (560 / (20 * 32))) < 0.001
    _assert_preview_centered(canvas)
    canvas.close()

    host = MainWindow(ROOT, Preferences.default())
    preview = host.placement_preview_window
    host.show_placement_preview()
    preview = host.placement_preview_window
    assert preview is not None
    preview.apply_theme("dark")
    qt.processEvents()
    for widget in (preview.family_combo, preview.pattern_combo, preview.zoom_label,
                   preview.grid_toggle, preview.summary_label):
        assert widget.palette().color(QPalette.ColorRole.WindowText).lightness() > 130
    for combo in (preview.family_combo, preview.pattern_combo):
        combo.showPopup()
        qt.processEvents()
        assert combo.view().model().rowCount() == combo.count()
        popup_palette = combo.view().palette()
        assert popup_palette.color(QPalette.ColorRole.Text).lightness() > 130
        assert popup_palette.color(QPalette.ColorRole.Base).lightness() < 100
        assert popup_palette.color(QPalette.ColorRole.HighlightedText).lightness() > 200
        assert popup_palette.color(QPalette.ColorRole.Highlight) != popup_palette.color(
            QPalette.ColorRole.Base
        )
        assert popup_palette.color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
        ).lightness() > 90
        combo.hidePopup()
    preview.apply_theme("light")
    qt.processEvents()
    assert preview.zoom_label.palette().color(
        QPalette.ColorRole.WindowText
    ).lightness() < 130
    for combo in (preview.family_combo, preview.pattern_combo):
        combo.showPopup()
        qt.processEvents()
        popup_palette = combo.view().palette()
        assert popup_palette.color(QPalette.ColorRole.Text).lightness() < 130
        assert popup_palette.color(QPalette.ColorRole.Base).lightness() > 200
        combo.hidePopup()
    host.close()
    qt.processEvents()


def test_combo_popups_fit_longest_text_after_family_changes() -> None:
    qt = app()
    host = MainWindow(ROOT, Preferences.default())
    host.show_placement_preview()
    preview = host.placement_preview_window
    assert preview is not None
    for theme in ("dark", "light"):
        preview.apply_theme(theme)
        for family in available_families():
            preview.family_combo.setCurrentText(family)
            qt.processEvents()
            for combo in (preview.family_combo, preview.pattern_combo):
                combo.showPopup()
                qt.processEvents()
                longest = max(
                    combo.view().fontMetrics().horizontalAdvance(combo.itemText(index))
                    for index in range(combo.count())
                )
                assert combo.view().viewport().width() >= longest + 14
                assert combo.view().width() >= combo.width()
                assert combo.view().textElideMode() == Qt.TextElideMode.ElideNone
                longest_index = max(
                    range(combo.count()),
                    key=lambda index: combo.view().fontMetrics().horizontalAdvance(
                        combo.itemText(index)
                    ),
                )
                item_rect = combo.view().visualRect(
                    combo.view().model().index(longest_index, 0)
                )
                assert item_rect.width() >= longest + 14
                assert all("..." not in combo.itemText(index) for index in range(combo.count()))
                combo.hidePopup()
    host.close()
    qt.processEvents()


def test_top_sequence_family_widget_missing_and_exact_candidate_navigation(
    tmp_path: Path,
) -> None:
    qt = app()
    source = tmp_path / "sequence.png"
    Image.new("RGBA", (384, 96), (70, 100, 130, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({
        "Solid_TopSequence_Start_01": [
            AssetAssignment("Solid_TopSequence_Start_01", 0, 0, 1, 2),
        ],
        "Solid_TopSequence_Repeat_01": [
            AssetAssignment("Solid_TopSequence_Repeat_01", x, 0, 2, 2)
            for x in (1, 3, 5)
        ],
        "Solid_TopSequence_End_01": [
            AssetAssignment("Solid_TopSequence_End_01", 7, 0, 1, 2),
        ],
    })
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    assert [preview.family_combo.itemText(index)
            for index in range(preview.family_combo.count())] == list(available_families())

    preview.family_combo.setCurrentText("Top Sequence 01")
    wait_for_preview()
    repeat = next(cell for cell in preview.canvas.result.cells if cell.role == "Repeat")
    expected = deterministic_candidate_index(
        "Solid_TopSequence_Repeat_01", repeat.x, repeat.y, 3,
    )
    assert repeat.candidate_index == expected
    repeat_pixmap = preview.canvas.pixmaps[repeat.assignment_identity]
    assert (repeat_pixmap.width(), repeat_pixmap.height()) == (64, 64)
    preview._canvas_tile_clicked(repeat)
    qt.processEvents()
    assert window.current_category() == "Solid_TopSequence_Repeat_01"
    assert window.assignment_list.currentRow() == expected
    assert f"Solid_TopSequence_Repeat_01_{expected:02d}.png" in (
        window.assignment_list.currentItem().text()
    )
    leaf = window.category_items["Solid_TopSequence_Repeat_01"]
    assert leaf.parent().text(0) == "Type 01"
    assert leaf.parent().parent().text(0) == "Top Sequence"

    # Leave only Start 00 available so Repeat/End become navigable Missing roles.
    window.model = AssignmentModel({
        "Solid_TopSequence_Start_00": [
            AssetAssignment("Solid_TopSequence_Start_00", 8, 0, 1, 3),
        ],
    })
    window.refresh_assignments()
    preview.family_combo.setCurrentText("Top Sequence 00")
    wait_for_preview()
    assert preview.summary_label.text() == "준비 1 / 3 · 누락 2"
    start_00 = next(cell for cell in preview.canvas.result.cells if cell.role == "Start")
    start_pixmap = preview.canvas.pixmaps[start_00.assignment_identity]
    assert (start_pixmap.width(), start_pixmap.height()) == (32, 96)
    missing = next(cell for cell in preview.canvas.result.cells if cell.role == "Repeat")
    preview._canvas_tile_clicked(missing)
    qt.processEvents()
    assert window.current_category() == "Solid_TopSequence_Repeat_00"
    assert window.assignment_list.count() == 0
    assert window.statusBar().currentMessage() == (
        "이 타일이 필요합니다: Solid_TopSequence_Repeat_00"
    )
    window.close()
    qt.processEvents()


def test_top_sequence_long_fit_hover_opaque_field_zoom_and_pan() -> None:
    qt = app()
    result = build_placement_result(
        "Top Sequence 00", AssignmentModel(), "긴 시퀀스",
    )
    canvas = PlacementCanvas()
    canvas.resize(760, 360)
    canvas.set_theme(True)
    canvas.set_grid_visible(False)
    canvas.set_preview(result, PlacementPreviewSource(
        AssignmentModel(), None, GridReference(),
        ((138, 143, 150, 255), (107, 112, 119, 255)),
    ))
    canvas.show()
    canvas.fit_view()
    qt.processEvents()
    assert canvas.fit_mode
    _assert_preview_centered(canvas)
    repeat = next(cell for cell in result.cells if cell.role == "Repeat")
    move_mouse(canvas, canvas.logical_cell_center(repeat.occupied_cells[0]))
    assert canvas.hovered_cell is repeat
    image = canvas.grab().toImage()
    assert image.pixelColor(canvas.logical_cell_center((0, 2)).toPoint()).alpha() == 255
    canvas.set_zoom(4.0)
    canvas.pan_offset = QPointF(31, -17)
    move_mouse(canvas, canvas.logical_cell_center(repeat.occupied_cells[-1]))
    assert canvas.hovered_cell is repeat
    before = QPointF(canvas.pan_offset)
    start = canvas.logical_cell_center(repeat.occupied_cells[0]).toPoint()
    QTest.keyPress(canvas, Qt.Key.Key_Space)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, start + QPoint(22, 14), delay=10)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=start + QPoint(22, 14))
    QTest.keyRelease(canvas, Qt.Key.Key_Space)
    assert canvas.pan_offset != before
    canvas.close()


def test_preview_logical_field_is_opaque_and_distinct_from_workspace() -> None:
    qt = app()
    canvas = PlacementCanvas()
    canvas.resize(900, 500)
    result = build_placement_result("Solid", AssignmentModel(), "종합")
    canvas.set_theme(True)
    canvas.set_grid_visible(False)
    canvas.set_preview(result, PlacementPreviewSource(
        AssignmentModel(), None, GridReference(),
        ((138, 143, 150, 255), (107, 112, 119, 255)),
    ))
    canvas.show()
    canvas.fit_view()
    qt.processEvents()
    image = canvas.grab().toImage()
    empty = canvas.logical_cell_center((3, 1)).toPoint()
    field_pixel = image.pixelColor(empty)
    workspace_pixel = image.pixelColor(2, 2)
    assert field_pixel.alpha() == 255
    assert field_pixel == canvas.field_color
    assert workspace_pixel == canvas.workspace_color
    assert field_pixel != workspace_pixel
    missing_pixel = image.pixelColor(canvas.logical_cell_center((0, 0)).toPoint())
    assert missing_pixel != field_pixel
    canvas.close()


def test_tile_alpha_composites_over_field_without_mutating_source() -> None:
    qt = app()
    source = Image.new("RGBA", (32, 32), (240, 40, 20, 128))
    model = AssignmentModel({"Platform_Center": [(0, 0)]})
    result = build_placement_result("Platform", model, "단일")
    canvas = PlacementCanvas()
    canvas.resize(160, 160)
    canvas.set_theme(True)
    canvas.set_grid_visible(False)
    canvas.set_preview(result, PlacementPreviewSource(
        model, source, GridReference(),
        ((138, 143, 150, 255), (107, 112, 119, 255)),
    ))
    canvas.show()
    canvas.set_zoom(1.0)
    qt.processEvents()
    pixel = canvas.grab().toImage().pixelColor(
        canvas.logical_cell_center((0, 0)).toPoint(),
    )
    background = canvas.field_color
    expected = tuple(
        round(foreground * 128 / 255 + backdrop * 127 / 255)
        for foreground, backdrop in zip(
            (240, 40, 20), (background.red(), background.green(), background.blue()),
        )
    )
    assert all(abs(actual - wanted) <= 1 for actual, wanted in zip(
        (pixel.red(), pixel.green(), pixel.blue()), expected,
    ))
    assert pixel.alpha() == 255
    assert source.getpixel((16, 16)) == (240, 40, 20, 128)
    canvas.close()


def test_preview_space_is_reserved_with_any_control_focus_and_focus_loss(
    tmp_path: Path,
) -> None:
    qt = app()
    source = tmp_path / "space.png"
    Image.new("RGBA", (64, 64), (30, 70, 110, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({"Solid_LeftTop": [(0, 0)]})
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    wait_for_preview()
    preview.activateWindow()
    qt.processEvents()

    fit_spy = QSignalSpy(preview.fit_button.clicked)
    grid_spy = QSignalSpy(preview.grid_toggle.toggled)
    locate_spy = QSignalSpy(preview.canvas.tile_clicked)
    original = (
        preview.canvas.zoom, QPointF(preview.canvas.pan_offset),
        preview.grid_toggle.isChecked(), preview.family_combo.currentIndex(),
        preview.pattern_combo.currentIndex(), preview.refresh_count,
        window.isWindowModified(),
    )
    for control in (
        preview.fit_button, preview.grid_toggle,
        preview.family_combo, preview.pattern_combo,
    ):
        control.setFocus()
        qt.processEvents()
        QTest.keyClick(control, Qt.Key.Key_Space)
        qt.processEvents()
        assert not preview.canvas._space_down
    preview.fit_button.setFocus()
    for _ in range(10):
        QTest.keyClick(preview.fit_button, Qt.Key.Key_Space)
    qt.processEvents()
    assert fit_spy.count() == 0
    assert grid_spy.count() == 0
    assert locate_spy.count() == 0
    assert not preview.family_combo.view().isVisible()
    assert not preview.pattern_combo.view().isVisible()
    assert (
        preview.canvas.zoom, preview.canvas.pan_offset,
        preview.grid_toggle.isChecked(), preview.family_combo.currentIndex(),
        preview.pattern_combo.currentIndex(), preview.refresh_count,
        window.isWindowModified(),
    ) == original

    for combo in (preview.family_combo, preview.pattern_combo):
        index_before = combo.currentIndex()
        combo.showPopup()
        qt.processEvents()
        combo.view().setFocus()
        QTest.keyClick(combo.view(), Qt.Key.Key_Space)
        qt.processEvents()
        assert combo.currentIndex() == index_before
        combo.hidePopup()

    QTest.keyPress(preview.fit_button, Qt.Key.Key_Space)
    assert preview.canvas._space_down
    QApplication.sendEvent(preview, QEvent(QEvent.Type.WindowDeactivate))
    assert not preview.canvas._space_down
    assert not preview.canvas._panning
    QTest.keyRelease(preview.fit_button, Qt.Key.Key_Space)

    preview.fit_button.setFocus()
    QTest.keyPress(preview.fit_button, Qt.Key.Key_Space)
    start = preview.canvas.logical_cell_center((0, 0)).toPoint()
    before_pan = QPointF(preview.canvas.pan_offset)
    QTest.mousePress(preview.canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(preview.canvas, start + QPoint(28, 16), delay=10)
    QTest.mouseRelease(preview.canvas, Qt.MouseButton.LeftButton,
                       pos=start + QPoint(28, 16))
    QTest.keyRelease(preview.fit_button, Qt.Key.Key_Space)
    assert preview.canvas.pan_offset != before_pan
    assert locate_spy.count() == 0
    assert not preview.canvas._space_down and not preview.canvas._panning
    window.close()
    qt.processEvents()
