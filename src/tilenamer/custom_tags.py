from __future__ import annotations

import re


WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def validate_temporary_tag(name: str, existing: list[str], built_in_prefixes: set[str]) -> str:
    if not name:
        raise ValueError("임시 태그 이름은 비어 있을 수 없습니다.")
    if name != name.strip() or name.endswith((".", " ")):
        raise ValueError("임시 태그 이름 앞뒤에 공백이나 마침표를 사용할 수 없습니다.")
    if len(name) > 120:
        raise ValueError("임시 태그 이름은 120자 이하여야 합니다.")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError("Windows 파일명에 사용할 수 없는 문자가 포함되어 있습니다.")
    if name.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        raise ValueError("Windows 예약 파일명은 사용할 수 없습니다.")
    folded = name.casefold()
    if folded in {value.casefold() for value in built_in_prefixes}:
        raise ValueError("기본 Export prefix와 중복됩니다.")
    if folded in {value.casefold() for value in existing}:
        raise ValueError("이미 존재하는 임시 태그입니다.")
    return name
