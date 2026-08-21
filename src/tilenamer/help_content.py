from __future__ import annotations


HELP_HTML = """
<h1>TileNamer 사용법</h1>
<h2>1. 시작하기</h2>
<ol>
  <li><b>이미지 열기</b> 또는 Drag &amp; Drop으로 타일시트를 불러옵니다.</li>
  <li>왼쪽 <b>타일 종류</b>에서 등록할 이름을 선택합니다.</li>
  <li>중앙 Viewport에서 한 칸을 클릭하거나 여러 칸을 드래그합니다.</li>
  <li>오른쪽 등록된 타일 목록에서 순서와 파일명을 확인합니다.</li>
  <li>전체 또는 현재 타일 내보내기를 실행합니다.</li>
</ol>

<h2>2. 지원 파일</h2>
<p>PNG, JPG/JPEG, BMP, WEBP, TIFF, ASE, ASEPRITE 파일을 지원합니다.</p>

<h2>3. 타일 종류</h2>
<p>Platform, Solid, Wall, Bridge, Top Sequence, Type 00/01, 임시 태그는 분류용 Group이며
선택할 수 없습니다. Group 아래의 실제 leaf만 Assignment category로 사용할 수 있습니다.</p>

<h2>4. 타일 선택</h2>
<p><b>사각형</b>은 드래그한 직사각형 전체를 선택합니다. <b>셀 그리기</b>는 왼쪽 버튼으로
드래그하면서 지나간 Grid cell만 추가하므로 ㄴ자, ㄷ자, 계단형도 만들 수 있습니다.
셀 그리기 도중 <b>Ctrl</b>을 누른 채 지나가면 해당 셀을 현재 preview에서 제거합니다.
같은 Shape를 다시 선택하면 해제되며, 다른 category의 동일 Shape를 선택하면 이동합니다.</p>

<h2>5. 멀티셀 타일</h2>
<ul>
  <li>1×1 → 32×32</li><li>2×1 → 64×32</li>
  <li>1×2 → 32×64</li><li>2×2 → 64×64</li>
</ul>
<p>사각형은 기존과 동일하게 전체가 PNG로 내보내집니다. 셀 그리기 Shape는 bounding box 크기로
내보내되 선택하지 않은 내부 cell을 완전 투명 RGBA로 유지합니다.</p>

<h2>6. 등록된 타일</h2>
<p>#00, #01, #02 순서는 파일 candidate 번호입니다. 예: Platform_Center_00.png,
Platform_Center_01.png. 위/아래 버튼으로 순서를 바꿀 수 있습니다. 목록 thumbnail과 Canvas의
cyan highlight는 같은 선택 항목을 나타냅니다.</p>

<h2>7. 임시 태그</h2>
<p>특정 Map이나 Tileset에만 필요한 프로젝트 전용 Export prefix입니다. 예를 들어
BossRoom_Pillar를 만들면 BossRoom_Pillar_00.png부터 순서대로 내보냅니다.
추가, 이름 변경, 삭제가 가능하며 프로젝트에 저장됩니다.</p>

<h2>8. Aseprite 레이어</h2>
<p>원본 Group 계층과 Layer 표시 상태를 확인하고 ON/OFF할 수 있습니다. TileNamer는 원본
.ase/.aseprite 파일을 저장하거나 수정하지 않고 임시 composite만 렌더링합니다.</p>

<h2>9. 격자 기준과 레이어 위치 보정</h2>
<p><b>격자 기준</b>은 32×32로 자르는 선의 위치이며 선택, thumbnail, Export 좌표를 바꿉니다.
<b>레이어 위치 보정</b>은 Layer pixel 자체를 현재 격자에 맞게 이동합니다. 보정 X의 양수는
Layer를 왼쪽으로, 보정 Y의 양수는 위쪽으로 이동하며 0이면 원래 위치입니다. 값 변경은 별도
적용 checkbox 없이 즉시 composite에 반영됩니다. <b>기준 격자에 자동 맞춤</b>은 신뢰 가능한
Document/Tilemap Grid 근거로 필요한 보정값을 계산합니다.</p>

<h2>10. 리소스 교체</h2>
<p>기존 태그, Assignment, 선택 영역을 유지하고 Source 이미지나 Aseprite만 교체합니다.
Normal/Red/Ice 같은 색상 Variant 작업에 사용할 수 있으며 범위를 벗어나는 교체는 적용되지 않습니다.</p>

<h2>11. Aseprite 자동 새로고침</h2>
<p>켜져 있으면 외부 Aseprite에서 현재 파일을 저장할 때 최신 composite를 자동 반영합니다.
Assignment와 작업 상태는 유지됩니다.</p>

<h2>12. 프로젝트 저장 / 불러오기</h2>
<p>Source 경로, Assignment와 후보 순서, 임시 태그, Grid Reference, Layer 표시 상태,
Layer position correction과 기본 출력 위치를 저장하고 복원합니다. 프로젝트 파일은 TileNamer의
작업 설정을 저장하는 <code>.tilenamer.json</code>이며 PNG 출력 폴더와는 별개입니다.</p>

<h2>13. 내보내기</h2>
<p><b>출력 위치</b>는 PNG 타일이 생성되는 기본 폴더이며 오른쪽 패널의 <b>출력</b> 섹션에서 실제
<code>TileImages</code> 경로를 항상 확인할 수 있습니다. 전체 내보내기와 현재 타일 내보내기는
이 기본 위치를 재사용합니다. <b>다른 위치로 내보내기</b>는 기본 출력 위치를 바꾸지 않는
일회성 Export입니다. 결과는 선택한 base 폴더의
<code>TileImages/&lt;prefix&gt;_00.png</code> 구조로 생성됩니다.</p>

<h2>14. Theme / Alpha 배경</h2>
<p>Light/Dark Theme과 Alpha checkerboard는 화면 표시 설정이며 Source와 Export RGBA 픽셀에는
영향을 주지 않습니다.</p>

<h2>15. Undo / Redo</h2>
<p>Assignment, 임시 태그, Layer 표시와 정렬 작업을 <code>Ctrl+Z</code>로 실행 취소하고
<code>Ctrl+Y</code> 또는 <code>Ctrl+Shift+Z</code>로 다시 실행할 수 있습니다.</p>

<h2>16. Viewport 이동 / Zoom</h2>
<p>Ctrl+Mouse Wheel로 확대/축소하고 Space+Drag로 Viewport를 이동합니다. Toolbar의 100%를
누르면 원래 배율로 돌아갑니다.</p>

<h2>17. 배치 미리보기</h2>
<p>Solid 배치는 필요한 경우 연결된 Platform과 Bridge 타일도 함께 사용합니다.
Top Sequence 00/01에서는 Start, Repeat, End의 최소·긴 시퀀스를 검수할 수 있으며,
미리보기 추가로 기존 Grid-aligned PNG Export 방식이 달라지지는 않습니다.
타일 위에 마우스를 올리면 현재 Placement 전체가 강조되며, 클릭하면 본창에서 정확한
타일 종류와 등록 후보를 찾아 강조합니다. 누락된 빨간 타일을 클릭하면 필요한 타일
종류로 이동합니다. 미리보기 안에서는 Ctrl+Mouse Wheel로 확대/축소하고 Space+Drag로
화면을 이동할 수 있습니다.</p>
"""


SHORTCUTS_HTML = """
<h1>TileNamer 단축키</h1>
<table cellspacing="8">
  <tr><td><b>Ctrl + Z</b></td><td>실행 취소</td></tr>
  <tr><td><b>Ctrl + Y</b></td><td>다시 실행</td></tr>
  <tr><td><b>Ctrl + Shift + Z</b></td><td>다시 실행</td></tr>
  <tr><td><b>Ctrl + Mouse Wheel</b></td><td>확대 / 축소</td></tr>
  <tr><td><b>Space + Drag</b></td><td>Viewport 이동</td></tr>
  <tr><td><b>Ctrl + Drag</b></td><td>셀 그리기 preview에서 셀 제거</td></tr>
</table>
"""
