from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import CategoryRule
from .model import AssetAssignment, AssignmentModel, TileCoord


@dataclass(frozen=True)
class ExportItem:
    category: str
    assignment: AssetAssignment
    output_path: Path

    @property
    def coord(self) -> TileCoord:
        return self.assignment.origin


def build_export_plan(output_root: str | Path, model: AssignmentModel,
                      rules: list[CategoryRule], category: str | None = None) -> list[ExportItem]:
    root = Path(output_root)
    by_name = {rule.name: rule for rule in rules}
    selected = [category] if category else list(model.assignments)
    plan: list[ExportItem] = []
    seen: set[Path] = set()
    for name in selected:
        if name not in by_name:
            raise ValueError(f"설정에 없는 카테고리: {name}")
        if "TopSequence" in name and model.assets(name):
            raise ValueError("Top Sequence는 현재 버전에서 내보내기를 지원하지 않습니다.")
        rule = by_name[name]
        for position, assignment in enumerate(model.assets(name)):
            target = root / rule.subfolder / rule.filename(position)
            key = target.resolve()
            if key in seen:
                raise ValueError(f"생성 계획 내부 파일명 충돌: {target}")
            seen.add(key)
            plan.append(ExportItem(name, assignment, target))
    return plan


def find_existing_collisions(plan: list[ExportItem]) -> list[Path]:
    return [item.output_path for item in plan if item.output_path.exists()]


def export_tiles(source: Image.Image, plan: list[ExportItem], tile_size: int = 32,
                 overwrite: bool = False) -> list[Path]:
    if tile_size != 32:
        raise ValueError("이 버전의 셀 크기는 32×32여야 합니다.")
    collisions = find_existing_collisions(plan)
    if collisions and not overwrite:
        raise FileExistsError(str(collisions[0]))
    width, height = source.size
    written: list[Path] = []
    for item in plan:
        asset = item.assignment
        left, top = asset.x_cell * tile_size, asset.y_cell * tile_size
        right, bottom = left + int(asset.output_width_px), top + int(asset.output_height_px)
        if left < 0 or top < 0 or right > width or bottom > height:
            raise ValueError(f"이미지 범위를 벗어난 에셋: {asset.origin}")
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        source.crop((left, top, right, bottom)).save(item.output_path, format="PNG")
        written.append(item.output_path)
    return written
