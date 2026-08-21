from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import CategoryRule
from .grid import GridReference
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


def extract_assignment_image(
    source: Image.Image, assignment: AssetAssignment, grid: GridReference,
) -> Image.Image:
    """Extract one rectangle or sparse cell mask with transparent holes."""
    left, top, right, bottom = grid.pixel_rect(assignment)
    if left < 0 or top < 0 or right > source.width or bottom > source.height:
        raise ValueError(f"이미지 범위를 벗어난 에셋: {assignment.origin}")
    if assignment.is_rectangular:
        return source.crop((left, top, right, bottom)).convert("RGBA")
    output = Image.new("RGBA", (assignment.output_width_px, assignment.output_height_px))
    for x_cell, y_cell in assignment.selected_cells or ():
        cell = AssetAssignment("_cell", x_cell, y_cell)
        cell_box = grid.pixel_rect(cell)
        relative = (
            (x_cell - assignment.x_cell) * grid.cell_width,
            (y_cell - assignment.y_cell) * grid.cell_height,
        )
        output.alpha_composite(source.crop(cell_box).convert("RGBA"), relative)
    return output


def export_tiles(source: Image.Image, plan: list[ExportItem], tile_size: int = 32,
                 overwrite: bool = False, grid: GridReference | None = None) -> list[Path]:
    if tile_size != 32:
        raise ValueError("이 버전의 셀 크기는 32×32여야 합니다.")
    collisions = find_existing_collisions(plan)
    if collisions and not overwrite:
        raise FileExistsError(str(collisions[0]))
    reference = grid or GridReference(tile_size, tile_size)
    written: list[Path] = []
    for item in plan:
        asset = item.assignment
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        extract_assignment_image(source, asset, reference).save(item.output_path, format="PNG")
        written.append(item.output_path)
    return written
