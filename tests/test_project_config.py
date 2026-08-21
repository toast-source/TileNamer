from pathlib import Path

import pytest

from tilenamer.config import CategoryRule, load_categories, validate_categories
from tilenamer.grid import GridReference
from tilenamer.model import AssetAssignment, AssignmentModel
from tilenamer.project import TileProject


ROOT = Path(__file__).resolve().parents[1]


def test_analyzed_category_config_is_valid() -> None:
    categories = load_categories(ROOT / "tile_names.json")
    assert {"Platform_Center", "Solid_Bottom", "Wall_InnerBackslash"} <= {rule.name for rule in categories}
    assert all(rule.filename(0).endswith("_00.png") for rule in categories)


def test_config_duplicate_validation() -> None:
    with pytest.raises(ValueError, match="중복"):
        validate_categories([CategoryRule("A", "A"), CategoryRule("A", "B")])


def test_project_round_trip(tmp_path: Path) -> None:
    original = TileProject(
        r"C:\art\tiles.aseprite",
        32,
        AssignmentModel({"Platform_Center": [AssetAssignment("Platform_Center", 3, 4, 2, 1), (1, 2)],
                         "Solid_Bottom": [(0, 0)]}),
        {"1": True, "1/2": False},
    )
    path = tmp_path / "work.tilenamer.json"
    original.save(path)
    restored = TileProject.load(path)
    assert restored.source_file == original.source_file
    assert restored.tile_size == 32
    assert restored.model.assignments == original.model.assignments
    assert restored.layer_visibility == original.layer_visibility
    assert '"format_version": 9' in path.read_text(encoding="utf-8")


def test_v1_project_migrates_coordinates_to_1x1_assets(tmp_path: Path) -> None:
    path = tmp_path / "old.tilenamer.json"
    path.write_text(
        '{"format_version":1,"source_file":"x.png","tile_size":32,'
        '"assignments":{"Solid_Top":[[2,3]]}}', encoding="utf-8"
    )
    restored = TileProject.load(path)
    asset = restored.model.assets("Solid_Top")[0]
    assert (asset.x_cell, asset.y_cell, asset.width_cells, asset.height_cells) == (2, 3, 1, 1)
    assert (asset.output_width_px, asset.output_height_px) == (32, 32)


def test_v2_project_migrates_with_default_layer_visibility(tmp_path: Path) -> None:
    path = tmp_path / "v2.tilenamer.json"
    path.write_text(
        '{"format_version":2,"source_file":"x.aseprite","tile_size":32,'
        '"assignments":{"Solid_Top":[{"x_cell":0,"y_cell":0,'
        '"width_cells":2,"height_cells":1}]}}',
        encoding="utf-8",
    )
    restored = TileProject.load(path)
    assert restored.model.assets("Solid_Top")[0].width_cells == 2
    assert restored.layer_visibility == {}


def test_v3_project_migrates_with_default_grid_settings(tmp_path: Path) -> None:
    path = tmp_path / "v3.tilenamer.json"
    path.write_text(
        '{"format_version":3,"source_file":"x.aseprite","tile_size":32,'
        '"assignments":{},"layer_visibility":{"1":false}}', encoding="utf-8"
    )
    restored = TileProject.load(path)
    assert restored.grid_reference == GridReference()
    assert restored.layer_alignment_offsets == {}
    assert restored.alignment_correction_enabled


def test_v8_project_round_trips_grid_alignment_and_manual_origin_provenance(tmp_path: Path) -> None:
    original = TileProject(
        "x.aseprite", 32, AssignmentModel(), {"2": True},
        GridReference(32, 32, 2, -1, "layer", "2"),
        {"2": (-1, 0)}, True, [], {"2": (2, -1)}, {"2"},
    )
    path = tmp_path / "v4.tilenamer.json"
    original.save(path)
    assert "alignment_correction_enabled" not in path.read_text(encoding="utf-8")
    assert TileProject.load(path) == original


def test_v7_alignment_enable_state_migrates_to_active_offsets_or_zero(tmp_path: Path) -> None:
    active = tmp_path / "active.json"
    active.write_text(
        '{"format_version":7,"source_file":"x.aseprite","tile_size":32,'
        '"assignments":{},"layer_alignment_offsets":{"a":{"x":2,"y":-1}},'
        '"alignment_correction_enabled":true}', encoding="utf-8",
    )
    assert TileProject.load(active).layer_alignment_offsets == {"a": (2, -1)}
    dormant = tmp_path / "dormant.json"
    dormant.write_text(active.read_text(encoding="utf-8").replace("true", "false"), encoding="utf-8")
    assert TileProject.load(dormant).layer_alignment_offsets == {}


def test_v5_project_migrates_only_explicit_layer_grid_reference(tmp_path: Path) -> None:
    path = tmp_path / "v5.tilenamer.json"
    path.write_text(
        '{"format_version":5,"source_file":"x.aseprite","tile_size":32,'
        '"assignments":{},"grid_reference":{"cell_width":32,"cell_height":32,'
        '"origin_x":3,"origin_y":2,"mode":"layer","layer_identity":"b"},'
        '"layer_alignment_offsets":{"b":{"x":-1,"y":0}}}',
        encoding="utf-8",
    )
    restored = TileProject.load(path)
    assert restored.layer_grid_origins == {"b": (3, 2)}
    assert restored.layer_grid_manual_overrides == {"b"}
    assert restored.layer_alignment_offsets == {}


def test_project_rejects_duplicate_coordinate(tmp_path: Path) -> None:
    path = tmp_path / "bad.tilenamer.json"
    path.write_text(
        '{"format_version":1,"source_file":"x.png","tile_size":32,'
        '"assignments":{"A":[[0,0]],"B":[[0,0]]}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="중복"):
        TileProject.load(path)
