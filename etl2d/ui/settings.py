"""The window's settings: where render2d is, a basemap (notes §9). Beside etl2d.ini, not beside the workflows."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from imgui_bundle import imgui
from imgui_bundle import portable_file_dialogs as pfd

from etl2d.render import settings_folder


def folder() -> Path:
    return settings_folder()


class Settings:
    def __init__(self):
        self.render2d = ""   # render2d.exe; empty: looked for on the PATH
        self.basemap = ""    # a SOURCE drawn under the kept file (basemaps/urbis-ortho-2024.xml...)
        self.load()

    def path(self) -> Path:
        return folder() / "settings.json"

    def load(self) -> None:
        try:
            d = json.loads(self.path().read_text(encoding="utf-8"))
            self.render2d = str(d.get("render2d", ""))
            self.basemap = str(d.get("basemap", ""))
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            self.path().parent.mkdir(parents=True, exist_ok=True)
            self.path().write_text(json.dumps({"render2d": self.render2d, "basemap": self.basemap}, indent=1),
                                   encoding="utf-8")
        except OSError:
            pass

    def render2d_exe(self) -> str | None:
        """The program to call: the one set, else render2d on the PATH; None when neither."""
        if self.render2d:
            return self.render2d if Path(self.render2d).is_file() else None
        return shutil.which("render2d")


def _path_field(label: str, value: str, title: str, filters: list[str]) -> tuple[bool, str]:
    imgui.text(label)
    imgui.set_next_item_width(-imgui.get_font_size() * 3)
    changed, value = imgui.input_text(f"##{label}", value)
    imgui.same_line()
    if imgui.button(f"...##{label}"):
        files = pfd.open_file(title, str(Path(value).parent) if value else "", filters).result()
        if files:
            value, changed = files[0], True
    return changed, value


def draw(settings: Settings, open_now: bool) -> None:
    """Window > Settings: a small modal window."""
    if open_now:
        imgui.open_popup("Settings")
    imgui.set_next_window_size(imgui.ImVec2(imgui.get_font_size() * 36, 0))
    if imgui.begin_popup_modal("Settings", None, imgui.WindowFlags_.always_auto_resize)[0]:
        c1, settings.render2d = _path_field("render2d (empty: the one on the PATH)", settings.render2d,
                                            "render2d", ["render2d", "render2d*", "All files", "*"])
        exe = settings.render2d_exe()
        imgui.text_colored(imgui.ImVec4(0.62, 0.62, 0.62, 1) if exe else imgui.ImVec4(1, 0.45, 0.4, 1),
                           f"uses {exe}" if exe else "not found: the Map cannot draw")
        imgui.separator()
        c2, settings.basemap = _path_field("basemap under the kept file (empty: none)", settings.basemap,
                                           "A basemap", ["Basemaps", "*.xml *.tif *.gpkg *.fgb", "All files", "*"])
        if c1 or c2:
            settings.save()
        imgui.separator()
        if imgui.button("Close"):
            imgui.close_current_popup()
        imgui.end_popup()
