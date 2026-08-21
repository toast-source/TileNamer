from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QTextBrowser

from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.placement import (
    NO_FIT_CANDIDATE,
    SAMPLE_PATTERNS,
    _bridge_candidate_options,
    _occupied,
    _ordinary_candidate_options,
    _resolve_logical_roles,
    build_placement_result,
)
from tilenamer.preferences import Preferences
from tilenamer.ui import MainWindow


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def preferences(path: Path) -> Preferences:
    return Preferences(QSettings(str(path), QSettings.Format.IniFormat))


def move_mouse(widget, position: QPointF) -> None:
    event = QMouseEvent(
        QEvent.Type.MouseMove, position, widget.mapToGlobal(position.toPoint()),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)
    app().processEvents()


def _linear(value: int) -> float:
    channel = value / 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _contrast(foreground: QColor, background: QColor) -> float:
    first = 0.2126 * _linear(foreground.red()) + 0.7152 * _linear(
        foreground.green()
    ) + 0.0722 * _linear(foreground.blue())
    second = 0.2126 * _linear(background.red()) + 0.7152 * _linear(
        background.green()
    ) + 0.0722 * _linear(background.blue())
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _assert_palette_contrast(
    widget, foreground_role: QPalette.ColorRole, background_role: QPalette.ColorRole,
    minimum: float = 3.0,
) -> None:
    palette = widget.palette()
    assert _contrast(
        palette.color(foreground_role), palette.color(background_role),
    ) >= minimum


def test_auxiliary_dialogs_inherit_readable_dark_and_light_palettes(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    for theme in ("dark", "light"):
        window._set_theme(theme)
        input_dialog = window._create_auto_alignment_dialog(["Aseprite 문서 Grid"])
        mismatch, keep, choose, correct = window._create_alignment_mismatch_dialog({
            "Ch2. Terrein_N_wall": (0, 2),
            "Ch2. Terrein_N": (0, 3),
        })
        help_dialog = window.create_help_dialog()
        about_dialog = window.create_about_dialog()
        for dialog in (input_dialog, mismatch, help_dialog, about_dialog):
            dialog.show()
        qt.processEvents()

        input_label = next(label for label in input_dialog.findChildren(QLabel) if label.text())
        mismatch_labels = [label for label in mismatch.findChildren(QLabel) if label.text()]
        combo = input_dialog.findChild(QComboBox)
        browser = help_dialog.findChild(QTextBrowser)
        assert combo is not None and browser is not None and mismatch_labels
        for label in [input_label, *mismatch_labels, *about_dialog.findChildren(QLabel)]:
            _assert_palette_contrast(
                label, QPalette.ColorRole.WindowText, QPalette.ColorRole.Window, 4.0,
            )
        _assert_palette_contrast(combo, QPalette.ColorRole.Text, QPalette.ColorRole.Base, 4.0)
        _assert_palette_contrast(browser, QPalette.ColorRole.Text, QPalette.ColorRole.Base, 4.0)
        for button in (keep, choose, correct):
            _assert_palette_contrast(
                button, QPalette.ColorRole.ButtonText, QPalette.ColorRole.Button, 4.0,
            )
        disabled = QPushButton("비활성", mismatch)
        disabled.setEnabled(False)
        disabled_palette = disabled.palette()
        assert _contrast(
            disabled_palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
            ),
            disabled_palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button,
            ),
        ) >= 3.0
        assert input_label.text() == "기준 격자"
        assert any("레이어의 타일 정렬 기준이 서로 다릅니다" in label.text()
                   for label in mismatch_labels)
        for dialog in (input_dialog, mismatch, help_dialog, about_dialog):
            dialog.close()
    window.close()


def _solid_roles():
    occupied = _occupied(SAMPLE_PATTERNS["Solid"]["종합"])
    return occupied, _resolve_logical_roles("Solid", occupied)


def test_candidate_pool_filters_size_role_bounds_and_reservation_before_crc() -> None:
    _occupied_cells, roles = _solid_roles()
    anchor = (13, 2)
    candidates = [
        AssetAssignment("Solid_Inner", 0, 0, 1, 1),
        AssetAssignment("Solid_Inner", 2, 0, 1, 2),
    ]
    semantic = roles[anchor]
    options = _ordinary_candidate_options(
        anchor, semantic, semantic.category, candidates, roles,
    )
    assert [option.index for option in options] == [0, 1]
    assert [option.index for option in _ordinary_candidate_options(
        anchor, semantic, semantic.category, candidates, roles, {(13, 1)},
    )] == [0]

    edge_anchor = (13, 1)
    assert [option.index for option in _ordinary_candidate_options(
        edge_anchor, roles[edge_anchor], "Solid_Inner", candidates, roles,
    )] == [0]

    wrong_role = [AssetAssignment("Solid_LeftTop", 4, 0, 2, 2)]
    assert not _ordinary_candidate_options(
        (0, 0), roles[(0, 0)], "Solid_LeftTop", wrong_role, roles,
    )


def test_irregular_footprint_hole_is_not_reserved() -> None:
    _occupied_cells, roles = _solid_roles()
    anchor = (14, 3)
    candidate = AssetAssignment.from_cells(
        "Solid_Inner", ((0, 0), (1, 0), (0, 1)),
    )
    semantic = roles[anchor]
    options = _ordinary_candidate_options(
        anchor, semantic, semantic.category, [candidate], roles, {(15, 3)},
    )
    assert len(options) == 1
    assert (15, 3) not in options[0].footprint
    assert not _ordinary_candidate_options(
        anchor, semantic, semantic.category, [candidate], roles, {(15, 2)},
    )


def test_mixed_size_candidates_are_deterministic_and_never_overlap() -> None:
    for size in ((1, 2), (2, 1), (2, 2)):
        model = AssignmentModel({
            "Solid_Inner": [
                AssetAssignment("Solid_Inner", 0, 0, 1, 1),
                AssetAssignment("Solid_Inner", 4, 0, *size),
            ],
        })
        first = build_placement_result("Solid", model)
        second = build_placement_result("Solid", model)
        assert first == second
        placed = [cell for cell in first.cells if cell.assignment_identity is not None]
        assert any((cell.width_cells, cell.height_cells) == size for cell in placed)
        reserved: set[tuple[int, int]] = set()
        for cell in placed:
            assert reserved.isdisjoint(cell.occupied_cells)
            reserved.update(cell.occupied_cells)
        assert not first.no_fit_counts


def test_platform_is_one_cell_and_reports_distinct_no_fit() -> None:
    model = AssignmentModel({
        "Platform_Center": [AssetAssignment("Platform_Center", 0, 0, 2, 1)],
    })
    result = build_placement_result("Platform", model)
    centers = [cell for cell in result.cells if cell.category == "Platform_Center"]
    assert centers and all(cell.diagnostic == NO_FIT_CANDIDATE for cell in centers)
    assert all(cell.assignment_identity is None and not cell.is_missing for cell in centers)
    assert dict(result.no_fit_counts) == {"Platform_Center": len(centers)}
    assert "Platform_Center" not in dict(result.missing_counts)
    assert "Platform_Center" in result.ready_roles


def test_bridge_pool_allows_only_context_compatible_1x1_or_2x1() -> None:
    _occupied_cells, roles = _solid_roles()
    anchor = (1, 0)
    semantic = roles[anchor]
    candidates = [
        AssetAssignment(semantic.category, 0, 0, 1, 1),
        AssetAssignment(semantic.category, 2, 0, 2, 1),
        AssetAssignment(semantic.category, 5, 0, 2, 2),
    ]
    options = _bridge_candidate_options(anchor, semantic, candidates, roles)
    assert [option.index for option in options] == [0, 1]
    platform_companion = (2, 0)
    assert [option.index for option in _bridge_candidate_options(
        anchor, semantic, candidates, roles, {platform_companion},
    )] == [0]


def test_wall_multicell_and_diagonal_fallback_respect_fit() -> None:
    model = AssignmentModel({
        "Wall_Inner": [AssetAssignment("Wall_Inner", 0, 0, 2, 2)],
    })
    result = build_placement_result("Wall", model)
    placed = [cell for cell in result.cells if cell.assignment_identity is not None]
    assert placed and all(cell.category == "Wall_Inner" for cell in placed)
    reserved: set[tuple[int, int]] = set()
    for cell in placed:
        assert reserved.isdisjoint(cell.occupied_cells)
        reserved.update(cell.occupied_cells)
    diagonal = [cell for cell in result.cells if cell.fallback_for is not None]
    assert not diagonal
    diagonal_cells = [
        cell for cell in result.cells
        if cell.required_category in {"Wall_InnerSlash", "Wall_InnerBackslash"}
    ]
    assert {cell.required_category for cell in diagonal_cells} == {
        "Wall_InnerSlash", "Wall_InnerBackslash",
    }
    assert all(cell.is_missing for cell in diagonal_cells)


def test_top_sequences_filter_exact_part_width_and_height() -> None:
    for sequence, height in (("00", 3), ("01", 2)):
        categories = {
            part: f"Solid_TopSequence_{part}_{sequence}"
            for part in ("Start", "Repeat", "End")
        }
        assignments = {}
        source_x = 0
        for part, category in categories.items():
            width = 2 if part == "Repeat" else 1
            assignments[category] = [
                AssetAssignment(category, source_x, 0, width, 1),
                AssetAssignment(category, source_x + 4, 0, width, height),
            ]
            source_x += 8
        result = build_placement_result(
            f"Top Sequence {sequence}", AssignmentModel(assignments), "최소 시퀀스",
        )
        assert [cell.candidate_index for cell in result.cells] == [1, 1, 1]
        assert [cell.width_cells for cell in result.cells] == [1, 2, 1]
        assert all(cell.height_cells == height for cell in result.cells)
        assert not result.missing_counts and not result.no_fit_counts

        wrong_only = {
            category: [AssetAssignment(category, index * 3, 0, 1, 1)]
            for index, category in enumerate(categories.values())
        }
        no_fit = build_placement_result(
            f"Top Sequence {sequence}", AssignmentModel(wrong_only), "최소 시퀀스",
        )
        assert not no_fit.missing_counts
        assert dict(no_fit.no_fit_counts) == {
            category: 1 for category in categories.values()
        }
        assert all(cell.diagnostic == NO_FIT_CANDIDATE for cell in no_fit.cells)
        assert [cell.width_cells for cell in no_fit.cells] == [1, 2, 1]
        assert all(cell.height_cells == height for cell in no_fit.cells)


def test_reference_solid_and_wall_single_role_coverage_remains_complete() -> None:
    for family in ("Solid", "Wall"):
        required = build_placement_result(family, AssignmentModel()).required_roles
        model = AssignmentModel({
            category: [(index, 0)] for index, category in enumerate(sorted(required))
        })
        result = build_placement_result(family, model)
        assert result.ready_roles == result.required_roles
        assert not result.missing_counts
        assert not result.no_fit_counts


def test_filtered_multicell_hover_exact_locate_and_no_fit_status(tmp_path: Path) -> None:
    qt = app()
    source = tmp_path / "mixed.png"
    Image.new("RGBA", (256, 96), (90, 120, 150, 255)).save(source)
    window = MainWindow(ROOT, preferences(tmp_path / "prefs.ini"))
    assert window._load_source(source)
    window.model = AssignmentModel({
        "Solid_Inner": [
            AssetAssignment("Solid_Inner", 0, 0, 1, 1),
            AssetAssignment("Solid_Inner", 2, 0, 2, 2),
        ],
    })
    window.refresh_assignments()
    window.show()
    window.show_placement_preview()
    preview = window.placement_preview_window
    assert preview is not None
    preview.refresh_preview()
    preview.canvas.fit_view()
    target = next(
        cell for cell in preview.canvas.result.cells
        if cell.category == "Solid_Inner" and cell.candidate_index == 1
    )
    for coordinate in target.occupied_cells:
        assert preview.canvas.hit_test(
            preview.canvas.logical_cell_center(coordinate)
        ) is target
    point = preview.canvas.logical_cell_center(target.occupied_cells[-1]).toPoint()
    move_mouse(preview.canvas, QPointF(point))
    assert preview.canvas.hovered_cell is target
    preview._canvas_tile_clicked(target)
    qt.processEvents()
    assert window.current_category() == "Solid_Inner"
    assert window.assignment_list.currentRow() == 1

    window.model = AssignmentModel({
        "Platform_Center": [AssetAssignment("Platform_Center", 6, 0, 2, 1)],
    })
    window.refresh_assignments()
    preview.family_combo.setCurrentText("Platform")
    preview.refresh_preview()
    no_fit = next(cell for cell in preview.canvas.result.cells if cell.is_no_fit)
    preview._canvas_tile_clicked(no_fit)
    assert window.statusBar().currentMessage() == "이 위치에 맞는 크기의 후보가 없습니다."
    window.close()
    qt.processEvents()
