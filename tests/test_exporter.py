from pathlib import Path

import pytest
from PIL import Image

from tilenamer.config import CategoryRule
from tilenamer.exporter import build_export_plan, export_tiles, find_existing_collisions
from tilenamer.model import AssignmentModel


def test_numbering_padding_and_subfolder(tmp_path: Path) -> None:
    rule = CategoryRule("Platform_Center", "Platform_Center", 0, 2, "TileImages")
    model = AssignmentModel({"Platform_Center": [(0, 0), (1, 0)]})
    plan = build_export_plan(tmp_path, model, [rule])
    assert [item.output_path.name for item in plan] == ["Platform_Center_00.png", "Platform_Center_01.png"]
    assert all(item.output_path.parent.name == "TileImages" for item in plan)


def test_crop_is_exact_and_alpha_preserved(tmp_path: Path) -> None:
    source = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    for x in range(32, 64):
        for y in range(32):
            source.putpixel((x, y), (10, 20, 30, 77))
    rule = CategoryRule("Solid_Top", "Solid_Top")
    plan = build_export_plan(tmp_path, AssignmentModel({"Solid_Top": [(1, 0)]}), [rule])
    written = export_tiles(source, plan)
    with Image.open(written[0]) as tile:
        assert tile.size == (32, 32)
        assert tile.mode == "RGBA"
        assert tile.getpixel((0, 0)) == (10, 20, 30, 77)


def test_collision_detection_and_overwrite_guard(tmp_path: Path) -> None:
    rule = CategoryRule("Wall_Top", "Wall_Top")
    plan = build_export_plan(tmp_path, AssignmentModel({"Wall_Top": [(0, 0)]}), [rule])
    plan[0].output_path.parent.mkdir()
    plan[0].output_path.write_bytes(b"existing")
    assert find_existing_collisions(plan) == [plan[0].output_path]
    with pytest.raises(FileExistsError):
        export_tiles(Image.new("RGBA", (32, 32)), plan)


def test_unknown_category_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="설정에 없는"):
        build_export_plan(tmp_path, AssignmentModel({"Unknown": [(0, 0)]}), [])

