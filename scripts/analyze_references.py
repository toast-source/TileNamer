from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "Platformer.Tile.Editor.v1.0.2"
SAMPLES = ROOT / "타일샘플"
NORMAL_NAME = re.compile(r"^(Platform|Solid|Wall)_([A-Za-z]+)_(\d+)\.png$", re.I)
SEQUENCE_NAME = re.compile(
    r"^Solid_TopSequence_(Start|Repeat|End)_(\d+)_(\d+)\.png$", re.I
)


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_assembly_identifiers(path: Path) -> list[str]:
    data = path.read_bytes()
    values = {m.decode("ascii") for m in re.findall(rb"[A-Za-z][A-Za-z0-9_]{3,}", data)}
    non_tile_helpers = {
        "PlatformAutoTile",
        "PlatformGround",
        "SolidGround",
        "SolidPreview",
        "WallPreview",
    }
    return sorted(
        value
        for value in values
        if re.fullmatch(r"(?:Platform|Solid|Wall)[A-Z][A-Za-z]+", value)
        and value not in non_tile_helpers
    )


def prefix_from_identifier(value: str) -> str:
    for category in ("Platform", "Solid", "Wall"):
        if value.startswith(category):
            return f"{category}_{value[len(category):]}"
    raise ValueError(value)


def analyze_reference() -> tuple[dict, list[str]]:
    dll = REFERENCE / "Platformer Tile Editor_Data" / "Managed" / "SP.PlatformerTiles.dll"
    identifiers = extract_assembly_identifiers(dll)
    prefixes = [prefix_from_identifier(value) for value in identifiers]
    docs = sorted(str(path.relative_to(ROOT)) for path in (REFERENCE / "Docs").glob("*.md"))
    payload = {
        "reference_root": str(REFERENCE),
        "read_only": True,
        "prefixes": prefixes,
        "top_sequence_pattern": "Solid_TopSequence_<Start|Repeat|End>_<type index>_<candidate index>.png",
        "evidence": {
            "primary_assembly": str(dll.relative_to(ROOT)),
            "assembly_identifiers": identifiers,
            "documentation": docs,
        },
    }
    return payload, prefixes


def analyze_samples() -> tuple[dict, list[str]]:
    images = []
    groups: dict[str, list[int]] = defaultdict(list)
    sequence_prefixes: set[str] = set()
    exceptions = []
    for path in sorted(SAMPLES.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
            continue
        with Image.open(path) as image:
            width, height = image.size
            alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
            record = {
                "relative_path": str(path.relative_to(SAMPLES)),
                "width": width,
                "height": height,
                "extension": path.suffix.lower(),
                "has_alpha": alpha,
            }
            images.append(record)
            if (width, height) != (32, 32):
                exceptions.append(record)
        normal = NORMAL_NAME.match(path.name)
        sequence = SEQUENCE_NAME.match(path.name)
        if normal:
            prefix = f"{normal.group(1)}_{normal.group(2)}"
            groups[prefix].append(int(normal.group(3)))
        elif sequence:
            prefix = f"Solid_TopSequence_{sequence.group(1)}_{int(sequence.group(2)):02d}"
            groups[prefix].append(int(sequence.group(3)))
            sequence_prefixes.add(prefix)
    manifests = []
    for path in sorted(SAMPLES.rglob("*.platformertileset")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        manifests.append(
            {
                "relative_path": str(path.relative_to(SAMPLES)),
                "display_name": data.get("displayName"),
                "tile_count": len(data.get("tiles", [])),
                "image_folder": str((path.parent / "TileImages").relative_to(SAMPLES)),
            }
        )
    grouped = {
        prefix: {
            "observed_indices": sorted(set(indices)),
            "index_start": min(indices),
            "padding": 2,
        }
        for prefix, indices in sorted(groups.items())
    }
    payload = {
        "sample_root": str(SAMPLES),
        "read_only": True,
        "output_structure": "<tile set folder>/TileImages/<prefix>_<two-digit index>.png",
        "manifests": manifests,
        "images": images,
        "groups": grouped,
        "non_32x32_images": exceptions,
    }
    return payload, sorted(sequence_prefixes)


def build_config(reference_prefixes: list[str], sequence_prefixes: list[str]) -> dict:
    names = sorted(set(reference_prefixes) | set(sequence_prefixes))
    return {
        "format_version": 1,
        "tile_size": 32,
        "categories": [
            {
                "name": name,
                "prefix": name,
                "index_start": 0,
                "padding": 2,
                "subfolder": "TileImages",
            }
            for name in names
        ],
    }


def main() -> None:
    reference, prefixes = analyze_reference()
    samples, sequence_prefixes = analyze_samples()
    write_json(ROOT / "analysis_reference_names.json", reference)
    write_json(ROOT / "analysis_tile_samples.json", samples)
    write_json(ROOT / "tile_names.json", build_config(prefixes, sequence_prefixes))
    print(f"reference prefixes: {len(prefixes)}")
    print(f"sample images: {len(samples['images'])}")
    print(f"configured categories: {len(set(prefixes) | set(sequence_prefixes))}")


if __name__ == "__main__":
    main()
