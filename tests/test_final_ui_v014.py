from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QTextBrowser

from tilenamer import __version__
from tilenamer.preferences import Preferences
from tilenamer.resources import application_icon, icon_path
from tilenamer.ui import MainWindow


ROOT = Path(__file__).resolve().parents[1]


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def isolated_preferences(tmp_path: Path) -> Preferences:
    return Preferences(QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat))


def menu_texts(window: MainWindow, object_name: str) -> list[str]:
    menu = window.menuBar().findChild(QMenu, object_name)
    assert menu is not None
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def test_version_is_shared_by_title_about_and_check_mode(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, isolated_preferences(tmp_path))
    assert __version__ == "0.1.5"
    assert window.windowTitle() == f"TileNamer v{__version__}"
    assert not hasattr(window, "version_label")
    about = window.create_about_dialog()
    assert about.findChild(QLabel, "aboutVersion").text() == f"Version {__version__}"
    result = subprocess.run(
        [sys.executable, "main.py", "--check"], cwd=ROOT,
        check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == f"TileNamer v{__version__}: import/dependency check OK"
    about.close()
    window.close()
    qt.processEvents()


def test_editor_menus_and_help_content(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, isolated_preferences(tmp_path))
    assert [action.text().replace("&", "") for action in window.menuBar().actions()] == [
        "파일", "편집", "보기", "도움말",
    ]
    assert "이미지 열기…" in menu_texts(window, "fileMenu")
    assert "내보내기" in menu_texts(window, "fileMenu")
    assert menu_texts(window, "exportMenu") == [
        "전체 내보내기…", "현재 타일 내보내기…", "다른 위치로 내보내기…",
        "출력 위치 변경…", "출력 폴더 열기",
    ]
    assert menu_texts(window, "editMenu") == ["실행 취소", "다시 실행"]
    assert "Aseprite 자동 새로고침" in menu_texts(window, "viewMenu")
    assert menu_texts(window, "helpMenu") == ["TileNamer 사용법", "단축키", "TileNamer 정보"]

    help_dialog = window.create_help_dialog()
    help_text = help_dialog.findChild(QTextBrowser, "helpBrowser").toPlainText()
    for text in (
        "이미지 열기", "타일 종류", "임시 태그", "레이어", "리소스 교체",
        "Aseprite 자동 새로고침", "전체 내보내기", "Ctrl+Mouse Wheel", "Space+Drag",
    ):
        assert text in help_text
    shortcuts = window.create_shortcuts_dialog().findChild(QTextBrowser, "helpBrowser").toPlainText()
    assert "Ctrl + Z" in shortcuts and "Space + Drag" in shortcuts
    help_dialog.close()
    window.close()
    qt.processEvents()


def test_user_icon_resource_has_alpha_and_is_applied_everywhere(tmp_path: Path) -> None:
    qt = app()
    path = icon_path(ROOT)
    assert path == ROOT / "icon.ico"
    assert path.is_file()
    with Image.open(ROOT / "icon.png") as image:
        assert image.format == "PNG"
        assert image.width == image.height and image.width >= 256
        assert "A" in image.getbands()
        assert image.getchannel("A").getextrema()[0] == 0
    with Image.open(path) as image:
        assert image.format == "ICO"
        assert image.ico.sizes() == {
            (16, 16), (24, 24), (32, 32), (48, 48),
            (64, 64), (128, 128), (256, 256),
        }
    assert not application_icon(ROOT).isNull()
    window = MainWindow(ROOT, isolated_preferences(tmp_path))
    assert not QApplication.instance().windowIcon().isNull()
    assert not window.windowIcon().isNull()
    about = window.create_about_dialog()
    assert not about.windowIcon().isNull()
    assert about.findChild(QLabel, "aboutIcon").pixmap() is not None
    about.close()
    window.close()
    qt.processEvents()


def test_compact_toolbar_is_responsive_and_settings_live_in_view_menu(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, isolated_preferences(tmp_path))
    window.resize(window.minimumSize())
    window.show()
    qt.processEvents()
    button_texts = [button.text() for button in window.toolbar_primary_buttons]
    assert button_texts[:5] == ["이미지 열기", "리소스 교체", "프로젝트 저장", "전체 내보내기", "현재 타일"]
    assert "프로젝트 불러오기" not in button_texts
    assert "다른 폴더로 내보내기" not in button_texts
    assert not hasattr(window, "alpha_background_combo")
    assert not hasattr(window, "auto_reload_check")
    assert window.toolbar_widget.sizeHint().width() <= window.width()
    assert window.viewport_stack.width() >= 300
    assert window.main_splitter.widget(0).width() >= 210
    assert window.main_splitter.widget(2).width() >= 250
    assert window.toolbar_layout.indexOf(window.zoom_out_button) > window.toolbar_layout.indexOf(window.redo_button)
    window.close()
    qt.processEvents()


def test_temporary_tag_buttons_follow_selected_category(tmp_path: Path) -> None:
    qt = app()
    window = MainWindow(ROOT, isolated_preferences(tmp_path))
    window.category_tree.setCurrentItem(window.category_items["Platform_Center"])
    assert window.add_tag_button.text() == "+ 임시 태그"
    assert window.add_tag_button.isEnabled()
    heights = {button.minimumHeight() for button in window.temporary_tag_buttons}
    assert len(heights) == 1 and next(iter(heights)) >= 32
    assert not window.rename_tag_button.isEnabled()
    assert not window.delete_tag_button.isEnabled()
    window.add_temporary_tag("Map_Only")
    assert window.rename_tag_button.isEnabled()
    assert window.delete_tag_button.isEnabled()
    window.category_tree.setCurrentItem(window.category_items["Solid_Top"])
    assert not window.rename_tag_button.isEnabled()
    assert not window.delete_tag_button.isEnabled()
    window.close()
    qt.processEvents()
