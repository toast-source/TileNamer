from __future__ import annotations

from dataclasses import dataclass

from .model import AssetAssignment


@dataclass(frozen=True)
class GridReference:
    """One shared pixel/cell transform used by canvas, thumbnails, and export."""

    cell_width: int = 32
    cell_height: int = 32
    origin_x: int = 0
    origin_y: int = 0
    mode: str = "image"
    layer_identity: str | None = None

    def __post_init__(self) -> None:
        if self.cell_width <= 0 or self.cell_height <= 0:
            raise ValueError("Grid cell 크기는 양수여야 합니다.")

    def pixel_rect(self, assignment: AssetAssignment) -> tuple[int, int, int, int]:
        left = self.origin_x + assignment.x_cell * self.cell_width
        top = self.origin_y + assignment.y_cell * self.cell_height
        return (
            left,
            top,
            left + assignment.width_cells * self.cell_width,
            top + assignment.height_cells * self.cell_height,
        )

    def cell_at(self, x: float, y: float, image_width: int, image_height: int) -> tuple[int, int] | None:
        column = int((x - self.origin_x) // self.cell_width)
        row = int((y - self.origin_y) // self.cell_height)
        if column < 0 or row < 0:
            return None
        probe = AssetAssignment("_grid", column, row)
        left, top, right, bottom = self.pixel_rect(probe)
        if left < 0 or top < 0 or right > image_width or bottom > image_height:
            return None
        return column, row

    def contains(self, assignment: AssetAssignment, image_width: int, image_height: int) -> bool:
        left, top, right, bottom = self.pixel_rect(assignment)
        return left >= 0 and top >= 0 and right <= image_width and bottom <= image_height

    def to_json(self) -> dict[str, object]:
        return {
            "cell_width": self.cell_width,
            "cell_height": self.cell_height,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "mode": self.mode,
            "layer_identity": self.layer_identity,
        }

    @classmethod
    def from_json(cls, payload: dict | None) -> "GridReference":
        if not payload:
            return cls()
        return cls(
            int(payload.get("cell_width", 32)),
            int(payload.get("cell_height", 32)),
            int(payload.get("origin_x", 0)),
            int(payload.get("origin_y", 0)),
            str(payload.get("mode", "image")),
            str(payload["layer_identity"]) if payload.get("layer_identity") else None,
        )
