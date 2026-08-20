from pathlib import Path

from PIL import Image

from tilenamer.image_loader import load_source_image


def test_png_and_jpg_loading(tmp_path: Path) -> None:
    png = tmp_path / "source.png"
    jpg = tmp_path / "source.jpg"
    Image.new("RGBA", (33, 35), (1, 2, 3, 4)).save(png)
    Image.new("RGB", (32, 32), (5, 6, 7)).save(jpg)
    assert load_source_image(png).mode == "RGBA"
    assert load_source_image(jpg).size == (32, 32)
