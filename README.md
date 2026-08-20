# TileNamer

32×32 논리 그리드에서 직사각형 에셋을 순서대로 선택하고 Platformer Tile Editor
규칙에 맞는 PNG 파일명으로 내보내는 Windows용 PySide6 도구입니다.

## v0.1.1 사용법

- 카테고리는 Platform, Solid, Wall, Top Sequence 트리로 구성되며 이름이나 prefix로 검색할 수 있습니다.
- 셀을 클릭하면 1×1 에셋을, 셀 사이를 드래그하면 2×1·1×2·2×2 이상의 멀티셀 에셋을 등록합니다.
- 드래그 중에는 셀/픽셀 크기가 표시되며 `Esc`로 취소할 수 있습니다.
- 완전히 같은 영역을 다시 선택하면 해제되고, 다른 카테고리에서 선택하면 이동합니다. 일부만 겹치면 등록하지 않습니다.
- 우측 목록의 위/아래 버튼으로 출력 순서를 바꾸며 각 에셋은 `TileImages/<prefix>_00.png` 형식으로 저장됩니다.

Top Sequence는 비정수 픽셀 높이를 위한 전용 선택 방식이 필요하므로 v0.1.1에서는
트리에 표시하되 편집과 잘못된 32배수 내보내기를 명시적으로 차단합니다.

## 실행

```powershell
python -m pip install -r requirements.txt
python main.py
```

또는 `run_app.bat`을 실행합니다. Aseprite 입력은 `ASEPRITE_PATH` 환경 변수 또는
일반 설치 경로에서 `Aseprite.exe`를 자동 탐색합니다.

## 분석 자료 재생성

```powershell
python scripts/analyze_references.py
```

이 명령은 참고 폴더를 수정하지 않고 루트의 `analysis_reference_names.json`,
`analysis_tile_samples.json`, `tile_names.json`만 갱신합니다.

## 테스트

```powershell
python -m pytest
python -m compileall -q main.py src tests scripts
```
