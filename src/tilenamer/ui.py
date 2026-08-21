from __future__ import annotations

import colorsys
import re
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import (
    QEvent, QFileSystemWatcher, QPoint, QRectF, QSignalBlocker, QSize, Qt, QTimer,
    QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont, QIcon, QImage,
    QKeyEvent, QKeySequence, QPainter, QPen, QPixmap, QUndoStack,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QColorDialog, QDialog,
    QDialogButtonBox, QFileDialog,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QHeaderView, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QComboBox, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
    QStyledItemDelegate, QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from . import __version__
from .config import CategoryRule, load_categories
from .custom_tags import validate_temporary_tag
from .exporter import (
    build_export_plan, effective_output_directory, export_tiles,
    find_existing_collisions, output_asset_count,
)
from .history import (
    AlignmentStateCommand, AssignmentStateCommand, LayerVisibilityCommand,
    TemporaryTagStateCommand,
)
from .grid import GridReference
from .help_content import HELP_HTML, SHORTCUTS_HTML
from .image_loader import (
    ASEPRITE_EXTENSIONS, RASTER_EXTENSIONS, SUPPORTED_EXTENSIONS, AsepriteLayer,
    LoadedSource, load_source_document,
)
from .model import AssetAssignment, AssignmentModel, normalized_region
from .placement import AssignmentIdentity
from .placement_window import PlacementPreviewSource, PlacementPreviewWindow
from .project import TileProject
from .preferences import Preferences
from .resources import application_icon
from .thumbnail import build_assignment_thumbnail, decorate_thumbnail_selection

ROLE_CATEGORY = int(Qt.ItemDataRole.UserRole)
ROLE_SEARCH = ROLE_CATEGORY + 1
ROLE_LAYER_ID = ROLE_SEARCH + 1
ROLE_TEMPORARY_TAG = ROLE_LAYER_ID + 1
ROLE_LOCATE_FLASH = ROLE_TEMPORARY_TAG + 1


class UiTokens:
    SPACE_XS = 4
    SPACE_SM = 8
    SPACE_MD = 12
    SPACE_LG = 16
    RADIUS = 4
    CONTROL_HEIGHT = 32
    PANEL_MIN_LEFT = 210
    PANEL_MIN_RIGHT = 250
    WINDOW_MIN_WIDTH = 1100
    WINDOW_MIN_HEIGHT = 680


class LocateFlashDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        if not index.data(ROLE_LOCATE_FLASH):
            return
        painter.save()
        painter.setPen(QPen(QColor("#55efff"), 3))
        painter.drawRect(option.rect.adjusted(2, 2, -3, -3))
        painter.restore()


class MiddleElideLabel(QLabel):
    """A copy-friendly path label that keeps both ends visible."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setFullText(text)

    def setFullText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self.setToolTip(text)
        self._update_elide()

    def fullText(self) -> str:  # noqa: N802
        return self._full_text

    def _update_elide(self) -> None:
        available = max(0, self.contentsRect().width())
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, available,
        ))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_elide()


def category_color(name: str) -> QColor:
    hue = (sum((index + 1) * ord(char) for index, char in enumerate(name)) % 360) / 360
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 1.0)
    return QColor(round(red * 255), round(green * 255), round(blue * 255))


class SourceDropStack(QStackedWidget):
    files_dropped = Signal(list)
    drop_rejected = Signal(str)
    drag_active_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def local_paths(mime_data) -> list[Path]:
        if not mime_data.hasUrls():
            return []
        return [Path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile()]

    @staticmethod
    def validate_paths(paths: list[Path]) -> tuple[bool, str]:
        if len(paths) != 1:
            return False, "현재는 한 번에 하나의 이미지만 열 수 있습니다."
        if paths[0].suffix.lower() not in SUPPORTED_EXTENSIONS:
            return False, "지원하지 않는 이미지 형식입니다."
        return True, ""

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        paths = self.local_paths(event.mimeData())
        valid, _ = self.validate_paths(paths)
        if valid or len(paths) > 1:
            event.acceptProposedAction()
            self.drag_active_changed.emit(valid)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.drag_active_changed.emit(False)
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802
        self.drag_active_changed.emit(False)
        paths = self.local_paths(event.mimeData())
        valid, message = self.validate_paths(paths)
        if not valid:
            self.drop_rejected.emit(message)
            event.acceptProposedAction()
            return
        self.files_dropped.emit(paths)
        event.acceptProposedAction()


class ViewportScrollArea(QScrollArea):
    zoom_requested = Signal(float, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.space_pressed = False
        self.panning = False
        self.pan_start = QPoint()
        self.pan_scroll_start = QPoint()
        self.viewport().installEventFilter(self)

    def setWidget(self, widget: QWidget) -> None:  # noqa: N802
        super().setWidget(widget)
        widget.installEventFilter(self)

    def _set_hand_cursor(self, closed: bool = False) -> None:
        cursor = Qt.CursorShape.ClosedHandCursor if closed else Qt.CursorShape.OpenHandCursor
        self.viewport().setCursor(cursor)
        if self.widget():
            self.widget().setCursor(cursor)

    def _clear_hand_cursor(self) -> None:
        self.viewport().unsetCursor()
        if self.widget():
            self.widget().unsetCursor()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 2.0 if event.angleDelta().y() > 0 else 0.5
            point = event.position().toPoint()
            if watched is self.widget():
                point = self.widget().mapTo(self.viewport(), point)
            self.zoom_requested.emit(factor, point)
            event.accept()
            return True
        if event_type == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self.space_pressed = True
                self._set_hand_cursor()
            event.accept()
            return True
        if event_type == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self.space_pressed = False
                self.panning = False
                self._clear_hand_cursor()
            event.accept()
            return True
        if event_type == QEvent.Type.MouseButtonPress and self.space_pressed:
            if event.button() == Qt.MouseButton.LeftButton:
                self.panning = True
                self.pan_start = event.globalPosition().toPoint()
                self.pan_scroll_start = QPoint(
                    self.horizontalScrollBar().value(), self.verticalScrollBar().value()
                )
                self._set_hand_cursor(closed=True)
                event.accept()
                return True
        if event_type == QEvent.Type.MouseMove and self.panning:
            delta = event.globalPosition().toPoint() - self.pan_start
            self.horizontalScrollBar().setValue(self.pan_scroll_start.x() - delta.x())
            self.verticalScrollBar().setValue(self.pan_scroll_start.y() - delta.y())
            event.accept()
            return True
        if event_type == QEvent.Type.MouseButtonRelease and self.panning:
            self.panning = False
            self._set_hand_cursor()
            event.accept()
            return True
        if event_type in (QEvent.Type.FocusOut, QEvent.Type.Leave) and not self.panning:
            if event_type == QEvent.Type.FocusOut:
                self.space_pressed = False
                self._clear_hand_cursor()
        return super().eventFilter(watched, event)


class TileCanvas(QWidget):
    region_selected = Signal(int, int, int, int)
    cells_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image = QImage()
        self.model = AssignmentModel()
        self.zoom = 1.0
        self.tile_size = 32
        self.grid_reference = GridReference()
        self.alpha_colors = (QColor(122, 122, 122), QColor(96, 96, 96))
        self.current_category = ""
        self.editing_enabled = True
        self.drag_start: tuple[int, int] | None = None
        self.drag_end: tuple[int, int] | None = None
        self.drag_cells: set[tuple[int, int]] = set()
        self.last_drag_cell: tuple[int, int] | None = None
        self.last_drag_remove: bool | None = None
        self.selection_mode = "rectangle"
        self.control_pressed = False
        self.hover_cell: tuple[int, int] | None = None
        self.preview_conflict: AssetAssignment | None = None
        self.selected_assignment_index = -1
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def set_content(self, image, model: AssignmentModel) -> None:
        self.set_qimage_content(ImageQt(image).copy(), model)

    def set_qimage_content(self, image: QImage, model: AssignmentModel) -> None:
        self.image = image
        self.model = model
        self.cancel_drag()
        self._resize_canvas()
        self.update()

    def set_category(self, category: str, enabled: bool = True) -> None:
        self.current_category = category
        self.editing_enabled = enabled
        self.selected_assignment_index = -1
        self.cancel_drag()

    def set_grid_reference(self, reference: GridReference) -> None:
        self.grid_reference = reference
        self.update()

    def set_selection_mode(self, mode: str) -> None:
        if mode not in {"rectangle", "paint"}:
            raise ValueError("지원하지 않는 선택 방식입니다.")
        self.selection_mode = mode
        self.cancel_drag()

    def set_alpha_colors(self, colors: tuple[tuple[int, int, int, int], tuple[int, int, int, int]]) -> None:
        self.alpha_colors = (QColor(*colors[0]), QColor(*colors[1]))
        self.update()

    def set_selected_assignment(self, index: int) -> None:
        assets = self.model.assets(self.current_category)
        self.selected_assignment_index = index if 0 <= index < len(assets) else -1
        self.update()

    def assignment_visual_state(self, category: str, index: int) -> str:
        if category != self.current_category:
            return "other-category"
        if index == self.selected_assignment_index:
            return "selected"
        if self.selected_assignment_index >= 0:
            return "current-dimmed"
        return "current"

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
        return self.grid_reference.cell_at(
            position.x() / self.zoom, position.y() / self.zoom,
            self.image.width(), self.image.height(),
        )

    def _update_preview(self, cell: tuple[int, int]) -> None:
        self.drag_end = cell
        if self.selection_mode == "rectangle":
            x, y, width, height = normalized_region(self.drag_start, self.drag_end)
            candidate = AssetAssignment(self.current_category, x, y, width, height)
        else:
            candidate = AssetAssignment.from_cells(self.current_category, self.drag_cells)
        self.preview_conflict = self.model.preview_conflict(candidate)
        self.update()

    @staticmethod
    def _cells_between(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
        """Return every grid cell crossed by a sampled drag segment."""
        x0, y0 = start
        x1, y1 = end
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error = dx - dy
        result = []
        while True:
            result.append((x0, y0))
            if (x0, y0) == (x1, y1):
                return result
            doubled = error * 2
            if doubled > -dy:
                error -= dy
                x0 += sx
            if doubled < dx:
                error += dx
                y0 += sy

    def _update_painted_cells(self, cell: tuple[int, int], remove: bool) -> None:
        start = self.last_drag_cell or cell
        path = self._cells_between(start, cell)
        if self.last_drag_remove is not None and self.last_drag_remove != remove:
            path = path[1:]
        for visited in path:
            if remove:
                self.drag_cells.discard(visited)
            else:
                self.drag_cells.add(visited)
        self.last_drag_cell = cell
        self.last_drag_remove = remove
        self.drag_end = cell
        if self.drag_cells:
            self._update_preview(cell)
        else:
            self.preview_conflict = None
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.editing_enabled:
            return
        cell = self._cell_at(event.position())
        if cell is None:
            return
        self.setFocus()
        self.drag_start = cell
        self.last_drag_cell = cell
        if self.selection_mode == "paint":
            self.drag_cells = set()
            self.last_drag_remove = None
            self._update_painted_cells(
                cell, self.control_pressed or bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
            )
        else:
            self._update_preview(cell)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        cell = self._cell_at(event.position())
        if self.drag_start is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            if cell != self.hover_cell:
                self.hover_cell = cell
                self.update()
            return
        if cell is not None:
            if self.selection_mode == "paint":
                self._update_painted_cells(
                    cell, self.control_pressed or bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
                )
            else:
                self._update_preview(cell)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.drag_start is None:
            return
        cell = self._cell_at(event.position())
        if cell is None:
            self.cancel_drag()
            return
        if self.selection_mode == "paint":
            self._update_painted_cells(
                cell, self.control_pressed or bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
            )
            cells = tuple(sorted(self.drag_cells, key=lambda value: (value[1], value[0])))
            self.cancel_drag()
            if cells:
                self.cells_selected.emit(cells)
        else:
            self._update_preview(cell)
            x, y, width, height = normalized_region(self.drag_start, self.drag_end)
            self.cancel_drag()
            self.region_selected.emit(x, y, width, height)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Control:
            self.control_pressed = True
        if event.key() == Qt.Key.Key_Escape and self.drag_start is not None:
            self.cancel_drag()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Control:
            self.control_pressed = False
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.control_pressed = False
        self.cancel_drag()
        super().focusOutEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_cell = None
        self.update()
        super().leaveEvent(event)

    def cancel_drag(self) -> None:
        self.drag_start = None
        self.drag_end = None
        self.drag_cells.clear()
        self.last_drag_cell = None
        self.last_drag_remove = None
        self.preview_conflict = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.fillRect(self.rect(), QColor(31, 34, 38))
        if self.image.isNull():
            return
        target = QRectF(0, 0, self.image.width() * self.zoom, self.image.height() * self.zoom)
        checker = max(4.0, 8.0 * self.zoom)
        rows = int(target.height() // checker) + 1
        columns = int(target.width() // checker) + 1
        for row_index in range(rows):
            for column_index in range(columns):
                painter.fillRect(
                    QRectF(column_index * checker, row_index * checker, checker, checker),
                    self.alpha_colors[(row_index + column_index) % 2],
                )
        painter.drawImage(target, self.image)
        cell_x = self.grid_reference.cell_width * self.zoom
        cell_y = self.grid_reference.cell_height * self.zoom
        origin_x = self.grid_reference.origin_x * self.zoom
        origin_y = self.grid_reference.origin_y * self.zoom
        painter.setPen(QPen(QColor(235, 240, 245, 76), max(1.0, self.zoom * 0.35)))
        column = 0
        while origin_x + column * cell_x <= target.width():
            x = origin_x + column * cell_x
            painter.drawLine(x, max(0.0, origin_y), x, target.height())
            column += 1
        row = 0
        while origin_y + row * cell_y <= target.height():
            y = origin_y + row * cell_y
            painter.drawLine(max(0.0, origin_x), y, target.width(), y)
            row += 1
        if self.hover_cell is not None and self.drag_start is None and self.editing_enabled:
            hover_x, hover_y = self.hover_cell
            left = origin_x + hover_x * cell_x
            top = origin_y + hover_y * cell_y
            hover_rect = QRectF(left + 1, top + 1, cell_x - 2, cell_y - 2)
            painter.setPen(QPen(QColor(255, 255, 255, 205), max(1.5, self.zoom)))
            painter.drawRect(hover_rect)
        painter.setFont(QFont("Segoe UI", max(7, round(9 * min(self.zoom, 2.0))), QFont.Weight.Bold))
        ordered_categories = sorted(
            self.model.assignments,
            key=lambda category: category == self.current_category,
        )
        for category in ordered_categories:
            assets = self.model.assignments[category]
            color = category_color(category)
            is_current = category == self.current_category
            for index, asset in enumerate(assets):
                visual_state = self.assignment_visual_state(category, index)
                left, top, right, bottom = self.grid_reference.pixel_rect(asset)
                rect = QRectF(left * self.zoom + 1, top * self.zoom + 1,
                              (right - left) * self.zoom - 2, (bottom - top) * self.zoom - 2)
                overlay = QColor(color)
                overlay_alpha = {
                    "selected": 62,
                    "current": 70,
                    "current-dimmed": 42,
                    "other-category": 24,
                }[visual_state]
                if visual_state == "selected":
                    overlay = QColor(55, 220, 240)
                overlay.setAlpha(overlay_alpha)
                border_width = (
                    max(3.0, self.zoom * 2.0) if visual_state == "selected"
                    else (max(2.2, self.zoom * 1.6) if is_current else max(1.2, self.zoom * 0.8))
                )
                border = QColor(62, 228, 247) if visual_state == "selected" else QColor(color)
                border.setAlpha(255 if visual_state == "selected" or is_current else 110)
                for cell in asset.selected_cells or ():
                    cell_left = origin_x + cell[0] * cell_x
                    cell_top = origin_y + cell[1] * cell_y
                    cell_rect = QRectF(cell_left + 1, cell_top + 1, cell_x - 2, cell_y - 2)
                    painter.fillRect(cell_rect, overlay)
                    painter.setPen(QPen(border, border_width))
                    painter.drawRect(cell_rect)
                if is_current:
                    shape = (
                        f"{asset.width_cells}×{asset.height_cells}"
                        if asset.is_rectangular else f"{asset.cell_count} cells"
                    )
                    label = f"#{index:02d}\n{shape}"
                    painter.setPen(QColor(20, 22, 25, 220))
                    painter.drawText(rect.translated(1, 1), Qt.AlignmentFlag.AlignCenter, label)
                    painter.setPen(QColor(105, 240, 252) if visual_state == "selected" else Qt.GlobalColor.white)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        if self.drag_start is not None and self.drag_end is not None:
            if self.selection_mode == "paint":
                if not self.drag_cells:
                    return
                preview = AssetAssignment.from_cells(self.current_category, self.drag_cells)
            else:
                x, y, width, height = normalized_region(self.drag_start, self.drag_end)
                preview = AssetAssignment(self.current_category, x, y, width, height)
            left, top, right, bottom = self.grid_reference.pixel_rect(preview)
            rect = QRectF(left * self.zoom + 1, top * self.zoom + 1,
                          (right - left) * self.zoom - 2, (bottom - top) * self.zoom - 2)
            color = QColor(230, 55, 55) if self.preview_conflict else category_color(self.current_category)
            fill = QColor(color)
            fill.setAlpha(85)
            painter.setPen(QPen(color, max(3.0, self.zoom * 2)))
            for cell in preview.selected_cells or ():
                cell_left = origin_x + cell[0] * cell_x
                cell_top = origin_y + cell[1] * cell_y
                cell_rect = QRectF(cell_left + 1, cell_top + 1, cell_x - 2, cell_y - 2)
                painter.fillRect(cell_rect, fill)
                painter.drawRect(cell_rect)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                (
                    f"{preview.width_cells}×{preview.height_cells}\n"
                    f"{preview.output_width_px}×{preview.output_height_px}"
                    if preview.is_rectangular else
                    f"{preview.cell_count} cells\n"
                    f"Bounds {preview.width_cells}×{preview.height_cells}"
                ),
            )


class MainWindow(QMainWindow):
    preview_data_changed = Signal()
    preview_theme_changed = Signal(str)
    preview_alpha_changed = Signal(object)

    def __init__(self, root: Path, preferences: Preferences | None = None) -> None:
        super().__init__()
        self.root = root
        self.preferences = preferences or Preferences.default()
        self.rules = load_categories(root / "tile_names.json")
        self.built_in_rules = list(self.rules)
        self.rule_by_name = {rule.name: rule for rule in self.rules}
        self.category_items: dict[str, QTreeWidgetItem] = {}
        self.model = AssignmentModel()
        self.source_image = None
        self.source_path: Path | None = None
        self.project_path: Path | None = None
        self.export_base_directory: Path | None = None
        self._settings_dirty = False
        self.layers: tuple[AsepriteLayer, ...] = ()
        self.layer_visibility: dict[str, bool] = {}
        self.document_grid: GridReference | None = None
        self.grid_reference = GridReference()
        self.layer_grid_origins: dict[str, tuple[int, int]] = {}
        self.layer_grid_manual_overrides: set[str] = set()
        self.layer_alignment_offsets: dict[str, tuple[int, int]] = {}
        self._updating_layers = False
        self._updating_grid_controls = False
        self._current_category = ""
        self._assignment_list_category = ""
        self.placement_preview_window: PlacementPreviewWindow | None = None
        self._locate_flash_timer = QTimer(self)
        self._locate_flash_timer.setInterval(180)
        self._locate_flash_timer.timeout.connect(self._advance_locate_flash)
        self._locate_flash_remaining = 0
        self._locate_flash_item: QListWidgetItem | None = None
        self.temporary_tags: list[str] = []
        self.source_revision = 0
        self.undo_stack = QUndoStack(self)
        self.undo_stack.cleanChanged.connect(self._sync_project_dirty_state)
        self.file_watcher = QFileSystemWatcher(self)
        self.file_watcher.fileChanged.connect(self._source_file_changed)
        self.auto_reload_timer = QTimer(self)
        self.auto_reload_timer.setSingleShot(True)
        self.auto_reload_timer.setInterval(400)
        self.auto_reload_timer.timeout.connect(self._auto_reload_source)
        self._auto_reload_retry_count = 0
        self.alignment_preview_timer = QTimer(self)
        self.alignment_preview_timer.setSingleShot(True)
        self.alignment_preview_timer.setInterval(150)
        self.alignment_preview_timer.timeout.connect(self._render_alignment_preview)
        self._alignment_edit_start_offsets: dict[str, tuple[int, int]] = {}
        self._alignment_last_good_offsets: dict[str, tuple[int, int]] = {}
        self.app_icon = application_icon(root)
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)
            app = QApplication.instance()
            if app is not None:
                app.setWindowIcon(self.app_icon)
        self.setWindowTitle(f"TileNamer v{__version__}")
        self.setMinimumSize(UiTokens.WINDOW_MIN_WIDTH, UiTokens.WINDOW_MIN_HEIGHT)
        self.resize(1400, 850)
        self._build_actions()
        self._build_ui()

    def _build_actions(self) -> None:
        def action(text: str, callback, tooltip: str = "") -> QAction:
            value = QAction(text, self)
            value.triggered.connect(callback)
            if tooltip:
                value.setToolTip(tooltip)
                value.setStatusTip(tooltip)
            return value

        self.open_action = action("이미지 열기…", self.open_source, "타일시트 이미지 또는 Aseprite 파일 열기")
        self.replace_action = action("리소스 교체…", self.replace_resource, "Assignment를 유지하고 Source 교체")
        self.save_project_action = action("프로젝트 저장", self.save_project)
        self.load_project_action = action("프로젝트 불러오기…", self.load_project)
        self.export_all_action = action("전체 내보내기…", self.export_all)
        self.export_current_action = action("현재 타일 내보내기…", self.export_current)
        self.export_other_action = action("다른 위치로 내보내기…", self.export_other_location)
        self.change_output_action = action(
            "출력 위치 변경…", self.choose_output_destination,
            "프로젝트의 기본 타일 이미지 출력 위치 변경",
        )
        self.open_output_action = action(
            "출력 폴더 열기", self.open_output_folder,
            "현재 TileImages 출력 폴더를 Explorer에서 열기",
        )
        self.open_output_action.setEnabled(False)
        self.exit_action = action("종료", self.close)

        self.undo_action = self.undo_stack.createUndoAction(self, "실행 취소")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.setToolTip("실행 취소 (Ctrl+Z)")
        self.redo_action = self.undo_stack.createRedoAction(self, "다시 실행")
        self.redo_action.setShortcuts((QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Shift+Z")))
        self.redo_action.setToolTip("다시 실행 (Ctrl+Y / Ctrl+Shift+Z)")

        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        self.light_theme_action = action("밝은 테마", lambda: self._set_theme("light"))
        self.dark_theme_action = action("어두운 테마", lambda: self._set_theme("dark"))
        for value in (self.light_theme_action, self.dark_theme_action):
            value.setCheckable(True)
            self.theme_action_group.addAction(value)

        self.alpha_actions: dict[str, QAction] = {}
        self.alpha_action_group = QActionGroup(self)
        self.alpha_action_group.setExclusive(True)
        for preset, text in (
            ("light", "밝게"), ("medium", "중간"), ("dark", "어둡게"),
            ("custom", "사용자 지정…"),
        ):
            value = action(text, lambda checked=False, name=preset: self._set_alpha_background(name))
            value.setCheckable(True)
            self.alpha_action_group.addAction(value)
            self.alpha_actions[preset] = value
        self.auto_reload_action = action(
            "Aseprite 자동 새로고침", self._auto_reload_toggled,
            "외부 Aseprite 저장 내용을 자동으로 반영",
        )
        self.auto_reload_action.setCheckable(True)
        self.placement_preview_action = action(
            "배치 미리보기", self.show_placement_preview,
            "현재 Assignment의 Auto Tile 배치 결과를 별도 창에서 확인",
        )
        self.reset_zoom_action = action("100%로 보기", lambda: self._set_zoom(1.0))

        self.help_action = action("TileNamer 사용법", self.show_help_dialog)
        self.shortcuts_action = action("단축키", self.show_shortcuts_dialog)
        self.about_action = action("TileNamer 정보", self.show_about_dialog)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("파일")
        file_menu.setObjectName("fileMenu")
        file_menu.addActions((self.open_action, self.replace_action))
        file_menu.addSeparator()
        file_menu.addActions((self.save_project_action, self.load_project_action))
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("내보내기")
        export_menu.setObjectName("exportMenu")
        export_menu.addActions((
            self.export_all_action, self.export_current_action, self.export_other_action,
        ))
        export_menu.addSeparator()
        export_menu.addActions((self.change_output_action, self.open_output_action))
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        edit_menu = self.menuBar().addMenu("편집")
        edit_menu.setObjectName("editMenu")
        edit_menu.addActions((self.undo_action, self.redo_action))

        view_menu = self.menuBar().addMenu("보기")
        view_menu.setObjectName("viewMenu")
        view_menu.addActions((self.light_theme_action, self.dark_theme_action))
        view_menu.addSeparator()
        alpha_menu = view_menu.addMenu("Alpha 배경")
        alpha_menu.setObjectName("alphaMenu")
        alpha_menu.addActions(tuple(self.alpha_actions.values()))
        view_menu.addSeparator()
        view_menu.addAction(self.auto_reload_action)
        view_menu.addSeparator()
        view_menu.addAction(self.placement_preview_action)
        view_menu.addSeparator()
        view_menu.addAction(self.reset_zoom_action)

        help_menu = self.menuBar().addMenu("도움말")
        help_menu.setObjectName("helpMenu")
        help_menu.addActions((self.help_action, self.shortcuts_action))
        help_menu.addSeparator()
        help_menu.addAction(self.about_action)

    def _build_ui(self) -> None:
        self._build_menus()
        root_widget = QWidget()
        root_widget.setObjectName("appRoot")
        outer = QVBoxLayout(root_widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.toolbar_widget = QWidget()
        self.toolbar_widget.setObjectName("topToolbar")
        toolbar = QHBoxLayout(self.toolbar_widget)
        self.toolbar_layout = toolbar
        toolbar.setContentsMargins(UiTokens.SPACE_MD, UiTokens.SPACE_SM, UiTokens.SPACE_MD, UiTokens.SPACE_SM)
        toolbar.setSpacing(UiTokens.SPACE_SM)

        def add_button(text: str, action_value: QAction, object_name: str = "") -> QPushButton:
            button = QPushButton(text)
            if object_name:
                button.setObjectName(object_name)
            button.clicked.connect(action_value.trigger)
            button.setToolTip(action_value.toolTip() or action_value.text().replace("…", ""))
            button.setEnabled(action_value.isEnabled())
            action_value.enabledChanged.connect(button.setEnabled)
            button.setMinimumHeight(UiTokens.CONTROL_HEIGHT)
            toolbar.addWidget(button)
            return button

        def add_separator() -> None:
            toolbar.addSpacing(3)
            separator = QFrame()
            separator.setObjectName("toolbarSeparator")
            separator.setFrameShape(QFrame.Shape.VLine)
            separator.setFixedHeight(24)
            toolbar.addWidget(separator)
            toolbar.addSpacing(3)

        self.open_button = add_button("이미지 열기", self.open_action, "primaryButton")
        self.replace_button = add_button("리소스 교체", self.replace_action)
        add_separator()
        self.save_project_button = add_button("프로젝트 저장", self.save_project_action)
        add_separator()
        self.export_all_button = add_button("전체 내보내기", self.export_all_action)
        self.export_current_button = add_button("현재 타일", self.export_current_action)
        self.export_current_button.setToolTip("현재 선택한 타일 종류만 내보내기")
        add_separator()
        self.undo_button = add_button("↶", self.undo_action, "historyButton")
        self.undo_button.setToolTip("실행 취소 (Ctrl+Z)")
        self.undo_button.setAccessibleName("실행 취소")
        self.undo_button.setFixedSize(40, 32)
        self.undo_button.setFont(QFont("Segoe UI Symbol", 15, QFont.Weight.DemiBold))
        self.redo_button = add_button("↷", self.redo_action, "historyButton")
        self.redo_button.setToolTip("다시 실행 (Ctrl+Y)")
        self.redo_button.setAccessibleName("다시 실행")
        self.redo_button.setFixedSize(40, 32)
        self.redo_button.setFont(QFont("Segoe UI Symbol", 15, QFont.Weight.DemiBold))
        toolbar.addStretch(1)
        self.zoom_out_action = QAction("축소", self)
        self.zoom_out_action.triggered.connect(lambda: self._set_zoom(self.canvas.zoom / 2))
        self.zoom_out_button = add_button("−", self.zoom_out_action)
        self.zoom_out_button.setObjectName("zoomControlButton")
        self.zoom_out_button.setAccessibleName("축소")
        self.zoom_out_button.setFixedWidth(34)
        self.zoom_label = QPushButton("100%")
        self.zoom_label.setObjectName("zoomLabel")
        self.zoom_label.setToolTip("100%로 재설정")
        self.zoom_label.clicked.connect(lambda: self._set_zoom(1.0))
        self.zoom_label.setFlat(False)
        self.zoom_label.setFixedWidth(58)
        toolbar.addWidget(self.zoom_label)
        self.zoom_in_action = QAction("확대", self)
        self.zoom_in_action.triggered.connect(lambda: self._set_zoom(self.canvas.zoom * 2))
        self.zoom_in_button = add_button("+", self.zoom_in_action)
        self.zoom_in_button.setObjectName("zoomControlButton")
        self.zoom_in_button.setAccessibleName("확대")
        self.zoom_in_button.setFixedWidth(34)
        self.toolbar_primary_buttons = (
            self.open_button, self.replace_button, self.save_project_button,
            self.export_all_button, self.export_current_button, self.undo_button, self.redo_button,
        )
        outer.addWidget(self.toolbar_widget)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        left = QWidget()
        left.setObjectName("sidePanel")
        left.setMinimumWidth(UiTokens.PANEL_MIN_LEFT)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(*(UiTokens.SPACE_MD,) * 4)
        left_layout.setSpacing(UiTokens.SPACE_SM)
        self.left_tabs = QTabWidget()
        self.left_tabs.setObjectName("leftTabs")
        tile_tab = QWidget()
        tile_layout = QVBoxLayout(tile_tab)
        tile_layout.setContentsMargins(0, 8, 0, 0)
        tile_layout.setSpacing(9)
        self.category_search = QLineEdit()
        self.category_search.setPlaceholderText("타일 이름 또는 prefix 검색")
        self.category_search.setClearButtonEnabled(True)
        self.category_search.setMinimumHeight(32)
        self.category_search.textChanged.connect(self.filter_categories)
        tile_layout.addWidget(self.category_search)
        self.category_tree = QTreeWidget()
        self.category_tree.setColumnCount(2)
        self.category_tree.setHeaderHidden(True)
        self.category_tree.header().setStretchLastSection(False)
        self.category_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.category_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.category_tree.header().resizeSection(1, 24)
        self.category_tree.setIndentation(14)
        self.category_tree.setUniformRowHeights(True)
        self.category_tree.currentItemChanged.connect(self._category_changed)
        tile_layout.addWidget(self.category_tree)
        self.add_tag_button = QPushButton("+ 임시 태그")
        self.add_tag_button.setObjectName("temporaryTagButton")
        self.add_tag_button.setToolTip("프로젝트 전용 임시 태그 추가")
        self.add_tag_button.clicked.connect(self.prompt_add_temporary_tag)
        self.add_tag_button.setMinimumHeight(UiTokens.CONTROL_HEIGHT)
        tile_layout.addWidget(self.add_tag_button)
        tag_actions = QHBoxLayout()
        self.rename_tag_button = QPushButton("이름 변경")
        self.rename_tag_button.setObjectName("temporaryTagButton")
        self.rename_tag_button.setToolTip("선택한 임시 태그 이름 변경")
        self.rename_tag_button.clicked.connect(self.prompt_rename_temporary_tag)
        self.delete_tag_button = QPushButton("삭제")
        self.delete_tag_button.setObjectName("temporaryTagButton")
        self.delete_tag_button.setToolTip("선택한 임시 태그 삭제")
        self.delete_tag_button.clicked.connect(self.delete_selected_temporary_tag)
        for button in (self.rename_tag_button, self.delete_tag_button):
            button.setMinimumHeight(UiTokens.CONTROL_HEIGHT)
            tag_actions.addWidget(button, 1)
        tile_layout.addLayout(tag_actions)
        self.temporary_tag_buttons = (
            self.add_tag_button, self.rename_tag_button, self.delete_tag_button,
        )
        self.left_tabs.addTab(tile_tab, "타일 종류")
        self._populate_category_tree()
        layer_tab = QWidget()
        layer_layout = QVBoxLayout(layer_tab)
        layer_layout.setContentsMargins(0, 8, 0, 0)
        grid_label = QLabel("격자 기준")
        grid_label.setObjectName("assignmentCount")
        layer_layout.addWidget(grid_label)
        self.grid_reference_combo = QComboBox()
        self.grid_reference_combo.currentIndexChanged.connect(self._grid_reference_changed)
        layer_layout.addWidget(self.grid_reference_combo)
        grid_separator = QFrame()
        grid_separator.setFrameShape(QFrame.Shape.HLine)
        grid_separator.setObjectName("sectionSeparator")
        layer_layout.addWidget(grid_separator)
        layer_list_label = QLabel("레이어")
        layer_list_label.setObjectName("assignmentCount")
        layer_layout.addWidget(layer_list_label)
        self.layer_tree = QTreeWidget()
        self.layer_tree.setColumnCount(2)
        self.layer_tree.setHeaderLabels(["Layer", "Grid"])
        self.layer_tree.header().setStretchLastSection(False)
        self.layer_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.layer_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.layer_tree.header().resizeSection(1, 82)
        self.layer_tree.setIndentation(14)
        self.layer_tree.setUniformRowHeights(True)
        self.layer_tree.itemChanged.connect(self._layer_item_changed)
        self.layer_tree.currentItemChanged.connect(self._layer_selection_changed)
        layer_layout.addWidget(self.layer_tree)
        self.advanced_layer_settings = QGroupBox("고급 정렬 설정")
        self.advanced_layer_settings.setCheckable(True)
        self.advanced_layer_settings.setChecked(False)
        advanced_layout = QVBoxLayout(self.advanced_layer_settings)
        advanced_layout.setContentsMargins(8, 8, 8, 8)
        advanced_layout.setSpacing(6)
        layer_grid_title = QLabel("수동 Grid fallback")
        layer_grid_title.setObjectName("panelSectionTitle")
        advanced_layout.addWidget(layer_grid_title)
        self.selected_grid_layer_name = QLabel("레이어를 선택하세요")
        self.selected_grid_layer_name.setObjectName("selectedLayerName")
        self.selected_grid_layer_name.setToolTip("격자 원점을 수정할 레이어를 선택하세요.")
        advanced_layout.addWidget(self.selected_grid_layer_name)
        layer_grid_controls = QGridLayout()
        layer_grid_controls.setHorizontalSpacing(8)
        layer_grid_controls.setVerticalSpacing(5)
        layer_grid_controls.addWidget(QLabel("Grid X"), 0, 0)
        self.layer_grid_x = QSpinBox()
        self.layer_grid_x.setRange(-31, 31)
        self.layer_grid_x.setSuffix(" px")
        self.layer_grid_x.setToolTip("선택 Layer의 32×32 격자 X 원점입니다. Layer 픽셀은 이동하지 않습니다.")
        self.layer_grid_x.valueChanged.connect(self._layer_grid_origin_edited)
        layer_grid_controls.addWidget(self.layer_grid_x, 0, 1)
        layer_grid_controls.addWidget(QLabel("Grid Y"), 1, 0)
        self.layer_grid_y = QSpinBox()
        self.layer_grid_y.setRange(-31, 31)
        self.layer_grid_y.setSuffix(" px")
        self.layer_grid_y.setToolTip("선택 Layer의 32×32 격자 Y 원점입니다. Layer 픽셀은 이동하지 않습니다.")
        self.layer_grid_y.valueChanged.connect(self._layer_grid_origin_edited)
        layer_grid_controls.addWidget(self.layer_grid_y, 1, 1)
        advanced_layout.addLayout(layer_grid_controls)
        self.layer_grid_reset = QPushButton("격자값 초기화")
        self.layer_grid_reset.setToolTip("일반 Layer는 (0,0), Tilemap은 native Grid 값으로 복원합니다.")
        self.layer_grid_reset.clicked.connect(self._reset_layer_grid_origin)
        advanced_layout.addWidget(self.layer_grid_reset)
        alignment_separator = QFrame()
        alignment_separator.setFrameShape(QFrame.Shape.HLine)
        alignment_separator.setObjectName("sectionSeparator")
        advanced_layout.addWidget(alignment_separator)
        alignment_title = QLabel("레이어 위치 보정")
        alignment_title.setObjectName("panelSectionTitle")
        advanced_layout.addWidget(alignment_title)
        self.selected_layer_name = QLabel("레이어를 선택하세요")
        self.selected_layer_name.setObjectName("selectedLayerName")
        self.selected_layer_name.setToolTip("정렬값을 수정할 레이어를 선택하세요.")
        advanced_layout.addWidget(self.selected_layer_name)
        offset_grid = QGridLayout()
        offset_grid.setHorizontalSpacing(8)
        offset_grid.setVerticalSpacing(5)
        offset_grid.addWidget(QLabel("보정 X"), 0, 0)
        self.layer_offset_x = QSpinBox()
        self.layer_offset_x.setRange(-31, 31)
        self.layer_offset_x.setSuffix(" px")
        self.layer_offset_x.setToolTip("양수는 레이어를 왼쪽으로, 음수는 오른쪽으로 이동합니다.")
        self.layer_offset_x.valueChanged.connect(self._layer_offset_edited)
        self.layer_offset_x.editingFinished.connect(self._alignment_edit_finished)
        offset_grid.addWidget(self.layer_offset_x, 0, 1)
        offset_grid.addWidget(QLabel("보정 Y"), 1, 0)
        self.layer_offset_y = QSpinBox()
        self.layer_offset_y.setRange(-31, 31)
        self.layer_offset_y.setSuffix(" px")
        self.layer_offset_y.setToolTip("양수는 레이어를 위쪽으로, 음수는 아래쪽으로 이동합니다.")
        self.layer_offset_y.valueChanged.connect(self._layer_offset_edited)
        self.layer_offset_y.editingFinished.connect(self._alignment_edit_finished)
        offset_grid.addWidget(self.layer_offset_y, 1, 1)
        advanced_layout.addLayout(offset_grid)
        self.layer_offset_reset = QPushButton("정렬값 초기화")
        self.layer_offset_reset.clicked.connect(self._reset_layer_offset)
        advanced_layout.addWidget(self.layer_offset_reset)
        self.auto_alignment_button = QPushButton("기준 격자에 자동 맞춤")
        self.auto_alignment_button.setToolTip("신뢰할 수 있는 32×32 문서/Tilemap 격자 메타데이터만 사용합니다.")
        self.auto_alignment_button.clicked.connect(self.prompt_auto_alignment)
        advanced_layout.addWidget(self.auto_alignment_button)
        self.advanced_layer_settings.toggled.connect(self._set_advanced_layer_settings_visible)
        self._set_advanced_layer_settings_visible(False)
        layer_layout.addWidget(self.advanced_layer_settings)
        self.left_tabs.addTab(layer_tab, "레이어")
        left_layout.addWidget(self.left_tabs)
        self._populate_layer_tree()
        self._populate_grid_references()
        self.main_splitter.addWidget(left)

        self.canvas = TileCanvas()
        self.canvas.region_selected.connect(self.assign_region)
        self.canvas.cells_selected.connect(self.assign_cells)
        self.viewport_scroll = ViewportScrollArea()
        self.viewport_scroll.setObjectName("viewportScroll")
        self.viewport_scroll.setWidget(self.canvas)
        self.viewport_scroll.setWidgetResizable(False)
        self.viewport_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.viewport_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.viewport_scroll.zoom_requested.connect(self._zoom_by_at)
        self.viewport_empty = QLabel("이미지를 열어 타일 작업을 시작하세요\n\nPNG / JPG / Aseprite 지원")
        self.viewport_empty.setObjectName("viewportEmpty")
        self.viewport_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.viewport_stack = SourceDropStack()
        self.viewport_stack.setObjectName("viewportStack")
        self.viewport_stack.addWidget(self.viewport_scroll)
        self.viewport_stack.addWidget(self.viewport_empty)
        self.viewport_stack.setCurrentWidget(self.viewport_empty)
        self.viewport_stack.files_dropped.connect(lambda paths: self._request_source(paths[0]))
        self.viewport_stack.drop_rejected.connect(self._show_drop_rejection)
        self.viewport_stack.drag_active_changed.connect(self._set_drop_active)
        center = QWidget()
        center.setObjectName("viewportPanel")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        self.selection_control = QFrame()
        self.selection_control.setObjectName("selectionControl")
        selection_layout = QHBoxLayout(self.selection_control)
        selection_layout.setContentsMargins(
            UiTokens.SPACE_XS, 2, UiTokens.SPACE_XS, 2,
        )
        selection_layout.setSpacing(0)
        self.selection_mode_combo = QComboBox(center)
        self.selection_mode_combo.addItem("사각형", "rectangle")
        self.selection_mode_combo.addItem("셀 그리기", "paint")
        self.selection_mode_combo.setToolTip(
            "사각형은 드래그 범위 전체를, 셀 그리기는 지나간 셀만 선택합니다. "
            "셀 그리기 중 Ctrl을 누르면 지나가는 셀을 제거합니다."
        )
        self.selection_mode_combo.currentIndexChanged.connect(self._selection_mode_changed)
        self.selection_mode_combo.hide()
        self.selection_mode_segment = QWidget()
        self.selection_mode_segment.setObjectName("selectionModeSegment")
        segment_layout = QHBoxLayout(self.selection_mode_segment)
        segment_layout.setContentsMargins(0, 0, 0, 0)
        segment_layout.setSpacing(0)
        self.selection_mode_group = QButtonGroup(self)
        self.selection_mode_group.setExclusive(True)
        self.rectangle_mode_button = QPushButton("사각형")
        self.paint_mode_button = QPushButton("셀 그리기")
        self.selection_mode_buttons = {
            "rectangle": self.rectangle_mode_button,
            "paint": self.paint_mode_button,
        }
        for mode, button in self.selection_mode_buttons.items():
            button.setCheckable(True)
            button.setProperty("segment", True)
            button.setMinimumHeight(UiTokens.CONTROL_HEIGHT)
            button.clicked.connect(
                lambda checked=False, value=mode: self._set_selection_mode(value)
            )
            self.selection_mode_group.addButton(button)
            segment_layout.addWidget(button)
        self.rectangle_mode_button.setChecked(True)
        self.selection_mode_segment.setToolTip(self.selection_mode_combo.toolTip())
        selection_layout.addWidget(self.selection_mode_segment)
        self.center_tool_row = QWidget()
        self.center_tool_row.setObjectName("centerToolRow")
        self.center_tool_row.setFixedHeight(38)
        center_tools = QHBoxLayout(self.center_tool_row)
        center_tools.setContentsMargins(UiTokens.SPACE_SM, 0, UiTokens.SPACE_SM, 0)
        center_tools.setSpacing(0)
        center_tools.addStretch(1)
        center_tools.addWidget(self.selection_control)
        center_layout.addWidget(self.center_tool_row)
        center_layout.addWidget(self.viewport_stack, 1)
        self.main_splitter.addWidget(center)

        right = QWidget()
        right.setObjectName("sidePanel")
        right.setMinimumWidth(UiTokens.PANEL_MIN_RIGHT)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(*(UiTokens.SPACE_MD,) * 4)
        right_layout.setSpacing(UiTokens.SPACE_SM)
        right_header = QHBoxLayout()
        right_title = QLabel("등록된 타일")
        right_title.setObjectName("panelTitle")
        right_header.addWidget(right_title)
        right_header.addStretch(1)
        self.total_assignment_label = QLabel("0")
        self.total_assignment_label.setObjectName("headerCount")
        right_header.addWidget(self.total_assignment_label)
        right_layout.addLayout(right_header)
        self.current_label = QLabel("카테고리를 선택하세요")
        self.current_label.setObjectName("assignmentCategory")
        right_layout.addWidget(self.current_label)
        self.assignment_count_label = QLabel("0개")
        self.assignment_count_label.setObjectName("assignmentCount")
        right_layout.addWidget(self.assignment_count_label)
        self.assignment_list = QListWidget()
        self.assignment_list.setObjectName("assignmentList")
        self.assignment_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.assignment_list.setSpacing(2)
        self.assignment_list.setIconSize(QSize(48, 48))
        self.assignment_list.setItemDelegate(LocateFlashDelegate(self.assignment_list))
        self.assignment_list.currentRowChanged.connect(self._assignment_row_changed)
        self.assignment_empty = QLabel("등록된 타일이 없습니다\n\n현재 타일 종류에서\n캔버스의 영역을 선택하세요")
        self.assignment_empty.setObjectName("assignmentEmpty")
        self.assignment_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.assignment_empty.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_layout.addWidget(self.assignment_empty)
        right_layout.addWidget(self.assignment_list)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.right_control_buttons = []
        for text, callback, object_name in (
            ("↑ 위로", lambda: self.reorder(-1), ""),
            ("↓ 아래로", lambda: self.reorder(1), ""),
            ("제거", self.remove_selected, "removeButton"),
        ):
            button = QPushButton(text)
            if object_name:
                button.setObjectName(object_name)
            button.setMinimumHeight(UiTokens.CONTROL_HEIGHT)
            button.clicked.connect(callback)
            controls.addWidget(button, 1)
            self.right_control_buttons.append(button)
        right_layout.addLayout(controls)

        output_separator = QFrame()
        output_separator.setFrameShape(QFrame.Shape.HLine)
        output_separator.setObjectName("sectionSeparator")
        right_layout.addWidget(output_separator)
        self.output_section = QWidget()
        self.output_section.setObjectName("outputSection")
        output_layout = QVBoxLayout(self.output_section)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(UiTokens.SPACE_XS)
        output_header = QHBoxLayout()
        output_title = QLabel("출력")
        output_title.setObjectName("panelSectionTitle")
        output_header.addWidget(output_title)
        output_header.addStretch(1)
        self.output_summary_label = QLabel("0 PNG")
        self.output_summary_label.setObjectName("outputSummary")
        output_header.addWidget(self.output_summary_label)
        output_layout.addLayout(output_header)
        self.output_path_label = MiddleElideLabel("출력 위치가 지정되지 않았습니다.")
        self.output_path_label.setObjectName("outputPath")
        output_layout.addWidget(self.output_path_label)
        output_actions = QHBoxLayout()
        output_actions.setSpacing(UiTokens.SPACE_SM)
        self.change_output_button = QPushButton("출력 위치 선택")
        self.change_output_button.clicked.connect(self.change_output_action.trigger)
        self.open_output_button = QPushButton("폴더 열기")
        self.open_output_button.clicked.connect(self.open_output_action.trigger)
        self.open_output_button.setEnabled(False)
        self.open_output_action.enabledChanged.connect(self.open_output_button.setEnabled)
        for button in (self.change_output_button, self.open_output_button):
            button.setMinimumHeight(UiTokens.CONTROL_HEIGHT)
            output_actions.addWidget(button, 1)
        output_layout.addLayout(output_actions)
        right_layout.addWidget(self.output_section)
        self.main_splitter.addWidget(right)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([260, 820, 320])
        outer.addWidget(self.main_splitter, 1)

        self.setStyleSheet(
            """
            #appRoot { background: #dfe3e7; }
            #topToolbar { background: #f1f3f5; border-bottom: 1px solid #969fa8; }
            #centerToolRow { background: #e9ecef; border-bottom: 1px solid #9da5ae; }
            QPushButton {
                min-height: 32px; padding: 0 11px; border-radius: 4px;
                color: #2f353b; background: #f3f5f6; border: 1px solid #9da5ae;
            }
            QPushButton:hover { background: #e2edf7; border-color: #568fc1; }
            QPushButton:pressed { background: #ccddea; border-color: #4c82b1; }
            QPushButton:disabled { color: #9299a0; background: #e4e7e9; border-color: #bcc2c7; }
            #topToolbar QPushButton#primaryButton { font-weight: 600; }
            #topToolbar QPushButton#historyButton { padding: 0; color: #303940; background: #eef1f3; border: 1px solid #929ba4; }
            #topToolbar QPushButton#historyButton:disabled { color: #8c949b; background: #e1e4e7; border-color: #aeb5bb; }
            #topToolbar QPushButton#zoomControlButton, #topToolbar QPushButton#zoomLabel {
                color: #30373d; background: #eef1f3; border: 1px solid #929ba4;
            }
            #toolbarSeparator { color: #929ba4; }
            #zoomLabel { font-weight: 600; }
            #sidePanel { background: #eef0f2; }
            #panelTitle { color: #25292e; font-size: 15px; font-weight: 700; }
            #headerCount { color: #697079; font-size: 15px; font-weight: 700; }
            #assignmentCategory { color: #30343a; font-weight: 600; }
            #assignmentCount { color: #697079; }
            #panelSectionTitle { color: #34393f; font-weight: 700; }
            #selectedLayerName { color: #2e6088; font-weight: 600; padding: 2px 0 4px 0; }
            #sectionSeparator { color: #a7afb7; max-height: 1px; margin-top: 3px; }
            QLineEdit, QComboBox, QSpinBox {
                min-height: 32px; color: #2f353b; background: #ffffff;
                border: 1px solid #929ba4; border-radius: 4px;
            }
            QLineEdit { padding: 4px 8px; }
            QLineEdit:focus { border-color: #4d86c6; }
            QTreeWidget, QListWidget { border: 1px solid #a5adb5; background: #f8f9fa; outline: 0; }
            QTabWidget::pane { border: 1px solid #a5adb5; }
            QTreeWidget::item { min-height: 27px; padding: 1px 3px; }
            QTreeWidget::item:hover, QListWidget::item:hover { background: #e8f0fa; }
            QTreeWidget::item:selected, QListWidget::item:selected { background: #3979b9; color: white; }
            QListWidget::item { min-height: 66px; padding: 6px 8px; border-bottom: 1px solid #e1e4e7; }
            QListWidget#assignmentList::item:selected {
                background: #3274ad; color: white;
                border-left: 4px solid #46e1f5;
            }
            #assignmentEmpty { color: #727981; background: #f2f3f4; }
            QPushButton#removeButton { color: #a22d2d; }
            #selectionControl { background: #e4e8eb; border: 1px solid #949da6; border-radius: 5px; }
            #selectionModeSegment { border: 1px solid #949da6; border-radius: 4px; }
            #selectionModeSegment QPushButton { border: 0; border-radius: 0; padding: 0 14px; background: #e5e9ec; color: #30363c; }
            #selectionModeSegment QPushButton:checked { background: #287fb4; color: white; font-weight: 600; }
            #selectionModeSegment QPushButton:hover:!checked { background: #dce8f1; }
            #selectionModeSegment QPushButton:disabled { background: #eceeef; color: #a0a6ac; }
            QSplitter::handle { background: #969fa8; width: 2px; }
            QSplitter::handle:hover { background: #4d86c6; }
            QScrollArea#viewportScroll { background: #24272b; }
            QScrollArea#viewportScroll > QWidget > QWidget { background: #24272b; }
            #viewportStack { background: #24272b; border: 1px solid #737b84; }
            #viewportEmpty { color: #aeb4bb; background: #24272b; border: 0; font-size: 13px; }
            #viewportEmpty[dropActive="true"] { color: #ffffff; background: #31587d; border: 2px solid #77b7ee; }
            #outputSection { background: transparent; }
            #outputPath { color: #40464d; font-weight: 600; }
            #outputSummary { color: #6d747c; font-size: 11px; }
            QStatusBar { background: #eef0f2; border-top: 1px solid #969fa8; }
            QStatusBar QLabel { color: #444a51; padding: 3px 8px; }
            """
        )
        self._base_stylesheet = self.styleSheet()
        self._apply_theme()
        self._sync_preference_controls()
        self.warning = QLabel("이미지를 열어 주세요.")
        self.setCentralWidget(root_widget)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addWidget(self.warning, 1)
        if self.category_items:
            first = next(iter(self.category_items.values()))
            self.category_tree.setCurrentItem(first)

    def _apply_theme(self) -> None:
        dark = self.preferences.theme == "dark"
        dark_stylesheet = """
            #appRoot, #sidePanel { background: #25282c; color: #e2e5e8; }
            #topToolbar { background: #2c3035; border-bottom-color: #454b52; }
            #centerToolRow { background: #292e33; border-bottom-color: #4d555d; }
            QPushButton, QComboBox, QSpinBox, QLineEdit {
                background: #343940; color: #e4e7ea; border: 1px solid #555c64;
            }
            QPushButton:hover { background: #414951; border-color: #687582; }
            QPushButton:pressed { background: #293038; }
            QPushButton:disabled, QSpinBox:disabled { color: #777e86; background: #2a2e33; border-color: #3e444a; }
            #topToolbar QPushButton#historyButton {
                color: #f0f3f5; background: #343a40; border-color: #59626b;
            }
            #topToolbar QPushButton#historyButton:disabled {
                color: #9ca4ac; background: #2f3439; border-color: #4e565e;
            }
            #topToolbar QPushButton#zoomControlButton, #topToolbar QPushButton#zoomLabel {
                color: #f2f4f6; background: #343a40; border-color: #5c6570;
            }
            #topToolbar QPushButton#zoomControlButton:hover, #topToolbar QPushButton#zoomLabel:hover {
                color: #ffffff; background: #414951; border-color: #74808b;
            }
            #panelTitle, #panelSectionTitle, #assignmentCategory, #outputPath { color: #eef1f3; }
            #headerCount { color: #aab0b7; }
            #assignmentCount { color: #aab0b7; }
            #selectedLayerName { color: #70cfee; }
            QTreeWidget, QListWidget { background: #2b2f34; color: #dfe3e7; border-color: #4b5158; }
            QHeaderView::section { background: #343940; color: #d8dce0; border-color: #4c5259; }
            QTabWidget::pane { border-color: #4a5057; }
            QTabBar::tab { background: #30343a; color: #bec4ca; padding: 6px 8px; }
            QTabBar::tab:selected { background: #3a4047; color: white; }
            #assignmentEmpty { color: #aeb4bb; border-color: #4b5158; background: #2b2f34; }
            #selectionControl { background: #30363b; border-color: #555d65; }
            #selectionModeSegment { border-color: #59616a; }
            #selectionModeSegment QPushButton { color: #d9dde1; background: #343a40; }
            #selectionModeSegment QPushButton:checked { background: #2786ad; color: white; }
            #selectionModeSegment QPushButton:hover:!checked { background: #3a424a; }
            #selectionModeSegment QPushButton:disabled { background: #292e33; color: #6f767d; }
            #outputSection { background: transparent; }
            #outputSummary { color: #aab0b7; }
            #sectionSeparator { color: #474d54; }
            QStatusBar { background: #2d3136; border-top-color: #484e55; }
            QStatusBar QLabel { color: #d2d6da; }
            QMenuBar { background: #2c3035; color: #e2e5e8; }
            QMenuBar::item:selected { background: #41474e; }
            QMenu { background: #30343a; color: #e2e5e8; border: 1px solid #50565d; }
            QMenu::item:selected { background: #3979b9; color: white; }
            QDialog, QTextBrowser { background: #25282c; color: #e2e5e8; }
        """ if dark else ""
        self.setStyleSheet(self._base_stylesheet + dark_stylesheet)
        with QSignalBlocker(self.light_theme_action), QSignalBlocker(self.dark_theme_action):
            self.light_theme_action.setChecked(not dark)
            self.dark_theme_action.setChecked(dark)
        self.preview_theme_changed.emit(self.preferences.theme)

    def _set_theme(self, theme: str) -> None:
        self.preferences.theme = theme
        self._apply_theme()

    def toggle_theme(self) -> None:
        self._set_theme("light" if self.preferences.theme == "dark" else "dark")

    def _sync_preference_controls(self) -> None:
        for preset, action in self.alpha_actions.items():
            with QSignalBlocker(action):
                action.setChecked(preset == self.preferences.alpha_background)
        with QSignalBlocker(self.auto_reload_action):
            self.auto_reload_action.setChecked(self.preferences.auto_reload_aseprite)
        self._apply_alpha_background()

    def _set_alpha_background(self, preset: str) -> None:
        if preset == "custom":
            color = QColorDialog.getColor(QColor(self.preferences.custom_alpha_color), self, "Alpha 배경 기준 색상")
            if not color.isValid():
                self._sync_preference_controls()
                return
            self.preferences.custom_alpha_color = color.name()
        self.preferences.alpha_background = preset
        self._apply_alpha_background()

    def _apply_alpha_background(self) -> None:
        colors = self.preferences.alpha_colors()
        self.canvas.set_alpha_colors(colors)
        self._refresh_assignment_icons(self.assignment_list.currentRow())
        self.preview_alpha_changed.emit(colors)

    def _placement_preview_source(self) -> PlacementPreviewSource:
        return PlacementPreviewSource(
            self.model, self.source_image, self.grid_reference,
            self.preferences.alpha_colors(),
        )

    def show_placement_preview(self) -> None:
        if self.placement_preview_window is None:
            self.placement_preview_window = PlacementPreviewWindow(
                self, self._placement_preview_source, self.app_icon,
                self.preferences.theme,
            )
            self.preview_data_changed.connect(
                self.placement_preview_window.schedule_refresh
            )
            self.preview_theme_changed.connect(
                self.placement_preview_window.apply_theme
            )
            self.preview_alpha_changed.connect(
                self.placement_preview_window.update_alpha
            )
            self.placement_preview_window.assignment_locate_requested.connect(
                self._locate_preview_assignment
            )
            self.placement_preview_window.missing_role_locate_requested.connect(
                self._locate_preview_missing_role
            )
        self.placement_preview_window.show()
        self.placement_preview_window.raise_()
        self.placement_preview_window.activateWindow()

    def _select_preview_category(self, category: str) -> bool:
        item = self.category_items.get(category)
        if item is None:
            self.statusBar().showMessage(f"지원되지 않은 배치 역할입니다: {category}", 2200)
            return False
        self.category_tree.setCurrentItem(item)
        self.category_tree.scrollToItem(
            item, QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        return True

    def _locate_preview_assignment(self, identity: AssignmentIdentity) -> None:
        if not self._select_preview_category(identity.category):
            return
        row = next((index for index, assignment in enumerate(
            self.model.assets(identity.category)
        ) if assignment.selected_cells == identity.selected_cells), -1)
        if row < 0:
            self.statusBar().showMessage("해당 타일이 변경되었거나 삭제되었습니다.", 2200)
            if self.placement_preview_window is not None:
                self.placement_preview_window.schedule_refresh()
            return
        self.assignment_list.setCurrentRow(row)
        item = self.assignment_list.item(row)
        if item is not None:
            self.assignment_list.scrollToItem(
                item, QAbstractItemView.ScrollHint.PositionAtCenter,
            )
            self._start_locate_flash(item)
        rule = self.rule_by_name.get(identity.category)
        filename = rule.filename(row) if rule is not None else f"{identity.category}_{row:02d}.png"
        self.statusBar().showMessage(f"이 타일입니다!  {filename}", 2200)
        self.show()
        self.raise_()
        self.activateWindow()

    def _locate_preview_missing_role(self, category: str) -> None:
        if not self._select_preview_category(category):
            return
        self._stop_locate_flash()
        self.assignment_list.setCurrentRow(-1)
        self.statusBar().showMessage(f"이 타일이 필요합니다: {category}", 2200)
        self.show()
        self.raise_()
        self.activateWindow()

    def _start_locate_flash(self, item: QListWidgetItem) -> None:
        self._stop_locate_flash()
        self._locate_flash_item = item
        self._locate_flash_remaining = 3
        item.setData(ROLE_LOCATE_FLASH, True)
        self.assignment_list.viewport().update()
        self._locate_flash_timer.start()

    def _advance_locate_flash(self) -> None:
        item = self._locate_flash_item
        if item is None or self.assignment_list.row(item) < 0:
            self._stop_locate_flash()
            return
        current = bool(item.data(ROLE_LOCATE_FLASH))
        item.setData(ROLE_LOCATE_FLASH, not current)
        self._locate_flash_remaining -= 1
        self.assignment_list.viewport().update()
        if self._locate_flash_remaining <= 0:
            self._stop_locate_flash()

    def _stop_locate_flash(self) -> None:
        self._locate_flash_timer.stop()
        if self._locate_flash_item is not None:
            self._locate_flash_item.setData(ROLE_LOCATE_FLASH, False)
        self._locate_flash_item = None
        self._locate_flash_remaining = 0
        if hasattr(self, "assignment_list"):
            self.assignment_list.viewport().update()

    def _auto_reload_toggled(self, checked: bool) -> None:
        self.preferences.auto_reload_aseprite = checked
        self._configure_source_watcher()

    def _text_dialog(self, title: str, html: str, size: tuple[int, int]) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setWindowIcon(self.app_icon)
        dialog.setModal(False)
        dialog.resize(*size)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setObjectName("helpBrowser")
        browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        browser.setHtml(html)
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        return dialog

    def create_help_dialog(self) -> QDialog:
        return self._text_dialog("TileNamer 사용법", HELP_HTML, (760, 640))

    def create_shortcuts_dialog(self) -> QDialog:
        return self._text_dialog("TileNamer 단축키", SHORTCUTS_HTML, (540, 380))

    def create_about_dialog(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle("TileNamer 정보")
        dialog.setWindowIcon(self.app_icon)
        dialog.resize(420, 330)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(10)
        icon_label = QLabel()
        icon_label.setObjectName("aboutIcon")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not self.app_icon.isNull():
            icon_label.setPixmap(self.app_icon.pixmap(112, 112))
        layout.addWidget(icon_label)
        title = QLabel("TileNamer")
        title.setObjectName("panelTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        version = QLabel(f"Version {__version__}")
        version.setObjectName("aboutVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)
        description = QLabel("Tile Asset Naming / Export Tool")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        return dialog

    def show_help_dialog(self) -> None:
        self._help_dialog = self.create_help_dialog()
        self._help_dialog.show()

    def show_shortcuts_dialog(self) -> None:
        self._shortcuts_dialog = self.create_shortcuts_dialog()
        self._shortcuts_dialog.show()

    def show_about_dialog(self) -> None:
        self._about_dialog = self.create_about_dialog()
        self._about_dialog.show()

    def _set_zoom(self, zoom: float, anchor: QPoint | None = None) -> None:
        old_zoom = self.canvas.zoom
        old_zoom = old_zoom if old_zoom else 1.0
        if anchor is None:
            anchor = self.viewport_scroll.viewport().rect().center()
        canvas_point = self.canvas.mapFrom(self.viewport_scroll.viewport(), anchor)
        image_x, image_y = canvas_point.x() / old_zoom, canvas_point.y() / old_zoom
        self.canvas.set_zoom(zoom)
        new_canvas_point = QPoint(round(image_x * self.canvas.zoom), round(image_y * self.canvas.zoom))
        new_viewport_point = self.canvas.mapTo(self.viewport_scroll.viewport(), new_canvas_point)
        delta = new_viewport_point - anchor
        horizontal = self.viewport_scroll.horizontalScrollBar()
        vertical = self.viewport_scroll.verticalScrollBar()
        horizontal.setValue(horizontal.value() + delta.x())
        vertical.setValue(vertical.value() + delta.y())
        self.zoom_label.setText(f"{round(self.canvas.zoom * 100)}%")

    def _selection_mode_changed(self, index: int) -> None:
        mode = self.selection_mode_combo.itemData(index) or "rectangle"
        self.canvas.set_selection_mode(str(mode))
        button = self.selection_mode_buttons.get(str(mode))
        if button is not None:
            button.setChecked(True)

    def _set_selection_mode(self, mode: str) -> None:
        index = self.selection_mode_combo.findData(mode)
        if index >= 0:
            self.selection_mode_combo.setCurrentIndex(index)

    @property
    def project_dirty(self) -> bool:
        return self._settings_dirty or not self.undo_stack.isClean()

    def _sync_project_dirty_state(self, clean: bool | None = None) -> None:
        suffix = " *" if self.project_dirty else ""
        self.setWindowTitle(f"TileNamer v{__version__}{suffix}")

    def _mark_settings_dirty(self) -> None:
        self._settings_dirty = True
        self._sync_project_dirty_state()

    def _zoom_by_at(self, factor: float, anchor: QPoint) -> None:
        self._set_zoom(self.canvas.zoom * factor, anchor)

    def _set_drop_active(self, active: bool) -> None:
        if active:
            self.viewport_empty.setText("이미지 파일을 놓아 열기")
            self.viewport_empty.setProperty("dropActive", True)
            self.viewport_empty.style().unpolish(self.viewport_empty)
            self.viewport_empty.style().polish(self.viewport_empty)
            self.viewport_stack.setCurrentWidget(self.viewport_empty)
            return
        self.viewport_empty.setProperty("dropActive", False)
        self.viewport_empty.setText("이미지를 열어 타일 작업을 시작하세요\n\nPNG / JPG / Aseprite 지원")
        self.viewport_empty.style().unpolish(self.viewport_empty)
        self.viewport_empty.style().polish(self.viewport_empty)
        self.viewport_stack.setCurrentWidget(
            self.viewport_scroll if self.source_image is not None else self.viewport_empty
        )

    def _show_drop_rejection(self, message: str) -> None:
        QMessageBox.information(self, "이미지 열기", message)

    @staticmethod
    def _leaf_label(name: str) -> str:
        return name.split("_", 1)[1] if "_" in name else name

    @staticmethod
    def _display_label(value: str) -> str:
        words = value.replace("_", " ")
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", words)

    @classmethod
    def _display_category(cls, category: str) -> str:
        return cls._display_label(category) if category else "카테고리를 선택하세요"

    def _group_item(self, parent: QTreeWidget | QTreeWidgetItem, text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text, ""])
        item.setData(0, ROLE_SEARCH, text.casefold())
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        return item

    def _populate_category_tree(self) -> None:
        self.category_tree.clear()
        self.category_items.clear()
        groups = {name: self._group_item(self.category_tree, name) for name in ("Platform", "Solid", "Wall")}
        bridge = self._group_item(groups["Solid"], "Bridge")
        top_sequence = self._group_item(self.category_tree, "Top Sequence")
        types = {kind: self._group_item(top_sequence, f"Type {kind}") for kind in ("00", "01")}
        temporary_group = self._group_item(self.category_tree, "임시 태그")
        for rule in self.built_in_rules:
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
            label = self._display_label(label)
            item = QTreeWidgetItem(parent, [label, ""])
            item.setData(0, ROLE_CATEGORY, name)
            item.setData(0, ROLE_SEARCH, f"{label} {name} {rule.prefix}".casefold())
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip(0, name)
            self.category_items[name] = item
        for name in self.temporary_tags:
            label = self._display_label(name)
            item = QTreeWidgetItem(temporary_group, [label, ""])
            item.setData(0, ROLE_CATEGORY, name)
            item.setData(0, ROLE_TEMPORARY_TAG, True)
            item.setData(0, ROLE_SEARCH, f"{label} {name}".casefold())
            item.setToolTip(0, name)
            self.category_items[name] = item
        self.category_tree.expandAll()
        self._update_tree_counts()

    def _update_tree_counts(self) -> None:
        for category, item in self.category_items.items():
            base = category if item.data(0, ROLE_TEMPORARY_TAG) else self._leaf_label(category)
            if "TopSequence" in category:
                match = re.search(r"TopSequence_(Start|Repeat|End)_\d\d$", category)
                base = match.group(1) if match else base
            count = len(self.model.assets(category))
            item.setText(0, self._display_label(base))
            item.setText(1, str(count) if count else "")
            item.setToolTip(1, f"등록된 타일 {count}개" if count else "등록된 타일 없음")

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

    def _rebuild_rules(self) -> None:
        self.rules = self.built_in_rules + [CategoryRule(name, name) for name in self.temporary_tags]
        self.rule_by_name = {rule.name: rule for rule in self.rules}

    def _restore_temporary_tag_state(self, tags: list[str], assignments: dict) -> None:
        previous_category = self.current_category()
        self.temporary_tags = list(tags)
        self.model = AssignmentModel.from_json(assignments)
        self.canvas.model = self.model
        self._rebuild_rules()
        self._populate_category_tree()
        target = self.category_items.get(previous_category)
        if target is None and self.category_items:
            target = next(iter(self.category_items.values()))
        if target is not None:
            self.category_tree.setCurrentItem(target)
        self.refresh_assignments()

    def add_temporary_tag(self, name: str) -> None:
        validated = validate_temporary_tag(
            name, self.temporary_tags, {rule.prefix for rule in self.built_in_rules}
        )
        before_tags, before_assignments = list(self.temporary_tags), self.model.as_json()
        after_tags = before_tags + [validated]
        self.undo_stack.push(TemporaryTagStateCommand(
            self, before_tags, before_assignments, after_tags, before_assignments,
            "임시 태그 추가",
        ))
        self.category_tree.setCurrentItem(self.category_items[validated])

    def rename_temporary_tag(self, old_name: str, new_name: str) -> None:
        if old_name not in self.temporary_tags:
            raise ValueError("임시 태그가 아닙니다.")
        existing = [value for value in self.temporary_tags if value != old_name]
        validated = validate_temporary_tag(
            new_name, existing, {rule.prefix for rule in self.built_in_rules}
        )
        before_tags, before_assignments = list(self.temporary_tags), self.model.as_json()
        after_tags = [validated if value == old_name else value for value in before_tags]
        after_assignments = dict(before_assignments)
        if old_name in after_assignments:
            after_assignments[validated] = after_assignments.pop(old_name)
        self.undo_stack.push(TemporaryTagStateCommand(
            self, before_tags, before_assignments, after_tags, after_assignments,
            "임시 태그 이름 변경",
        ))
        self.category_tree.setCurrentItem(self.category_items[validated])

    def remove_temporary_tag(self, name: str, confirmed: bool = False) -> bool:
        if name not in self.temporary_tags:
            raise ValueError("임시 태그가 아닙니다.")
        count = len(self.model.assets(name))
        if count and not confirmed:
            answer = QMessageBox.question(
                self, "임시 태그 삭제",
                f"이 태그에는 등록된 타일 {count}개가 있습니다.\n태그와 등록된 타일을 함께 제거하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        before_tags, before_assignments = list(self.temporary_tags), self.model.as_json()
        after_tags = [value for value in before_tags if value != name]
        after_assignments = dict(before_assignments)
        after_assignments.pop(name, None)
        self.undo_stack.push(TemporaryTagStateCommand(
            self, before_tags, before_assignments, after_tags, after_assignments,
            "임시 태그 삭제",
        ))
        return True

    def prompt_add_temporary_tag(self) -> None:
        name, accepted = QInputDialog.getText(self, "임시 태그 추가", "Export prefix")
        if accepted:
            try:
                self.add_temporary_tag(name)
            except ValueError as error:
                QMessageBox.warning(self, "임시 태그", str(error))

    def prompt_rename_temporary_tag(self) -> None:
        current = self.current_category()
        if current not in self.temporary_tags:
            QMessageBox.information(self, "임시 태그", "이름을 변경할 임시 태그를 선택하세요.")
            return
        name, accepted = QInputDialog.getText(self, "임시 태그 이름 변경", "Export prefix", text=current)
        if accepted:
            try:
                self.rename_temporary_tag(current, name)
            except ValueError as error:
                QMessageBox.warning(self, "임시 태그", str(error))

    def delete_selected_temporary_tag(self) -> None:
        current = self.current_category()
        if current not in self.temporary_tags:
            QMessageBox.information(self, "임시 태그", "삭제할 임시 태그를 선택하세요.")
            return
        self.remove_temporary_tag(current)

    def _category_changed(self, current, previous=None) -> None:
        category = str(current.data(0, ROLE_CATEGORY) or "") if current else ""
        if not category:
            return
        self._current_category = category
        self._update_temporary_tag_buttons()
        self.canvas.set_category(category, True)
        if self.source_image is not None:
            self._set_image_status()
        self.refresh_assignments()

    def _update_temporary_tag_buttons(self) -> None:
        temporary = self.current_category() in self.temporary_tags
        self.rename_tag_button.setEnabled(temporary)
        self.delete_tag_button.setEnabled(temporary)

    def current_category(self) -> str:
        return self._current_category

    def open_source(self) -> None:
        extensions = " ".join(f"*{ext}" for ext in sorted(RASTER_EXTENSIONS | ASEPRITE_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(self, "원본 이미지 열기", "", f"지원 이미지 ({extensions})")
        if path:
            self._request_source(Path(path))

    def replace_resource(self) -> None:
        extensions = " ".join(f"*{ext}" for ext in sorted(RASTER_EXTENSIONS | ASEPRITE_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(self, "리소스 교체", "", f"지원 이미지 ({extensions})")
        if path:
            self._replace_resource_path(Path(path))

    def _active_alignment_corrections(self) -> dict[str, tuple[int, int]]:
        # Grid origins move coordinates/overlays; alignment offsets move source pixels.
        # They are intentionally independent and must never be derived from each other.
        return dict(self.layer_alignment_offsets)

    def _replacement_candidate(self, path: Path) -> tuple[LoadedSource, bool]:
        candidate = load_source_document(path)
        same_structure = bool(self.layers) and self._layer_signature(candidate.layers) == self._layer_signature(self.layers)
        if same_structure and candidate.layers:
            identities = self._layer_identities(candidate.layers)
            visibility = {key: value for key, value in self.layer_visibility.items() if key in identities}
            corrections = {
                key: value for key, value in self._active_alignment_corrections().items()
                if key in identities
            }
            candidate = load_source_document(path, visibility, corrections)
        return candidate, same_structure

    def _replace_resource_path(self, path: Path) -> bool:
        resolved = path.resolve()
        try:
            candidate, preserve_grid = self._replacement_candidate(resolved)
        except Exception as error:
            QMessageBox.critical(self, "리소스 교체 실패", str(error))
            return False
        reference = self.grid_reference if preserve_grid else GridReference()
        incompatible = self._incompatible_assignments(candidate.image, reference)
        if incompatible:
            QMessageBox.warning(
                self, "리소스 교체 불가",
                f"등록된 타일 {len(incompatible)}개가 새 이미지 범위를 벗어납니다.",
            )
            return False
        self._apply_source(
            resolved, candidate, keep_assignments=True,
            preserve_grid_settings=preserve_grid,
        )
        self.statusBar().showMessage("리소스를 교체했습니다. Assignment와 임시 태그를 유지했습니다.", 5000)
        return True

    def _configure_source_watcher(self) -> None:
        paths = self.file_watcher.files()
        if paths:
            self.file_watcher.removePaths(paths)
        if (self.preferences.auto_reload_aseprite and self.source_path is not None
                and self.source_path.suffix.lower() in ASEPRITE_EXTENSIONS
                and self.source_path.exists()):
            self.file_watcher.addPath(str(self.source_path))

    def _source_file_changed(self, path: str) -> None:
        if not self.preferences.auto_reload_aseprite:
            return
        self._auto_reload_retry_count = 0
        self.auto_reload_timer.start()

    def _auto_reload_source(self) -> None:
        if (not self.preferences.auto_reload_aseprite or self.source_path is None
                or self.source_path.suffix.lower() not in ASEPRITE_EXTENSIONS):
            return
        try:
            before_stat = self.source_path.stat()
            candidate = load_source_document(
                self.source_path, self.layer_visibility,
                self._active_alignment_corrections(),
            )
            after_stat = self.source_path.stat()
            if (before_stat.st_size, before_stat.st_mtime_ns) != (after_stat.st_size, after_stat.st_mtime_ns):
                raise RuntimeError("파일 저장이 아직 완료되지 않았습니다.")
            incompatible = self._incompatible_assignments(candidate.image, self.grid_reference)
            if incompatible:
                raise ValueError(f"등록된 타일 {len(incompatible)}개가 새 이미지 범위를 벗어납니다.")
        except Exception as error:
            self._auto_reload_retry_count += 1
            if self._auto_reload_retry_count <= 2:
                QTimer.singleShot(200, self._auto_reload_source)
            else:
                self.statusBar().showMessage(f"Aseprite 자동 반영 실패: {error}", 7000)
                self._configure_source_watcher()
            return
        zoom = self.canvas.zoom
        horizontal = self.viewport_scroll.horizontalScrollBar().value()
        vertical = self.viewport_scroll.verticalScrollBar().value()
        selected_key = self._selected_assignment_key()
        self._apply_source(
            self.source_path, candidate, keep_assignments=True,
            preserve_grid_settings=True, clear_history=False,
            notify_mismatch=False,
        )
        self.canvas.set_zoom(zoom)
        self.viewport_scroll.horizontalScrollBar().setValue(horizontal)
        self.viewport_scroll.verticalScrollBar().setValue(vertical)
        self.assignment_list.setCurrentRow(self._row_for_assignment_key(selected_key))
        self._auto_reload_retry_count = 0
        self._configure_source_watcher()
        self.statusBar().showMessage("Aseprite 변경사항을 자동 반영했습니다.", 5000)

    def _has_assignments(self) -> bool:
        return any(True for _ in self.model.all_assets())

    def _choose_source_mode(self, path: Path) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("원본 이미지 변경")
        box.setText(f"등록된 타일이 있습니다.\n새 원본으로 바꿀까요?\n\n{path.name}")
        keep = box.addButton("Assignment 유지", QMessageBox.ButtonRole.AcceptRole)
        new = box.addButton("새로 시작", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep)
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep:
            return "keep"
        if clicked is new:
            return "new"
        if clicked is cancel:
            return "cancel"
        return "cancel"

    def _incompatible_assignments(
        self, image, reference: GridReference | None = None
    ) -> list[AssetAssignment]:
        grid = reference or self.grid_reference
        return [
            asset for asset in self.model.all_assets()
            if not grid.contains(asset, image.width, image.height)
        ]

    @staticmethod
    def _layer_identities(layers: tuple[AsepriteLayer, ...]) -> set[str]:
        result: set[str] = set()
        def visit(layer: AsepriteLayer) -> None:
            result.add(layer.identity)
            for child in layer.children:
                visit(child)
        for layer in layers:
            visit(layer)
        return result

    @staticmethod
    def _layer_signature(layers: tuple[AsepriteLayer, ...]) -> dict[str, tuple[str, str, str]]:
        result: dict[str, tuple[str, str, str]] = {}
        def visit(layer: AsepriteLayer) -> None:
            result[layer.identity] = (layer.uuid, layer.name, layer.kind)
            for child in layer.children:
                visit(child)
        for layer in layers:
            visit(layer)
        return result

    def _request_source(self, path: Path) -> bool:
        resolved = path.resolve()
        same_path = self.source_path is not None and resolved == self.source_path
        visibility = self.layer_visibility if same_path else None
        corrections = (
            self._active_alignment_corrections()
            if same_path else None
        )
        try:
            candidate = load_source_document(resolved, visibility, corrections)
        except Exception as error:
            QMessageBox.critical(self, "불러오기 실패", str(error))
            return False
        if same_path:
            mode = "keep"
        elif self._has_assignments():
            mode = self._choose_source_mode(resolved)
        else:
            mode = "new"
        if mode == "cancel":
            return False
        same_structure = bool(self.layers) and self._layer_signature(candidate.layers) == self._layer_signature(self.layers)
        preserve_grid = same_path or (mode == "keep" and same_structure)
        if preserve_grid and not same_path and candidate.layers:
            try:
                candidate = load_source_document(
                    resolved,
                    {key: value for key, value in self.layer_visibility.items()
                     if key in self._layer_identities(candidate.layers)},
                    self._active_alignment_corrections(),
                )
            except Exception as error:
                QMessageBox.critical(self, "불러오기 실패", str(error))
                return False
        if mode == "keep":
            reference = self.grid_reference if preserve_grid else GridReference()
            incompatible = self._incompatible_assignments(candidate.image, reference)
            if incompatible:
                QMessageBox.warning(
                    self, "Assignment 유지 불가",
                    f"새 이미지의 32×32 사용 가능 영역을 벗어나는 Assignment가 "
                    f"{len(incompatible)}개 있습니다.\n기존 원본과 작업 상태를 유지합니다.",
                )
                return False
        self._apply_source(
            resolved, candidate, keep_assignments=mode == "keep",
            preserve_grid_settings=preserve_grid,
        )
        if same_path:
            self.statusBar().showMessage("동일한 원본을 다시 불러왔습니다. Assignment를 유지했습니다.", 5000)
        elif mode == "keep":
            count = sum(1 for _ in self.model.all_assets())
            self.statusBar().showMessage(
                f"새 이미지로 교체했습니다. 등록된 타일 {count}개를 유지했습니다.", 5000
            )
        return True

    def _load_source(self, path: Path, keep_assignments: bool = False) -> bool:
        """테스트와 프로젝트 로드용 명시적 공통 로드 진입점."""
        try:
            candidate = load_source_document(
                path,
                self.layer_visibility if keep_assignments else None,
                self._active_alignment_corrections()
                if keep_assignments else None,
            )
        except Exception as error:
            QMessageBox.critical(self, "불러오기 실패", str(error))
            return False
        if keep_assignments and self._incompatible_assignments(candidate.image):
            QMessageBox.warning(self, "Assignment 유지 불가", "원본 범위를 벗어나는 Assignment가 있습니다.")
            return False
        self._apply_source(
            path.resolve(), candidate, keep_assignments,
            preserve_grid_settings=keep_assignments,
        )
        return True

    def _default_alignment_offsets(self, layers: tuple[AsepriteLayer, ...]) -> dict[str, tuple[int, int]]:
        return {}

    def _set_advanced_layer_settings_visible(self, visible: bool) -> None:
        if not hasattr(self, "advanced_layer_settings"):
            return
        for child in self.advanced_layer_settings.findChildren(QWidget):
            child.setVisible(visible)

    @staticmethod
    def _normalized_grid_origin(x: int, y: int) -> tuple[int, int]:
        def component(value: int) -> int:
            phase = value % 32
            return phase - 32 if phase > 16 else phase
        return component(int(x)), component(int(y))

    def _default_layer_grid_origins(
        self, layers: tuple[AsepriteLayer, ...],
    ) -> dict[str, tuple[int, int]]:
        origins: dict[str, tuple[int, int]] = {}
        def visit(layer: AsepriteLayer) -> None:
            if layer.kind != "group":
                if layer.grid_origin_x is not None and layer.grid_origin_y is not None:
                    origins[layer.identity] = self._normalized_grid_origin(
                        int(layer.grid_origin_x or 0), int(layer.grid_origin_y or 0),
                    )
                else:
                    origins[layer.identity] = (0, 0)
            for child in layer.children:
                visit(child)
        for layer in layers:
            visit(layer)
        return origins

    def _apply_source(
        self, path: Path, source: LoadedSource, keep_assignments: bool,
        preserve_grid_settings: bool = False,
        clear_history: bool = True,
        notify_mismatch: bool = True,
    ) -> None:
        candidate_qimage = ImageQt(source.image).copy()
        if not keep_assignments:
            self.model.clear()
        self.source_image = source.image
        self.source_path = path.resolve()
        self.layers = source.layers
        self.layer_visibility = dict(source.layer_visibility or {})
        self.document_grid = source.document_grid
        identities = self._layer_identities(source.layers)
        default_grid_origins = self._default_layer_grid_origins(source.layers)
        if preserve_grid_settings:
            previous_origins = dict(self.layer_grid_origins)
            previous_manual = set(self.layer_grid_manual_overrides)
            self.layer_grid_origins = {
                identity: (
                    previous_origins[identity]
                    if identity in previous_manual and identity in previous_origins
                    else default_grid_origins.get(identity, (0, 0))
                )
                for identity in default_grid_origins
            }
            self.layer_grid_manual_overrides = previous_manual & identities
            self.layer_alignment_offsets = {
                identity: offset for identity, offset in self.layer_alignment_offsets.items()
                if identity in identities
            }
            if (self.grid_reference.mode == "layer"
                    and self.grid_reference.layer_identity not in identities):
                self.grid_reference = GridReference()
            elif self.grid_reference.mode == "layer":
                self.grid_reference = self._reference_from_choice(
                    "layer", self.grid_reference.layer_identity,
                )
        else:
            self.layer_grid_origins = default_grid_origins
            self.layer_grid_manual_overrides = set()
            self.layer_alignment_offsets = self._default_alignment_offsets(source.layers)
            self.grid_reference = GridReference()
        self.canvas.set_qimage_content(candidate_qimage, self.model)
        self.canvas.set_grid_reference(self.grid_reference)
        self.canvas.set_category(self.current_category(), True)
        self.viewport_stack.setCurrentWidget(self.viewport_scroll)
        self._populate_layer_tree()
        self._populate_grid_references()
        if clear_history:
            self.undo_stack.clear()
        self.source_revision += 1
        self._configure_source_watcher()
        self._set_image_status()
        self.refresh_assignments()
        if notify_mismatch and not preserve_grid_settings:
            self._show_alignment_mismatch_if_needed()

    def _set_image_status(self) -> None:
        if self.source_image is None:
            return
        image = self.source_image
        source_name = self.source_path.name if self.source_path else ""
        grid_status = f"Grid 32×32 @ ({self.grid_reference.origin_x:+d}, {self.grid_reference.origin_y:+d})"
        remainder = (image.width % 32, image.height % 32)
        if remainder != (0, 0):
            unavailable = []
            if remainder[0]:
                unavailable.append(f"오른쪽 {remainder[0]}px")
            if remainder[1]:
                unavailable.append(f"아래 {remainder[1]}px")
            self.warning.setText(
                f"{source_name}  |  {image.width}×{image.height}  |  {grid_status}  |  "
                f"{' / '.join(unavailable)} 선택 불가"
            )
            self.warning.setStyleSheet("color: #9a5a16; font-weight: 600;")
        else:
            self.warning.setText(
                f"{source_name}  |  {image.width}×{image.height}  |  {grid_status}  |  "
                f"{image.width // 32}×{image.height // 32} 셀"
            )
            self.warning.setStyleSheet("")

    def toggle_tile(self, column: int, row: int) -> None:
        self.assign_region(column, row, 1, 1)

    def _restore_assignment_state(self, state: dict) -> None:
        selected_key = self._selected_assignment_key()
        self.model = AssignmentModel.from_json(state)
        self.canvas.model = self.model
        self.refresh_assignments()
        row = self._row_for_assignment_key(selected_key)
        self.assignment_list.setCurrentRow(row)

    def _selected_assignment_key(self) -> tuple[tuple[int, int], ...] | None:
        row = self.assignment_list.currentRow()
        assets = self.model.assets(self.current_category())
        if not 0 <= row < len(assets):
            return None
        asset = assets[row]
        return asset.selected_cells

    def _row_for_assignment_key(self, key: tuple[tuple[int, int], ...] | None) -> int:
        if key is None:
            return -1
        for index, asset in enumerate(self.model.assets(self.current_category())):
            if asset.selected_cells == key:
                return index
        return -1

    def assign_region(self, x: int, y: int, width: int, height: int) -> None:
        asset = AssetAssignment("_selection", x, y, width, height)
        self.assign_cells(asset.selected_cells or ())

    def assign_cells(self, cells) -> None:
        category = self.current_category()
        if not category:
            return
        before = self.model.as_json()
        working = AssignmentModel.from_json(before)
        result = working.assign_cells(category, cells)
        if result.status == "conflict":
            conflict = result.conflict
            index = self.model.assets(conflict.category).index(conflict)
            rule = self.rule_by_name.get(conflict.category)
            filename = rule.filename(index) if rule else "?"
            QMessageBox.warning(
                self, "영역 충돌",
                f"선택 영역이 기존 에셋과 일부 겹칩니다.\n\n카테고리: {conflict.category}\n"
                f"파일명: {filename}\n순서: #{index + 1:02d}\n"
                f"겹치는 셀: {len(result.assignment.occupied_cells() & conflict.occupied_cells())}개\n"
                f"Bounds: {conflict.width_cells}×{conflict.height_cells} / "
                f"{conflict.output_width_px}×{conflict.output_height_px}",
            )
            return
        after = working.as_json()
        action = {
            "added": "타일 등록", "removed": "타일 제거", "moved": "타일 종류 변경"
        }.get(result.status, "타일 편집")
        self.undo_stack.push(AssignmentStateCommand(self, before, after, action))

    def refresh_assignments(self) -> None:
        self._stop_locate_flash()
        category = self.current_category()
        previous_row = (
            self.assignment_list.currentRow()
            if category == self._assignment_list_category else -1
        )
        assets = self.model.assets(category) if category else []
        self.current_label.setText(self._display_category(category))
        self.assignment_count_label.setText(f"{len(assets)}개")
        self.total_assignment_label.setText(str(output_asset_count(self.model)))
        self.assignment_empty.setVisible(not assets)
        self.assignment_list.setVisible(bool(assets))
        with QSignalBlocker(self.assignment_list):
            self.assignment_list.clear()
            rule = self.rule_by_name.get(category)
            if rule:
                for index, asset in enumerate(assets):
                    shape_description = (
                        f"{asset.width_cells}×{asset.height_cells} · "
                        f"{asset.output_width_px}×{asset.output_height_px}"
                        if asset.is_rectangular else
                        f"{asset.cell_count} cells · Bounds "
                        f"{asset.width_cells}×{asset.height_cells} · "
                        f"{asset.output_width_px}×{asset.output_height_px}"
                    )
                    item = QListWidgetItem(
                        f"#{index:02d}\n"
                        f"{rule.filename(index)}\n"
                        f"{shape_description} · "
                        f"셀 ({asset.x_cell}, {asset.y_cell})"
                    )
                    item.setToolTip(
                        f"{rule.filename(index)}\n"
                        f"논리 크기 {asset.width_cells}×{asset.height_cells}\n"
                        f"선택 셀 {asset.cell_count}개\n"
                        f"출력 크기 {asset.output_width_px}×{asset.output_height_px}"
                        f"{self._assignment_output_tooltip(rule, index)}"
                    )
                    self.assignment_list.addItem(item)
            self._assignment_list_category = category
            if 0 <= previous_row < self.assignment_list.count():
                self.assignment_list.setCurrentRow(previous_row)
        self._assignment_row_changed(self.assignment_list.currentRow())
        self._update_tree_counts()
        self._update_output_bar()
        self.canvas.update()
        self.preview_data_changed.emit()

    def _assignment_output_tooltip(self, rule: CategoryRule, index: int) -> str:
        if self.export_base_directory is None:
            return "\nOutput: 출력 위치 미지정"
        output = self.export_base_directory / rule.subfolder / rule.filename(index)
        return f"\nOutput: {output}"

    def _assignment_row_changed(self, row: int) -> None:
        self.canvas.set_selected_assignment(row)
        self._refresh_assignment_icons(row)

    def _refresh_assignment_icons(self, selected_row: int) -> None:
        if self.source_image is None:
            return
        assets = self.model.assets(self.current_category())
        for index, asset in enumerate(assets):
            item = self.assignment_list.item(index)
            if item is None:
                continue
            thumbnail = build_assignment_thumbnail(
                self.source_image, asset, self.grid_reference, 48,
                self.preferences.alpha_colors(),
            )
            decorated = decorate_thumbnail_selection(thumbnail, index == selected_row)
            pixmap = QPixmap.fromImage(ImageQt(decorated).copy())
            item.setIcon(QIcon(pixmap))

    def reorder(self, offset: int) -> None:
        row = self.assignment_list.currentRow()
        if row < 0:
            return
        before = self.model.as_json()
        working = AssignmentModel.from_json(before)
        target = working.move(self.current_category(), row, offset)
        if target == row:
            return
        self.undo_stack.push(AssignmentStateCommand(self, before, working.as_json(), "타일 순서 변경"))
        self.assignment_list.setCurrentRow(target)

    def remove_selected(self) -> None:
        row, category = self.assignment_list.currentRow(), self.current_category()
        if row >= 0 and category in self.model.assignments:
            before = self.model.as_json()
            working = AssignmentModel.from_json(before)
            working.remove(category, row)
            self.undo_stack.push(AssignmentStateCommand(self, before, working.as_json(), "타일 제거"))

    def _populate_layer_tree(self) -> None:
        if not hasattr(self, "layer_tree"):
            return
        self._updating_layers = True
        with QSignalBlocker(self.layer_tree):
            self.layer_tree.clear()
            if not self.layers:
                item = QTreeWidgetItem(self.layer_tree, ["레이어 정보 없음", ""])
                item.setDisabled(True)
                item.setToolTip(0, "Aseprite 문서에서만 레이어를 제어할 수 있습니다.")
            else:
                def append(parent, layer: AsepriteLayer, hierarchy: tuple[str, ...]) -> None:
                    path = hierarchy + (layer.name,)
                    grid_origin = self.layer_grid_origins.get(layer.identity, (0, 0))
                    is_manual = layer.identity in self.layer_grid_manual_overrides
                    confidence = "수동" if is_manual else (
                        "High" if layer.grid_confidence == "high" else "불확실"
                    )
                    item = QTreeWidgetItem(parent, [
                        layer.name, f"{grid_origin[0]:+d},{grid_origin[1]:+d}",
                    ])
                    item.setData(0, ROLE_LAYER_ID, layer.identity)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    visible = self.layer_visibility.get(layer.identity, layer.visible)
                    item.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
                    item.setToolTip(
                        0, f"{' / '.join(path)}\n{layer.kind} · {layer.identity}\n"
                        f"Grid 감지: {layer.grid_detection_method} · {confidence}"
                    )
                    item.setToolTip(1, f"Grid ({grid_origin[0]:+d}, {grid_origin[1]:+d}) · {confidence}")
                    for child in reversed(layer.children):
                        append(item, child, path)
                # Aseprite API stackIndex 1 is bottom; panel order is top-to-bottom.
                for layer in reversed(self.layers):
                    append(self.layer_tree, layer, ())
                self.layer_tree.expandAll()
        self._updating_layers = False
        self._layer_selection_changed(self.layer_tree.currentItem())

    def _iter_layer_items(self):
        def visit(item: QTreeWidgetItem):
            yield item
            for child_index in range(item.childCount()):
                yield from visit(item.child(child_index))
        for top_index in range(self.layer_tree.topLevelItemCount()):
            yield from visit(self.layer_tree.topLevelItem(top_index))

    def _sync_layer_tree(self) -> None:
        self._updating_layers = True
        with QSignalBlocker(self.layer_tree):
            for item in self._iter_layer_items():
                identity = str(item.data(0, ROLE_LAYER_ID) or "")
                if not identity:
                    continue
                visible = self.layer_visibility.get(identity, True)
                item.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
                grid_origin = self.layer_grid_origins.get(identity, (0, 0))
                item.setText(1, f"{grid_origin[0]:+d},{grid_origin[1]:+d}")
        self._updating_layers = False

    def _layer_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating_layers or self.source_path is None:
            return
        identity = str(item.data(0, ROLE_LAYER_ID) or "")
        if not identity:
            return
        try:
            before = dict(self.layer_visibility)
            after = dict(before)
            after[identity] = item.checkState(0) == Qt.CheckState.Checked
            if before == after:
                return
            if not self._restore_layer_visibility(after, show_error=True):
                self._sync_layer_tree()
                return
            self.undo_stack.push(
                LayerVisibilityCommand(self, before, after, "레이어 표시 변경", first_redo_applied=True)
            )
        except Exception as error:
            self._sync_layer_tree()
            QMessageBox.critical(self, "레이어 변경 실패", str(error))

    def _restore_layer_visibility(self, visibility: dict[str, bool], show_error: bool = True) -> bool:
        if self.source_path is None or self.source_path.suffix.lower() not in ASEPRITE_EXTENSIONS:
            return False
        try:
            corrections = self._active_alignment_corrections()
            candidate = load_source_document(self.source_path, visibility, corrections)
            candidate_qimage = ImageQt(candidate.image).copy()
        except Exception as error:
            if show_error:
                QMessageBox.critical(self, "레이어 렌더 실패", str(error))
            return False
        horizontal = self.viewport_scroll.horizontalScrollBar().value()
        vertical = self.viewport_scroll.verticalScrollBar().value()
        zoom = self.canvas.zoom
        self.source_image = candidate.image
        self.source_revision += 1
        self.layers = candidate.layers
        self.layer_visibility = dict(candidate.layer_visibility or visibility)
        self.document_grid = candidate.document_grid
        if self.grid_reference.mode == "layer" and self.grid_reference.layer_identity:
            self.grid_reference = self._reference_from_choice(
                "layer", self.grid_reference.layer_identity,
            )
        self.canvas.set_qimage_content(candidate_qimage, self.model)
        self.canvas.set_zoom(zoom)
        self.canvas.set_grid_reference(self.grid_reference)
        self._sync_layer_tree()
        self.viewport_scroll.horizontalScrollBar().setValue(horizontal)
        self.viewport_scroll.verticalScrollBar().setValue(vertical)
        self._set_image_status()
        self.refresh_assignments()
        return True

    def _flatten_layers(self) -> list[AsepriteLayer]:
        result: list[AsepriteLayer] = []
        def visit(layer: AsepriteLayer) -> None:
            result.append(layer)
            for child in layer.children:
                visit(child)
        for layer in self.layers:
            visit(layer)
        return result

    def _populate_grid_references(self) -> None:
        if not hasattr(self, "grid_reference_combo"):
            return
        self._updating_grid_controls = True
        with QSignalBlocker(self.grid_reference_combo):
            self.grid_reference_combo.clear()
            self.grid_reference_combo.addItem("전체 이미지 (0, 0)", ("image", None))
            if self.document_grid and self.document_grid.cell_width == 32 and self.document_grid.cell_height == 32:
                self.grid_reference_combo.addItem(
                    f"Aseprite 문서 Grid ({self.document_grid.origin_x:+d}, {self.document_grid.origin_y:+d})",
                    ("document", None),
                )
            for layer in reversed(self._flatten_layers()):
                if layer.kind == "group":
                    continue
                reference = self._reference_from_choice("layer", layer.identity)
                origin = (reference.origin_x, reference.origin_y)
                manual = layer.identity in self.layer_grid_manual_overrides
                status = "수동" if manual else (
                    "자동 High" if layer.grid_confidence == "high" else "자동 감지 불확실"
                )
                self.grid_reference_combo.addItem(
                    f"Layer: {layer.name} ({origin[0]:+d}, {origin[1]:+d}) · {status}",
                    ("layer", layer.identity),
                )
                index = self.grid_reference_combo.count() - 1
                self.grid_reference_combo.setItemData(
                    index,
                    f"Grid ({origin[0]:+d}, {origin[1]:+d}) · {status}\n"
                    f"감지 방식: {layer.grid_detection_method}",
                    Qt.ItemDataRole.ToolTipRole,
                )
            desired = (self.grid_reference.mode, self.grid_reference.layer_identity)
            for index in range(self.grid_reference_combo.count()):
                if self.grid_reference_combo.itemData(index) == desired:
                    self.grid_reference_combo.setCurrentIndex(index)
                    break
            else:
                self.grid_reference_combo.setCurrentIndex(0)
                self.grid_reference = GridReference()
        self._updating_grid_controls = False

    def alignment_mismatches(self) -> dict[str, tuple[int, int]]:
        baseline = (0, 0)
        if self.document_grid and self.document_grid.cell_width == 32 and self.document_grid.cell_height == 32:
            baseline = (self.document_grid.origin_x, self.document_grid.origin_y)
        defaults = self._default_layer_grid_origins(self.layers)
        return {
            layer.identity: defaults[layer.identity]
            for layer in self._flatten_layers()
            if self._has_native_tilemap_grid(layer) and defaults[layer.identity] != baseline
        }

    def auto_alignment_references(self) -> list[tuple[str, GridReference]]:
        references = [("전체 이미지 (0, 0)", GridReference())]
        if (self.document_grid is not None and self.document_grid.cell_width == 32
                and self.document_grid.cell_height == 32):
            references.append(("Aseprite 문서 Grid", self.document_grid))
        for layer in self._flatten_layers():
            if self._has_reliable_layer_grid(layer):
                references.append((
                    f"Layer: {layer.name}",
                    GridReference(32, 32, int(layer.grid_origin_x), int(layer.grid_origin_y or 0),
                                  "layer", layer.identity),
                ))
        return references

    def auto_alignment_plan(
        self, reference: GridReference,
    ) -> tuple[dict[str, tuple[int, int]], list[str]]:
        plan: dict[str, tuple[int, int]] = {}
        skipped: list[str] = []
        for layer in self._flatten_layers():
            if layer.kind == "group":
                continue
            if self._has_reliable_layer_grid(layer):
                plan[layer.identity] = (
                    int(layer.grid_origin_x) - reference.origin_x,
                    int(layer.grid_origin_y or 0) - reference.origin_y,
                )
            else:
                skipped.append(layer.name)
        return plan, skipped

    def apply_auto_alignment(self, reference: GridReference) -> bool:
        plan, _ = self.auto_alignment_plan(reference)
        if not plan:
            return False
        before = dict(self.layer_alignment_offsets)
        after = dict(before)
        after.update(plan)
        self.layer_alignment_offsets = after
        if not self._rerender_alignment():
            self.layer_alignment_offsets = before
            return False
        self._alignment_last_good_offsets = dict(after)
        self.undo_stack.push(AlignmentStateCommand(
            self, before, after, "기준 격자에 자동 맞춤", first_redo_applied=True,
        ))
        self._sync_layer_tree()
        self._populate_grid_references()
        self._layer_selection_changed(self.layer_tree.currentItem())
        self.refresh_assignments()
        return True

    def prompt_auto_alignment(self) -> None:
        references = self.auto_alignment_references()
        if not self.layers:
            QMessageBox.information(self, "기준 격자에 자동 맞춤", "Aseprite 레이어가 있는 파일을 먼저 열어 주세요.")
            return
        labels = [label for label, _ in references]
        label, accepted = QInputDialog.getItem(self, "기준 격자에 자동 맞춤", "기준 격자", labels, 0, False)
        if not accepted:
            return
        reference = references[labels.index(label)][1]
        plan, skipped = self.auto_alignment_plan(reference)
        if not plan:
            QMessageBox.information(self, "기준 격자에 자동 맞춤", "신뢰할 수 있는 32×32 Tilemap 격자 메타데이터가 없습니다.")
            return
        names = {layer.identity: layer.name for layer in self._flatten_layers()}
        rows = [
            f"{names.get(identity, identity)}: {self.layer_alignment_offsets.get(identity, (0, 0))} → {offset}"
            for identity, offset in plan.items()
        ]
        if skipped:
            rows.append(f"건너뜀(일반 이미지/불확실): {', '.join(skipped)}")
        answer = QMessageBox.question(
            self, "기준 격자에 자동 맞춤", "\n".join(rows) + "\n\n이 보정값을 적용하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.apply_auto_alignment(reference)

    def _show_alignment_mismatch_if_needed(self) -> None:
        mismatches = self.alignment_mismatches()
        if not mismatches:
            return
        names = {layer.identity: layer.name for layer in self._flatten_layers()}
        details = "\n".join(
            f"{names.get(identity, identity)}    X {offset[0]:+d}px / Y {offset[1]:+d}px"
            for identity, offset in mismatches.items()
        )
        box = QMessageBox(self)
        box.setWindowTitle("레이어 격자 정렬 차이")
        box.setText(f"레이어의 타일 정렬 기준이 서로 다릅니다.\n\n{details}")
        keep = box.addButton("그대로 열기", QMessageBox.ButtonRole.RejectRole)
        choose = box.addButton("격자 기준 선택", QMessageBox.ButtonRole.ActionRole)
        correct = box.addButton("기준에 맞게 보정", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() is choose:
            self.left_tabs.setCurrentIndex(1)
            self.grid_reference_combo.setFocus()
        elif box.clickedButton() is correct:
            self.prompt_auto_alignment()

    @staticmethod
    def _has_native_tilemap_grid(layer: AsepriteLayer) -> bool:
        return (
            layer.kind == "tilemap" and layer.grid_origin_x is not None
            and layer.grid_width == 32 and layer.grid_height == 32
        )

    def _has_reliable_layer_grid(self, layer: AsepriteLayer) -> bool:
        return (
            layer.grid_origin_x is not None and layer.grid_origin_y is not None
            and (layer.grid_confidence == "high" or self._has_native_tilemap_grid(layer))
        )

    def _layer_grid_origin(self, identity: str) -> tuple[int, int]:
        return self.layer_grid_origins.get(identity, (0, 0))

    def _reference_from_choice(self, mode: str, identity: str | None) -> GridReference:
        """Resolve grid coordinates independently from composite alignment correction.

        Tilemaps initialize from native Aseprite grid metadata. Generic image layers
        initialize at (0, 0). Both can then use an explicit TileNamer grid override,
        which is stored separately from pixel-render alignment offsets.
        """
        if mode == "document" and self.document_grid is not None:
            return self.document_grid
        if mode == "layer" and identity:
            origin = self._layer_grid_origin(identity)
            return GridReference(32, 32, origin[0], origin[1], "layer", identity)
        return GridReference()

    def _grid_reference_changed(self, index: int) -> None:
        if self._updating_grid_controls or index < 0:
            return
        mode, identity = self.grid_reference_combo.itemData(index)
        candidate = self._reference_from_choice(mode, identity)
        if candidate == self.grid_reference:
            return
        count = sum(1 for _ in self.model.all_assets())
        if self.source_image is not None:
            incompatible = self._incompatible_assignments(self.source_image, candidate)
            if incompatible:
                QMessageBox.warning(
                    self, "격자 기준 변경 불가",
                    f"새 격자 기준에서 이미지 범위를 벗어나는 타일이 {len(incompatible)}개 있습니다.",
                )
                self._populate_grid_references()
                return
        if count:
            answer = QMessageBox.question(
                self, "격자 기준 변경",
                f"격자 기준을 변경하면 등록된 타일 {count}개의 실제 픽셀 위치가 변경됩니다.\n\n계속하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._populate_grid_references()
                return
        self.grid_reference = candidate
        self.canvas.set_grid_reference(candidate)
        self.undo_stack.clear()
        self.refresh_assignments()
        self._set_image_status()

    def _layer_selection_changed(self, current, previous=None) -> None:
        identity = str(current.data(0, ROLE_LAYER_ID) or "") if current else ""
        layer = next((value for value in self._flatten_layers() if value.identity == identity), None)
        enabled = bool(identity and layer and layer.kind != "group")
        self._updating_grid_controls = True
        if layer is None:
            self.selected_grid_layer_name.setText("레이어를 선택하세요")
            self.selected_grid_layer_name.setToolTip("격자 원점을 수정할 레이어를 선택하세요.")
            self.selected_layer_name.setText("레이어를 선택하세요")
            self.selected_layer_name.setToolTip("정렬값을 수정할 레이어를 선택하세요.")
        else:
            manual = identity in self.layer_grid_manual_overrides
            detection = "수동 override" if manual else (
                f"자동 감지 High · {layer.grid_detection_method}"
                if layer.grid_confidence == "high"
                else "Grid 자동 감지 불확실 · 수동 fallback 사용 가능"
            )
            self.selected_grid_layer_name.setText(f"{layer.name}\n{detection}")
            self.selected_grid_layer_name.setToolTip(current.toolTip(0))
            self.selected_layer_name.setText(layer.name)
            self.selected_layer_name.setToolTip(current.toolTip(0))
        grid_origin = self.layer_grid_origins.get(identity, (0, 0))
        self.layer_grid_x.setValue(grid_origin[0])
        self.layer_grid_y.setValue(grid_origin[1])
        offset = self.layer_alignment_offsets.get(identity, (0, 0))
        self.layer_offset_x.setValue(offset[0])
        self.layer_offset_y.setValue(offset[1])
        grid_fallback_enabled = bool(
            enabled and layer is not None
            and (layer.grid_confidence != "high" or identity in self.layer_grid_manual_overrides)
        )
        self.layer_grid_x.setEnabled(grid_fallback_enabled)
        self.layer_grid_y.setEnabled(grid_fallback_enabled)
        self.layer_grid_reset.setEnabled(bool(enabled and identity in self.layer_grid_manual_overrides))
        self.layer_offset_x.setEnabled(enabled)
        self.layer_offset_y.setEnabled(enabled)
        self.layer_offset_reset.setEnabled(enabled)
        self._updating_grid_controls = False
        self._alignment_edit_start_offsets = dict(self.layer_alignment_offsets)
        self._alignment_last_good_offsets = dict(self.layer_alignment_offsets)

    def _layer_grid_origin_edited(self, value: int) -> None:
        if self._updating_grid_controls:
            return
        item = self.layer_tree.currentItem()
        identity = str(item.data(0, ROLE_LAYER_ID) or "") if item else ""
        if not identity:
            return
        before = self.layer_grid_origins.get(identity, (0, 0))
        origin = self._normalized_grid_origin(self.layer_grid_x.value(), self.layer_grid_y.value())
        if origin != (self.layer_grid_x.value(), self.layer_grid_y.value()):
            self._updating_grid_controls = True
            self.layer_grid_x.setValue(origin[0])
            self.layer_grid_y.setValue(origin[1])
            self._updating_grid_controls = False
        active = (
            self.grid_reference.mode == "layer"
            and self.grid_reference.layer_identity == identity
        )
        candidate = GridReference(32, 32, origin[0], origin[1], "layer", identity)
        if (active and self.source_image is not None
                and self._incompatible_assignments(self.source_image, candidate)):
            QMessageBox.warning(
                self, "Layer Grid 변경 불가",
                "새 Grid origin에서 이미지 범위를 벗어나는 타일이 있습니다.",
            )
            self._updating_grid_controls = True
            self.layer_grid_x.setValue(before[0])
            self.layer_grid_y.setValue(before[1])
            self._updating_grid_controls = False
            return
        self.layer_grid_origins[identity] = origin
        self.layer_grid_manual_overrides.add(identity)
        if active:
            self.grid_reference = candidate
            self.canvas.set_grid_reference(candidate)
            self.refresh_assignments()
            if self.source_image is not None:
                self._set_image_status()
        self._sync_layer_tree()
        self._populate_grid_references()

    def _reset_layer_grid_origin(self) -> None:
        item = self.layer_tree.currentItem()
        identity = str(item.data(0, ROLE_LAYER_ID) or "") if item else ""
        layer = next((value for value in self._flatten_layers() if value.identity == identity), None)
        if layer is None or layer.kind == "group":
            return
        default = self._default_layer_grid_origins((layer,)).get(identity, (0, 0))
        self.layer_grid_manual_overrides.discard(identity)
        self._updating_grid_controls = True
        self.layer_grid_x.setValue(default[0])
        self.layer_grid_y.setValue(default[1])
        self._updating_grid_controls = False
        self._layer_grid_origin_edited(0)
        self.layer_grid_manual_overrides.discard(identity)
        self._sync_layer_tree()
        self._populate_grid_references()
        self._layer_selection_changed(item)

    def _layer_offset_edited(self, value: int) -> None:
        if self._updating_grid_controls:
            return
        item = self.layer_tree.currentItem()
        identity = str(item.data(0, ROLE_LAYER_ID) or "") if item else ""
        if not identity:
            return
        after = dict(self.layer_alignment_offsets)
        after[identity] = (self.layer_offset_x.value(), self.layer_offset_y.value())
        self.layer_alignment_offsets = after
        self.alignment_preview_timer.start()
        self._sync_layer_tree()
        self.refresh_assignments()

    def _reset_layer_offset(self) -> None:
        before = dict(self.layer_alignment_offsets)
        self._updating_grid_controls = True
        self.layer_offset_x.setValue(0)
        self.layer_offset_y.setValue(0)
        self._updating_grid_controls = False
        self._layer_offset_edited(0)
        self.alignment_preview_timer.stop()
        if not self._render_alignment_preview():
            return
        after = dict(self.layer_alignment_offsets)
        if before != after:
            self.undo_stack.push(AlignmentStateCommand(
                self, before, after, "레이어 정렬값 초기화", first_redo_applied=True,
            ))
            self._alignment_edit_start_offsets = dict(after)

    def _render_alignment_preview(self) -> bool:
        proposed = dict(self.layer_alignment_offsets)
        if self._rerender_alignment():
            self._alignment_last_good_offsets = proposed
            return True
        self.layer_alignment_offsets = dict(self._alignment_last_good_offsets)
        self._layer_selection_changed(self.layer_tree.currentItem())
        self._sync_layer_tree()
        self.statusBar().showMessage("정렬 미리보기에 실패하여 마지막 정상값으로 복원했습니다.", 5000)
        return False

    def _alignment_edit_finished(self) -> None:
        if self._updating_grid_controls:
            return
        self.alignment_preview_timer.stop()
        if (self.layer_alignment_offsets != self._alignment_last_good_offsets
                and not self._render_alignment_preview()):
            return
        before = dict(self._alignment_edit_start_offsets)
        after = dict(self.layer_alignment_offsets)
        if before != after:
            self.undo_stack.push(AlignmentStateCommand(
                self, before, after, "레이어 정렬값 변경", first_redo_applied=True,
            ))
        self._alignment_edit_start_offsets = dict(after)

    def _restore_alignment_state(
        self, offsets: dict[str, tuple[int, int]], enabled: bool | None = None,
    ) -> None:
        previous = dict(self.layer_alignment_offsets)
        self.layer_alignment_offsets = dict(offsets)
        if not self._rerender_alignment():
            self.layer_alignment_offsets = previous
            return
        self._alignment_last_good_offsets = dict(self.layer_alignment_offsets)
        self._alignment_edit_start_offsets = dict(self.layer_alignment_offsets)
        self._sync_layer_tree()
        self._layer_selection_changed(self.layer_tree.currentItem())
        self.refresh_assignments()

    def _rerender_alignment(self) -> bool:
        if self.source_path is None or self.source_path.suffix.lower() not in ASEPRITE_EXTENSIONS:
            return True
        restored = self._restore_layer_visibility(self.layer_visibility, show_error=False)
        if not restored:
            self.statusBar().showMessage("레이어 정렬 미리보기를 렌더링하지 못했습니다.", 5000)
        return restored

    def effective_output_path(self) -> Path | None:
        if self.export_base_directory is None:
            return None
        return effective_output_directory(self.export_base_directory, self.rules)

    def set_export_base_directory(
        self, path: str | Path | None, *, mark_dirty: bool = True,
    ) -> None:
        resolved = Path(path).resolve() if path else None
        if resolved == self.export_base_directory:
            return
        self.export_base_directory = resolved
        if resolved is not None:
            self.preferences.last_export_directory = str(resolved)
        if mark_dirty:
            self._mark_settings_dirty()
        self._update_output_bar()
        self.refresh_assignments()

    def choose_output_destination(self) -> bool:
        initial = str(self.export_base_directory or self.preferences.last_export_directory)
        output = QFileDialog.getExistingDirectory(self, "기본 출력 위치 선택", initial)
        if not output:
            return False
        self.set_export_base_directory(output)
        self.statusBar().showMessage("프로젝트의 기본 출력 위치를 변경했습니다.", 5000)
        return True

    def _update_output_bar(self) -> None:
        if not hasattr(self, "output_path_label"):
            return
        count = output_asset_count(self.model)
        effective = self.effective_output_path()
        if effective is None:
            self.output_path_label.setFullText("출력 위치가 지정되지 않았습니다.")
            summary = f"{count} PNG"
            enabled = False
            button_text = "출력 위치 선택"
        else:
            self.output_path_label.setFullText(str(effective))
            collisions = 0
            if count:
                try:
                    collisions = len(find_existing_collisions(
                        build_export_plan(self.export_base_directory, self.model, self.rules)
                    ))
                except (OSError, ValueError):
                    collisions = 0
            summary = f"{count} PNG"
            if collisions:
                summary += f" · 기존 {collisions}개"
            enabled = True
            button_text = "위치 변경"
        self.output_summary_label.setText(summary)
        self.open_output_action.setEnabled(enabled)
        self.change_output_button.setText(button_text)
        source_text = str(self.source_path) if self.source_path else "열리지 않음"
        project_text = str(self.project_path) if self.project_path else "저장되지 않음"
        output_text = str(effective) if effective else "지정되지 않음"
        self.output_section.setToolTip(
            f"Source: {source_text}\nProject: {project_text}\nOutput: {output_text}"
        )

    def open_output_folder(self) -> None:
        effective = self.effective_output_path()
        if effective is None:
            QMessageBox.information(self, "출력 폴더", "먼저 출력 위치를 선택해 주세요.")
            return
        target = effective
        while not target.exists() and target != target.parent:
            target = target.parent
        if not target.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            QMessageBox.warning(self, "출력 폴더", f"폴더를 열 수 없습니다.\n\n{effective}")

    def save_project(self) -> None:
        if self.source_path is None:
            QMessageBox.information(self, "저장", "먼저 원본 이미지를 열어 주세요.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "프로젝트 저장", "", "TileNamer 프로젝트 (*.tilenamer.json)")
        if path:
            if not path.lower().endswith(".tilenamer.json"):
                path += ".tilenamer.json"
            TileProject(
                source_file=str(self.source_path), tile_size=32, model=self.model,
                layer_visibility=self.layer_visibility,
                grid_reference=self.grid_reference,
                layer_alignment_offsets=self.layer_alignment_offsets,
                alignment_correction_enabled=True,
                temporary_tags=self.temporary_tags,
                layer_grid_origins=self.layer_grid_origins,
                layer_grid_manual_overrides=self.layer_grid_manual_overrides,
                export_base_directory=(
                    str(self.export_base_directory)
                    if self.export_base_directory is not None else None
                ),
            ).save(path)
            self.project_path = Path(path).resolve()
            self._settings_dirty = False
            self.undo_stack.setClean()
            self._sync_project_dirty_state()
            self._update_output_bar()
            self.statusBar().showMessage(f"프로젝트 저장: {path}", 5000)

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 불러오기", "", "TileNamer 프로젝트 (*.tilenamer.json)")
        if not path:
            return
        try:
            project = TileProject.load(path)
            if project.tile_size != 32:
                raise ValueError("이 버전은 32×32 프로젝트만 지원합니다.")
            validated_tags: list[str] = []
            for tag in project.temporary_tags:
                validated_tags.append(validate_temporary_tag(
                    tag, validated_tags, {rule.prefix for rule in self.built_in_rules}
                ))
            allowed = {rule.name for rule in self.built_in_rules} | set(validated_tags)
            unknown = set(project.model.assignments) - allowed
            if unknown:
                raise ValueError(f"설정에 없는 카테고리: {', '.join(sorted(unknown))}")
            source_path = Path(project.source_file)
            if not source_path.exists():
                raise FileNotFoundError(f"원본 파일을 찾을 수 없습니다: {source_path}")
            candidate = load_source_document(
                source_path, project.layer_visibility,
                project.layer_alignment_offsets,
            )
            invalid = [asset for asset in project.model.all_assets()
                       if not project.grid_reference.contains(
                           asset, candidate.image.width, candidate.image.height
                       )]
            if invalid:
                raise ValueError("프로젝트 Assignment가 원본 이미지 범위를 벗어납니다.")
            self.model = project.model
            self.canvas.model = self.model
            self.temporary_tags = validated_tags
            self._rebuild_rules()
            self.grid_reference = project.grid_reference
            self.layer_grid_origins = dict(project.layer_grid_origins)
            self.layer_grid_manual_overrides = set(project.layer_grid_manual_overrides)
            self.layer_alignment_offsets = dict(project.layer_alignment_offsets)
            self.project_path = Path(path).resolve()
            self.export_base_directory = (
                Path(project.export_base_directory).resolve()
                if project.export_base_directory else None
            )
            self._apply_source(
                source_path, candidate, keep_assignments=True,
                preserve_grid_settings=True,
            )
            self._populate_category_tree()
            target = self.category_items.get(self.current_category())
            if target is None and self.category_items:
                target = next(iter(self.category_items.values()))
            if target is not None:
                self.category_tree.setCurrentItem(target)
            self._settings_dirty = False
            self.undo_stack.setClean()
            self._sync_project_dirty_state()
            self._update_output_bar()
        except Exception as error:
            QMessageBox.critical(self, "프로젝트 불러오기 실패", str(error))

    def export_all(self) -> None:
        self._export(None)

    def export_current(self) -> None:
        self._export(self.current_category())

    def export_other_location(self) -> None:
        if self.source_image is None:
            QMessageBox.information(self, "내보내기", "먼저 원본 이미지를 열어 주세요.")
            return
        output = QFileDialog.getExistingDirectory(
            self, "다른 위치로 전체 내보내기", self.preferences.last_export_directory
        )
        if not output:
            return
        self.preferences.last_export_directory = output
        self._run_export(None, Path(output))

    def _export(self, category: str | None) -> None:
        if self.source_image is None:
            QMessageBox.information(self, "내보내기", "먼저 원본 이미지를 열어 주세요.")
            return
        if self.export_base_directory is None and not self.choose_output_destination():
            return
        self._run_export(category, self.export_base_directory)

    def _run_export(self, category: str | None, output: Path) -> None:
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
            written = export_tiles(
                self.source_image, plan, overwrite=overwrite, grid=self.grid_reference
            )
            self._update_output_bar()
            QMessageBox.information(self, "내보내기 완료", f"PNG 에셋 {len(written)}개를 생성했습니다.")
        except Exception as error:
            QMessageBox.critical(self, "내보내기 실패", str(error))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.placement_preview_window is not None:
            self.placement_preview_window.close()
        super().closeEvent(event)
