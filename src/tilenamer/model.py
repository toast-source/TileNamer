from __future__ import annotations

from dataclasses import dataclass, field

TileCoord = tuple[int, int]


@dataclass
class AssignmentModel:
    """Ordered, exclusive category assignments for grid coordinates."""

    assignments: dict[str, list[TileCoord]] = field(default_factory=dict)

    def tiles(self, category: str) -> list[TileCoord]:
        return list(self.assignments.get(category, []))

    def category_for(self, coord: TileCoord) -> str | None:
        for category, coords in self.assignments.items():
            if coord in coords:
                return category
        return None

    def toggle(self, category: str, coord: TileCoord) -> str:
        current = self.category_for(coord)
        if current == category:
            self.assignments[current].remove(coord)
            if not self.assignments[current]:
                del self.assignments[current]
            return "removed"
        if current is not None:
            self.assignments[current].remove(coord)
            if not self.assignments[current]:
                del self.assignments[current]
        self.assignments.setdefault(category, []).append(coord)
        return "moved" if current is not None else "added"

    def remove(self, category: str, index: int) -> TileCoord:
        coord = self.assignments[category].pop(index)
        if not self.assignments[category]:
            del self.assignments[category]
        return coord

    def move(self, category: str, index: int, offset: int) -> int:
        coords = self.assignments.get(category, [])
        target = index + offset
        if index < 0 or index >= len(coords) or target < 0 or target >= len(coords):
            return index
        coords[index], coords[target] = coords[target], coords[index]
        return target

    def clear(self) -> None:
        self.assignments.clear()

    def as_json(self) -> dict[str, list[list[int]]]:
        return {key: [[x, y] for x, y in value] for key, value in self.assignments.items()}

    @classmethod
    def from_json(cls, data: dict[str, list[list[int]]]) -> "AssignmentModel":
        parsed: dict[str, list[TileCoord]] = {}
        occupied: set[TileCoord] = set()
        for category, coords in data.items():
            parsed[category] = []
            for raw in coords:
                if len(raw) != 2:
                    raise ValueError("타일 좌표는 [x, y] 형식이어야 합니다.")
                coord = (int(raw[0]), int(raw[1]))
                if coord in occupied:
                    raise ValueError(f"중복 assignment 좌표: {coord}")
                occupied.add(coord)
                parsed[category].append(coord)
        return cls(parsed)

