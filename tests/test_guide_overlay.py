from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from tilenamer.grid import GridReference
from tilenamer.guides import (
    GuideAsset, GuidePlacement, GuideRegistry, build_guide_placements, guide_resource_root,
    load_guide_registry,
)
from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.placement import (
    AssignmentIdentity, PlacementCell, PlacementResult, available_families,
    available_patterns, build_placement_result,
)
from tilenamer.placement_window import (
    PlacementCanvas, PlacementPreviewSource, PlacementPreviewWindow,
)
from tilenamer.preferences import Preferences
from tilenamer.ui import MainWindow


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(path: Path) -> Preferences:
    return Preferences(QSettings(str(path), QSettings.Format.IniFormat))


def test_bundled_manifest_inventory_metadata_and_family_coverage() -> None:
    registry = load_guide_registry()
    assert len(registry.assets) == 49
    assert len({asset.category for asset in registry.assets}) == 43
    assert all(asset.path.is_file() for asset in registry.assets)
    assert all(asset.source_reference.startswith("타일샘플/") for asset in registry.assets)
    assert all(1 <= asset.logical_width <= 4 for asset in registry.assets)
    categories = {asset.category for asset in registry.assets}
    assert sum(category.startswith("Solid_") and "TopSequence" not in category
               for category in categories) == 19
    assert sum(category.startswith("Wall_") for category in categories) == 15
    assert sum(category.startswith("Platform_") for category in categories) == 3
    assert sum(category.endswith("_00") and "TopSequence" in category
               for category in categories) == 3
    assert sum(category.endswith("_01") and "TopSequence" in category
               for category in categories) == 3


def test_type00_type01_and_multicell_metadata_are_isolated() -> None:
    registry = load_guide_registry()
    zero = build_guide_placements("Top Sequence 00", "긴 시퀀스", registry)
    one = build_guide_placements("Top Sequence 01", "긴 시퀀스", registry)
    assert zero and all(item.asset.category.endswith("_00") for item in zero)
    assert one and all(item.asset.category.endswith("_01") for item in one)
    assert {(item.asset.logical_width, item.asset.logical_height) for item in zero} == {
        (1, 3), (2, 3),
    }
    assert {(item.asset.logical_width, item.asset.logical_height) for item in one} == {
        (1, 2), (2, 2),
    }
    bridges = [asset for asset in registry.assets if asset.category.endswith("Bridge")]
    assert len(bridges) == 12
    assert {(asset.logical_width, asset.logical_height) for asset in bridges} == {
        (1, 1), (2, 1),
    }


def test_loader_skips_missing_wrong_traversal_and_malformed_entries(tmp_path: Path) -> None:
    guide_root = tmp_path / "guides"
    guide_root.mkdir()
    Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(guide_root / "valid.png")
    manifest = {
        "format_version": 1,
        "guides": [
            {"category": "Platform_Center", "resource": "valid.png",
             "logical_width": 1, "logical_height": 1},
            {"category": "Platform_LeftEnd", "resource": "missing.png",
             "logical_width": 1, "logical_height": 1},
            {"category": "Platform_RightEnd", "resource": "../outside.png",
             "logical_width": 1, "logical_height": 1},
            {"category": "Solid_Top", "resource": "valid.png",
             "logical_width": 0, "logical_height": 1},
            {"resource": "valid.png", "logical_width": 1, "logical_height": 1},
        ],
    }
    (guide_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    registry = load_guide_registry(tmp_path)
    assert [asset.category for asset in registry.assets] == ["Platform_Center"]
    assert load_guide_registry(tmp_path / "absent") == GuideRegistry()
    (guide_root / "manifest.json").write_text("not-json", encoding="utf-8")
    assert load_guide_registry(tmp_path) == GuideRegistry()


def test_packaged_style_resource_path_helper(tmp_path: Path) -> None:
    assert guide_resource_root(tmp_path) == (tmp_path / "guides").resolve()
    direct = tmp_path / "direct"
    direct.mkdir()
    (direct / "manifest.json").write_text("{}", encoding="utf-8")
    assert guide_resource_root(direct) == direct.resolve()


def test_partial_registry_never_substitutes_another_role(tmp_path: Path) -> None:
    image_path = tmp_path / "inner.png"
    Image.new("RGBA", (32, 32), (80, 120, 160, 255)).save(image_path)
    registry = GuideRegistry((GuideAsset(
        "Wall_Inner", "inner.png", 1, 1, "sample", image_path,
    ),))
    placements = build_guide_placements("Wall", "대각선", registry)
    assert placements == ()
    ordinary = build_guide_placements("Wall", "종합", registry)
    assert ordinary
    assert {placement.asset.category for placement in ordinary} == {"Wall_Inner"}
    assert build_guide_placements("Platform", "단일", registry) == ()


def test_guide_resolution_does_not_change_actual_placement_result() -> None:
    model = AssignmentModel({
        "Platform_LeftEnd": [AssetAssignment("Platform_LeftEnd", 0, 0)],
        "Platform_Center": [AssetAssignment("Platform_Center", 1, 0)],
        "Platform_RightEnd": [AssetAssignment("Platform_RightEnd", 2, 0)],
    })
    before = build_placement_result("Platform", model, "종합")
    guides = build_guide_placements("Platform", "종합", load_guide_registry())
    after = build_placement_result("Platform", model, "종합")
    assert guides
    assert before == after


def test_preferences_default_clamp_and_restore(tmp_path: Path) -> None:
    path = tmp_path / "prefs.ini"
    first = preferences(path)
    assert first.placement_guide_enabled is True
    assert first.placement_guide_opacity == 25
    first.placement_guide_enabled = False
    first.placement_guide_opacity = 37
    first.settings.sync()
    restored = preferences(path)
    assert restored.placement_guide_enabled is False
    assert restored.placement_guide_opacity == 37
    restored.placement_guide_opacity = 999
    assert restored.placement_guide_opacity == 80


def test_toggle_slider_render_state_and_view_state_are_independent(tmp_path: Path) -> None:
    qt = app()
    prefs = preferences(tmp_path / "prefs.ini")
    source = PlacementPreviewSource(
        AssignmentModel(), Image.new("RGBA", (32, 32)), GridReference(),
        prefs.alpha_colors(),
    )
    host = QWidget()
    window = PlacementPreviewWindow(host, lambda: source, QIcon(), "dark", prefs)
    window.show()
    qt.processEvents()
    assert window.guide_toggle.isChecked()
    assert window.guide_opacity_slider.minimum() == 5
    assert window.guide_opacity_slider.maximum() == 80
    assert window.guide_opacity_slider.value() == 25
    assert window.guide_opacity_value.text() == "25%"
    assert window.canvas.guide_placements

    window.canvas.set_zoom(2.0)
    window.canvas.pan_offset = QPointF(17, -9)
    zoom, pan, fit = window.canvas.zoom, QPointF(window.canvas.pan_offset), window.canvas.fit_mode
    result = window.canvas.result
    window.guide_opacity_slider.setValue(37)
    window.guide_toggle.setChecked(False)
    assert not window.guide_opacity_slider.isEnabled()
    assert not window.guide_opacity_value.isEnabled()
    assert not window.canvas.guides_enabled
    window.guide_toggle.setChecked(True)
    assert window.guide_opacity_slider.value() == 37
    assert window.canvas.result is result
    assert (window.canvas.zoom, window.canvas.pan_offset, window.canvas.fit_mode) == (
        zoom, pan, fit,
    )
    window.close()


def test_offscreen_guide_is_visible_below_warning_and_off_skips_it(tmp_path: Path) -> None:
    qt = app()
    prefs = preferences(tmp_path / "prefs.ini")
    source = PlacementPreviewSource(
        AssignmentModel(), Image.new("RGBA", (32, 32)), GridReference(),
        prefs.alpha_colors(),
    )
    host = QWidget()
    window = PlacementPreviewWindow(host, lambda: source, QIcon(), "light", prefs)
    window.resize(900, 620)
    window.show()
    qt.processEvents()
    window.canvas.set_grid_visible(False)
    with_guide = window.canvas.grab().toImage()
    window.guide_toggle.setChecked(False)
    qt.processEvents()
    without_guide = window.canvas.grab().toImage()
    assert with_guide != without_guide
    assert window.canvas.hit_test(window.canvas.logical_cell_center((0, 0))) is not None
    window.close()


def test_controls_remain_readable_and_disabled_in_both_themes(tmp_path: Path) -> None:
    qt = app()
    prefs = preferences(tmp_path / "prefs.ini")
    source = PlacementPreviewSource(AssignmentModel(), None, GridReference(), prefs.alpha_colors())
    host = QWidget()
    window = PlacementPreviewWindow(host, lambda: source, QIcon(), "light", prefs)
    for theme in ("light", "dark"):
        window.apply_theme(theme)
        window.guide_toggle.setChecked(True)
        window.show()
        qt.processEvents()
        assert window.guide_toggle.isVisible()
        assert window.guide_opacity_slider.isEnabled()
        assert window.guide_opacity_value.text().endswith("%")
        window.guide_toggle.setChecked(False)
        assert not window.guide_opacity_slider.isEnabled()
        assert "QSlider::handle" in window.styleSheet()
    window.close()


def test_guide_preference_does_not_dirty_project_or_add_undo(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    window.show_placement_preview()
    qt.processEvents()
    preview = window.placement_preview_window
    assert preview is not None
    assert not window.project_dirty
    undo_count = window.undo_stack.count()
    preview.guide_opacity_slider.setValue(64)
    preview.guide_toggle.setChecked(False)
    assert not window.project_dirty
    assert window.undo_stack.count() == undo_count
    window.close()


def test_actual_tile_is_rendered_above_persistent_guide(tmp_path: Path) -> None:
    qt = app()
    prefs = preferences(tmp_path / "prefs.ini")
    source = PlacementPreviewSource(
        AssignmentModel({"Platform_Center": [AssetAssignment("Platform_Center", 0, 0)]}),
        Image.new("RGBA", (32, 32), (250, 20, 20, 255)), GridReference(),
        prefs.alpha_colors(),
    )
    host = QWidget()
    window = PlacementPreviewWindow(host, lambda: source, QIcon(), "light", prefs)
    window.family_combo.setCurrentText("Platform")
    window.pattern_combo.setCurrentText("단일")
    window.resize(820, 600)
    window.show()
    qt.processEvents()
    window.canvas.set_grid_visible(False)
    actual_cell = next(
        cell for cell in window.canvas.result.cells if cell.assignment_identity is not None
    )
    actual_pixmap_key = window.canvas.pixmaps[actual_cell.assignment_identity].cacheKey()
    window.guide_opacity_slider.setValue(5)
    qt.processEvents()
    low_opacity = window.canvas.grab().toImage()
    window.guide_opacity_slider.setValue(80)
    qt.processEvents()
    guide_on = window.canvas.grab().toImage()
    window.guide_toggle.setChecked(False)
    qt.processEvents()
    guide_off = window.canvas.grab().toImage()
    center = window.canvas.logical_cell_center((0, 0)).toPoint()
    assert low_opacity.pixelColor(center) == guide_on.pixelColor(center)
    assert guide_on.pixelColor(center) == guide_off.pixelColor(center)
    assert window.canvas.pixmaps[actual_cell.assignment_identity].cacheKey() == actual_pixmap_key
    window.close()


def _persistent_overlay_render(
    tmp_path: Path, size: tuple[int, int], actual_cells: tuple[tuple[int, int], ...],
    *, transparent_actual: bool = False,
) -> tuple[PlacementCanvas, object, object]:
    width, height = size
    guide_path = tmp_path / f"guide_{width}x{height}.png"
    Image.new("RGBA", (width * 32, height * 32), (20, 90, 240, 255)).save(guide_path)
    guide_asset = GuideAsset(
        "Test_Guide", guide_path.name, width, height, "test", guide_path,
    )
    guide = GuidePlacement(
        guide_asset,
        tuple((x, y) for y in range(height) for x in range(width)),
    )
    assignment = AssetAssignment.from_cells("Actual", actual_cells)
    identity = AssignmentIdentity("Actual", assignment.selected_cells or ())
    cell = PlacementCell(
        min(x for x, _ in actual_cells), min(y for _, y in actual_cells),
        "Test", "Actual", "Actual", "Actual", 0, identity, "Actual_00.png",
        actual_cells, assignment.width_cells, assignment.height_cells,
    )
    result = PlacementResult(
        "Test", "Test", width, height, (cell,), frozenset({"Actual"}),
        frozenset({"Actual"}), (), frozenset(guide.occupied_cells), (),
    )
    source_color = (0, 0, 0, 0) if transparent_actual else (245, 35, 25, 255)
    source = PlacementPreviewSource(
        AssignmentModel({"Actual": [assignment]}),
        Image.new("RGBA", (width * 32, height * 32), source_color),
        GridReference(), ((122, 122, 122, 255), (96, 96, 96, 255)),
    )
    canvas = PlacementCanvas()
    canvas.resize(360, 280)
    canvas.show()
    canvas.set_theme(False)
    canvas.set_grid_visible(False)
    canvas.set_preview(result, source)
    canvas.set_guides((guide,), True, 80)
    app().processEvents()
    canvas.fit_view()
    app().processEvents()
    guide_on = canvas.grab().toImage()
    canvas.set_guides_enabled(False)
    app().processEvents()
    guide_off = canvas.grab().toImage()
    return canvas, guide_on, guide_off


def _cell_color(image, canvas: PlacementCanvas, cell: tuple[int, int]):
    return image.pixelColor(canvas.logical_cell_center(cell).toPoint())


@pytest.mark.parametrize(
    ("actual", "survivor"),
    [(((0, 0),), (1, 0)), (((1, 0),), (0, 0))],
)
def test_2x1_guide_is_below_actual_and_keeps_opposite_cell(
    tmp_path: Path, actual, survivor,
) -> None:
    canvas, guide_on, guide_off = _persistent_overlay_render(tmp_path, (2, 1), actual)
    assert _cell_color(guide_on, canvas, actual[0]) == _cell_color(
        guide_off, canvas, actual[0],
    )
    assert _cell_color(guide_on, canvas, survivor) != _cell_color(
        guide_off, canvas, survivor,
    )
    canvas.close()


def test_1x2_and_2x2_guides_are_below_actual_and_visible_elsewhere(tmp_path: Path) -> None:
    for size, actual, survivors in (
        ((1, 2), ((0, 0),), ((0, 1),)),
        ((2, 2), ((1, 0),), ((0, 0), (0, 1), (1, 1))),
    ):
        case_root = tmp_path / f"{size[0]}x{size[1]}"
        case_root.mkdir()
        canvas, guide_on, guide_off = _persistent_overlay_render(case_root, size, actual)
        assert _cell_color(guide_on, canvas, actual[0]) == _cell_color(
            guide_off, canvas, actual[0],
        )
        assert all(
            _cell_color(guide_on, canvas, cell) != _cell_color(guide_off, canvas, cell)
            for cell in survivors
        )
        canvas.close()


def test_irregular_actual_covers_guide_only_with_its_rendered_pixels(
    tmp_path: Path,
) -> None:
    selected = ((0, 0), (1, 1))
    holes = ((1, 0), (0, 1))
    canvas, guide_on, guide_off = _persistent_overlay_render(tmp_path, (2, 2), selected)
    assert all(
        _cell_color(guide_on, canvas, cell) == _cell_color(guide_off, canvas, cell)
        for cell in selected
    )
    assert all(
        _cell_color(guide_on, canvas, cell) != _cell_color(guide_off, canvas, cell)
        for cell in holes
    )
    canvas.close()


def test_transparent_actual_still_receives_guide_and_neighbor_survives(
    tmp_path: Path,
) -> None:
    canvas, guide_on, guide_off = _persistent_overlay_render(
        tmp_path, (2, 1), ((0, 0),), transparent_actual=True,
    )
    assert _cell_color(guide_on, canvas, (0, 0)) != _cell_color(
        guide_off, canvas, (0, 0),
    )
    assert _cell_color(guide_on, canvas, (1, 0)) != _cell_color(
        guide_off, canvas, (1, 0),
    )
    canvas.close()


def test_guide_plan_is_identical_for_zero_one_and_multiple_actual_assignments() -> None:
    registry = load_guide_registry()
    models = (
        AssignmentModel(),
        AssignmentModel({
            "Platform_Center": [AssetAssignment("Platform_Center", 0, 0)],
        }),
        AssignmentModel({
            "Platform_Center": [AssetAssignment("Platform_Center", 0, 0)],
            "Solid_Top": [AssetAssignment("Solid_Top", 1, 0)],
            "Solid_Inner": [AssetAssignment("Solid_Inner", 2, 0)],
        }),
    )
    plans = []
    for model in models:
        build_placement_result("Solid", model, "종합")
        plans.append(build_guide_placements("Solid", "종합", registry))
    assert plans[0] == plans[1] == plans[2]


def test_solid_comprehensive_bridge_platform_strips_cover_five_cells() -> None:
    plan = build_guide_placements("Solid", "종합", load_guide_registry())
    for row in (0, 2, 4):
        strip_placements = [
            placement for placement in plan
            if any(y == row and 1 <= x <= 5 for x, y in placement.occupied_cells)
            and (
                placement.asset.category.endswith("Bridge")
                or placement.asset.category == "Platform_Center"
            )
        ]
        strip = {
            cell
            for placement in plan
            if placement.asset.category in {
                "Solid_RightTopBridge", "Solid_LeftTopBridge",
                "Solid_RightBridge", "Solid_LeftBridge",
                "Solid_RightBottomBridge", "Solid_LeftBottomBridge",
                "Platform_Center",
            }
            for cell in placement.occupied_cells
            if cell[1] == row and 1 <= cell[0] <= 5
        }
        assert strip == {(x, row) for x in range(1, 6)}
        assert sorted(len(placement.occupied_cells) for placement in strip_placements) == [1, 2, 2]
        assert sum(
            placement.asset.category == "Platform_Center"
            for placement in strip_placements
        ) == 1


PATTERN_CASES = tuple(
    (family, pattern)
    for family in available_families()
    for pattern in available_patterns(family)
)


def _canonical_guide_plan(plan: tuple[GuidePlacement, ...]) -> tuple:
    return tuple(sorted(
        (
            placement.asset.category,
            min(placement.occupied_cells, key=lambda cell: (cell[1], cell[0])),
            tuple(sorted(placement.occupied_cells)),
            placement.asset.resource,
            placement.asset.logical_width,
            placement.asset.logical_height,
        )
        for placement in plan
    ))


def _actual_model_for(
    categories: tuple[str, ...], registry: GuideRegistry, *, candidates: int = 1,
    mixed_sizes: bool = False,
) -> AssignmentModel:
    assignments: dict[str, list[AssetAssignment]] = {}
    cursor = 0
    size_cycle = ((1, 1), (2, 1), (1, 2), (2, 2))
    for category_index, category in enumerate(categories):
        guides = registry.for_category(category)
        default_size = (
            (guides[0].logical_width, guides[0].logical_height) if guides else (1, 1)
        )
        for candidate_index in range(candidates):
            width, height = (
                size_cycle[candidate_index % len(size_cycle)]
                if mixed_sizes else default_size
            )
            assignments.setdefault(category, []).append(
                AssetAssignment(category, cursor, category_index * 5, width, height),
            )
            cursor += width + 2
    return AssignmentModel(assignments)


@pytest.mark.parametrize(("family", "pattern"), PATTERN_CASES)
def test_full_pattern_guide_invariant_holes_overlap_and_bounds(
    family: str, pattern: str,
) -> None:
    registry = load_guide_registry()
    baseline = build_guide_placements(family, pattern, registry)
    snapshot = _canonical_guide_plan(baseline)
    logical = build_placement_result(family, AssignmentModel(), pattern)
    coverage = [cell for placement in baseline for cell in placement.occupied_cells]
    counts = Counter(coverage)

    assert set(coverage) == set(logical.logical_occupied), "UNEXPECTED_GUIDE_HOLE"
    assert all(count == 1 for count in counts.values()), "GUIDE_FOOTPRINT_OVERLAP"
    assert all(
        0 <= x < logical.width and 0 <= y < logical.height for x, y in coverage
    ), "GUIDE_LOGICAL_BOUNDS_VIOLATION"

    roles = tuple(sorted(logical.required_roles))
    # Every individual role injection must leave the immutable Guide plan intact.
    for category in roles:
        actual = _actual_model_for((category,), registry)
        build_placement_result(family, actual, pattern)
        assert _canonical_guide_plan(
            build_guide_placements(family, pattern, registry)
        ) == snapshot

    # 0/25%/50%/all roles and multiple candidate counts remain independent.
    subsets = (
        (), roles[:max(1, len(roles) // 4)], roles[:max(1, len(roles) // 2)], roles,
    )
    for subset in subsets:
        actual = _actual_model_for(tuple(subset), registry)
        build_placement_result(family, actual, pattern)
        assert _canonical_guide_plan(
            build_guide_placements(family, pattern, registry)
        ) == snapshot
    if roles:
        for candidate_count in (1, 2, 4):
            actual = _actual_model_for((roles[0],), registry, candidates=candidate_count)
            build_placement_result(family, actual, pattern)
            assert _canonical_guide_plan(
                build_guide_placements(family, pattern, registry)
            ) == snapshot
        mixed = _actual_model_for((roles[0],), registry, candidates=4, mixed_sizes=True)
        build_placement_result(family, mixed, pattern)
        assert _canonical_guide_plan(
            build_guide_placements(family, pattern, registry)
        ) == snapshot


def test_all_43_manifest_roles_are_reachable_through_real_guide_plans() -> None:
    registry = load_guide_registry()
    reachable = {
        placement.asset.category
        for family, pattern in PATTERN_CASES
        for placement in build_guide_placements(family, pattern, registry)
    }
    manifest_roles = {asset.category for asset in registry.assets}
    assert len(manifest_roles) == 43
    assert reachable == manifest_roles


@pytest.mark.parametrize(
    ("family", "pattern", "actual_category"),
    (
        ("Solid", "종합", "Platform_Center"),
        ("Wall", "종합", "Wall_Inner"),
        ("Solid", "브릿지", "Solid_RightBridge"),
        ("Top Sequence 00", "긴 시퀀스", "Solid_TopSequence_Repeat_00"),
        ("Top Sequence 01", "긴 시퀀스", "Solid_TopSequence_Repeat_01"),
        ("Platform", "종합", "Platform_Center"),
    ),
)
def test_user_reproduction_actual_role_never_changes_guide_plan(
    family: str, pattern: str, actual_category: str,
) -> None:
    registry = load_guide_registry()
    baseline = _canonical_guide_plan(build_guide_placements(family, pattern, registry))
    actual = _actual_model_for((actual_category,), registry)
    build_placement_result(family, actual, pattern)
    assert _canonical_guide_plan(build_guide_placements(family, pattern, registry)) == baseline


def test_reused_wall_inner_actual_pixels_are_all_above_guides(
    tmp_path: Path,
) -> None:
    qt = app()
    prefs = preferences(tmp_path / "prefs.ini")
    model = AssignmentModel({"Wall_Inner": [AssetAssignment("Wall_Inner", 0, 0)]})
    source = PlacementPreviewSource(
        model, Image.new("RGBA", (32, 32), (245, 35, 25, 255)), GridReference(),
        prefs.alpha_colors(),
    )
    host = QWidget()
    window = PlacementPreviewWindow(host, lambda: source, QIcon(), "light", prefs)
    window.family_combo.setCurrentText("Wall")
    window.pattern_combo.setCurrentText("종합")
    window.resize(900, 620)
    window.show()
    qt.processEvents()
    window.canvas.set_grid_visible(False)
    window.guide_opacity_slider.setValue(80)
    with_guides = window.canvas.grab().toImage()
    inner_cells = [
        cell for cell in window.canvas.result.cells if cell.category == "Wall_Inner"
    ]
    actual_cell = next(cell for cell in inner_cells if cell.assignment_identity is not None)
    window.guide_toggle.setChecked(False)
    qt.processEvents()
    without_guides = window.canvas.grab().toImage()
    assert len(inner_cells) > 1
    assert all(cell.assignment_identity is not None for cell in inner_cells)
    assert all(
        _cell_color(with_guides, window.canvas, cell.occupied_cells[0])
        == _cell_color(without_guides, window.canvas, cell.occupied_cells[0])
        for cell in inner_cells
    )
    window.close()
