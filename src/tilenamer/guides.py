from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .model import AssetAssignment, AssignmentModel
from .placement import AssignmentIdentity, build_placement_result
from .resources import project_root


@dataclass(frozen=True)
class GuideAsset:
    category: str
    resource: str
    logical_width: int
    logical_height: int
    source_reference: str
    path: Path


@dataclass(frozen=True)
class GuidePlacement:
    asset: GuideAsset
    occupied_cells: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class GuideRegistry:
    assets: tuple[GuideAsset, ...] = ()

    def for_category(self, category: str) -> tuple[GuideAsset, ...]:
        return tuple(asset for asset in self.assets if asset.category == category)


def guide_resource_root(base: Path | None = None) -> Path:
    """Locate bundled guide resources without depending on the process CWD."""
    if base is not None:
        resolved = base.resolve()
        if (resolved / "manifest.json").is_file():
            return resolved
        return resolved / "guides"
    root = project_root()
    candidates = (
        root / "guides",  # PyInstaller datas target
        root / "src" / "tilenamer" / "assets" / "guides",  # source checkout
        Path(__file__).resolve().parent / "assets" / "guides",  # installed package
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])


def load_guide_registry(root: Path | None = None) -> GuideRegistry:
    """Load valid guide records; unavailable or malformed resources are optional."""
    guide_root = guide_resource_root(root)
    manifest_path = guide_root / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("format_version") != 1 or not isinstance(payload.get("guides"), list):
            return GuideRegistry()
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return GuideRegistry()

    assets: list[GuideAsset] = []
    for record in payload["guides"]:
        try:
            category = str(record["category"]).strip()
            resource = str(record["resource"]).replace("\\", "/").strip()
            width = int(record["logical_width"])
            height = int(record["logical_height"])
            source_reference = str(record.get("source_reference", ""))
            path = (guide_root / resource).resolve()
            path.relative_to(guide_root.resolve())
            if not category or not resource or not (1 <= width <= 4 and 1 <= height <= 4):
                continue
            if path.suffix.lower() != ".png" or not path.is_file():
                continue
        except (KeyError, TypeError, ValueError, OSError):
            continue
        assets.append(GuideAsset(
            category, resource, width, height, source_reference, path,
        ))
    return GuideRegistry(tuple(assets))


def _resolve_guide_assets(
    family: str, pattern_name: str, assets: tuple[GuideAsset, ...],
) -> tuple[GuidePlacement, ...]:
    assignments: dict[str, list[AssetAssignment]] = {}
    identity_to_asset: dict[AssignmentIdentity, GuideAsset] = {}
    cursor = 0
    for asset in assets:
        assignment = AssetAssignment(
            asset.category, cursor, 0, asset.logical_width, asset.logical_height,
        )
        assignments.setdefault(asset.category, []).append(assignment)
        identity_to_asset[AssignmentIdentity(
            asset.category, assignment.selected_cells or (),
        )] = asset
        cursor += asset.logical_width + 1
    try:
        result = build_placement_result(family, AssignmentModel(assignments), pattern_name)
    except ValueError:
        return ()

    placements: list[GuidePlacement] = []
    for cell in result.cells:
        identity = cell.assignment_identity
        asset = identity_to_asset.get(identity) if identity is not None else None
        # A guide is never substituted for a different semantic role. In
        # particular, omit production bridge/diagonal fallbacks when its exact
        # guide resource is unavailable.
        if asset is None or cell.fallback_for is not None:
            continue
        if family in {"Solid", "Wall", "Platform"}:
            expected_category = (
                "Platform_Center"
                if family == "Solid" and cell.role == "Center"
                else f"{family}_{cell.role}"
            )
            if asset.category != expected_category:
                continue
        placements.append(GuidePlacement(asset, cell.occupied_cells))
    return tuple(placements)


def build_guide_placements(
    family: str, pattern_name: str, registry: GuideRegistry,
) -> tuple[GuidePlacement, ...]:
    """Build an Actual-independent Guide plan through the production fit pipeline.

    Larger exact-role samples get the first opportunity to fit. Subsequent
    smaller size tiers fill only still-uncovered topology, allowing 2×1 Bridge
    strips and 1×1 corner cases to coexist without overlap or guessed roles.
    """
    by_category: dict[str, dict[int, list[GuideAsset]]] = {}
    for asset in registry.assets:
        area = asset.logical_width * asset.logical_height
        by_category.setdefault(asset.category, {}).setdefault(area, []).append(asset)
    size_tiers = {
        category: tuple(
            tuple(by_area[area]) for area in sorted(by_area, reverse=True)
        )
        for category, by_area in by_category.items()
    }
    tier_count = max((len(tiers) for tiers in size_tiers.values()), default=0)
    placements: list[GuidePlacement] = []
    covered: set[tuple[int, int]] = set()
    for tier_index in range(tier_count):
        tier_assets = tuple(
            asset
            for tiers in size_tiers.values()
            for asset in tiers[min(tier_index, len(tiers) - 1)]
        )
        for placement in _resolve_guide_assets(family, pattern_name, tier_assets):
            footprint = set(placement.occupied_cells)
            if footprint.intersection(covered):
                continue
            placements.append(placement)
            covered.update(footprint)
    return tuple(placements)
