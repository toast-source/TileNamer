from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class ThemeColors:
    window: str
    panel: str
    field: str
    primary_text: str
    secondary_text: str
    disabled_text: str
    border: str
    button: str
    accent: str
    selection_text: str
    warning: str
    error: str


THEME_COLORS = {
    "light": ThemeColors(
        window="#f1f3f5", panel="#eef0f2", field="#ffffff",
        primary_text="#2f353b", secondary_text="#697079", disabled_text="#7d858d",
        border="#929ba4", button="#f3f5f6", accent="#3979b9",
        selection_text="#ffffff", warning="#8a4b08", error="#a22d2d",
    ),
    "dark": ThemeColors(
        window="#25282c", panel="#2b2f34", field="#30343a",
        primary_text="#e2e5e8", secondary_text="#b0b7be", disabled_text="#929aa2",
        border="#555c64", button="#343940", accent="#3979b9",
        selection_text="#ffffff", warning="#f2b66d", error="#f09a9a",
    ),
}


def colors_for(theme: str) -> ThemeColors:
    return THEME_COLORS["dark" if theme == "dark" else "light"]


def application_palette(theme: str) -> QPalette:
    colors = colors_for(theme)
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: colors.window,
        QPalette.ColorRole.WindowText: colors.primary_text,
        QPalette.ColorRole.Base: colors.field,
        QPalette.ColorRole.AlternateBase: colors.panel,
        QPalette.ColorRole.Text: colors.primary_text,
        QPalette.ColorRole.Button: colors.button,
        QPalette.ColorRole.ButtonText: colors.primary_text,
        QPalette.ColorRole.ToolTipBase: colors.field,
        QPalette.ColorRole.ToolTipText: colors.primary_text,
        QPalette.ColorRole.Highlight: colors.accent,
        QPalette.ColorRole.HighlightedText: colors.selection_text,
        QPalette.ColorRole.PlaceholderText: colors.secondary_text,
        QPalette.ColorRole.Link: colors.accent,
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(
            QPalette.ColorGroup.Disabled, role, QColor(colors.disabled_text),
        )
    return palette


def auxiliary_stylesheet(theme: str) -> str:
    colors = colors_for(theme)
    return f"""
        QDialog, QMessageBox, QInputDialog {{
            background: {colors.window}; color: {colors.primary_text};
        }}
        QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel,
        QDialog QGroupBox, QDialog QRadioButton, QDialog QCheckBox {{
            color: {colors.primary_text};
        }}
        QDialog QLineEdit, QDialog QComboBox, QDialog QSpinBox,
        QDialog QTextEdit, QDialog QPlainTextEdit, QDialog QTextBrowser,
        QDialog QListView, QDialog QTreeView {{
            color: {colors.primary_text}; background: {colors.field};
            border: 1px solid {colors.border};
        }}
        QDialog QPushButton {{
            color: {colors.primary_text}; background: {colors.button};
            border: 1px solid {colors.border}; border-radius: 4px;
            min-height: 28px; padding: 0 10px;
        }}
        QDialog QPushButton:disabled, QDialog QLineEdit:disabled,
        QDialog QComboBox:disabled, QDialog QSpinBox:disabled {{
            color: {colors.disabled_text};
        }}
        QMenu {{
            color: {colors.primary_text}; background: {colors.panel};
            border: 1px solid {colors.border};
        }}
        QMenu::item:selected {{
            color: {colors.selection_text}; background: {colors.accent};
        }}
        QToolTip {{
            color: {colors.primary_text}; background: {colors.field};
            border: 1px solid {colors.border};
        }}
    """


def apply_application_theme(application: QApplication | None, theme: str) -> None:
    if application is None:
        return
    normalized = "dark" if theme == "dark" else "light"
    if application.property("tilenamerTheme") == normalized:
        return
    application.setPalette(application_palette(normalized))
    application.setStyleSheet(auxiliary_stylesheet(normalized))
    application.setProperty("tilenamerTheme", normalized)
