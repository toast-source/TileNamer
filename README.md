# TileNamer

32×32 타일 시트에서 셀을 순서대로 선택하고 Platformer Tile Editor 규칙에 맞는
PNG 파일명으로 내보내는 Windows용 PySide6 도구입니다.

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

