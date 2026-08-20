from pathlib import Path

import pytest

from tilenamer.config import CategoryRule, load_categories, validate_categories
from tilenamer.model import AssignmentModel
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
        AssignmentModel({"Platform_Center": [(3, 4), (1, 2)], "Solid_Bottom": [(0, 0)]}),
    )
    path = tmp_path / "work.tilenamer.json"
    original.save(path)
    restored = TileProject.load(path)
    assert restored.source_file == original.source_file
    assert restored.tile_size == 32
    assert restored.model.assignments == original.model.assignments


def test_project_rejects_duplicate_coordinate(tmp_path: Path) -> None:
    path = tmp_path / "bad.tilenamer.json"
    path.write_text(
        '{"format_version":1,"source_file":"x.png","tile_size":32,'
        '"assignments":{"A":[[0,0]],"B":[[0,0]]}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="중복"):
        TileProject.load(path)
