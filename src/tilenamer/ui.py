from __future__ import annotations

import colorsys
import re
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .config import load_categories
from .exporter import build_export_plan, export_tiles, find_existing_collisions
from .image_loader import ASEPRITE_EXTENSIONS, RASTER_EXTENSIONS, load_source_image
from .model import AssetAssignment, AssignmentModel, normalized_region
from .project import TileProject

TOP_SEQUENCE_NOTICE = (
    "Top Sequence는 전용 영역 선택 방식이 필요해 현재 버전에서는 "
    "아직 편집을 지원하지 않습니다."
)
ROLE_CATEGORY = int(Qt.ItemDataRole.UserRole)
ROLE_SEARCH = ROLE_CATEGORY + 1


def category_color(name: str) -> QColor:
    hue = (sum((index + 1) * ord(char) for index, char in enumerate(name)) % 360) / 360
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 1.0)
    return QColor(round(red * 255), round(green * 255), round(blue * 255))


class TileCanvas(QWidget):
    region_selected = Signal(int, int, int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image = QImage()
        self.model = AssignmentModel()
        self.zoom = 1.0
        self.tile_size = 32
        self.current_category = ""
        self.editing_enabled = True
        self.drag_start: tuple[int, int] | None = None
        self.drag_end: tuple[int, int] | None = None
        self.preview_conflict: AssetAssignment | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_content(self, image, model: AssignmentModel) -> None:
        self.image = ImageQt(image).copy()
        self.model = model
        self.cancel_drag()
        self._resize_canvas()
        self.update()

    def set_category(self, category: str, enabled: bool = True) -> None:
        self.current_category = category
        self.editing_enabled = enabled
        self.cancel_drag()

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.25, min(8.0, zoom))
        self._resize_canvas()
        self.update()

    def _resize_canvas(self) -> None:
        if self.image.isNull():
            self.setFixedSize(1, 1)
        else:
            self.setFixedSize(round(self.image.width() * self.zoom), round(self.image.height() * self.zoom))

    def _cell_at(self, position) -> tuple[int, int] | None:
        if self.image.isNull() or position.x() < 0 or position.y() < 0:
            return None
        column = int((position.x() / self.zoom) // self.tile_size)
        row = int((position.y() / self.zoom) // self.tile_size)
        if column < 0 or row < 0:
            return None
        if (column + 1) * self.tile_size > self.image.width():
            return None
        if (row + 1) * self.tile_size > self.image.height():
            return None
        return column, row

    def _update_preview(self, cell: tuple[int, int]) -> None:
        self.drag_end = cell
        x, y, width, height = normalized_region(self.drag_start, self.drag_end)
        candidate = AssetAssignment(self.current_category, x, y, width, height)
        self.preview_conflict = self.model.preview_conflict(candidate)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.editing_enabled:
            return
        cell = self._cell_at(event.position())
        if cell is None:
            return
        self.setFocus()
        self.drag_start = cell
        self._update_preview(cell)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.drag_start is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        cell = self._cell_at(event.position())
        if cell is not None:
            self._update_preview(cell)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.drag_start is None:
            return
        cell = self._cell_at(event.position())
        if cell is None:
            self.cancel_drag()
            return
        self._update_preview(cell)
        x, y, width, height = normalized_region(self.drag_start, self.drag_end)
        self.cancel_drag()
        self.region_selected.emit(x, y, width, height)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.drag_start is not None:
            self.cancel_drag()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.cancel_drag()
        super().focusOutEvent(event)

    def cancel_drag(self) -> None:
        self.drag_start = None
        self.drag_end = None
        self.preview_conflict = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        if self.image.isNull():
            return
        target = QRectF(0, 0, self.image.width() * self.zoom, self.image.height() * self.zoom)
        painter.drawImage(target, self.image)
        cell = self.tile_size * self.zoom
        columns, rows = self.image.width() // self.tile_size, self.image.height() // self.tile_size
        full_width, full_height = columns * cell, rows * cell
        painter.setPen(QPen(QColor(255, 255, 255, 125), max(1.0, self.zoom / 2)))
        for column in range(columns + 1):
            painter.drawLine(column * cell, 0, column * cell, full_height)
        for row in range(rows + 1):
            painter.drawLine(0, row * cell, full_width, row * cell)
        if full_width < self.width() or full_height < self.height():
            painter.fillRect(QRectF(full_width, 0, self.width() - full_width, self.height()), QColor(70, 20, 20, 150))
            painter.fillRect(QRectF(0, full_height, self.width(), self.height() - full_height), QColor(70, 20, 20, 150))
        painter.setFont(QFont("Segoe UI", max(7, round(9 * min(self.zoom, 2.0))), QFont.Weight.Bold))
        for category, assets in self.model.assignments.items():
            color = category_color(category)
            for index, asset in enumerate(assets, 1):
                rect = QRectF(asset.x_cell * cell + 1, asset.y_cell * cell + 1,
                              asset.width_cells * cell - 2, asset.height_cells * cell - 2)
                overlay = QColor(color)
                overlay.setAlpha(65)
                painter.fillRect(rect, overlay)
                painter.setPen(QPen(color, max(2.0, self.zoom * 1.5)))
                painter.drawRect(rect)
                label = f"#{index:02d}\n{asset.width_cells}×{asset.height_cells}"
                if rect.width() >= 140:
                    label = f"{category}\n{label}"
                painter.setPen(Qt.GlobalColor.black)
                painter.drawText(rect.translated(1, 1), Qt.AlignmentFlag.AlignCenter, label)
                painter.setPen(Qt.GlobalColor.white)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        if self.drag_start is not None and self.drag_end is not None:
            x, y, width, height = normalized_region(self.drag_start, self.drag_end)
            rect = QRectF(x * cell + 1, y * cell + 1, width * cell - 2, height * cell - 2)
            color = QColor(230, 55, 55) if self.preview_conflict else category_color(self.current_category)
            fill = QColor(color)
            fill.setAlpha(85)
            painter.fillRect(rect, fill)
            painter.setPen(QPen(color, max(3.0, self.zoom * 2)))
            painter.drawRect(rect)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             f"{width}×{height} / {width * 32}×{height * 32}")


class MainWindow(QMainWindow):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.rules = load_categories(root / "tile_names.json")
        self.rule_by_name = {rule.name: rule for rule in self.rules}
        self.category_items: dict[str, QTreeWidgetItem] = {}
        self.model = AssignmentModel()
        self.source_image = None
        self.source_path: Path | None = None
        self._current_category = ""
        self.setWindowTitle("TileNamer v0.1.1")
        self.resize(1400, 850)
        self._build_ui()

    def _build_ui(self) -> None:
        root_widget = QWidget()
        outer = QVBoxLayout(root_widget)
        toolbar = QHBoxLayout()
        for text, callback in (
            ("이미지 열기", self.open_source), ("프로젝트 저장", self.save_project),
            ("프로젝트 불러오기", self.load_project), ("전체 내보내기", self.export_all),
            ("현재 카테고리 내보내기", self.export_current),
            ("축소", lambda: self.canvas.set_zoom(self.canvas.zoom / 2)),
            ("100%", lambda: self.canvas.set_zoom(1.0)),
            ("확대", lambda: self.canvas.set_zoom(self.canvas.zoom * 2)),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("이름 카테고리"))
        self.category_search = QLineEdit()
        self.category_search.setPlaceholderText("이름 또는 prefix 검색")
        self.category_search.setClearButtonEnabled(True)
        self.category_search.textChanged.connect(self.filter_categories)
        left_layout.addWidget(self.category_search)
        self.category_tree = QTreeWidget()
        self.category_tree.setHeaderHidden(True)
        self.category_tree.currentItemChanged.connect(self._category_changed)
        left_layout.addWidget(self.category_tree)
        self._populate_category_tree()
        splitter.addWidget(left)

        self.canvas = TileCanvas()
        self.canvas.region_selected.connect(self.assign_region)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        splitter.addWidget(scroll)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.current_label = QLabel("등록 순서 / 예상 파일명")
        right_layout.addWidget(self.current_label)
        self.assignment_list = QListWidget()
        right_layout.addWidget(self.assignment_list)
        controls = QHBoxLayout()
        for text, callback in (("▲", lambda: self.reorder(-1)), ("▼", lambda: self.reorder(1)),
                               ("해제", self.remove_selected)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
        right_layout.addLayout(controls)
        splitter.addWidget(right)
        splitter.setSizes([280, 820, 360])
        outer.addWidget(splitter)
        self.warning = QLabel("이미지를 열어 주세요.")
        self.warning.setWordWrap(True)
        outer.addWidget(self.warning)
        self.setCentralWidget(root_widget)
        if self.category_items:
            first = next(iter(self.category_items.values()))
            self.category_tree.setCurrentItem(first)

    @staticmethod
    def _leaf_label(name: str) -> str:
        return name.split("_", 1)[1] if "_" in name else name

    def _group_item(self, parent: QTreeWidget | QTreeWidgetItem, text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, ROLE_SEARCH, text.casefold())
        return item

    def _populate_category_tree(self) -> None:
        self.category_tree.clear()
        self.category_items.clear()
        groups = {name: self._group_item(self.category_tree, name) for name in ("Platform", "Solid", "Wall")}
        bridge = self._group_item(groups["Solid"], "Bridge")
        top_sequence = self._group_item(self.category_tree, "Top Sequence")
        types = {kind: self._group_item(top_sequence, f"Type {kind}") for kind in ("00", "01")}
        for rule in self.rules:
            name = rule.name
            if "TopSequence" in name:
                match = re.search(r"TopSequence_(Start|Repeat|End)_(00|01)$", name)
                parent = types[match.group(2)] if match else top_sequence
                label = match.group(1) if match else self._leaf_label(name)
            elif name.startswith("Platform_"):
                parent, label = groups["Platform"], self._leaf_label(name)
            elif name.startswith("Wall_"):
                parent, label = groups["Wall"], self._leaf_label(name)
            elif "Bridge" in name:
                parent, label = bridge, self._leaf_label(name)
            else:
                parent, label = groups["Solid"], self._leaf_label(name)
            item = QTreeWidgetItem(parent, [label])
            item.setData(0, ROLE_CATEGORY, name)
            item.setData(0, ROLE_SEARCH, f"{label} {name} {rule.prefix}".casefold())
            self.category_items[name] = item
        self.category_tree.expandAll()
        self._update_tree_counts()

    def _update_tree_counts(self) -> None:
        for category, item in self.category_items.items():
            base = self._leaf_label(category)
            if "TopSequence" in category:
                match = re.search(r"TopSequence_(Start|Repeat|End)_\d\d$", category)
                base = match.group(1) if match else base
            count = len(self.model.assets(category))
            item.setText(0, f"{base} ({count})" if count else base)

    def filter_categories(self, text: str) -> None:
        needle = text.strip().casefold()

        def visit(item: QTreeWidgetItem) -> bool:
            child_results = [visit(item.child(i)) for i in range(item.childCount())]
            child_match = any(child_results)
            own_match = not needle or needle in str(item.data(0, ROLE_SEARCH) or "")
            visible = own_match or child_match
            item.setHidden(not visible)
            if needle and child_match:
                item.setExpanded(True)
            return visible

        for index in range(self.category_tree.topLevelItemCount()):
            visit(self.category_tree.topLevelItem(index))
        if not needle:
            self.category_tree.expandAll()

    def _category_changed(self, current, previous=None) -> None:
        category = str(current.data(0, ROLE_CATEGORY) or "") if current else ""
        if not category:
            return
        self._current_category = category
        is_top_sequence = "TopSequence" in category
        self.canvas.set_category(category, not is_top_sequence)
        if is_top_sequence:
            self.warning.setText(TOP_SEQUENCE_NOTICE)
        elif self.source_image is not None:
            self._set_image_status()
        self.refresh_assignments()

    def current_category(self) -> str:
        return self._current_category

    def open_source(self) -> None:
        extensions = " ".join(f"*{ext}" for ext in sorted(RASTER_EXTENSIONS | ASEPRITE_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(self, "원본 이미지 열기", "", f"지원 이미지 ({extensions})")
        if path:
            self._load_source(Path(path))

    def _load_source(self, path: Path, keep_assignments: bool = False) -> None:
        try:
            image = load_source_image(path)
        except Exception as error:
            QMessageBox.critical(self, "불러오기 실패", str(error))
            return
        self.source_image = image
        self.source_path = path.resolve()
        if not keep_assignments:
            self.model.clear()
        self.canvas.set_content(image, self.model)
        self.canvas.set_category(self.current_category(), "TopSequence" not in self.current_category())
        self._set_image_status()
        self.refresh_assignments()

    def _set_image_status(self) -> None:
        if self.source_image is None:
            return
        if "TopSequence" in self.current_category():
            self.warning.setText(TOP_SEQUENCE_NOTICE)
            return
        image = self.source_image
        remainder = (image.width % 32, image.height % 32)
        if remainder != (0, 0):
            self.warning.setText(f"경고: {image.width}×{image.height}, 오른쪽 {remainder[0]}px / 아래 "
                                 f"{remainder[1]}px 남는 영역은 선택할 수 없습니다.")
        else:
            self.warning.setText(f"{image.width}×{image.height} / {image.width // 32}×{image.height // 32} 셀")

    def toggle_tile(self, column: int, row: int) -> None:
        self.assign_region(column, row, 1, 1)

    def assign_region(self, x: int, y: int, width: int, height: int) -> None:
        category = self.current_category()
        if not category or "TopSequence" in category:
            return
        result = self.model.assign_region(category, x, y, width, height)
        if result.status == "conflict":
            conflict = result.conflict
            index = self.model.assets(conflict.category).index(conflict)
            rule = self.rule_by_name.get(conflict.category)
            filename = rule.filename(index) if rule else "?"
            QMessageBox.warning(
                self, "영역 충돌",
                f"선택 영역이 기존 에셋과 일부 겹칩니다.\n\n카테고리: {conflict.category}\n"
                f"파일명: {filename}\n순서: #{index + 1:02d}\n"
                f"크기: {conflict.width_cells}×{conflict.height_cells} / "
                f"{conflict.output_width_px}×{conflict.output_height_px}",
            )
        self.refresh_assignments()

    def refresh_assignments(self) -> None:
        category = self.current_category()
        self.assignment_list.clear()
        self.current_label.setText(f"{category or '카테고리'} 등록 순서 / 예상 파일명")
        rule = self.rule_by_name.get(category)
        if rule:
            for index, asset in enumerate(self.model.assets(category)):
                self.assignment_list.addItem(
                    f"({asset.x_cell}, {asset.y_cell}) · {asset.width_cells}×{asset.height_cells} · "
                    f"{asset.output_width_px}×{asset.output_height_px}  →  {rule.filename(index)}"
                )
        self._update_tree_counts()
        self.canvas.update()

    def reorder(self, offset: int) -> None:
        row = self.assignment_list.currentRow()
        if row < 0:
            return
        target = self.model.move(self.current_category(), row, offset)
        self.refresh_assignments()
        self.assignment_list.setCurrentRow(target)

    def remove_selected(self) -> None:
        row, category = self.assignment_list.currentRow(), self.current_category()
        if row >= 0 and category in self.model.assignments:
            self.model.remove(category, row)
            self.refresh_assignments()

    def save_project(self) -> None:
        if self.source_path is None:
            QMessageBox.information(self, "저장", "먼저 원본 이미지를 열어 주세요.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "프로젝트 저장", "", "TileNamer 프로젝트 (*.tilenamer.json)")
        if path:
            if not path.lower().endswith(".tilenamer.json"):
                path += ".tilenamer.json"
            TileProject(str(self.source_path), 32, self.model).save(path)
            self.statusBar().showMessage(f"프로젝트 저장: {path}", 5000)

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 불러오기", "", "TileNamer 프로젝트 (*.tilenamer.json)")
        if not path:
            return
        try:
            project = TileProject.load(path)
            if project.tile_size != 32:
                raise ValueError("이 버전은 32×32 프로젝트만 지원합니다.")
            unknown = set(project.model.assignments) - set(self.rule_by_name)
            if unknown:
                raise ValueError(f"설정에 없는 카테고리: {', '.join(sorted(unknown))}")
            source_path = Path(project.source_file)
            if not source_path.exists():
                raise FileNotFoundError(f"원본 파일을 찾을 수 없습니다: {source_path}")
            self.model = project.model
            self.canvas.model = self.model
            self._load_source(source_path, keep_assignments=True)
        except Exception as error:
            QMessageBox.critical(self, "프로젝트 불러오기 실패", str(error))

    def export_all(self) -> None:
        self._export(None)

    def export_current(self) -> None:
        self._export(self.current_category())

    def _export(self, category: str | None) -> None:
        if category and "TopSequence" in category:
            QMessageBox.information(self, "내보내기", TOP_SEQUENCE_NOTICE)
            return
        if self.source_image is None:
            QMessageBox.information(self, "내보내기", "먼저 원본 이미지를 열어 주세요.")
            return
        output = QFileDialog.getExistingDirectory(self, "타일 세트 폴더 선택")
        if not output:
            return
        try:
            plan = build_export_plan(output, self.model, self.rules, category)
            if not plan:
                QMessageBox.information(self, "내보내기", "등록된 에셋이 없습니다.")
                return
            collisions = find_existing_collisions(plan)
            overwrite = False
            if collisions:
                preview = "\n".join(str(path) for path in collisions[:10])
                if len(collisions) > 10:
                    preview += f"\n... 외 {len(collisions) - 10}개"
                answer = QMessageBox.question(
                    self, "기존 파일 덮어쓰기",
                    f"기존 파일 {len(collisions)}개를 덮어씁니다. 계속할까요?\n\n{preview}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                overwrite = True
            written = export_tiles(self.source_image, plan, overwrite=overwrite)
            QMessageBox.information(self, "내보내기 완료", f"PNG 에셋 {len(written)}개를 생성했습니다.")
        except Exception as error:
            QMessageBox.critical(self, "내보내기 실패", str(error))
