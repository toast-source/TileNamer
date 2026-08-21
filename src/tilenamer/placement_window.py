from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QIcon, QMouseEvent, QPainter, QPalette, QPen, QPixmap, QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QStyle, QVBoxLayout, QWidget,
)

from .exporter import extract_assignment_image
from .grid import GridReference
from .model import AssignmentModel
from .placement import (
    PlacementCell, PlacementResult, available_families, available_patterns,
    build_placement_result,
)


@dataclass(frozen=True)
class PlacementPreviewSource:
    model: AssignmentModel
    image: Image.Image | None
    grid: GridReference
    alpha_colors: tuple[tuple[int, int, int, int], tuple[int, int, int, int]]


class ContentAwareComboBox(QComboBox):
    """Keep the control compact while sizing its popup from rendered text."""

    def _popup_widths(self) -> tuple[int, int]:
        view = self.view()
        metrics = view.fontMetrics()
        text_width = max(
            (metrics.horizontalAdvance(self.itemText(index)) for index in range(self.count())),
            default=0,
        )
        delegate_width = max(
            (view.sizeHintForIndex(view.model().index(index, 0)).width()
             for index in range(self.count())),
            default=0,
        )
        style = self.style()
        scrollbar = style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, self)
        frame = view.frameWidth() * 2
        icon_area = self.view().iconSize().width() + 8 if any(
            not self.itemIcon(index).isNull() for index in range(self.count())
        ) else 0
        # Use both the delegate's real size hint and font metrics. Scrollbar and
        # frame space belong outside the item rect and must be reserved separately.
        item_width = max(delegate_width, text_width + 14 + icon_area)
        view_width = item_width + scrollbar + frame + 6
        popup = view.window()
        margins = popup.contentsMargins() if popup is not None else None
        popup_extra = margins.left() + margins.right() if margins is not None else 0
        required = view_width + popup_extra
        screen = self.screen()
        maximum = screen.availableGeometry().width() - 16 if screen is not None else required
        popup_width = max(self.width(), min(required, maximum))
        return popup_width, min(view_width, max(1, popup_width - popup_extra))

    def popup_content_width(self) -> int:
        return self._popup_widths()[0]

    def update_popup_width(self) -> int:
        view = self.view()
        view.setTextElideMode(Qt.TextElideMode.ElideNone)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        width, view_width = self._popup_widths()
        view.setMinimumWidth(view_width)
        view.setMaximumWidth(16777215)
        popup = view.window()
        if popup is not None and popup is not self:
            popup.setMinimumWidth(width)
            popup.setMaximumWidth(16777215)
            if popup.isVisible():
                popup.resize(width, popup.height())
        return width

    def showPopup(self) -> None:  # noqa: N802
        self.update_popup_width()
        super().showPopup()
        self.update_popup_width()
        QTimer.singleShot(0, self.update_popup_width)


class PlacementCanvas(QWidget):
    tile_clicked = Signal(object)
    zoom_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("placementCanvas")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(360, 240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.result: PlacementResult | None = None
        self.pixmaps: dict[object, QPixmap] = {}
        self.show_grid = True
        self.alpha_colors = (QColor(138, 143, 150), QColor(107, 112, 119))
        self.workspace_color = QColor("#24272b")
        self.field_color = QColor("#30353a")
        self.zoom = 1.0
        self.pan_offset = QPointF()
        self.fit_mode = True
        self._view_initialized = False
        self._fit_pending = False
        self._space_down = False
        self._panning = False
        self._press_pos: QPointF | None = None
        self._last_pan_pos: QPointF | None = None
        self.hovered_cell: PlacementCell | None = None

    def set_preview(self, result: PlacementResult | None, source: PlacementPreviewSource) -> None:
        self.result = result
        self.hovered_cell = None
        self.alpha_colors = tuple(QColor(*color) for color in source.alpha_colors)
        self.pixmaps.clear()
        if result is not None and source.image is not None:
            for cell in result.cells:
                identity = cell.assignment_identity
                if identity is None or identity in self.pixmaps:
                    continue
                assignment = next((a for a in source.model.assets(cell.category)
                                   if a.selected_cells == identity.selected_cells), None)
                if assignment is None:
                    continue
                try:
                    image = extract_assignment_image(source.image, assignment, source.grid)
                except ValueError:
                    continue
                self.pixmaps[identity] = QPixmap.fromImage(ImageQt(image).copy())
        if result is not None and self.fit_mode:
            self.request_fit()
        self.update()

    def set_grid_visible(self, visible: bool) -> None:
        self.show_grid = visible
        self.update()

    def set_theme(self, dark: bool) -> None:
        self.workspace_color = QColor("#24272b" if dark else "#d9dee3")
        self.field_color = QColor("#343a40" if dark else "#f4f6f8")
        self.update()

    def fit_view(self) -> None:
        self.fit_mode = True
        self._apply_fit()

    def request_fit(self) -> None:
        self.fit_mode = True
        if self._fit_pending:
            return
        self._fit_pending = True
        QTimer.singleShot(0, self._apply_fit)

    def reset_for_open(self) -> None:
        self._view_initialized = False
        self.fit_mode = True
        self.pan_offset = QPointF()

    def _fit_zoom(self) -> float | None:
        result = self.result
        if result is None or result.width <= 0 or result.height <= 0:
            return None
        margin = 20.0
        available_width = self.width() - margin * 2
        available_height = self.height() - margin * 2
        if available_width <= 0 or available_height <= 0:
            return None
        return min(
            available_width / (result.width * 32.0),
            available_height / (result.height * 32.0),
            8.0,
        )

    def _apply_fit(self) -> None:
        self._fit_pending = False
        if not self.fit_mode:
            return
        fit_zoom = self._fit_zoom()
        if fit_zoom is None:
            return
        self.zoom = max(0.01, fit_zoom)
        self.pan_offset = QPointF()
        self._view_initialized = True
        self.zoom_changed.emit(self.zoom)
        self.update()

    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        old_scale, old_origin = self._view_transform()
        new_zoom = max(0.25, min(8.0, float(zoom)))
        self.fit_mode = False
        self._view_initialized = True
        if math.isclose(new_zoom, self.zoom):
            return
        anchor = anchor or QPointF(self.width() / 2, self.height() / 2)
        world = (anchor - old_origin) / old_scale if old_scale else QPointF()
        self.zoom = new_zoom
        new_scale, new_origin = self._view_transform(include_pan=False)
        self.pan_offset = anchor - new_origin - world * new_scale
        self.zoom_changed.emit(self.zoom)
        self.update()

    def _view_transform(self, include_pan: bool = True) -> tuple[float, QPointF]:
        result = self.result
        if result is None or result.width <= 0 or result.height <= 0:
            return 1.0, QPointF()
        scale = 32.0 * self.zoom
        origin = QPointF(
            (self.width() - result.width * scale) / 2,
            (self.height() - result.height * scale) / 2,
        )
        if include_pan:
            origin += self.pan_offset
        return scale, origin

    def content_screen_rect(self) -> QRectF:
        if self.result is None:
            return QRectF()
        scale, origin = self._view_transform()
        return QRectF(origin.x(), origin.y(),
                      self.result.width * scale, self.result.height * scale)

    def logical_cell_center(self, coord: tuple[int, int]) -> QPointF:
        scale, origin = self._view_transform()
        return origin + QPointF((coord[0] + .5) * scale, (coord[1] + .5) * scale)

    def hit_test(self, position: QPointF) -> PlacementCell | None:
        scale, origin = self._view_transform()
        if self.result is None or scale <= 0:
            return None
        world = (position - origin) / scale
        coord = (math.floor(world.x()), math.floor(world.y()))
        return next((cell for cell in reversed(self.result.cells)
                     if coord in cell.occupied_cells), None)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(720, 420)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.set_space_held(True)
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.set_space_held(False)
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._press_pos = event.position()
        if self._space_down:
            self._panning = True
            self.fit_mode = False
            self._view_initialized = True
            self._last_pan_pos = event.position()
            self._set_hovered_cell(None)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning and self._last_pan_pos is not None:
            self.pan_offset += event.position() - self._last_pan_pos
            self._last_pan_pos = event.position()
            self.update()
            event.accept()
        else:
            self._set_hovered_cell(self.hit_test(event.position()))
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        was_panning, press_pos = self._panning, self._press_pos
        self._panning = False
        self._last_pan_pos = self._press_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._space_down
                       else Qt.CursorShape.ArrowCursor)
        if not was_panning and press_pos is not None:
            if (event.position() - press_pos).manhattanLength() <= 5:
                cell = self.hit_test(event.position())
                if cell is not None:
                    self.tile_clicked.emit(cell)
        if not self._space_down:
            self._set_hovered_cell(self.hit_test(event.position()))
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom * 1.2 ** (event.angleDelta().y() / 120), event.position())
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.fit_mode and self.result is not None:
            self.request_fit()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hovered_cell(None)
        super().leaveEvent(event)

    def set_space_held(self, held: bool) -> None:
        self._space_down = held
        if not held:
            self._panning = False
            self._last_pan_pos = None
        self._update_interaction_cursor()

    def cancel_interaction(self) -> None:
        self._space_down = False
        self._panning = False
        self._last_pan_pos = None
        self._press_pos = None
        self._set_hovered_cell(None)
        self.unsetCursor()

    def _set_hovered_cell(self, cell: PlacementCell | None) -> None:
        if cell is self.hovered_cell:
            return
        self.hovered_cell = cell
        self._update_interaction_cursor()
        self.update()

    def _update_interaction_cursor(self) -> None:
        if self._panning:
            cursor = Qt.CursorShape.ClosedHandCursor
        elif self._space_down:
            cursor = Qt.CursorShape.OpenHandCursor
        elif self.hovered_cell is not None:
            cursor = Qt.CursorShape.PointingHandCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)

    def _paint_hover_outline(self, painter: QPainter, cell: PlacementCell,
                             scale: float, origin: QPointF) -> None:
        occupied = set(cell.occupied_cells)
        color = QColor("#ff9a76") if cell.is_warning else QColor("#55efff")
        painter.setPen(QPen(color, 3.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
        for x, y in occupied:
            left = origin.x() + x * scale
            top = origin.y() + y * scale
            right, bottom = left + scale, top + scale
            if (x, y - 1) not in occupied:
                painter.drawLine(QPointF(left, top), QPointF(right, top))
            if (x + 1, y) not in occupied:
                painter.drawLine(QPointF(right, top), QPointF(right, bottom))
            if (x, y + 1) not in occupied:
                painter.drawLine(QPointF(right, bottom), QPointF(left, bottom))
            if (x - 1, y) not in occupied:
                painter.drawLine(QPointF(left, bottom), QPointF(left, top))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.fillRect(self.rect(), self.workspace_color)
        if self.result is None:
            return
        scale, origin = self._view_transform()
        field_rect = QRectF(
            origin.x(), origin.y(), self.result.width * scale, self.result.height * scale,
        )
        painter.fillRect(field_rect, self.field_color)
        for cell in self.result.cells:
            for x, y in cell.occupied_cells:
                rect = QRectF(origin.x() + x * scale, origin.y() + y * scale, scale, scale)
                if cell.is_missing:
                    painter.fillRect(rect.adjusted(2, 2, -2, -2), QColor(176, 64, 64, 105))
            pixmap = self.pixmaps.get(cell.assignment_identity)
            if not cell.is_missing and pixmap is not None and not pixmap.isNull():
                xs, ys = zip(*cell.occupied_cells)
                target = QRectF(origin.x() + min(xs) * scale, origin.y() + min(ys) * scale,
                                (max(xs) - min(xs) + 1) * scale,
                                (max(ys) - min(ys) + 1) * scale)
                painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            if cell.fallback_for is not None:
                for x, y in cell.occupied_cells:
                    rect = QRectF(origin.x() + x * scale, origin.y() + y * scale,
                                  scale, scale)
                    painter.fillRect(rect.adjusted(2, 2, -2, -2), QColor(210, 65, 65, 88))
        if self.show_grid:
            painter.setPen(QPen(QColor(235, 239, 242, 105), 1))
            for y in range(self.result.height):
                for x in range(self.result.width):
                    painter.drawRect(QRectF(origin.x() + x * scale,
                                            origin.y() + y * scale, scale, scale))
        if self.hovered_cell is not None:
            self._paint_hover_outline(painter, self.hovered_cell, scale, origin)


class PlacementPreviewWindow(QWidget):
    assignment_locate_requested = Signal(object)
    missing_role_locate_requested = Signal(str)

    def __init__(self, parent: QWidget,
                 source_provider: Callable[[], PlacementPreviewSource],
                 app_icon: QIcon, theme: str) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("placementPreviewWindow")
        self.setWindowTitle("TileNamer — 배치 미리보기")
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.setMinimumSize(640, 480)
        self.resize(820, 600)
        self.source_provider = source_provider
        self.refresh_count = 0
        self._positioned = False
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(60)
        self.refresh_timer.timeout.connect(self.refresh_preview)
        self._build_ui()
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        self.apply_theme(theme)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        controls = QFrame(objectName="placementControls")
        row = QHBoxLayout(controls)
        row.setContentsMargins(8, 6, 8, 6)
        row.addWidget(QLabel("세트"))
        self.family_combo = ContentAwareComboBox(objectName="placementFamily")
        for family in available_families():
            self.family_combo.addItem(family, family)
        row.addWidget(self.family_combo)
        row.addSpacing(12)
        row.addWidget(QLabel("패턴"))
        self.pattern_combo = ContentAwareComboBox(objectName="placementPattern")
        row.addWidget(self.pattern_combo)
        row.addStretch(1)
        self.fit_button = QPushButton("맞춤", objectName="placementFit")
        self.zoom_out_button = QPushButton("−", objectName="placementZoomOut")
        self.zoom_label = QLabel("—", objectName="placementZoomLabel")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_button = QPushButton("+", objectName="placementZoomIn")
        for widget in (self.fit_button, self.zoom_out_button, self.zoom_label,
                       self.zoom_in_button):
            row.addWidget(widget)
        self.grid_toggle = QCheckBox("격자")
        self.grid_toggle.setChecked(True)
        row.addWidget(self.grid_toggle)
        outer.addWidget(controls)
        self.canvas = PlacementCanvas()
        outer.addWidget(self.canvas, 1)
        self.empty_label = QLabel(
            "등록된 타일이 없습니다.\n\n타일을 등록하면 배치 결과가 여기에 표시됩니다.",
            self.canvas, objectName="placementEmpty",
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.summary_label = QLabel("준비 0 / 0 · 누락 0", objectName="placementSummary")
        self.missing_label = QLabel("", objectName="placementMissing")
        self.missing_label.setWordWrap(True)
        outer.addWidget(self.summary_label)
        outer.addWidget(self.missing_label)
        self.family_combo.currentIndexChanged.connect(self._family_changed)
        self.pattern_combo.currentIndexChanged.connect(self._pattern_changed)
        self.grid_toggle.toggled.connect(self.canvas.set_grid_visible)
        self.canvas.tile_clicked.connect(self._canvas_tile_clicked)
        self.canvas.zoom_changed.connect(
            lambda zoom: self.zoom_label.setText(f"{round(zoom * 100):d}%"))
        self.zoom_out_button.clicked.connect(lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25))
        self.zoom_in_button.clicked.connect(lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25))
        self.fit_button.clicked.connect(self.canvas.fit_view)
        self._family_changed()

    def _family_changed(self, *_args) -> None:
        family = str(self.family_combo.currentData() or "Solid")
        self.pattern_combo.blockSignals(True)
        self.pattern_combo.clear()
        for pattern in available_patterns(family):
            self.pattern_combo.addItem(pattern, pattern)
        self.pattern_combo.blockSignals(False)
        self.family_combo.update_popup_width()
        self.pattern_combo.update_popup_width()
        self.canvas.request_fit()
        if self.isVisible():
            self.refresh_preview()

    def _pattern_changed(self, *_args) -> None:
        self.canvas.request_fit()
        if self.isVisible():
            self.refresh_preview()

    def _canvas_tile_clicked(self, cell: PlacementCell) -> None:
        if cell.fallback_for is not None:
            self.missing_role_locate_requested.emit(cell.fallback_for)
        elif cell.assignment_identity is None:
            self.missing_role_locate_requested.emit(cell.category)
        else:
            self.assignment_locate_requested.emit(cell.assignment_identity)

    def _is_preview_object(self, obj) -> bool:
        current = obj
        while current is not None:
            if current is self:
                return True
            current = current.parent() if hasattr(current, "parent") else None
        return False

    def _space_scope_active(self, receiver) -> bool:
        application = QApplication.instance()
        if application is None or not self.isVisible():
            return False
        return (
            self._is_preview_object(application.focusWidget())
            or self._is_preview_object(application.activeWindow())
        )

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type == QEvent.Type.WindowDeactivate and watched is self:
            self.canvas.cancel_interaction()
            return False
        if event_type not in (
            QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
        ):
            return super().eventFilter(watched, event)
        if not hasattr(event, "key") or event.key() != Qt.Key.Key_Space:
            return super().eventFilter(watched, event)
        if not self._space_scope_active(watched):
            return super().eventFilter(watched, event)
        event.accept()
        if event_type == QEvent.Type.KeyPress:
            self.canvas.set_space_held(True)
        elif event_type == QEvent.Type.KeyRelease:
            self.canvas.set_space_held(False)
        return True

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.empty_label.setGeometry(self.canvas.rect())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.canvas.reset_for_open()
        self.refresh_preview()
        if not self._positioned:
            self._position_next_to_parent()
            self._positioned = True

    def closeEvent(self, event) -> None:  # noqa: N802
        self.refresh_timer.stop()
        self.canvas.cancel_interaction()
        super().closeEvent(event)

    def _position_next_to_parent(self) -> None:
        parent, screen = self.parentWidget(), self.parentWidget().screen()
        if parent is None or screen is None:
            return
        available, frame = screen.availableGeometry(), parent.frameGeometry()
        x, y = frame.right() + 8, max(available.top(), frame.top())
        if x + self.width() <= available.right():
            self.move(x, min(y, available.bottom() - self.height()))

    def schedule_refresh(self, *_args) -> None:
        if self.isVisible():
            self.refresh_timer.start()

    def refresh_preview(self) -> None:
        source = self.source_provider()
        if source.image is None or not any(source.model.all_assets()):
            result = None
            self.summary_label.setText("준비 0 / 0 · 누락 0")
            self.missing_label.clear()
            self.empty_label.show()
        else:
            family = str(self.family_combo.currentData() or "Solid")
            pattern = str(self.pattern_combo.currentData() or "종합")
            result = build_placement_result(family, source.model, pattern)
            self.summary_label.setText(
                f"준비 {len(result.ready_roles)} / {len(result.required_roles)} · "
                f"누락 {result.missing_cell_count}")
            self.missing_label.setText("\n".join(
                f"{category} ×{count}" for category, count in result.missing_counts))
            self.empty_label.hide()
        self.canvas.set_preview(result, source)
        self.refresh_count += 1

    def update_alpha(self, colors) -> None:
        source = self.source_provider()
        self.canvas.set_preview(self.canvas.result, PlacementPreviewSource(
            source.model, source.image, source.grid, colors))

    def apply_theme(self, theme: str) -> None:
        dark = theme == "dark"
        self.setStyleSheet("""
            QWidget#placementPreviewWindow { background: %s; color: %s; }
            #placementControls { background: %s; border: 1px solid %s; }
            QComboBox, QPushButton { min-height: 30px; background: %s; color: %s; border: 1px solid %s; }
            #placementControls QLabel, #placementControls QCheckBox { color: %s; background: transparent; }
            #placementZoomLabel { color: %s; font-weight: 600; }
            #placementCanvas { background: #24272b; border: 1px solid %s; }
            #placementEmpty { color: #aeb4bb; background: transparent; }
            #placementSummary { color: %s; font-weight: 600; }
            #placementMissing { color: %s; }
        """ % (("#25282c", "#e2e5e8", "#2c3035", "#4b5158", "#343940",
                  "#e4e7ea", "#555c64", "#e2e5e8", "#eef1f3", "#555c64",
                  "#b9c0c7", "#e59a9a") if dark else
                 ("#eef0f2", "#30363c", "#f1f3f5", "#a5adb5", "white",
                  "#30363c", "#929ba4", "#30363c", "#30363c", "#737b84",
                  "#5f6871", "#9c3939")))
        self._apply_combo_popup_theme(dark)
        self.canvas.set_theme(dark)

    def _apply_combo_popup_theme(self, dark: bool) -> None:
        if dark:
            foreground, background = QColor("#e4e7ea"), QColor("#2f343a")
            selected_foreground, selected_background = QColor("#ffffff"), QColor("#256f82")
            hover_background, disabled = QColor("#414850"), QColor("#8b9299")
        else:
            foreground, background = QColor("#30363c"), QColor("#ffffff")
            selected_foreground, selected_background = QColor("#ffffff"), QColor("#327f91")
            hover_background, disabled = QColor("#dce7ea"), QColor("#8a9299")
        for combo in (self.family_combo, self.pattern_combo):
            view = combo.view()
            palette = view.palette()
            palette.setColor(QPalette.ColorRole.Text, foreground)
            palette.setColor(QPalette.ColorRole.Base, background)
            palette.setColor(QPalette.ColorRole.HighlightedText, selected_foreground)
            palette.setColor(QPalette.ColorRole.Highlight, selected_background)
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled,
            )
            view.setPalette(palette)
            view.setStyleSheet("""
                QAbstractItemView {
                    color: %s; background: %s; border: 1px solid %s;
                    outline: 0; selection-color: %s; selection-background-color: %s;
                }
                QAbstractItemView::item { min-height: 28px; padding: 2px 7px; }
                QAbstractItemView::item:hover { color: %s; background: %s; }
                QAbstractItemView::item:selected { color: %s; background: %s; }
                QAbstractItemView::item:disabled { color: %s; }
            """ % (
                foreground.name(), background.name(),
                QColor("#555c64" if dark else "#929ba4").name(),
                selected_foreground.name(), selected_background.name(),
                foreground.name(), hover_background.name(),
                selected_foreground.name(), selected_background.name(), disabled.name(),
            ))
            combo.update_popup_width()
