from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CategoryRule:
    name: str
    prefix: str
    index_start: int = 0
    padding: int = 2
    subfolder: str = "TileImages"

    def filename(self, position: int) -> str:
        index = self.index_start + position
        return f"{self.prefix}_{index:0{self.padding}d}.png"


def load_categories(path: str | Path) -> list[CategoryRule]:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    categories = [CategoryRule(**item) for item in data["categories"]]
    validate_categories(categories)
    return categories


def validate_categories(categories: list[CategoryRule]) -> None:
    if not categories:
        raise ValueError("카테고리가 비어 있습니다.")
    names: set[str] = set()
    prefixes: set[str] = set()
    for rule in categories:
        if not rule.name or not rule.prefix:
            raise ValueError("카테고리 name/prefix는 비어 있을 수 없습니다.")
        if rule.name in names or rule.prefix in prefixes:
            raise ValueError(f"중복 카테고리: {rule.name}")
        if rule.index_start < 0 or rule.padding < 1:
            raise ValueError(f"잘못된 번호 규칙: {rule.name}")
        if Path(rule.subfolder).is_absolute() or ".." in Path(rule.subfolder).parts:
            raise ValueError(f"안전하지 않은 하위 폴더: {rule.subfolder}")
        names.add(rule.name)
        prefixes.add(rule.prefix)

