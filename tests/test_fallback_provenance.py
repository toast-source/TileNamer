from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QWidget

from tilenamer.guides import build_guide_placements, load_guide_registry
from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.placement import (
    ALLOWED_FALLBACK, MISSING, NORMAL, NO_FIT_CANDIDATE, OPTIONAL_ABSENT,
    REFERENCE_FALLBACK,
    REFERENCE_FALLBACK_RULES, WARNING_FALLBACK, available_families,
    available_patterns, build_placement_result, required_role_matrix,
)
from tilenamer.placement_window import (
    PlacementCanvas, PlacementPreviewSource, PlacementPreviewWindow,
)
from tilenamer.preferences import Preferences
from tilenamer.grid import GridReference
from tilenamer.ui import MainWindow


ROOT = Path(__file__).resolve().parents[1]
SOLID_FALLBACKS = {
    "Solid_LeftTopBridge": "Solid_LeftTop",
    "Solid_RightTopBridge": "Solid_RightTop",
    "Solid_LeftBridge": "Solid_Left",
    "Solid_RightBridge": "Solid_Right",
    "Solid_LeftBottomBridge": "Solid_LeftBottom",
    "Solid_RightBottomBridge": "Solid_RightBottom",
}
WALL_FALLBACKS = {
    "Wall_InnerSlash": "Wall_Inner",
    "Wall_InnerBackslash": "Wall_Inner",
}
PATTERN_CASES = tuple(
    (family, pattern)
    for family in available_families()
    for pattern in available_patterns(family)
)


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(path: Path) -> Preferences:
    return Preferences(QSettings(str(path), QSettings.Format.IniFormat))


def _target_cells(result, required: str):
    return [cell for cell in result.cells if cell.required_role == required]


def _model(categories: dict[str, tuple[int, int]]) -> AssignmentModel:
    cursor = 0
    assignments = {}
    for category, (width, height) in categories.items():
        assignments[category] = [AssetAssignment(category, cursor, 0, width, height)]
        cursor += width + 2
    return AssignmentModel(assignments)


def _all_exact_model() -> AssignmentModel:
    registry = load_guide_registry()
    assignments: dict[str, list[AssetAssignment]] = {}
    cursor = 0
    for category in sorted({asset.category for asset in registry.assets}):
        candidates = sorted(
            registry.for_category(category),
            key=lambda asset: asset.logical_width * asset.logical_height,
        )
        asset = candidates[0]
        assignment = AssetAssignment(
            category, cursor, 0, asset.logical_width, asset.logical_height,
        )
        assignments[category] = [assignment]
        cursor += asset.logical_width + 2
    return AssignmentModel(assignments)


def _guide_role_at(family: str, pattern: str, coord: tuple[int, int]) -> str | None:
    plan = build_guide_placements(family, pattern, load_guide_registry())
    owner = next((placement for placement in plan if coord in placement.occupied_cells), None)
    return owner.asset.category if owner is not None else None


def test_fallback_rules_are_one_canonical_eight_entry_table() -> None:
    assert {key: value.source_category for key, value in REFERENCE_FALLBACK_RULES.items()} == {
        **SOLID_FALLBACKS, **WALL_FALLBACKS,
    }
    assert all(
        REFERENCE_FALLBACK_RULES[key].severity == ALLOWED_FALLBACK
        for key in SOLID_FALLBACKS
    )
    assert all(
        REFERENCE_FALLBACK_RULES[key].severity == WARNING_FALLBACK
        for key in WALL_FALLBACKS
    )


def test_user_reproduction_a_normal_required_source_equality() -> None:
    result = build_placement_result(
        "Solid", _model({"Solid_Right": (1, 1)}), "종합",
    )
    cells = _target_cells(result, "Solid_Right")
    assert cells
    assert all(cell.required_role == cell.rendered_source_role == "Solid_Right" for cell in cells)
    assert all(cell.resolution_state == NORMAL and cell.fallback_for is None for cell in cells)


@pytest.mark.parametrize(("required", "source"), tuple(SOLID_FALLBACKS.items()))
def test_solid_bridge_six_fallback_provenance_and_required_guide(
    required: str, source: str,
) -> None:
    result = build_placement_result("Solid", _model({source: (1, 1)}), "종합")
    cells = _target_cells(result, required)
    assert cells
    assert all(cell.required_role == required for cell in cells)
    assert all(cell.rendered_source_role == source for cell in cells)
    assert all(cell.resolution_state == REFERENCE_FALLBACK for cell in cells)
    assert all(cell.fallback_severity == ALLOWED_FALLBACK for cell in cells)
    assert all(_guide_role_at("Solid", "종합", (cell.x, cell.y)) == required for cell in cells)
    assert (required, source, len(cells)) in result.fallback_counts
    assert required not in result.ready_roles
    assert required not in dict(result.missing_counts)
    assert required not in dict(result.optional_absent_counts)


@pytest.mark.parametrize(("required", "source"), tuple(WALL_FALLBACKS.items()))
def test_wall_two_fallback_provenance_warning_and_required_guide(
    required: str, source: str,
) -> None:
    result = build_placement_result("Wall", _model({source: (1, 1)}), "종합")
    cells = _target_cells(result, required)
    assert cells
    assert all(cell.rendered_source_role == source for cell in cells)
    assert all(cell.resolution_state == REFERENCE_FALLBACK for cell in cells)
    assert all(cell.fallback_severity == WARNING_FALLBACK for cell in cells)
    assert all(_guide_role_at("Wall", "종합", (cell.x, cell.y)) == required for cell in cells)
    assert dict(result.missing_counts)[required] == len(cells)
    assert (required, source, len(cells)) in result.fallback_counts


def test_exact_candidate_beats_fallback_then_remove_transitions() -> None:
    required, source = "Solid_LeftBridge", "Solid_Left"
    exact = build_placement_result(
        "Solid", _model({required: (1, 1), source: (1, 1)}), "종합",
    )
    assert all(
        cell.resolution_state == NORMAL and cell.rendered_source_role == required
        for cell in _target_cells(exact, required)
    )
    assert not any(item[0] == required for item in exact.fallback_counts)
    fallback = build_placement_result("Solid", _model({source: (1, 1)}), "종합")
    assert all(
        cell.resolution_state == REFERENCE_FALLBACK
        and cell.rendered_source_role == source
        for cell in _target_cells(fallback, required)
    )
    assert (required, source, len(_target_cells(fallback, required))) in fallback.fallback_counts
    assert required not in dict(fallback.missing_counts)
    missing = build_placement_result("Solid", AssignmentModel(), "종합")
    assert all(
        cell.resolution_state == OPTIONAL_ABSENT and cell.rendered_source_role is None
        for cell in _target_cells(missing, required)
    )
    assert required not in dict(missing.missing_counts)
    assert dict(missing.optional_absent_counts)[required] == len(_target_cells(missing, required))


def test_exact_no_fit_is_not_silently_hidden_by_fallback() -> None:
    required, source = "Solid_LeftBridge", "Solid_Left"
    result = build_placement_result(
        "Solid", _model({required: (2, 2), source: (1, 1)}), "종합",
    )
    cells = _target_cells(result, required)
    assert cells
    assert all(cell.resolution_state == NO_FIT_CANDIDATE for cell in cells)
    assert all(cell.rendered_source_role is None and cell.fallback_for is None for cell in cells)
    assert not result.fallback_counts


@pytest.mark.parametrize(("family", "pattern"), PATTERN_CASES)
def test_all_exact_14_pattern_state_and_illegal_fallback_audit(
    family: str, pattern: str,
) -> None:
    result = build_placement_result(family, _all_exact_model(), pattern)
    assert result.cells
    assert all(cell.resolution_state == NORMAL for cell in result.cells)
    assert all(cell.required_role == cell.rendered_source_role for cell in result.cells)
    assert not result.fallback_counts
    assert not result.missing_counts
    assert not result.no_fit_counts


@pytest.mark.parametrize(("family", "pattern"), PATTERN_CASES)
def test_required_role_matrix_is_candidate_state_independent(
    family: str, pattern: str,
) -> None:
    baseline = required_role_matrix(family, pattern)
    empty = build_placement_result(family, AssignmentModel(), pattern)
    partial = build_placement_result(
        family, _model({next(iter(empty.required_roles)): (1, 1)}), pattern,
    )
    exact = build_placement_result(family, _all_exact_model(), pattern)
    assert required_role_matrix(family, pattern) == baseline
    assert {category for _, category in baseline} == empty.required_roles
    assert empty.required_roles == partial.required_roles == exact.required_roles


def test_every_cross_role_source_is_explicitly_legal_across_fallback_fixtures() -> None:
    for required, source in REFERENCE_FALLBACK_RULES.items():
        family = "Solid" if required.startswith("Solid_") else "Wall"
        result = build_placement_result(
            family, _model({source.source_category: (1, 1)}), "종합",
        )
        for cell in result.cells:
            if cell.rendered_source_role is None or cell.required_role == cell.rendered_source_role:
                continue
            rule = REFERENCE_FALLBACK_RULES.get(cell.required_role)
            assert rule is not None, "ILLEGAL_ROLE_FALLBACK"
            assert rule.source_category == cell.rendered_source_role


def test_platform_and_top_sequence_have_no_cross_role_fallback() -> None:
    model = _all_exact_model()
    for family in ("Platform", "Top Sequence 00", "Top Sequence 01"):
        for pattern in available_patterns(family):
            result = build_placement_result(family, model, pattern)
            assert all(not cell.is_reference_fallback for cell in result.cells)
            assert all(
                cell.rendered_source_role in {None, cell.required_role} for cell in result.cells
            )


def test_49_guide_manifest_entries_remain_distinct_from_fallback_policy() -> None:
    registry = load_guide_registry()
    assert len(registry.assets) == 49
    assert len({asset.category for asset in registry.assets}) == 43
    for asset in registry.assets:
        assert asset.path.name == Path(asset.resource).name
        assert asset.source_reference.startswith("타일샘플/타일샘플/Chapter")
        assert asset.logical_width >= 1 and asset.logical_height >= 1
    bridge_assets = [asset for asset in registry.assets if asset.category in SOLID_FALLBACKS]
    assert Counter(
        (asset.logical_width, asset.logical_height) for asset in bridge_assets
    ) == {(1, 1): 6, (2, 1): 6}


def test_bridge_strip_exact_and_fallback_owner_canonical_role_matrix() -> None:
    matrix = dict(required_role_matrix("Solid", "종합"))
    for row, right, left in (
        (0, "Solid_RightTopBridge", "Solid_LeftTopBridge"),
        (2, "Solid_RightBridge", "Solid_LeftBridge"),
        (4, "Solid_RightBottomBridge", "Solid_LeftBottomBridge"),
    ):
        assert [matrix[(x, row)] for x in range(1, 6)] == [
            right, "Platform_Center", "Platform_Center", "Platform_Center", left,
        ]

    exact_sizes = {category: (2, 1) for category in SOLID_FALLBACKS}
    exact_sizes["Platform_Center"] = (1, 1)
    exact = build_placement_result("Solid", _model(exact_sizes), "종합")
    fallback_sizes = {source: (1, 1) for source in SOLID_FALLBACKS.values()}
    fallback_sizes["Platform_Center"] = (1, 1)
    fallback = build_placement_result("Solid", _model(fallback_sizes), "종합")
    for row in (0, 2, 4):
        exact_owners = [
            next(cell for cell in exact.cells if (x, row) in cell.occupied_cells)
            for x in range(1, 6)
        ]
        assert [len(set(cell.occupied_cells)) for cell in exact_owners] == [2, 2, 1, 2, 2]
        fallback_cells = [
            next(cell for cell in fallback.cells if (cell.x, cell.y) == (x, row))
            for x in (1, 5)
        ]
        assert all(cell.is_reference_fallback for cell in fallback_cells)
        assert all(cell.guide_role == matrix[(cell.x, cell.y)] for cell in fallback_cells)


def _image_contains(image, color: QColor) -> bool:
    return any(
        image.pixelColor(x, y).rgb() == color.rgb()
        for y in range(image.height()) for x in range(image.width())
    )


@pytest.mark.parametrize("dark", (False, True))
def test_fallback_visual_marker_and_hover_provenance_in_both_themes(
    tmp_path: Path, dark: bool,
) -> None:
    qt = app()
    result = build_placement_result(
        "Solid", _model({"Solid_Left": (1, 1)}), "종합",
    )
    fallback = _target_cells(result, "Solid_LeftBridge")[0]
    source = PlacementPreviewSource(
        _model({"Solid_Left": (1, 1)}), Image.new("RGBA", (32, 32), (220, 30, 20, 255)),
        GridReference(), ((122, 122, 122, 255), (96, 96, 96, 255)),
    )
    canvas = PlacementCanvas()
    canvas.resize(900, 420)
    canvas.set_theme(dark)
    canvas.set_grid_visible(False)
    canvas.set_preview(result, source)
    canvas.set_guides((), True, 5)
    canvas.show()
    qt.processEvents()
    canvas.fit_view()
    qt.processEvents()
    assert _image_contains(canvas.grab().toImage(), canvas.allowed_fallback_color)
    tooltip = canvas._provenance_tooltip(fallback)
    assert "필요: Solid_LeftBridge" in tooltip
    assert "대체 표시: Solid_Left" in tooltip
    assert "Solid_Left_00.png" in tooltip
    canvas.close()


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_footer_separates_solid_fallback_from_missing_in_both_themes(
    tmp_path: Path, theme: str,
) -> None:
    qt = app()
    model = _model({"Solid_Left": (1, 1)})
    prefs = preferences(tmp_path / f"{theme}.ini")
    source = PlacementPreviewSource(
        model, Image.new("RGBA", (32, 32), (220, 30, 20, 255)), GridReference(),
        prefs.alpha_colors(),
    )
    host = QWidget()
    window = PlacementPreviewWindow(host, lambda: source, QIcon(), theme, prefs)
    window.show()
    qt.processEvents()
    window.apply_theme(theme)
    window.refresh_preview()
    result = window.canvas.result
    assert result is not None and result.fallback_cell_count > 0
    assert window.summary_label.text() == (
        f"준비 {len(result.ready_roles)} / {len(result.required_roles)} · "
        f"대체 {result.fallback_cell_count} · 누락 {result.missing_cell_count}"
    )
    assert window.summary_label.isVisible()
    assert window.missing_label.isVisible()
    assert "Solid_LeftBridge" not in window.missing_label.text()
    assert window.summary_label.palette().color(window.summary_label.foregroundRole()).isValid()
    window.close()


def test_fallback_click_navigates_required_leaf_and_reports_source(tmp_path: Path) -> None:
    qt = app()
    source_path = tmp_path / "solid.png"
    Image.new("RGBA", (32, 32), (90, 110, 130, 255)).save(source_path)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source_path)
    window.model = _model({"Solid_Left": (1, 1)})
    window.refresh_assignments()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    preview.refresh_preview()
    fallback = _target_cells(preview.canvas.result, "Solid_LeftBridge")[0]
    preview._canvas_tile_clicked(fallback)
    qt.processEvents()
    assert window.current_category() == "Solid_LeftBridge"
    assert window.assignment_list.count() == 0
    assert window.statusBar().currentMessage() == (
        "Solid_LeftBridge가 필요합니다. 현재 Solid_Left_00.png을 대체 표시 중입니다."
    )
    window.close()


def test_all_eight_fallbacks_navigate_their_required_leaf(tmp_path: Path) -> None:
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    for required, rule in REFERENCE_FALLBACK_RULES.items():
        family = "Solid" if required.startswith("Solid_") else "Wall"
        result = build_placement_result(
            family, _model({rule.source_category: (1, 1)}), "종합",
        )
        cell = _target_cells(result, required)[0]
        window._locate_preview_fallback_role(cell)
        assert window.current_category() == required
        assert required in window.statusBar().currentMessage()
        assert cell.filename in window.statusBar().currentMessage()
    window.close()
