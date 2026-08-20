from __future__ import annotations

import colorsys
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .config import CategoryRule, load_categories
from .exporter import build_export_plan, export_tiles, find_existing_collisions
from .image_loader import ASEPRITE_EXTENSIONS, RASTER_EXTENSIONS, load_source_image
from .model import AssignmentModel
from .project import TileProject


def category_color(name: str) -> QColor:
    hue = (sum((index + 1) * ord(char) for index, char in enumerate(name)) % 360) / 360
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 1.0)
    return QColor(round(red * 255), round(green * 255), round(blue * 255))


class TileCanvas(QWidget):
    tile_clicked = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image = QImage()
        self.model = AssignmentModel()
        self.zoom = 1.0
        self.tile_size = 32
        self.setMouseTracking(True)

    def set_content(self, image, model: AssignmentModel) -> None:
        self.image = ImageQt(image).copy()
        self.model = model
        self._resize_canvas()
        self.update()

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.25, min(8.0, zoom))
        self._resize_canvas()
        self.update()

    def _resize_canvas(self) -> None:
        if self.image.isNull():
            self.setFixedSize(1, 1)
        else:
            self.setFixedSize(round(self.image.width() * self.zoom), round(self.image.height() * self.zoom))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.image.isNull():
            return
        source_x = event.position().x() / self.zoom
        source_y = event.position().y() / self.zoom
        column = int(source_x // self.tile_size)
        row = int(source_y // self.tile_size)
        if (column + 1) * self.tile_size <= self.image.width() and (row + 1) * self.tile_size <= self.image.height():
            self.tile_clicked.emit(column, row)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        if self.image.isNull():
            return
        target = QRectF(0, 0, self.image.width() * self.zoom, self.image.height() * self.zoom)
        painter.drawImage(target, self.image)
        cell = self.tile_size * self.zoom
        full_width = (self.image.width() // self.tile_size) * cell
        full_height = (self.image.height() // self.tile_size) * cell
        painter.setPen(QPen(QColor(255, 255, 255, 125), max(1.0, self.zoom / 2)))
        for column in range(self.image.width() // self.tile_size + 1):
            x = column * cell
            painter.drawLine(x, 0, x, full_height)
        for row in range(self.image.height() // self.tile_size + 1):
            y = row * cell
            painter.drawLine(0, y, full_width, y)
        if full_width < self.width() or full_height < self.height():
            painter.fillRect(QRectF(full_width, 0, self.width() - full_width, self.height()), QColor(70, 20, 20, 150))
            painter.fillRect(QRectF(0, full_height, self.width(), self.height() - full_height), QColor(70, 20, 20, 150))
        painter.setFont(QFont("Segoe UI", max(7, round(9 * min(self.zoom, 2.0))), QFont.Weight.Bold))
        for category, coords in self.model.assignments.items():
            color = category_color(category)
            for index, (column, row) in enumerate(coords):
                rect = QRectF(column * cell + 1, row * cell + 1, cell - 2, cell - 2)
                overlay = QColor(color)
                overlay.setAlpha(65)
                painter.fillRect(rect, overlay)
                painter.setPen(QPen(color, max(2.0, self.zoom * 1.5)))
                painter.drawRect(rect)
                painter.setPen(Qt.GlobalColor.black)
                painter.drawText(rect.translated(1, 1), Qt.AlignmentFlag.AlignCenter, str(index))
                painter.setPen(Qt.GlobalColor.white)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(index))


class MainWindow(QMainWindow):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.rules = load_categories(root / "tile_names.json")
        self.rule_by_name = {rule.name: rule for rule in self.rules}
        self.model = AssignmentModel()
        self.source_image = None
        self.source_path: Path | None = None
        self.setWindowTitle("TileNamer v0.1.0")
        self.resize(1400, 850)
        self._build_ui()

    def _build_ui(self) -> None:
        root_widget = QWidget()
        outer = QVBoxLayout(root_widget)
        toolbar = QHBoxLayout()
        buttons = (
            ("이미지 열기", self.open_source),
            ("프로젝트 저장", self.save_project),
            ("프로젝트 불러오기", self.load_project),
            ("전체 내보내기", self.export_all),
            ("현재 카테고리 내보내기", self.export_current),
            ("축소", lambda: self.canvas.set_zoom(self.canvas.zoom / 2)),
            ("100%", lambda: self.canvas.set_zoom(1.0)),
            ("확대", lambda: self.canvas.set_zoom(self.canvas.zoom * 2)),
        )
        for text, callback in buttons:
            button = QPushButton(text)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("이름 카테고리"))
        self.categories = QListWidget()
        self.categories.addItems([rule.name for rule in self.rules])
        self.categories.currentTextChanged.connect(self.refresh_assignments)
        left_layout.addWidget(self.categories)
        splitter.addWidget(left)

        self.canvas = TileCanvas()
        self.canvas.tile_clicked.connect(self.toggle_tile)
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
        for text, callback in (("▲", lambda: self.reorder(-1)), ("▼", lambda: self.reorder(1)), ("해제", self.remove_selected)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
        right_layout.addLayout(controls)
        splitter.addWidget(right)
        splitter.setSizes([260, 880, 340])
        outer.addWidget(splitter)
        self.warning = QLabel("이미지를 열어 주세요.")
        outer.addWidget(self.warning)
        self.setCentralWidget(root_widget)
        if self.rules:
            self.categories.setCurrentRow(0)

    def current_category(self) -> str:
        return self.categories.currentItem().text() if self.categories.currentItem() else ""

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
        remainder = (image.width % 32, image.height % 32)
        if remainder != (0, 0):
            self.warning.setText(
                f"경고: {image.width}×{image.height}, 오른쪽 {remainder[0]}px / 아래 {remainder[1]}px 남는 영역은 선택할 수 없습니다."
            )
        else:
            self.warning.setText(f"{image.width}×{image.height} / {image.width // 32}×{image.height // 32} 셀")
        self.refresh_assignments()

    def toggle_tile(self, column: int, row: int) -> None:
        category = self.current_category()
        if not category:
            return
        self.model.toggle(category, (column, row))
        self.canvas.update()
        self.refresh_assignments()

    def refresh_assignments(self) -> None:
        category = self.current_category()
        self.assignment_list.clear()
        self.current_label.setText(f"{category or '카테고리'} 등록 순서 / 예상 파일명")
        rule = self.rule_by_name.get(category)
        if rule:
            for index, (column, row) in enumerate(self.model.tiles(category)):
                self.assignment_list.addItem(f"({column}, {row})  →  {rule.filename(index)}")
        self.canvas.update()

    def reorder(self, offset: int) -> None:
        row = self.assignment_list.currentRow()
        if row < 0:
            return
        target = self.model.move(self.current_category(), row, offset)
        self.refresh_assignments()
        self.assignment_list.setCurrentRow(target)

    def remove_selected(self) -> None:
        row = self.assignment_list.currentRow()
        category = self.current_category()
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
        if self.source_image is None:
            QMessageBox.information(self, "내보내기", "먼저 원본 이미지를 열어 주세요.")
            return
        output = QFileDialog.getExistingDirectory(self, "타일 세트 폴더 선택")
        if not output:
            return
        try:
            plan = build_export_plan(output, self.model, self.rules, category)
            if not plan:
                QMessageBox.information(self, "내보내기", "등록된 타일이 없습니다.")
                return
            collisions = find_existing_collisions(plan)
            overwrite = False
            if collisions:
                preview = "\n".join(str(path) for path in collisions[:10])
                if len(collisions) > 10:
                    preview += f"\n... 외 {len(collisions) - 10}개"
                answer = QMessageBox.question(
                    self,
                    "기존 파일 덮어쓰기",
                    f"기존 파일 {len(collisions)}개를 덮어씁니다. 계속할까요?\n\n{preview}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                overwrite = True
            written = export_tiles(self.source_image, plan, overwrite=overwrite)
            QMessageBox.information(self, "내보내기 완료", f"32×32 PNG {len(written)}개를 생성했습니다.")
        except Exception as error:
            QMessageBox.critical(self, "내보내기 실패", str(error))

