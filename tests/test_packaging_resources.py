from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tilenamer.config import load_categories
from tilenamer.guides import load_guide_registry


ROOT = Path(__file__).resolve().parents[1]


def _packaging_datas() -> set[tuple[Path, str]]:
    def analysis(*_args, **kwargs):
        return SimpleNamespace(
            pure=(), scripts=(), binaries=(), datas=tuple(kwargs["datas"]),
        )

    namespace = {
        "SPECPATH": str(ROOT),
        "Analysis": analysis,
        "PYZ": lambda *_args, **_kwargs: object(),
        "EXE": lambda *_args, **_kwargs: object(),
    }
    spec_path = ROOT / "TileNamer.spec"
    exec(compile(spec_path.read_text(encoding="utf-8"), spec_path.name, "exec"), namespace)
    return {(Path(source).resolve(), destination) for source, destination in namespace["a"].datas}


def test_packaging_contains_all_bundled_runtime_resources() -> None:
    guide_root = ROOT / "src" / "tilenamer" / "assets" / "guides"
    assert _packaging_datas() == {
        ((ROOT / "tile_names.json").resolve(), "."),
        ((ROOT / "icon.ico").resolve(), "."),
        ((ROOT / "icon.png").resolve(), "."),
        (guide_root.resolve(), "guides"),
    }

    assert load_categories(ROOT / "tile_names.json")
    registry = load_guide_registry(guide_root)
    assert len(registry.assets) == 49
    assert len({asset.category for asset in registry.assets}) == 43
