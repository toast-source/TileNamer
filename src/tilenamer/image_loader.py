from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
ASEPRITE_EXTENSIONS = {".ase", ".aseprite"}


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


def render_aseprite_first_frame(source_path: str | Path, executable: Path | None = None) -> Image.Image:
    aseprite = executable or find_aseprite()
    if aseprite is None:
        raise RuntimeError("Aseprite.exe를 찾지 못했습니다. ASEPRITE_PATH를 설정해 주세요.")
    source = Path(source_path).resolve()
    with tempfile.TemporaryDirectory(prefix="tilenamer-") as temp_dir:
        output = Path(temp_dir) / "first-frame.png"
        command = [
            str(aseprite), "--batch", str(source), "--frame-range", "0,0", "--save-as", str(output)
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode != 0 or not output.exists():
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"Aseprite 첫 프레임 렌더 실패: {detail}")
        with Image.open(output) as rendered:
            return rendered.convert("RGBA").copy()


def load_source_image(path: str | Path) -> Image.Image:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in ASEPRITE_EXTENSIONS:
        return render_aseprite_first_frame(source)
    if suffix not in RASTER_EXTENSIONS:
        raise ValueError(f"지원하지 않는 이미지 형식: {suffix}")
    with Image.open(source) as image:
        return image.convert("RGBA").copy()

