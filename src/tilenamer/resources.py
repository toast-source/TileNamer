from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon


WINDOWS_APP_USER_MODEL_ID = "toast-source.TileNamer"


def project_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def icon_path(root: Path | None = None, platform_name: str | None = None) -> Path | None:
    base = root.resolve() if root is not None else project_root()
    names = ("icon.ico", "icon.png") if (platform_name or sys.platform) == "win32" else ("icon.png",)
    return next((base / name for name in names if (base / name).is_file()), None)


def application_icon(root: Path | None = None) -> QIcon:
    path = icon_path(root)
    return QIcon(str(path)) if path is not None else QIcon()


def set_windows_app_user_model_id(
    platform_name: str | None = None, shell32=None,
) -> bool:
    """Set taskbar identity before QApplication is created on Windows."""
    if (platform_name or sys.platform) != "win32":
        return False
    try:
        api = shell32 if shell32 is not None else ctypes.windll.shell32
        api.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        return False
    return True
