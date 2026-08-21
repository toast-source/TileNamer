from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

TileCoord = tuple[int, int]


@dataclass(frozen=True)
class AssetAssignment:
    """One exported asset backed by an immutable set of 32 px grid cells."""

    category: str
    x_cell: int
    y_cell: int
    width_cells: int = 1
    height_cells: int = 1
    output_width_px: int | None = None
    output_height_px: int | None = None
    selected_cells: tuple[TileCoord, ...] | None = None

    def __post_init__(self) -> None:
        if self.selected_cells is None:
            if self.x_cell < 0 or self.y_cell < 0:
                raise ValueError("에셋 셀 좌표는 0 이상이어야 합니다.")
            if self.width_cells <= 0 or self.height_cells <= 0:
                raise ValueError("에셋 셀 크기는 양수여야 합니다.")
            cells = tuple(
                (x, y)
                for y in range(self.y_cell, self.y_cell + self.height_cells)
                for x in range(self.x_cell, self.x_cell + self.width_cells)
            )
        else:
            cells = tuple(sorted({(int(x), int(y)) for x, y in self.selected_cells}, key=lambda c: (c[1], c[0])))
            if not cells:
                raise ValueError("에셋은 셀을 하나 이상 포함해야 합니다.")
            if any(x < 0 or y < 0 for x, y in cells):
                raise ValueError("에셋 셀 좌표는 0 이상이어야 합니다.")
            left = min(x for x, _ in cells)
            top = min(y for _, y in cells)
            right = max(x for x, _ in cells)
            bottom = max(y for _, y in cells)
            object.__setattr__(self, "x_cell", left)
            object.__setattr__(self, "y_cell", top)
            object.__setattr__(self, "width_cells", right - left + 1)
            object.__setattr__(self, "height_cells", bottom - top + 1)
        object.__setattr__(self, "selected_cells", cells)
        width = self.width_cells * 32 if self.output_width_px is None else self.output_width_px
        height = self.height_cells * 32 if self.output_height_px is None else self.output_height_px
        if width != self.width_cells * 32 or height != self.height_cells * 32:
            raise ValueError("출력 크기는 32px 셀 경계에 맞아야 합니다.")
        object.__setattr__(self, "output_width_px", int(width))
        object.__setattr__(self, "output_height_px", int(height))

    @property
    def origin(self) -> TileCoord:
        return self.x_cell, self.y_cell

    def occupied_cells(self) -> set[TileCoord]:
        return set(self.selected_cells or ())

    @property
    def cell_count(self) -> int:
        return len(self.selected_cells or ())

    @property
    def is_rectangular(self) -> bool:
        return self.cell_count == self.width_cells * self.height_cells

    @classmethod
    def from_cells(cls, category: str, cells: Iterable[TileCoord]) -> "AssetAssignment":
        normalized = tuple(cells)
        return cls(category, 0, 0, selected_cells=normalized)

    def same_region(self, other: "AssetAssignment") -> bool:
        return self.occupied_cells() == other.occupied_cells()

    def to_json(self) -> dict[str, Any]:
        return {
            "x_cell": self.x_cell,
            "y_cell": self.y_cell,
            "width_cells": self.width_cells,
            "height_cells": self.height_cells,
            "output_width_px": int(self.output_width_px),
            "output_height_px": int(self.output_height_px),
            "selected_cells": [[x, y] for x, y in self.selected_cells or ()],
        }


@dataclass(frozen=True)
class AssignmentResult:
    status: str
    assignment: AssetAssignment
    conflict: AssetAssignment | None = None


def normalized_region(start: TileCoord, end: TileCoord) -> tuple[int, int, int, int]:
    """Return x, y, width, height for an inclusive two-cell drag."""

    left, right = sorted((start[0], end[0]))
    top, bottom = sorted((start[1], end[1]))
    return left, top, right - left + 1, bottom - top + 1


@dataclass
class AssignmentModel:
    """Ordered, exclusive cell-shape asset assignments."""

    assignments: dict[str, list[AssetAssignment | TileCoord | list[int]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: dict[str, list[AssetAssignment]] = {}
        occupied: set[TileCoord] = set()
        for category, values in self.assignments.items():
            normalized[category] = []
            for value in values:
                asset = self._coerce(category, value)
                overlap = occupied.intersection(asset.occupied_cells())
                if overlap:
                    raise ValueError(f"중복 assignment 셀: {sorted(overlap)[0]}")
                occupied.update(asset.occupied_cells())
                normalized[category].append(asset)
        self.assignments = normalized

    @staticmethod
    def _coerce(category: str, value: Any) -> AssetAssignment:
        if isinstance(value, AssetAssignment):
            if value.category == category:
                return value
            return AssetAssignment.from_cells(category, value.selected_cells or ())
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return AssetAssignment(category, int(value[0]), int(value[1]))
        if isinstance(value, dict):
            if "selected_cells" in value:
                return AssetAssignment.from_cells(category, value["selected_cells"])
            return AssetAssignment(
                category, int(value["x_cell"]), int(value["y_cell"]),
                int(value.get("width_cells", 1)), int(value.get("height_cells", 1)),
                int(value.get("output_width_px", value.get("width_cells", 1) * 32)),
                int(value.get("output_height_px", value.get("height_cells", 1) * 32)),
            )
        raise ValueError("assignment는 [x, y] 또는 에셋 객체여야 합니다.")

    def assets(self, category: str) -> list[AssetAssignment]:
        return list(self.assignments.get(category, []))

    def all_assets(self) -> Iterable[AssetAssignment]:
        for assets in self.assignments.values():
            yield from assets

    def tiles(self, category: str) -> list[TileCoord]:
        return [asset.origin for asset in self.assets(category)]

    def assignment_at(self, coord: TileCoord) -> AssetAssignment | None:
        return next((asset for asset in self.all_assets() if coord in asset.occupied_cells()), None)

    def category_for(self, coord: TileCoord) -> str | None:
        asset = self.assignment_at(coord)
        return asset.category if asset else None

    def overlapping(self, candidate: AssetAssignment) -> list[AssetAssignment]:
        cells = candidate.occupied_cells()
        return [asset for asset in self.all_assets() if cells.intersection(asset.occupied_cells())]

    def preview_conflict(self, candidate: AssetAssignment) -> AssetAssignment | None:
        overlaps = self.overlapping(candidate)
        if not overlaps or (len(overlaps) == 1 and overlaps[0].same_region(candidate)):
            return None
        return overlaps[0]

    def assign_region(self, category: str, x: int, y: int, width: int, height: int) -> AssignmentResult:
        candidate = AssetAssignment(category, x, y, width, height)
        return self.assign_cells(category, candidate.selected_cells or ())

    def assign_cells(self, category: str, cells: Iterable[TileCoord]) -> AssignmentResult:
        candidate = AssetAssignment.from_cells(category, cells)
        overlaps = self.overlapping(candidate)
        if overlaps:
            if len(overlaps) != 1 or not overlaps[0].same_region(candidate):
                return AssignmentResult("conflict", candidate, overlaps[0])
            current = overlaps[0]
            self._remove_asset(current)
            if current.category == category:
                return AssignmentResult("removed", current)
            self.assignments.setdefault(category, []).append(candidate)
            return AssignmentResult("moved", candidate)
        self.assignments.setdefault(category, []).append(candidate)
        return AssignmentResult("added", candidate)

    def toggle(self, category: str, coord: TileCoord) -> str:
        return self.assign_region(category, coord[0], coord[1], 1, 1).status

    def _remove_asset(self, asset: AssetAssignment) -> None:
        values = self.assignments[asset.category]
        values.remove(asset)
        if not values:
            del self.assignments[asset.category]

    def remove(self, category: str, index: int) -> TileCoord:
        asset = self.assignments[category].pop(index)
        if not self.assignments[category]:
            del self.assignments[category]
        return asset.origin

    def move(self, category: str, index: int, offset: int) -> int:
        assets = self.assignments.get(category, [])
        target = index + offset
        if index < 0 or index >= len(assets) or target < 0 or target >= len(assets):
            return index
        assets[index], assets[target] = assets[target], assets[index]
        return target

    def clear(self) -> None:
        self.assignments.clear()

    def as_json(self) -> dict[str, list[dict[str, Any]]]:
        return {category: [asset.to_json() for asset in assets]
                for category, assets in self.assignments.items()}

    @classmethod
    def from_json(cls, data: dict[str, list[Any]]) -> "AssignmentModel":
        return cls({category: list(values) for category, values in data.items()})
