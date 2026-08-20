from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .model import AssignmentModel


@dataclass
class TileProject:
    source_file: str
    tile_size: int
    model: AssignmentModel

    def save(self, path: str | Path) -> None:
        payload = {
            "format_version": 2,
            "source_file": self.source_file,
            "tile_size": self.tile_size,
            "assignments": self.model.as_json(),
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "TileProject":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("format_version") not in (1, 2):
            raise ValueError("지원하지 않는 프로젝트 파일 버전입니다.")
        tile_size = int(payload["tile_size"])
        if tile_size <= 0:
            raise ValueError("tile_size는 양수여야 합니다.")
        return cls(str(payload["source_file"]), tile_size,
                   AssignmentModel.from_json(payload.get("assignments", {})))
