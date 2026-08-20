from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import CategoryRule
from .model import AssignmentModel, TileCoord


@dataclass(frozen=True)
class ExportItem:
    category: str
    coord: TileCoord
    output_path: Path


def build_export_plan(
    output_root: str | Path,
    model: AssignmentModel,
    rules: list[CategoryRule],
    category: str | None = None,
) -> list[ExportItem]:
    root = Path(output_root)
    by_name = {rule.name: rule for rule in rules}
    selected = [category] if category else list(model.assignments)
    plan: list[ExportItem] = []
    seen: set[Path] = set()
    for name in selected:
        if name not in by_name:
            raise ValueError(f"설정에 없는 카테고리: {name}")
        rule = by_name[name]
        for position, coord in enumerate(model.assignments.get(name, [])):
            target = root / rule.subfolder / rule.filename(position)
            key = target.resolve()
            if key in seen:
                raise ValueError(f"생성 계획 내부 파일명 충돌: {target}")
            seen.add(key)
            plan.append(ExportItem(name, coord, target))
    return plan


def find_existing_collisions(plan: list[ExportItem]) -> list[Path]:
    return [item.output_path for item in plan if item.output_path.exists()]


def export_tiles(
    source: Image.Image,
    plan: list[ExportItem],
    tile_size: int = 32,
    overwrite: bool = False,
) -> list[Path]:
    if tile_size != 32:
        raise ValueError("내보내기 타일 크기는 항상 32×32여야 합니다.")
    collisions = find_existing_collisions(plan)
    if collisions and not overwrite:
        raise FileExistsError(str(collisions[0]))
    width, height = source.size
    written: list[Path] = []
    for item in plan:
        x, y = item.coord
        box = (x * tile_size, y * tile_size, (x + 1) * tile_size, (y + 1) * tile_size)
        if x < 0 or y < 0 or box[2] > width or box[3] > height:
            raise ValueError(f"이미지 범위를 벗어난 타일: {item.coord}")
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        source.crop(box).save(item.output_path, format="PNG")
        written.append(item.output_path)
    return written

