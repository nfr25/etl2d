"""Where render2d is, for the window's Map and the MapWriter (notes §9, §7)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def settings_folder() -> Path:
    """The user's settings folder, as hello_imgui's for etl2d.ini: %APPDATA%\\etl2d, ~/.config/etl2d."""
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    return Path(base or Path.home() / ".config") / "etl2d"


def find_render2d(given: str = "") -> str | None:
    """render2d's program: the one given; else the window's setting (settings.json); else the PATH.
    None when none is found."""
    if given:
        return given if Path(given).is_file() else None
    try:
        d = json.loads((settings_folder() / "settings.json").read_text(encoding="utf-8"))
        p = str(d.get("render2d", ""))
        if p and Path(p).is_file():
            return p
    except (OSError, ValueError):
        pass
    return shutil.which("render2d")
