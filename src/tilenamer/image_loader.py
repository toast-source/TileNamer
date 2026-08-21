from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .grid import GridReference
from .grid_detection import CelMetadata, detect_layer_grid

RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
ASEPRITE_EXTENSIONS = {".ase", ".aseprite"}
SUPPORTED_EXTENSIONS = RASTER_EXTENSIONS | ASEPRITE_EXTENSIONS


@dataclass(frozen=True)
class AsepriteLayer:
    identity: str
    name: str
    kind: str
    visible: bool
    children: tuple["AsepriteLayer", ...] = ()
    uuid: str = ""
    cel_x: int | None = None
    cel_y: int | None = None
    grid_origin_x: int | None = None
    grid_origin_y: int | None = None
    grid_width: int | None = None
    grid_height: int | None = None
    stack_index: int = 0
    cels: tuple[CelMetadata, ...] = ()
    grid_confidence: str = "low"
    grid_detection_method: str = "unavailable"
    grid_detection_score: float = 0.0


@dataclass(frozen=True)
class LoadedSource:
    image: Image.Image
    layers: tuple[AsepriteLayer, ...] = ()
    layer_visibility: dict[str, bool] | None = None
    document_grid: GridReference | None = None


def find_aseprite() -> Path | None:
    override = os.environ.get("ASEPRITE_PATH")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    command = shutil.which("aseprite") or shutil.which("Aseprite.exe")
    if command:
        candidates.append(Path(command))
    for base in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))):
        root = Path(base)
        candidates.extend(
            (
                root / "Aseprite" / "Aseprite.exe",
                root / "Steam" / "steamapps" / "common" / "Aseprite" / "Aseprite.exe",
            )
        )
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Aseprite" / "Aseprite.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


ASEPRITE_LAYER_SCRIPT = r'''
local source = app.activeSprite
if source == nil then error("Aseprite document is not open") end

local function escape_json(value)
  value = string.gsub(value, "\\", "\\\\")
  value = string.gsub(value, '"', '\\"')
  value = string.gsub(value, "\n", "\\n")
  value = string.gsub(value, "\r", "\\r")
  return value
end

local requested = {}
for identity, visible in string.gmatch(app.params["visibility"] or "", "([^|=]+)=([01])") do
  requested[identity] = visible == "1"
end

local alignment = {}
for identity, x, y in string.gmatch(app.params["alignment"] or "", "([^|=]+)=(-?%d+),(-?%d+)") do
  alignment[identity] = { x=tonumber(x), y=tonumber(y) }
end

local stack_index = 0
local function layer_json(layers, prefix)
  local result = {}
  for index, layer in ipairs(layers) do
    stack_index = stack_index + 1
    local identity = prefix == "" and tostring(index) or prefix .. "/" .. tostring(index)
    if requested[identity] ~= nil then layer.isVisible = requested[identity] end
    local kind = "image"
    if layer.isGroup then kind = "group" elseif layer.isTilemap then kind = "tilemap" end
    local children = "[]"
    if layer.isGroup then children = layer_json(layer.layers, identity) end
    local cels = {}
    local cel_x = "null"
    local cel_y = "null"
    for frame_index, frame in ipairs(source.frames) do
      local cel = layer:cel(frame)
      if cel ~= nil then
        if cel_x == "null" then
          cel_x = tostring(cel.position.x)
          cel_y = tostring(cel.position.y)
        end
        local image_file = ""
        if kind == "image" and app.params["cel_dir"] ~= nil then
          image_file = "layer_" .. string.gsub(identity, "/", "_") ..
            "_frame_" .. tostring(frame_index - 1) .. ".png"
          cel.image:saveAs(app.params["cel_dir"] .. "/" .. image_file)
        end
        table.insert(cels,
          '{"frame_index":' .. tostring(frame_index - 1) ..
          ',"x":' .. tostring(cel.position.x) .. ',"y":' .. tostring(cel.position.y) ..
          ',"width":' .. tostring(cel.bounds.width) ..
          ',"height":' .. tostring(cel.bounds.height) ..
          ',"image_width":' .. tostring(cel.image.width) ..
          ',"image_height":' .. tostring(cel.image.height) ..
          ',"opacity":' .. tostring(cel.opacity) ..
          ',"image_file":"' .. escape_json(image_file) .. '"}')
      end
    end
    local grid_x = "null"
    local grid_y = "null"
    local grid_width = "null"
    local grid_height = "null"
    if layer.isTilemap and layer.tileset ~= nil then
      local grid = layer.tileset.grid
      grid_x = tostring(grid.origin.x)
      grid_y = tostring(grid.origin.y)
      grid_width = tostring(grid.tileSize.width)
      grid_height = tostring(grid.tileSize.height)
    end
    local correction = alignment[identity]
    if correction ~= nil then
      for _, frame in ipairs(source.frames) do
        local corrected_cel = layer:cel(frame)
        if corrected_cel ~= nil then
          corrected_cel.position = Point(
            corrected_cel.position.x - correction.x,
            corrected_cel.position.y - correction.y
          )
        end
      end
    end
    table.insert(result,
      '{"identity":"' .. identity .. '","name":"' .. escape_json(layer.name) ..
      '","kind":"' .. kind .. '","visible":' .. tostring(layer.isVisible) ..
      ',"uuid":"' .. escape_json(tostring(layer.uuid or "")) ..
      '","stack_index":' .. tostring(stack_index) ..
      ',"cel_x":' .. cel_x .. ',"cel_y":' .. cel_y ..
      ',"cels":[' .. table.concat(cels, ",") .. ']' ..
      ',"grid_origin_x":' .. grid_x .. ',"grid_origin_y":' .. grid_y ..
      ',"grid_width":' .. grid_width .. ',"grid_height":' .. grid_height ..
      ',"children":' .. children .. '}')
  end
  return "[" .. table.concat(result, ",") .. "]"
end

local metadata = io.open(app.params["metadata"], "w")
local grid = source.gridBounds
metadata:write('{"canvas":{"width":' .. tostring(source.width) ..
  ',"height":' .. tostring(source.height) .. '},"document_grid":{"x":' .. tostring(grid.x) ..
  ',"y":' .. tostring(grid.y) .. ',"width":' .. tostring(grid.width) ..
  ',"height":' .. tostring(grid.height) .. '},"layers":' .. layer_json(source.layers, "") .. '}')
metadata:close()
app.activeFrame = source.frames[1]
source:saveCopyAs(app.params["output"])
'''


def _coerce_layer(
    payload: dict,
    canvas_size: tuple[int, int] = (0, 0),
    document_origin: tuple[int, int] | None = None,
    cel_dir: Path | None = None,
) -> AsepriteLayer:
    cels = tuple(
        CelMetadata(
            int(cel.get("frame_index", 0)), int(cel.get("x", 0)), int(cel.get("y", 0)),
            int(cel.get("width", cel.get("image_width", 0))),
            int(cel.get("height", cel.get("image_height", 0))),
            int(cel.get("opacity", 255)),
        )
        for cel in payload.get("cels", [])
    )
    images: list[Image.Image] = []
    if cel_dir is not None:
        for cel in payload.get("cels", []):
            filename = str(cel.get("image_file", ""))
            if not filename:
                continue
            path = cel_dir / filename
            if path.is_file():
                with Image.open(path) as image:
                    images.append(image.convert("RGBA").copy())
    raw_grid_x = int(payload["grid_origin_x"]) if payload.get("grid_origin_x") is not None else None
    raw_grid_y = int(payload["grid_origin_y"]) if payload.get("grid_origin_y") is not None else None
    detection = detect_layer_grid(
        str(payload.get("kind", "image")), cels, canvas_size,
        tileset_origin=(raw_grid_x or 0, raw_grid_y or 0) if raw_grid_x is not None else None,
        layer_images=tuple(images), document_origin=document_origin,
    )
    return AsepriteLayer(
        identity=str(payload["identity"]),
        name=str(payload["name"]),
        kind=str(payload.get("kind", "image")),
        visible=bool(payload.get("visible", True)),
        children=tuple(
            _coerce_layer(child, canvas_size, document_origin, cel_dir)
            for child in payload.get("children", [])
        ),
        uuid=str(payload.get("uuid", "")),
        cel_x=int(payload["cel_x"]) if payload.get("cel_x") is not None else None,
        cel_y=int(payload["cel_y"]) if payload.get("cel_y") is not None else None,
        grid_origin_x=detection.origin_x,
        grid_origin_y=detection.origin_y,
        grid_width=int(payload["grid_width"]) if payload.get("grid_width") is not None else None,
        grid_height=int(payload["grid_height"]) if payload.get("grid_height") is not None else None,
        stack_index=int(payload.get("stack_index", 0)),
        cels=cels,
        grid_confidence=detection.confidence,
        grid_detection_method=detection.method,
        grid_detection_score=detection.score,
    )


def _flatten_visibility(layers: tuple[AsepriteLayer, ...]) -> dict[str, bool]:
    result: dict[str, bool] = {}

    def visit(layer: AsepriteLayer) -> None:
        result[layer.identity] = layer.visible
        for child in layer.children:
            visit(child)

    for layer in layers:
        visit(layer)
    return result


def render_aseprite_document(
    source_path: str | Path,
    visibility: dict[str, bool] | None = None,
    executable: Path | None = None,
    alignment_offsets: dict[str, tuple[int, int]] | None = None,
) -> LoadedSource:
    aseprite = executable or find_aseprite()
    if aseprite is None:
        raise RuntimeError("Aseprite.exe를 찾지 못했습니다. ASEPRITE_PATH를 설정해 주세요.")
    source = Path(source_path).resolve()
    with tempfile.TemporaryDirectory(prefix="tilenamer-") as temp_dir:
        temp_root = Path(temp_dir)
        output = temp_root / "first-frame.png"
        metadata = temp_root / "layers.json"
        cel_dir = temp_root / "cels"
        cel_dir.mkdir()
        script = temp_root / "render-layers.lua"
        script.write_text(ASEPRITE_LAYER_SCRIPT, encoding="utf-8")
        encoded_visibility = "|".join(
            f"{identity}={1 if visible else 0}"
            for identity, visible in sorted((visibility or {}).items())
        )
        encoded_alignment = "|".join(
            f"{identity}={offset[0]},{offset[1]}"
            for identity, offset in sorted((alignment_offsets or {}).items())
            if offset != (0, 0)
        )
        command = [
            str(aseprite), "--batch", str(source),
            "--script-param", f"output={output}",
            "--script-param", f"metadata={metadata}",
            "--script-param", f"visibility={encoded_visibility}",
            "--script-param", f"alignment={encoded_alignment}",
            "--script-param", f"cel_dir={cel_dir}",
            "--script", str(script),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode != 0 or not output.exists() or not metadata.exists():
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"Aseprite 첫 프레임 렌더 실패: {detail}")
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        document = payload.get("document_grid") or {}
        document_grid = GridReference(
            int(document.get("width", 32)), int(document.get("height", 32)),
            int(document.get("x", 0)), int(document.get("y", 0)), "document",
        )
        canvas = payload.get("canvas") or {}
        canvas_size = (int(canvas.get("width", 0)), int(canvas.get("height", 0)))
        document_origin = None
        if (document_grid.cell_width, document_grid.cell_height) == (32, 32):
            document_origin = (document_grid.origin_x, document_grid.origin_y)
        layers = tuple(
            _coerce_layer(layer, canvas_size, document_origin, cel_dir)
            for layer in payload.get("layers", [])
        )
        with Image.open(output) as rendered:
            image = rendered.convert("RGBA").copy()
        return LoadedSource(image, layers, _flatten_visibility(layers), document_grid)


def render_aseprite_first_frame(source_path: str | Path, executable: Path | None = None) -> Image.Image:
    return render_aseprite_document(source_path, executable=executable).image


def load_source_document(
    path: str | Path,
    layer_visibility: dict[str, bool] | None = None,
    alignment_offsets: dict[str, tuple[int, int]] | None = None,
) -> LoadedSource:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in ASEPRITE_EXTENSIONS:
        return render_aseprite_document(
            source, layer_visibility, alignment_offsets=alignment_offsets
        )
    if suffix not in RASTER_EXTENSIONS:
        raise ValueError(f"지원하지 않는 이미지 형식: {suffix}")
    with Image.open(source) as image:
        return LoadedSource(image.convert("RGBA").copy(), (), {})


def load_source_image(path: str | Path) -> Image.Image:
    return load_source_document(path).image
