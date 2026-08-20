import pytest

from tilenamer.model import AssetAssignment, AssignmentModel, normalized_region


def test_click_order_and_same_category_toggle() -> None:
    model = AssignmentModel()
    model.toggle("Solid_Top", (2, 1))
    model.toggle("Solid_Top", (0, 0))
    assert model.tiles("Solid_Top") == [(2, 1), (0, 0)]
    assert model.toggle("Solid_Top", (2, 1)) == "removed"
    assert model.tiles("Solid_Top") == [(0, 0)]


def test_exclusive_move_between_categories() -> None:
    model = AssignmentModel()
    model.toggle("Solid_Top", (1, 1))
    assert model.toggle("Wall_Top", (1, 1)) == "moved"
    assert model.tiles("Solid_Top") == []
    assert model.tiles("Wall_Top") == [(1, 1)]


def test_reorder_and_remove() -> None:
    model = AssignmentModel({"Solid_Top": [(0, 0), (1, 0), (2, 0)]})
    assert model.move("Solid_Top", 2, -1) == 1
    assert model.tiles("Solid_Top") == [(0, 0), (2, 0), (1, 0)]
    assert model.remove("Solid_Top", 1) == (2, 0)


@pytest.mark.parametrize("size", [(1, 1), (2, 1), (1, 2), (2, 2)])
def test_general_asset_sizes_and_occupied_cells(size: tuple[int, int]) -> None:
    model = AssignmentModel()
    result = model.assign_region("Solid_Top", 2, 3, *size)
    assert result.status == "added"
    asset = model.assets("Solid_Top")[0]
    assert (asset.width_cells, asset.height_cells) == size
    assert (asset.output_width_px, asset.output_height_px) == (size[0] * 32, size[1] * 32)
    assert len(asset.occupied_cells()) == size[0] * size[1]


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [((1, 1), (3, 2), (1, 1, 3, 2)), ((3, 2), (1, 1), (1, 1, 3, 2)),
     ((1, 2), (3, 1), (1, 1, 3, 2)), ((3, 1), (1, 2), (1, 1, 3, 2))],
)
def test_drag_normalizes_in_all_directions(start, end, expected) -> None:
    assert normalized_region(start, end) == expected


def test_exact_region_toggles_or_moves_but_partial_overlap_is_rejected() -> None:
    model = AssignmentModel()
    assert model.assign_region("Solid_Top", 1, 1, 2, 2).status == "added"
    conflict = model.assign_region("Wall_Top", 2, 1, 2, 1)
    assert conflict.status == "conflict"
    assert conflict.conflict.category == "Solid_Top"
    assert model.tiles("Wall_Top") == []
    assert model.assign_region("Wall_Top", 1, 1, 2, 2).status == "moved"
    assert model.assets("Wall_Top")[0].width_cells == 2
    assert model.assign_region("Wall_Top", 1, 1, 2, 2).status == "removed"


def test_non_grid_aligned_output_is_rejected() -> None:
    with pytest.raises(ValueError, match="셀 경계"):
        AssetAssignment("Solid_Top", 0, 0, 2, 1, 63, 32)
