from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from .model import AssetAssignment, AssignmentModel

LogicalCoord = tuple[int, int]
NO_FIT_CANDIDATE = "NO_FIT_CANDIDATE"
NORMAL = "NORMAL"
REFERENCE_FALLBACK = "REFERENCE_FALLBACK"
MISSING = "MISSING"
OPTIONAL_ABSENT = "OPTIONAL_ABSENT"
ALLOWED_FALLBACK = "ALLOWED_FALLBACK"
WARNING_FALLBACK = "WARNING_FALLBACK"


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
    required_category: str
    candidate_index: int | None
    assignment_identity: AssignmentIdentity | None
    filename: str | None
    occupied_cells: tuple[LogicalCoord, ...]
    width_cells: int = 1
    height_cells: int = 1
    fallback_for: str | None = None
    diagnostic: str | None = None
    resolution_state: str = NORMAL
    fallback_reason: str | None = None
    fallback_severity: str | None = None

    @property
    def required_role(self) -> str:
        return self.required_category

    @property
    def rendered_source_role(self) -> str | None:
        return self.category if self.assignment_identity is not None else None

    @property
    def guide_role(self) -> str:
        return self.required_category

    @property
    def is_reference_fallback(self) -> bool:
        return self.resolution_state == REFERENCE_FALLBACK

    @property
    def is_missing(self) -> bool:
        return self.resolution_state == MISSING

    @property
    def is_no_fit(self) -> bool:
        return self.resolution_state == NO_FIT_CANDIDATE

    @property
    def is_optional_absent(self) -> bool:
        return self.resolution_state == OPTIONAL_ABSENT

    @property
    def is_warning(self) -> bool:
        return self.is_missing or self.is_no_fit or self.is_reference_fallback

    def provenance_tuple(
        self, guide_resource: str | None = None,
    ) -> tuple[object, ...]:
        return (
            (self.x, self.y),
            self.required_role,
            self.resolution_state,
            self.rendered_source_role,
            self.filename,
            self.occupied_cells,
            self.fallback_reason,
            self.guide_role,
            guide_resource,
        )


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
    no_fit_counts: tuple[tuple[str, int], ...] = ()
    fallback_counts: tuple[tuple[str, str, int], ...] = ()
    optional_absent_counts: tuple[tuple[str, int], ...] = ()

    @property
    def missing_cell_count(self) -> int:
        return sum(count for _, count in self.missing_counts)

    @property
    def fallback_cell_count(self) -> int:
        return sum(count for _, _, count in self.fallback_counts)

    @property
    def optional_absent_cell_count(self) -> int:
        return sum(count for _, count in self.optional_absent_counts)

    @property
    def resolution_counts(self) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(Counter(cell.resolution_state for cell in self.cells).items()))


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


@dataclass(frozen=True)
class FallbackRule:
    source_category: str
    severity: str
    reason: str


REFERENCE_FALLBACK_RULES = {
    "Solid_LeftTopBridge": FallbackRule(
        "Solid_LeftTop", ALLOWED_FALLBACK, "Reference Bridge fallback",
    ),
    "Solid_RightTopBridge": FallbackRule(
        "Solid_RightTop", ALLOWED_FALLBACK, "Reference Bridge fallback",
    ),
    "Solid_LeftBridge": FallbackRule(
        "Solid_Left", ALLOWED_FALLBACK, "Reference Bridge fallback",
    ),
    "Solid_RightBridge": FallbackRule(
        "Solid_Right", ALLOWED_FALLBACK, "Reference Bridge fallback",
    ),
    "Solid_LeftBottomBridge": FallbackRule(
        "Solid_LeftBottom", ALLOWED_FALLBACK, "Reference Bridge fallback",
    ),
    "Solid_RightBottomBridge": FallbackRule(
        "Solid_RightBottom", ALLOWED_FALLBACK, "Reference Bridge fallback",
    ),
    "Wall_InnerSlash": FallbackRule(
        "Wall_Inner", WARNING_FALLBACK, "Reference Wall diagonal fallback",
    ),
    "Wall_InnerBackslash": FallbackRule(
        "Wall_Inner", WARNING_FALLBACK, "Reference Wall diagonal fallback",
    ),
}
BRIDGE_FALLBACKS = {
    category: rule.source_category
    for category, rule in REFERENCE_FALLBACK_RULES.items()
    if category.startswith("Solid_")
}
OPTIONAL_REFERENCE_ROLES = frozenset(BRIDGE_FALLBACKS)
WALL_DIAGONAL_FALLBACKS = {
    category: rule.source_category
    for category, rule in REFERENCE_FALLBACK_RULES.items()
    if category.startswith("Wall_")
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


def required_role_matrix(
    family: str, pattern_name: str,
) -> tuple[tuple[LogicalCoord, str], ...]:
    """Return topology-only canonical roles without consulting candidates."""
    if family in TOP_SEQUENCE_SPECS:
        sequence_index, height = TOP_SEQUENCE_SPECS[family]
        repeat_count = TOP_SEQUENCE_REPEAT_COUNTS[pattern_name]
        parts = ("Start",) + ("Repeat",) * repeat_count + ("End",)
        matrix: list[tuple[LogicalCoord, str]] = []
        x = 0
        for part in parts:
            width = 2 if part == "Repeat" else 1
            category = f"Solid_TopSequence_{part}_{sequence_index}"
            matrix.extend(
                ((x + dx, dy), category)
                for dy in range(height) for dx in range(width)
            )
            x += width
        return tuple(matrix)
    pattern = SAMPLE_PATTERNS[family][pattern_name]
    logical_roles = _resolve_logical_roles(family, _occupied(pattern))
    return tuple(sorted(
        ((position, role.category) for position, role in logical_roles.items()),
        key=lambda item: (item[0][1], item[0][0]),
    ))


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
    fallback_rule = REFERENCE_FALLBACK_RULES.get(semantic_category)
    if fallback_rule:
        fallback_candidates = assets_by_category.get(fallback_rule.source_category, ())
        if fallback_candidates:
            return fallback_rule.source_category, fallback_candidates
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
    reserved: set[LogicalCoord] | frozenset[LogicalCoord] = frozenset(),
) -> _PlacementProposal | None:
    options = _bridge_candidate_options(
        anchor, semantic, candidates, logical_roles, reserved,
    )
    selected = _select_candidate_option(semantic.category, anchor, options)
    if selected is None or selected.assignment.width_cells == 1:
        return None
    return _PlacementProposal(
        anchor, semantic, semantic.category, selected.index, selected.assignment,
        selected.footprint, selected.bounds,
    )


@dataclass(frozen=True)
class _CandidateOption:
    index: int
    assignment: AssetAssignment
    footprint: tuple[LogicalCoord, ...]
    bounds: tuple[LogicalCoord, ...]


def _select_candidate_option(
    category: str, anchor: LogicalCoord, options: Sequence[_CandidateOption],
) -> _CandidateOption | None:
    selected = deterministic_candidate_index(category, anchor[0], anchor[1], len(options))
    return options[selected] if selected is not None else None


def _ordinary_origin(
    anchor: LogicalCoord, role: str, width: int, height: int,
) -> LogicalCoord:
    x, y = anchor
    if role.startswith("Right") and not role.startswith("InnerRight"):
        x -= width - 1
    if role == "Bottom" or role.endswith("Bottom") or role.startswith("Inner"):
        y -= height - 1
    return x, y


def _expected_ordinary_role(
    role: str, offset: LogicalCoord, width: int, height: int,
) -> str:
    dx, dy = offset
    if role == "Inner":
        return "Inner"
    if role.startswith("Inner"):
        return role if (dx, dy) == (0, height - 1) else "Inner"

    expose_left = role.startswith("Left")
    expose_right = role.startswith("Right")
    expose_top = role == "Top" or role.endswith("Top")
    expose_bottom = role == "Bottom" or role.endswith("Bottom")
    horizontal = (
        "Left" if expose_left and dx == 0
        else "Right" if expose_right and dx == width - 1
        else ""
    )
    vertical = (
        "Top" if expose_top and dy == 0
        else "Bottom" if expose_bottom and dy == height - 1
        else ""
    )
    if horizontal and vertical:
        return horizontal + vertical
    return horizontal or vertical or "Inner"


def _ordinary_candidate_options(
    anchor: LogicalCoord,
    semantic: _LogicalRole,
    actual_category: str,
    candidates: Sequence[AssetAssignment],
    logical_roles: Mapping[LogicalCoord, _LogicalRole],
    reserved: set[LogicalCoord] | frozenset[LogicalCoord] = frozenset(),
) -> list[_CandidateOption]:
    options: list[_CandidateOption] = []
    direct = actual_category == semantic.category
    for index, candidate in enumerate(candidates):
        width, height = candidate.width_cells, candidate.height_cells
        if semantic.family == "Platform":
            if (width, height) != (1, 1):
                continue
        elif width > 4 or height > 4:
            continue
        if not direct and (width, height) != (1, 1):
            # Reference fallbacks substitute the missing single role; they are
            # not permission to stamp an unrelated multi-cell pattern.
            continue
        origin = _ordinary_origin(anchor, semantic.role, width, height)
        footprint, bounds = _placement_geometry(candidate, origin)
        if anchor not in footprint or any(position in reserved for position in footprint):
            continue
        if (width, height) == (1, 1):
            options.append(_CandidateOption(index, candidate, footprint, bounds))
            continue
        valid = True
        origin_x, origin_y = origin
        for position in footprint:
            logical = logical_roles.get(position)
            expected_role = _expected_ordinary_role(
                semantic.role,
                (position[0] - origin_x, position[1] - origin_y),
                width,
                height,
            )
            if logical is None or logical != _LogicalRole(semantic.family, expected_role):
                valid = False
                break
        if valid:
            options.append(_CandidateOption(index, candidate, footprint, bounds))
    return options


def _bridge_candidate_options(
    anchor: LogicalCoord,
    semantic: _LogicalRole,
    candidates: Sequence[AssetAssignment],
    logical_roles: Mapping[LogicalCoord, _LogicalRole],
    reserved: set[LogicalCoord] | frozenset[LogicalCoord] = frozenset(),
) -> list[_CandidateOption]:
    if semantic.category not in BRIDGE_FALLBACKS:
        return []
    x, y = anchor
    options: list[_CandidateOption] = []
    for index, candidate in enumerate(candidates):
        size = candidate.width_cells, candidate.height_cells
        if size == (1, 1):
            footprint, bounds = _placement_geometry(candidate, anchor)
            if anchor not in reserved:
                options.append(_CandidateOption(index, candidate, footprint, bounds))
            continue
        if size != (2, 1):
            continue
        origin = (x - 1, y) if semantic.role.startswith("Left") else (x, y)
        footprint, bounds = _placement_geometry(candidate, origin)
        if set(footprint) != set(bounds) or anchor not in footprint:
            continue
        if any(position in reserved for position in footprint):
            continue
        platform_position = (x - 1, y) if semantic.role.startswith("Left") else (x + 1, y)
        if (
            logical_roles.get(anchor) == semantic
            and logical_roles.get(platform_position) is not None
            and logical_roles[platform_position].family == "Platform"
        ):
            options.append(_CandidateOption(index, candidate, footprint, bounds))
    return options


def _ordinary_proposal(
    anchor: LogicalCoord,
    semantic: _LogicalRole,
    actual_category: str,
    candidates: Sequence[AssetAssignment],
    logical_roles: Mapping[LogicalCoord, _LogicalRole],
    reserved: set[LogicalCoord] | frozenset[LogicalCoord] = frozenset(),
) -> _PlacementProposal | None:
    options = _ordinary_candidate_options(
        anchor, semantic, actual_category, candidates, logical_roles, reserved,
    )
    selected = _select_candidate_option(actual_category, anchor, options)
    if selected is None or selected.assignment.width_cells * selected.assignment.height_cells == 1:
        return None
    return _PlacementProposal(
        anchor, semantic, actual_category, selected.index, selected.assignment,
        selected.footprint, selected.bounds,
        semantic.category if actual_category != semantic.category else None,
    )


def _make_cell(
    anchor: LogicalCoord, semantic: _LogicalRole, actual_category: str,
    candidate_index: int | None, assignment: AssetAssignment | None,
    footprint: tuple[LogicalCoord, ...], fallback_for: str | None = None,
    diagnostic: str | None = None,
    logical_size: tuple[int, int] | None = None,
    required_category: str | None = None,
) -> PlacementCell:
    x, y = anchor
    required_category = required_category or semantic.category
    identity = (
        AssignmentIdentity(actual_category, assignment.selected_cells or ())
        if assignment is not None else None
    )
    if diagnostic == NO_FIT_CANDIDATE:
        resolution_state = NO_FIT_CANDIDATE
    elif diagnostic == OPTIONAL_ABSENT:
        resolution_state = OPTIONAL_ABSENT
    elif assignment is None:
        resolution_state = MISSING
    elif actual_category != required_category:
        resolution_state = REFERENCE_FALLBACK
    else:
        resolution_state = NORMAL
    fallback_rule = (
        REFERENCE_FALLBACK_RULES.get(required_category)
        if resolution_state == REFERENCE_FALLBACK else None
    )
    fallback_for = required_category if fallback_rule is not None else None
    return PlacementCell(
        x=x,
        y=y,
        family=semantic.family,
        role=semantic.role,
        category=actual_category,
        required_category=required_category,
        candidate_index=candidate_index,
        assignment_identity=identity,
        filename=(f"{actual_category}_{candidate_index:02d}.png"
                  if candidate_index is not None else None),
        occupied_cells=footprint,
        width_cells=(assignment.width_cells if assignment is not None
                     else logical_size[0] if logical_size is not None else 1),
        height_cells=(assignment.height_cells if assignment is not None
                      else logical_size[1] if logical_size is not None else 1),
        fallback_for=fallback_for,
        diagnostic=diagnostic,
        resolution_state=resolution_state,
        fallback_reason=fallback_rule.reason if fallback_rule is not None else None,
        fallback_severity=fallback_rule.severity if fallback_rule is not None else None,
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
    no_fit: Counter[str] = Counter()
    cells: list[PlacementCell] = []
    logical_occupied: set[LogicalCoord] = set()
    x = 0
    height = reference_height
    for part in parts:
        category = f"Solid_TopSequence_{part}_{sequence_index}"
        candidates = assets_by_category.get(category, ())
        default_width = 2 if part == "Repeat" else 1
        eligible = [
            (index, candidate)
            for index, candidate in enumerate(candidates)
            if candidate.width_cells == default_width
            and candidate.height_cells == reference_height
            and candidate.width_cells <= 4
            and candidate.height_cells <= 4
        ]
        selected = deterministic_candidate_index(category, x, 0, len(eligible))
        candidate_index, assignment = eligible[selected] if selected is not None else (None, None)
        width_cells, height_cells = default_width, reference_height
        diagnostic = None
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
            if candidates:
                diagnostic = NO_FIT_CANDIDATE
                no_fit[category] += 1
                ready.add(category)
            else:
                missing[category] += 1
        logical_occupied.update(bounds)
        cells.append(_make_cell(
            (x, 0), _LogicalRole(family, part), category,
            candidate_index, assignment, footprint, diagnostic=diagnostic,
            logical_size=(width_cells, height_cells),
            required_category=category,
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
        no_fit_counts=tuple(sorted(no_fit.items())),
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
    no_fit: Counter[str] = Counter()
    fallback_counts: Counter[tuple[str, str]] = Counter()
    optional_absent: Counter[str] = Counter()
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
                position, semantic, actual_category, candidates, logical_roles,
            )
        if proposal is not None:
            proposals.append(proposal)
    proposals.sort(key=lambda proposal: (
        -proposal.priority,
        -len(proposal.footprint),
        -(zlib.crc32(
            f"{proposal.semantic.category}:{proposal.anchor[0]}:{proposal.anchor[1]}".encode(
                "utf-8"
            )
        ) & 0xFFFFFFFF),
        proposal.anchor[1],
        proposal.anchor[0],
    ))
    for proposal in proposals:
        if any(position in covered for position in proposal.footprint):
            continue
        covered.update(proposal.footprint)
        for position in proposal.footprint:
            if position == proposal.anchor and proposal.fallback_for is not None:
                if proposal.fallback_for not in OPTIONAL_REFERENCE_ROLES:
                    missing[proposal.fallback_for] += 1
                fallback_counts[(proposal.fallback_for, proposal.actual_category)] += 1
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
        fallback_for = semantic_category if category != semantic_category else None
        candidate_index = None
        placement_cells = ((x, y),)
        assignment: AssetAssignment | None = None
        diagnostic = None
        if category == semantic_category and semantic_category in BRIDGE_FALLBACKS:
            options = _bridge_candidate_options(
                (x, y), semantic, candidates, logical_roles, covered,
            )
        else:
            options = _ordinary_candidate_options(
                (x, y), semantic, category, candidates, logical_roles, covered,
            )
        selected = _select_candidate_option(category, (x, y), options)
        if selected is not None:
            candidate_index = selected.index
            assignment = selected.assignment
            placement_cells = selected.footprint
        if candidate_index is None:
            if candidates and category == semantic_category:
                diagnostic = NO_FIT_CANDIDATE
                no_fit[semantic_category] += 1
                ready.add(semantic_category)
            elif semantic_category in OPTIONAL_REFERENCE_ROLES:
                optional_absent[semantic_category] += 1
                diagnostic = OPTIONAL_ABSENT
            else:
                missing[semantic_category] += 1
        elif fallback_for is not None:
            if semantic_category not in OPTIONAL_REFERENCE_ROLES:
                missing[semantic_category] += 1
            fallback_counts[(semantic_category, category)] += 1
        else:
            ready.add(semantic_category)
            covered.update(placement_cells)
        cells.append(_make_cell(
            (x, y), semantic, category, candidate_index, assignment, placement_cells,
            fallback_for, diagnostic,
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
        no_fit_counts=tuple(sorted(no_fit.items())),
        fallback_counts=tuple(
            (required, source, count)
            for (required, source), count in sorted(fallback_counts.items())
        ),
        optional_absent_counts=tuple(sorted(optional_absent.items())),
    )
