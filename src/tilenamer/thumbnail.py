from __future__ import annotations

from PIL import Image

from .grid import GridReference
from .model import AssetAssignment
from .exporter import extract_assignment_image


def build_assignment_thumbnail(
    source: Image.Image,
    assignment: AssetAssignment,
    grid: GridReference,
    size: int = 48,
    checker_colors: tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None = None,
) -> Image.Image:
    """Build a fresh nearest-neighbor preview from the current composite."""

    colors = checker_colors or ((122, 122, 122, 255), (96, 96, 96, 255))
    background = Image.new("RGBA", (size, size), colors[1])
    pixels = background.load()
    checker = max(4, size // 6)
    for y in range(size):
        for x in range(size):
            pixels[x, y] = colors[((x // checker) + (y // checker)) % 2]
    try:
        crop = extract_assignment_image(source, assignment, grid)
    except ValueError:
        return background
    scale = min(size / crop.width, size / crop.height)
    preview_size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    preview = crop.resize(preview_size, Image.Resampling.NEAREST)
    position = ((size - preview.width) // 2, (size - preview.height) // 2)
    background.alpha_composite(preview, position)
    return background


def decorate_thumbnail_selection(thumbnail: Image.Image, selected: bool) -> Image.Image:
    """Add a UI-only cyan frame without altering the source-derived preview pixels."""

    result = thumbnail.copy()
    if not selected:
        return result
    pixels = result.load()
    accent = (70, 225, 245, 255)
    for inset in (0, 1):
        last = result.width - 1 - inset
        bottom = result.height - 1 - inset
        for x in range(inset, last + 1):
            pixels[x, inset] = accent
            pixels[x, bottom] = accent
        for y in range(inset, bottom + 1):
            pixels[inset, y] = accent
            pixels[last, y] = accent
    return result
