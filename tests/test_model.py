from tilenamer.model import AssignmentModel


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

