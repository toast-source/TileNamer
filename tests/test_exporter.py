from pathlib import Path

import pytest
from PIL import Image

from tilenamer.config import CategoryRule
from tilenamer.exporter import build_export_plan, export_tiles, find_existing_collisions
from tilenamer.model import AssetAssignment, AssignmentModel


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


@pytest.mark.parametrize("size", [(1, 1), (2, 1), (1, 2), (2, 2)])
def test_multicell_export_sizes(tmp_path: Path, size: tuple[int, int]) -> None:
    source = Image.new("RGBA", (128, 128), (12, 34, 56, 78))
    rule = CategoryRule("Solid_Top", "Solid_Top")
    asset = AssetAssignment("Solid_Top", 1, 1, *size)
    model = AssignmentModel({"Solid_Top": [asset]})
    written = export_tiles(source, build_export_plan(tmp_path, model, [rule]))
    with Image.open(written[0]) as result:
        assert result.size == (size[0] * 32, size[1] * 32)
        assert result.getpixel((0, 0)) == (12, 34, 56, 78)


def test_mixed_sizes_keep_candidate_order_and_names(tmp_path: Path) -> None:
    rule = CategoryRule("Solid_Top", "Solid_Top")
    model = AssignmentModel({"Solid_Top": [
        AssetAssignment("Solid_Top", 0, 0),
        AssetAssignment("Solid_Top", 1, 0, 2, 1),
        AssetAssignment("Solid_Top", 0, 2, 2, 2),
    ]})
    plan = build_export_plan(tmp_path, model, [rule])
    assert [item.output_path.name for item in plan] == [
        "Solid_Top_00.png", "Solid_Top_01.png", "Solid_Top_02.png"
    ]
    assert [(item.assignment.output_width_px, item.assignment.output_height_px) for item in plan] == [
        (32, 32), (64, 32), (64, 64)
    ]


def test_top_sequence_export_is_explicitly_rejected(tmp_path: Path) -> None:
    name = "Solid_TopSequence_Start_00"
    with pytest.raises(ValueError, match="Top Sequence"):
        build_export_plan(tmp_path, AssignmentModel({name: [(0, 0)]}), [CategoryRule(name, name)])
