from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from PIL import Image, ImageChops, ImageOps


GRID_SIZE = 32


@dataclass(frozen=True)
class CelMetadata:
    frame_index: int
    x: int
    y: int
    width: int
    height: int
    opacity: int = 255

    def covers_canvas(self, canvas_width: int, canvas_height: int) -> bool:
        return (
            self.x <= 0 and self.y <= 0
            and self.x + self.width >= canvas_width
            and self.y + self.height >= canvas_height
        )


@dataclass(frozen=True)
class LayerGridDetection:
    origin_x: int
    origin_y: int
    confidence: str
    method: str
    score: float = 0.0

    @property
    def origin(self) -> tuple[int, int]:
        return self.origin_x, self.origin_y

    @property
    def is_automatic(self) -> bool:
        return self.confidence == "high"


def normalized_phase(value: int, grid_size: int = GRID_SIZE) -> int:
    """Return a compact signed representation of a periodic grid phase."""
    phase = int(value) % grid_size
    return phase - grid_size if phase > grid_size // 2 else phase


def _pixels(image: Image.Image):
    flattened = getattr(image, "get_flattened_data", None)
    return flattened() if flattened is not None else image.getdata()


def _profile(image: Image.Image, axis: str) -> tuple[list[float], list[float]]:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    luminance = ImageOps.grayscale(rgba.convert("RGB"))
    if axis == "x":
        if rgba.width < 2:
            return [], []
        alpha_edge = ImageChops.difference(alpha.crop((1, 0, rgba.width, rgba.height)),
                                            alpha.crop((0, 0, rgba.width - 1, rgba.height)))
        color_edge = ImageChops.difference(luminance.crop((1, 0, rgba.width, rgba.height)),
                                            luminance.crop((0, 0, rgba.width - 1, rgba.height)))
        transparent = ImageOps.invert(alpha).resize((rgba.width, 1), Image.Resampling.BOX)
        alpha_values = list(_pixels(alpha_edge.resize((rgba.width - 1, 1), Image.Resampling.BOX)))
        color_values = list(_pixels(color_edge.resize((rgba.width - 1, 1), Image.Resampling.BOX)))
        transparent_values = list(_pixels(transparent))
    else:
        if rgba.height < 2:
            return [], []
        alpha_edge = ImageChops.difference(alpha.crop((0, 1, rgba.width, rgba.height)),
                                            alpha.crop((0, 0, rgba.width, rgba.height - 1)))
        color_edge = ImageChops.difference(luminance.crop((0, 1, rgba.width, rgba.height)),
                                            luminance.crop((0, 0, rgba.width, rgba.height - 1)))
        transparent = ImageOps.invert(alpha).resize((1, rgba.height), Image.Resampling.BOX)
        alpha_values = list(_pixels(alpha_edge.resize((1, rgba.height - 1), Image.Resampling.BOX)))
        color_values = list(_pixels(color_edge.resize((1, rgba.height - 1), Image.Resampling.BOX)))
        transparent_values = list(_pixels(transparent))
    edges = [(a * 0.55 + c * 0.45) / 255.0 for a, c in zip(alpha_values, color_values)]
    gutters = [value / 255.0 for value in transparent_values]
    return edges, gutters


def _axis_phase(image: Image.Image, axis: str, grid_size: int) -> tuple[int, float, bool]:
    edges, gutters = _profile(image, axis)
    extent = image.width if axis == "x" else image.height
    if extent < grid_size * 2 or not edges:
        return 0, 0.0, False
    scores: list[float] = []
    samples: list[int] = []
    for phase in range(grid_size):
        positions = list(range(phase if phase else grid_size, extent, grid_size))
        positions = [position for position in positions if 0 < position < extent]
        samples.append(len(positions))
        if not positions:
            scores.append(0.0)
            continue
        # A real cell boundary is supported by recurring alpha/color discontinuity
        # and by transparent gutter pixels immediately on either side.
        evidence = []
        for position in positions:
            edge = edges[position - 1]
            gutter = max(gutters[position - 1], gutters[position])
            evidence.append(edge * 0.72 + gutter * 0.28)
        scores.append(sum(evidence) / len(evidence))
    ranked = sorted(range(grid_size), key=lambda value: scores[value], reverse=True)
    best, second = ranked[0], ranked[1]
    baseline = median(scores)
    contrast = scores[best] - baseline
    separation = scores[best] - scores[second]
    reliable = (
        samples[best] >= 2 and scores[best] >= 0.08
        and contrast >= 0.025 and separation >= 0.004
    )
    quality = max(0.0, min(1.0, contrast * 4.0 + separation * 8.0))
    return best, quality, reliable


def detect_pixel_phase(
    image: Image.Image, canvas_position: tuple[int, int] = (0, 0),
    grid_size: int = GRID_SIZE,
) -> LayerGridDetection:
    """Search all periodic X/Y origins using repeated edge and gutter evidence."""
    x_phase, x_quality, x_reliable = _axis_phase(image, "x", grid_size)
    y_phase, y_quality, y_reliable = _axis_phase(image, "y", grid_size)
    origin_x = normalized_phase(x_phase + canvas_position[0], grid_size)
    origin_y = normalized_phase(y_phase + canvas_position[1], grid_size)
    confidence = "high" if x_reliable and y_reliable else "low"
    return LayerGridDetection(
        origin_x, origin_y, confidence, "pixel-periodicity",
        round((x_quality + y_quality) / 2.0, 4),
    )


def detect_layer_grid(
    kind: str,
    cels: tuple[CelMetadata, ...],
    canvas_size: tuple[int, int],
    *,
    tileset_origin: tuple[int, int] | None = None,
    layer_images: tuple[Image.Image, ...] = (),
    document_origin: tuple[int, int] | None = None,
    grid_size: int = GRID_SIZE,
) -> LayerGridDetection:
    if not cels:
        fallback = document_origin or (0, 0)
        return LayerGridDetection(
            normalized_phase(fallback[0], grid_size), normalized_phase(fallback[1], grid_size),
            "low", "no-cels",
        )

    tile_origin = tileset_origin or (0, 0)
    phases = {
        ((cel.x + tile_origin[0]) % grid_size, (cel.y + tile_origin[1]) % grid_size)
        for cel in cels
    }
    if kind == "tilemap":
        phase = next(iter(phases))
        confidence = "high" if len(phases) == 1 else "low"
        method = "tilemap-cel+tileset" if len(phases) == 1 else "inconsistent-tilemap-cel-phase"
        return LayerGridDetection(
            normalized_phase(phase[0], grid_size), normalized_phase(phase[1], grid_size),
            confidence, method, 1.0 if confidence == "high" else 0.0,
        )

    covers_canvas = all(cel.covers_canvas(*canvas_size) for cel in cels)
    if len(phases) == 1 and not covers_canvas:
        phase = next(iter(phases))
        return LayerGridDetection(
            normalized_phase(phase[0], grid_size), normalized_phase(phase[1], grid_size),
            "high", "cel-position", 1.0,
        )

    pixel_results = tuple(
        detect_pixel_phase(image, (cel.x, cel.y), grid_size)
        for cel, image in zip(cels, layer_images)
    )
    reliable = [result for result in pixel_results if result.confidence == "high"]
    reliable_phases = {result.origin for result in reliable}
    if reliable and len(reliable) == len(pixel_results) and len(reliable_phases) == 1:
        result = reliable[0]
        return LayerGridDetection(
            result.origin_x, result.origin_y, "high", "pixel-periodicity",
            round(sum(value.score for value in reliable) / len(reliable), 4),
        )

    fallback = document_origin or (0, 0)
    method = "inconsistent-cel-phase" if len(phases) > 1 else "document-grid-fallback"
    return LayerGridDetection(
        normalized_phase(fallback[0], grid_size), normalized_phase(fallback[1], grid_size),
        "low", method, max((result.score for result in pixel_results), default=0.0),
    )
