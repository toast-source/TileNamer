"""List local Aseprite documents containing at least one tilemap layer."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def has_tilemap_layer(path: Path) -> bool:
    with path.open("rb") as stream:
        header = stream.read(128)
        if len(header) != 128 or struct.unpack_from("<H", header, 4)[0] != 0xA5E0:
            return False
        frame_count = struct.unpack_from("<H", header, 6)[0]
        for _ in range(frame_count):
            frame_start = stream.tell()
            frame_header = stream.read(16)
            if len(frame_header) != 16 or struct.unpack_from("<H", frame_header, 4)[0] != 0xF1FA:
                return False
            frame_size = struct.unpack_from("<I", frame_header, 0)[0]
            old_count = struct.unpack_from("<H", frame_header, 6)[0]
            new_count = struct.unpack_from("<I", frame_header, 12)[0]
            chunk_count = new_count if new_count else old_count
            for _ in range(chunk_count):
                chunk_header = stream.read(6)
                if len(chunk_header) != 6:
                    return False
                chunk_size, chunk_type = struct.unpack("<IH", chunk_header)
                payload_size = chunk_size - 6
                if chunk_type == 0x2004:
                    payload = stream.read(payload_size)
                    if len(payload) >= 4 and struct.unpack_from("<H", payload, 2)[0] == 2:
                        return True
                else:
                    stream.seek(payload_size, 1)
            stream.seek(frame_start + frame_size)
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()
    for root in args.roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".ase", ".aseprite"}:
                try:
                    if has_tilemap_layer(path):
                        print(path)
                except (OSError, struct.error):
                    continue


if __name__ == "__main__":
    main()
