from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor


@dataclass
class Preferences:
    settings: QSettings

    @classmethod
    def default(cls) -> "Preferences":
        return cls(QSettings("SOUTHPAW GAMES", "TileNamer"))

    @property
    def theme(self) -> str:
        value = str(self.settings.value("appearance/theme", "light"))
        return value if value in {"light", "dark"} else "light"

    @theme.setter
    def theme(self, value: str) -> None:
        self.settings.setValue("appearance/theme", value)

    @property
    def alpha_background(self) -> str:
        value = str(self.settings.value("appearance/alpha_background", "medium"))
        return value if value in {"light", "medium", "dark", "custom"} else "medium"

    @alpha_background.setter
    def alpha_background(self, value: str) -> None:
        self.settings.setValue("appearance/alpha_background", value)

    @property
    def custom_alpha_color(self) -> str:
        return str(self.settings.value("appearance/custom_alpha_color", "#8a8f96"))

    @custom_alpha_color.setter
    def custom_alpha_color(self, value: str) -> None:
        self.settings.setValue("appearance/custom_alpha_color", value)

    @property
    def auto_reload_aseprite(self) -> bool:
        return self.settings.value("source/auto_reload_aseprite", True, type=bool)

    @auto_reload_aseprite.setter
    def auto_reload_aseprite(self, value: bool) -> None:
        self.settings.setValue("source/auto_reload_aseprite", value)

    @property
    def last_export_directory(self) -> str:
        return str(self.settings.value("export/last_directory", ""))

    @last_export_directory.setter
    def last_export_directory(self, value: str) -> None:
        self.settings.setValue("export/last_directory", value)

    def alpha_colors(self) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        presets = {
            "light": ((232, 234, 237, 255), (202, 206, 211, 255)),
            "medium": ((122, 122, 122, 255), (96, 96, 96, 255)),
            "dark": ((67, 71, 76, 255), (45, 48, 52, 255)),
        }
        if self.alpha_background != "custom":
            return presets[self.alpha_background]
        base = QColor(self.custom_alpha_color)
        if not base.isValid():
            base = QColor("#8a8f96")
        light = base.lighter(118)
        dark = base.darker(118)
        return (
            (light.red(), light.green(), light.blue(), 255),
            (dark.red(), dark.green(), dark.blue(), 255),
        )
