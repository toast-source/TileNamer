from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from .model import AssetAssignment, AssignmentModel

LogicalCoord = tuple[int, int]


SAMPLE_PATTERNS: dict[str, dict[str, tuple[str, ...]]] = {
    "Platform": {
        "종합": ("#####",),
        "단일": ("#",),
        "짧은 연결": ("##",),
    },
    "Solid": {
        "종합": (
            "###################",
            "##...##############",
            "###########.#######",
            "##...##############",
            "###################",
        ),
        "외곽": (
            "#########",
            "#.......#",
            "#.......#",
            "#########",
        ),
        "내부 코너": (
            "#########",
            "##.###.##",
            "#########",
            "#.#####.#",
            "#########",
        ),
        "브릿지": (
            "##########",
            "##......##",
            "##########",
            "##......##",
            "##########",
        ),
    },
    "Wall": {
        "종합": (
            "###########.",
            "############",
            "##.######.##",
            "############",
            "###########.",
        ),
        "외곽": (
            "#######",
            "#.....#",
            "#######",
        ),
        "대각선": (
            "###.",
            "####",
            "#.##",
            "####",
            "###.",
        ),
    },
    "Top Sequence 00": {
        "최소 시퀀스": (),
        "긴 시퀀스": (),
    },
    "Top Sequence 01": {
        "최소 시퀀스": (),
        "긴 시퀀스": (),
    },
}


TOP_SEQUENCE_SPECS = {
    # Chapter2 manifest: requireRepeat=true. The observed layouts are
    # Start 1×H, Repeat 2×H and End 1×H for both independent sequence sets.
    "Top Sequence 00": ("00", 3),
    "Top Sequence 01": ("01", 2),
}
TOP_SEQUENCE_REPEAT_COUNTS = {
    "최소 시퀀스": 1,
    "긴 시퀀스": 3,
}


@dataclass(frozen=True)
class AssignmentIdentity:
    category: str
    selected_cells: tuple[LogicalCoord, ...]


@dataclass(frozen=True)
class PlacementCell:
    x: int
    y: int
    family: str
    role: str
    category: str
    candidate_index: int | None
    assignment_identity: AssignmentIdentity | None
    filename: str | None
    occupied_cells: tuple[LogicalCoord, ...]
    width_cells: int = 1
    height_cells: int = 1
    fallback_for: str | None = None

    @property
    def is_missing(self) -> bool:
        return self.candidate_index is None

    @property
    def is_warning(self) -> bool:
        return self.is_missing or self.fallback_for is not None


@dataclass(frozen=True)
class PlacementResult:
    family: str
    pattern_name: str
    width: int
    height: int
    cells: tuple[PlacementCell, ...]
    required_roles: frozenset[str]
    ready_roles: frozenset[str]
    missing_counts: tuple[tuple[str, int], ...]
    logical_occupied: frozenset[LogicalCoord] = frozenset()

    @property
    def missing_cell_count(self) -> int:
        return sum(count for _, count in self.missing_counts)


def _occupied(pattern: Sequence[str]) -> set[LogicalCoord]:
    return {
        (x, y)
        for y, row in enumerate(pattern)
        for x, marker in enumerate(row)
        if marker == "#"
    }


@dataclass(frozen=True)
class TerrainConnectionState:
    top: bool
    left: bool
    right: bool
    bottom: bool
    left_top: bool
    right_top: bool
    left_bottom: bool
    right_bottom: bool
    left_bridge: bool = False
    right_bridge: bool = False


@dataclass(frozen=True)
class _LogicalRole:
    family: str
    role: str

    @property
    def category(self) -> str:
        return f"{self.family}_{self.role}"


BRIDGE_FALLBACKS = {
    "Solid_LeftTopBridge": "Solid_LeftTop",
    "Solid_RightTopBridge": "Solid_RightTop",
    "Solid_LeftBridge": "Solid_Left",
    "Solid_RightBridge": "Solid_Right",
    "Solid_LeftBottomBridge": "Solid_LeftBottom",
    "Solid_RightBottomBridge": "Solid_RightBottom",
}
WALL_DIAGONAL_FALLBACKS = {
    "Wall_InnerSlash": "Wall_Inner",
    "Wall_InnerBackslash": "Wall_Inner",
}


def resolve_platform_connections(
    has_left: bool, has_right: bool,
    has_left_bridge: bool = False, has_right_bridge: bool = False,
) -> str:
    """Resolve a Platform role, including Solid anchors at either end."""

    if not has_left and has_right and not has_left_bridge:
        return "LeftEnd"
    if has_left and not has_right and not has_right_bridge:
        return "RightEnd"
    return "Center"


def resolve_platform_role(occupied: set[LogicalCoord], position: LogicalCoord) -> str:
    x, y = position
    has_left = (x - 1, y) in occupied
    has_right = (x + 1, y) in occupied
    return resolve_platform_connections(has_left, has_right)


def resolve_terrain_connections(state: TerrainConnectionState, family: str) -> str:
    """Resolve the reference editor's local Terrain branch order."""

    if not state.top:
        if not state.left:
            return "LeftTopBridge" if state.left_bridge else "LeftTop"
        if not state.right:
            return "RightTopBridge" if state.right_bridge else "RightTop"
        return "Top"
    if not state.bottom:
        if not state.left:
            return "LeftBottomBridge" if state.left_bridge else "LeftBottom"
        if not state.right:
            return "RightBottomBridge" if state.right_bridge else "RightBottom"
        return "Bottom"
    if not state.left:
        return "LeftBridge" if state.left_bridge else "Left"
    if not state.right:
        return "RightBridge" if state.right_bridge else "Right"

    if (state.left_top and state.right_top
            and state.left_bottom and state.right_bottom):
        return "Inner"
    if family == "Wall":
        if (not state.left_top and state.right_top
                and state.left_bottom and not state.right_bottom):
            return "InnerSlash"
        if (state.left_top and not state.right_top
                and not state.left_bottom and state.right_bottom):
            return "InnerBackslash"
    if not state.left_top:
        return "InnerRightBottom"
    if not state.right_top:
        return "InnerLeftBottom"
    if not state.left_bottom:
        return "InnerRightTop"
    if not state.right_bottom:
        return "InnerLeftTop"
    return "Inner"


def resolve_terrain_role(
    occupied: set[LogicalCoord], position: LogicalCoord, family: str,
) -> str:
    """Resolve a conventional 8-neighbor Terrain role without delegation."""

    x, y = position
    top = (x, y - 1) in occupied
    left = (x - 1, y) in occupied
    right = (x + 1, y) in occupied
    bottom = (x, y + 1) in occupied

    left_top = (x - 1, y - 1) in occupied
    right_top = (x + 1, y - 1) in occupied
    left_bottom = (x - 1, y + 1) in occupied
    right_bottom = (x + 1, y + 1) in occupied

    return resolve_terrain_connections(TerrainConnectionState(
        top, left, right, bottom, left_top, right_top, left_bottom, right_bottom,
    ), family)


def _resolve_logical_roles(
    family: str, occupied: set[LogicalCoord],
) -> dict[LogicalCoord, _LogicalRole]:
    if family != "Solid":
        return {
            position: _LogicalRole(
                family,
                resolve_platform_role(occupied, position)
                if family == "Platform"
                else resolve_terrain_role(occupied, position, family),
            )
            for position in occupied
        }

    platform_cells = {
        (x, y) for x, y in occupied
        if (x, y - 1) not in occupied and (x, y + 1) not in occupied
    }
    resolved: dict[LogicalCoord, _LogicalRole] = {}
    for x, y in occupied:
        if (x, y) in platform_cells:
            left, right = (x - 1, y), (x + 1, y)
            role = resolve_platform_connections(
                left in platform_cells,
                right in platform_cells,
                left in occupied and left not in platform_cells,
                right in occupied and right not in platform_cells,
            )
            resolved[(x, y)] = _LogicalRole("Platform", role)
            continue
        left_position, right_position = (x - 1, y), (x + 1, y)
        left_bridge = left_position in platform_cells
        right_bridge = right_position in platform_cells
        state = TerrainConnectionState(
            top=(x, y - 1) in occupied,
            left=left_position in occupied and not left_bridge,
            right=right_position in occupied and not right_bridge,
            bottom=(x, y + 1) in occupied,
            left_top=(x - 1, y - 1) in occupied,
            right_top=(x + 1, y - 1) in occupied,
            left_bottom=(x - 1, y + 1) in occupied,
            right_bottom=(x + 1, y + 1) in occupied,
            left_bridge=left_bridge,
            right_bridge=right_bridge,
        )
        resolved[(x, y)] = _LogicalRole(
            "Solid", resolve_terrain_connections(state, "Solid"),
        )
    return resolved


def deterministic_candidate_index(category: str, x: int, y: int, count: int) -> int | None:
    if count <= 0:
        return None
    key = f"{category}:{x}:{y}".encode("utf-8")
    return (zlib.crc32(key) & 0xFFFFFFFF) % count


def available_patterns(family: str) -> tuple[str, ...]:
    return tuple(SAMPLE_PATTERNS.get(family, {}))


def available_families() -> tuple[str, ...]:
    return ("Solid", "Wall", "Platform", "Top Sequence 00", "Top Sequence 01")


def _relative_footprint(assignment: AssetAssignment) -> tuple[LogicalCoord, ...]:
    cells = assignment.selected_cells or ((assignment.x_cell, assignment.y_cell),)
    left = min(x for x, _ in cells)
    top = min(y for _, y in cells)
    return tuple((x - left, y - top) for x, y in cells)


def _candidate_pool(
    semantic_category: str,
    assets_by_category: Mapping[str, Sequence[AssetAssignment]],
) -> tuple[str, Sequence[AssetAssignment]]:
    candidates = assets_by_category.get(semantic_category, ())
    if candidates:
        return semantic_category, candidates
    fallback = BRIDGE_FALLBACKS.get(semantic_category) or WALL_DIAGONAL_FALLBACKS.get(
        semantic_category
    )
    if fallback:
        fallback_candidates = assets_by_category.get(fallback, ())
        if fallback_candidates:
            return fallback, fallback_candidates
    return semantic_category, ()


def _placement_geometry(
    assignment: AssetAssignment, origin: LogicalCoord,
) -> tuple[tuple[LogicalCoord, ...], tuple[LogicalCoord, ...]]:
    x, y = origin
    footprint = tuple(
        (x + offset_x, y + offset_y)
        for offset_x, offset_y in _relative_footprint(assignment)
    )
    bounds = tuple(
        (x + offset_x, y + offset_y)
        for offset_y in range(assignment.height_cells)
        for offset_x in range(assignment.width_cells)
    )
    return footprint, bounds


def _placement_priority(category: str) -> int:
    role = category.split("_", 1)[-1]
    if role in {
        "LeftTopBridge", "RightTopBridge", "LeftBottomBridge", "RightBottomBridge",
    }:
        return 3
    if role in {"LeftTop", "RightTop", "LeftBottom", "RightBottom"}:
        return 2
    if role in {"LeftBridge", "RightBridge"}:
        return 1
    return 0


@dataclass(frozen=True)
class _PlacementProposal:
    anchor: LogicalCoord
    semantic: _LogicalRole
    actual_category: str
    candidate_index: int
    assignment: AssetAssignment
    footprint: tuple[LogicalCoord, ...]
    bounds: tuple[LogicalCoord, ...]
    fallback_for: str | None = None

    @property
    def priority(self) -> int:
        return _placement_priority(self.actual_category)


def _bridge_proposal(
    anchor: LogicalCoord,
    semantic: _LogicalRole,
    candidates: Sequence[AssetAssignment],
    logical_roles: Mapping[LogicalCoord, _LogicalRole],
) -> _PlacementProposal | None:
    if semantic.category not in BRIDGE_FALLBACKS or not candidates:
        return None
    x, y = anchor
    start = deterministic_candidate_index(semantic.category, x, y, len(candidates))
    if start is None:
        return None
    for offset in range(len(candidates)):
        index = (start + offset) % len(candidates)
        candidate = candidates[index]
        if candidate.width_cells == 1 and candidate.height_cells == 1:
            return None
        if candidate.height_cells != 1:
            continue
        origin_x = x - candidate.width_cells + 1 if semantic.role.startswith("Left") else x
        footprint, bounds = _placement_geometry(candidate, (origin_x, y))
        anchor_column = max(px for px, _ in bounds) if semantic.role.startswith("Left") else min(
            px for px, _ in bounds
        )
        valid = True
        for position in bounds:
            logical = logical_roles.get(position)
            if position[0] == anchor_column:
                valid = valid and position == anchor and logical == semantic
            else:
                valid = valid and logical is not None and logical.family == "Platform"
        if valid and anchor in footprint:
            return _PlacementProposal(
                anchor, semantic, semantic.category, index, candidate, footprint, bounds,
            )
    return None


def _ordinary_proposal(
    anchor: LogicalCoord,
    semantic: _LogicalRole,
    actual_category: str,
    candidates: Sequence[AssetAssignment],
    occupied: set[LogicalCoord],
) -> _PlacementProposal | None:
    if not candidates:
        return None
    x, y = anchor
    start = deterministic_candidate_index(actual_category, x, y, len(candidates))
    if start is None:
        return None
    for offset in range(len(candidates)):
        index = (start + offset) % len(candidates)
        candidate = candidates[index]
        if candidate.width_cells == 1 and candidate.height_cells == 1:
            return None
        footprint, bounds = _placement_geometry(candidate, anchor)
        if all(position in occupied for position in bounds):
            return _PlacementProposal(
                anchor, semantic, actual_category, index, candidate, footprint, bounds,
                semantic.category if semantic.category in WALL_DIAGONAL_FALLBACKS else None,
            )
    return None


def _make_cell(
    anchor: LogicalCoord, semantic: _LogicalRole, actual_category: str,
    candidate_index: int | None, assignment: AssetAssignment | None,
    footprint: tuple[LogicalCoord, ...], fallback_for: str | None = None,
) -> PlacementCell:
    x, y = anchor
    identity = (
        AssignmentIdentity(actual_category, assignment.selected_cells or ())
        if assignment is not None else None
    )
    return PlacementCell(
        x=x,
        y=y,
        family=semantic.family,
        role=semantic.role,
        category=actual_category,
        candidate_index=candidate_index,
        assignment_identity=identity,
        filename=(f"{actual_category}_{candidate_index:02d}.png"
                  if candidate_index is not None else None),
        occupied_cells=footprint,
        width_cells=assignment.width_cells if assignment is not None else 1,
        height_cells=assignment.height_cells if assignment is not None else 1,
        fallback_for=fallback_for,
    )


def _build_top_sequence_result(
    family: str,
    assets_by_category: Mapping[str, Sequence[AssetAssignment]],
    pattern_name: str,
) -> PlacementResult:
    sequence_index, reference_height = TOP_SEQUENCE_SPECS[family]
    if pattern_name not in TOP_SEQUENCE_REPEAT_COUNTS:
        raise ValueError(f"지원하지 않는 배치 패턴입니다: {pattern_name}")
    repeat_count = TOP_SEQUENCE_REPEAT_COUNTS[pattern_name]
    parts = ("Start",) + ("Repeat",) * repeat_count + ("End",)
    required = {
        f"Solid_TopSequence_{part}_{sequence_index}"
        for part in ("Start", "Repeat", "End")
    }
    ready: set[str] = set()
    missing: Counter[str] = Counter()
    cells: list[PlacementCell] = []
    logical_occupied: set[LogicalCoord] = set()
    x = 0
    height = reference_height
    for part in parts:
        category = f"Solid_TopSequence_{part}_{sequence_index}"
        candidates = assets_by_category.get(category, ())
        candidate_index = deterministic_candidate_index(category, x, 0, len(candidates))
        assignment = candidates[candidate_index] if candidate_index is not None else None
        default_width = 2 if part == "Repeat" else 1
        width_cells = assignment.width_cells if assignment is not None else default_width
        height_cells = assignment.height_cells if assignment is not None else reference_height
        if assignment is not None:
            footprint, bounds = _placement_geometry(assignment, (x, 0))
            ready.add(category)
        else:
            bounds = tuple(
                (x + offset_x, offset_y)
                for offset_y in range(height_cells)
                for offset_x in range(width_cells)
            )
            footprint = bounds
            missing[category] += 1
        logical_occupied.update(bounds)
        cells.append(_make_cell(
            (x, 0), _LogicalRole(family, part), category,
            candidate_index, assignment, footprint,
        ))
        x += width_cells
        height = max(height, height_cells)
    return PlacementResult(
        family=family,
        pattern_name=pattern_name,
        width=x,
        height=height,
        cells=tuple(cells),
        required_roles=frozenset(required),
        ready_roles=frozenset(ready),
        missing_counts=tuple(sorted(missing.items())),
        logical_occupied=frozenset(logical_occupied),
    )
def build_placement_result(
    family: str,
    assignments: AssignmentModel | Mapping[str, Sequence[AssetAssignment]],
    pattern_name: str = "종합",
) -> PlacementResult:
    if family not in SAMPLE_PATTERNS:
        raise ValueError(f"지원하지 않는 배치 미리보기 세트입니다: {family}")
    if family in TOP_SEQUENCE_SPECS:
        if pattern_name == "종합":
            pattern_name = "최소 시퀀스"
        assets_by_category = (
            assignments.assignments if isinstance(assignments, AssignmentModel) else assignments
        )
        return _build_top_sequence_result(family, assets_by_category, pattern_name)
    patterns = SAMPLE_PATTERNS[family]
    if pattern_name not in patterns:
        raise ValueError(f"지원하지 않는 배치 패턴입니다: {pattern_name}")
    pattern = patterns[pattern_name]
    occupied = _occupied(pattern)
    assets_by_category = (
        assignments.assignments if isinstance(assignments, AssignmentModel) else assignments
    )
    logical_roles = _resolve_logical_roles(family, occupied)
    cells: list[PlacementCell] = []
    missing: Counter[str] = Counter()
    required = {logical.category for logical in logical_roles.values()}
    ready: set[str] = set()
    covered: set[LogicalCoord] = set()

    # Resolve competing multi-cell placements globally using the reference role
    # priority before falling back to coordinate-order single-cell placement.
    proposals: list[_PlacementProposal] = []
    for position, semantic in logical_roles.items():
        actual_category, candidates = _candidate_pool(semantic.category, assets_by_category)
        if actual_category == semantic.category and semantic.category in BRIDGE_FALLBACKS:
            proposal = _bridge_proposal(position, semantic, candidates, logical_roles)
        else:
            proposal = _ordinary_proposal(
                position, semantic, actual_category, candidates, occupied,
            )
        if proposal is not None:
            proposals.append(proposal)
    proposals.sort(key=lambda proposal: (
        -proposal.priority,
        -len(proposal.bounds),
        -(zlib.crc32(
            f"{proposal.semantic.category}:{proposal.anchor[0]}:{proposal.anchor[1]}".encode(
                "utf-8"
            )
        ) & 0xFFFFFFFF),
        proposal.anchor[1],
        proposal.anchor[0],
    ))
    for proposal in proposals:
        if any(position in covered for position in proposal.bounds):
            continue
        covered.update(proposal.bounds)
        for position in proposal.bounds:
            if position == proposal.anchor and proposal.fallback_for is not None:
                missing[proposal.fallback_for] += 1
            else:
                ready.add(logical_roles[position].category)
        cells.append(_make_cell(
            proposal.anchor,
            proposal.semantic,
            proposal.actual_category,
            proposal.candidate_index,
            proposal.assignment,
            proposal.footprint,
            proposal.fallback_for,
        ))

    for x, y in sorted(occupied, key=lambda value: (value[1], value[0])):
        if (x, y) in covered:
            continue
        semantic = logical_roles[(x, y)]
        semantic_category = semantic.category
        category, candidates = _candidate_pool(semantic_category, assets_by_category)
        fallback_for = (
            semantic_category
            if category != semantic_category and semantic_category in WALL_DIAGONAL_FALLBACKS
            else None
        )
        candidate_index = None
        placement_cells = ((x, y),)
        assignment: AssetAssignment | None = None
        start = deterministic_candidate_index(category, x, y, len(candidates))
        if start is not None:
            for candidate_offset in range(len(candidates)):
                index = (start + candidate_offset) % len(candidates)
                candidate = candidates[index]
                # Direct multi-cell Bridge candidates were handled by the
                # priority pass above; do not place them with a top-left anchor.
                if category == semantic_category and semantic_category in BRIDGE_FALLBACKS \
                        and (candidate.width_cells > 1 or candidate.height_cells > 1):
                    continue
                footprint, bounds = _placement_geometry(candidate, (x, y))
                if all(cell in occupied and cell not in covered for cell in bounds):
                    candidate_index = index
                    assignment = candidate
                    placement_cells = footprint
                    break
        if candidate_index is None:
            missing[semantic_category] += 1
        elif fallback_for is not None:
            missing[semantic_category] += 1
        else:
            ready.add(semantic_category)
            covered.update(
                (x + offset_x, y + offset_y)
                for offset_y in range(assignment.height_cells)
                for offset_x in range(assignment.width_cells)
            )
        cells.append(_make_cell(
            (x, y), semantic, category, candidate_index, assignment, placement_cells,
            fallback_for,
        ))
    return PlacementResult(
        family=family,
        pattern_name=pattern_name,
        width=max((len(row) for row in pattern), default=0),
        height=len(pattern),
        cells=tuple(cells),
        required_roles=frozenset(required),
        ready_roles=frozenset(ready),
        missing_counts=tuple(sorted(missing.items())),
        logical_occupied=frozenset(occupied),
    )
