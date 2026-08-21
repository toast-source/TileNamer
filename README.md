# TileNamer

32×32 논리 그리드에서 직사각형 또는 비정형 셀 Shape를 순서대로 선택하고 Platformer Tile Editor
규칙에 맞는 PNG 파일명으로 내보내는 Windows용 PySide6 도구입니다.

## TileNamer v0.1.6 사용법

- 카테고리는 Platform, Solid, Wall, Top Sequence 트리로 구성되며 이름이나 prefix로 검색할 수 있습니다.
- 셀을 클릭하면 1×1 에셋을, 셀 사이를 드래그하면 2×1·1×2·2×2 이상의 멀티셀 에셋을 등록합니다.
- `셀 그리기`는 지나간 셀만 모아 ㄴ자·ㄷ자·계단형 Asset을 만들며, 비선택 cell은 Export에서 투명하게 유지합니다.
- 드래그 중에는 셀/픽셀 크기가 표시되며 `Esc`로 취소할 수 있습니다.
- 완전히 같은 영역을 다시 선택하면 해제되고, 다른 카테고리에서 선택하면 이동합니다. 일부만 겹치면 등록하지 않습니다.
- 우측 목록의 위/아래 버튼으로 출력 순서를 바꾸며 각 에셋은 `TileImages/<prefix>_00.png` 형식으로 저장됩니다.
- 오른쪽 패널의 `출력` 섹션에서 실제 `TileImages` 경로와 PNG 개수를 항상 확인하고 프로젝트별 기본 출력 위치를 변경할 수 있습니다.
- `다른 위치로 내보내기…`는 프로젝트의 기본 출력 위치를 바꾸지 않는 일회성 Export입니다.
- 이미지 파일을 Viewport에 놓아 열거나 교체할 수 있으며, 기존 Assignment 배치를 호환되는 Variant 이미지에 재사용할 수 있습니다.
- `Ctrl+Z`/`Ctrl+Y`로 Assignment와 Aseprite 레이어 표시 변경을 실행 취소하거나 다시 실행합니다.
- `Ctrl+마우스 휠`로 확대/축소하고 `Space+드래그`로 Viewport를 이동합니다.
- Aseprite 입력은 레이어 탭에서 원본 계층과 표시 상태를 확인하고 임시 composite를 다시 렌더할 수 있습니다.
- Aseprite를 열면 Cel 배치와 레이어 픽셀 주기를 분석해 Layer별 32×32 Grid phase를 자동 준비합니다. 신뢰도가 낮은 레이어만 `고급 정렬 설정`의 수동 Grid fallback을 사용합니다.
- 레이어 위치 보정값은 별도 적용 checkbox 없이 즉시 composite에 반영되며 원본 문서는 수정하지 않습니다.
- 등록된 타일 목록은 현재 composite에서 매번 생성한 nearest-neighbor thumbnail을 표시합니다.
- Light/Dark 테마와 투명 영역 checkerboard를 전역 설정으로 기억하며, Source 픽셀과 Export 결과는 변경하지 않습니다.
- 프로젝트별 임시 태그를 추가·이름 변경·삭제하여 별도의 Export prefix로 사용할 수 있습니다.
- `보기 > 배치 미리보기`에서 현재 Assignment를 실제 이웃 규칙에 적용한 결과를 별도 창으로 확인할 수 있습니다. 창을 열어 둔 채 MainWindow에서 계속 편집하면 결과가 자동 갱신됩니다.
- `리소스 교체`는 Assignment와 임시 태그를 유지한 채 호환되는 Source만 안전하게 교체합니다.
- Aseprite 자동 새로고침은 저장 이벤트를 debounce하여 반영하며 설정에서 끌 수 있습니다.
- 신뢰 가능한 문서/Tilemap grid metadata가 있으면 미리보기 확인 후 레이어 정렬값을 자동 적용할 수 있습니다.
- 전체 기능 안내와 단축키는 프로그램의 `도움말` 메뉴에서 바로 확인할 수 있습니다.

Top Sequence도 다른 카테고리와 동일하게 사각형 또는 셀 그리기 Shape를 선택하고, bounding 크기 그대로
`Solid_TopSequence_<Start|Repeat|End>_<00|01>_00.png` 형식으로 내보냅니다.

## 실행

```powershell
python -m pip install -r requirements.txt
python main.py
```

또는 `run_app.bat`을 실행합니다. Aseprite 입력은 `ASEPRITE_PATH` 환경 변수 또는
일반 설치 경로에서 `Aseprite.exe`를 자동 탐색합니다.

GUI를 열지 않고 설치 상태만 확인하려면 `run_app.bat --check`를 실행합니다.

향후 Windows EXE를 만들 때는 루트의 다중 해상도 `icon.ico`를 정식 아이콘으로 사용해
`PyInstaller --icon=icon.ico ...` 또는 spec 파일의 `icon='icon.ico'`를 지정해야 합니다.
이번 소스 체크포인트에는 EXE 빌드 결과물을 포함하지 않습니다.

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
