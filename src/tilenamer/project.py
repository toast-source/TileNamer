from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .model import AssignmentModel
from .grid import GridReference


@dataclass
class TileProject:
    source_file: str
    tile_size: int
    model: AssignmentModel
    layer_visibility: dict[str, bool] = field(default_factory=dict)
    grid_reference: GridReference = field(default_factory=GridReference)
    layer_alignment_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)
    alignment_correction_enabled: bool = False
    temporary_tags: list[str] = field(default_factory=list)
    layer_grid_origins: dict[str, tuple[int, int]] = field(default_factory=dict)
    layer_grid_manual_overrides: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        # v8 removed the runtime enable switch; a non-zero offset is always active.
        self.alignment_correction_enabled = True

    def save(self, path: str | Path) -> None:
        payload = {
            "format_version": 8,
            "source_file": self.source_file,
            "tile_size": self.tile_size,
            "assignments": self.model.as_json(),
            "layer_visibility": self.layer_visibility,
            "grid_reference": self.grid_reference.to_json(),
            "layer_alignment_offsets": {
                identity: {"x": offset[0], "y": offset[1]}
                for identity, offset in self.layer_alignment_offsets.items()
            },
            "temporary_tags": self.temporary_tags,
            "layer_grid_origins": {
                identity: {"x": origin[0], "y": origin[1]}
                for identity, origin in self.layer_grid_origins.items()
            },
            "layer_grid_manual_overrides": sorted(self.layer_grid_manual_overrides),
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "TileProject":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        version = payload.get("format_version")
        if version not in (1, 2, 3, 4, 5, 6, 7, 8):
            raise ValueError("지원하지 않는 프로젝트 파일 버전입니다.")
        tile_size = int(payload["tile_size"])
        if tile_size <= 0:
            raise ValueError("tile_size는 양수여야 합니다.")
        visibility = {
            str(identity): bool(visible)
            for identity, visible in payload.get("layer_visibility", {}).items()
        }
        offsets = {
            str(identity): (int(value.get("x", 0)), int(value.get("y", 0)))
            for identity, value in payload.get("layer_alignment_offsets", {}).items()
            if isinstance(value, dict)
        }
        if version <= 7 and not bool(payload.get("alignment_correction_enabled", False)):
            # Disabled legacy offsets were dormant UI state. v8 has no enable switch,
            # so migrate them to the equivalent original-position state.
            offsets = {}
        grid_reference = GridReference.from_json(payload.get("grid_reference"))
        if (grid_reference.cell_width, grid_reference.cell_height) != (tile_size, tile_size):
            raise ValueError("Grid cell 크기는 프로젝트 tile_size와 같아야 합니다.")
        grid_origins = {
            str(identity): (int(value.get("x", 0)), int(value.get("y", 0)))
            for identity, value in payload.get("layer_grid_origins", {}).items()
            if isinstance(value, dict)
        }
        # v1-v5 had no per-layer grid-origin map. Preserve only an explicitly
        # selected/saved layer GridReference; never infer it from alignment offsets.
        if (version <= 5 and grid_reference.mode == "layer"
                and grid_reference.layer_identity and not grid_origins):
            grid_origins[grid_reference.layer_identity] = (
                grid_reference.origin_x, grid_reference.origin_y,
            )
        manual_overrides = {
            str(value) for value in payload.get("layer_grid_manual_overrides", [])
        }
        if version <= 6:
            # Older projects could only contain user-edited/final values and had no
            # provenance bit. Preserve them rather than replacing them on reload.
            manual_overrides.update(grid_origins)
        return cls(
            str(payload["source_file"]), tile_size,
            AssignmentModel.from_json(payload.get("assignments", {})), visibility,
            grid_reference, offsets,
            True,
            [str(value) for value in payload.get("temporary_tags", [])],
            grid_origins,
            manual_overrides,
        )
