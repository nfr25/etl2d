"""etl2d's window: the node library, the graph, the properties, the log (notes §11)."""

from __future__ import annotations

import webbrowser
from pathlib import Path

from imgui_bundle import hello_imgui, imgui, immapp
from imgui_bundle import imgui_node_editor as ed
from imgui_bundle import portable_file_dialogs as pfd

from etl2d import __version__

DOCS = Path(__file__).resolve().parents[2] / "docs"


def _browse(path: Path) -> None:
    """A page of the notes in the browser (a file of the program's folder)."""
    webbrowser.open(path.resolve().as_uri())


_version_cache: list = []


def _versions(st) -> list:
    """What etl2d runs on; render2d asked once (it is another program)."""
    if not _version_cache:
        import platform
        from osgeo import gdal, osr
        from imgui_bundle import imgui as _im
        import imgui_bundle
        out = [("etl2d", __version__), ("Python", platform.python_version()),
               ("GDAL", gdal.__version__)]
        try:
            out.append(("PROJ", f"{osr.GetPROJVersionMajor()}.{osr.GetPROJVersionMinor()}.{osr.GetPROJVersionMicro()}"))
        except Exception:
            pass
        out += [("imgui-bundle", getattr(imgui_bundle, "__version__", "?")), ("Dear ImGui", _im.get_version())]
        try:
            import numpy
            out.append(("numpy", numpy.__version__))
        except ImportError:
            pass
        exe = st.render2d_exe()
        v = "not found (Window > Settings)"
        if exe:
            import subprocess
            try:
                r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10,
                                   creationflags=0x08000000 if __import__("os").name == "nt" else 0)
                v = (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else exe
            except (OSError, subprocess.SubprocessError):
                v = f"{exe} (does not answer)"
        out.append(("render2d", v))
        _version_cache.extend(out)
    return _version_cache
from etl2d.ui import editor, properties, settings, table
from etl2d.ui.mapview import MapView
from etl2d.ui.editor import GREEN, GREY, RED
from etl2d.ui.state import State


def _library(state: State) -> None:
    """The node types found (§8), by category: a click adds one to the graph."""
    imgui.text_colored(GREY, "a click adds the node\n(or right click in the graph)")
    by_cat: dict[str, list] = {}
    for spec in state.registry.specs.values():
        by_cat.setdefault(spec.category, []).append(spec)
    for cat in sorted(by_cat):
        if imgui.collapsing_header(cat, imgui.TreeNodeFlags_.default_open):
            for spec in sorted(by_cat[cat], key=lambda s: s.name):
                if imgui.selectable(f"  {spec.name}", False)[0] and not state.running:
                    editor.add_node(state, spec.name)
                if spec.help:
                    imgui.set_item_tooltip(spec.help)
    if state.registry.errors:
        imgui.separator()
        imgui.text_colored(RED, "not loaded:")
        for e in state.registry.errors:
            imgui.text_wrapped(e)


def _log(state: State) -> None:
    colours = {"ok": GREEN, "error": RED}
    for kind, text in state.log:
        imgui.push_text_wrap_pos(0)
        if kind in colours:
            imgui.text_colored(colours[kind], text)
        else:
            imgui.text_unformatted(text)
        imgui.pop_text_wrap_pos()
    if imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 4:
        imgui.set_scroll_here_y(1.0)   # follows the last line, unless one scrolled up


class App:
    def __init__(self, path: Path | None = None):
        self.state = State()
        if path is not None:
            self.state.open(path)
        self.pending = None      # what waits for the answer to "Save first?" (quit, new, open)
        self.ask_now = False
        self.about = False       # Help > About etl2d
        self.settings = settings.Settings()
        if self.state.path is not None:
            self.settings.remember(self.state.path)
        self.map = MapView(self.settings)
        self.open_settings = False
        self.last_looked = self.state.looked   # the Table comes to the front when it changes, not at start
        self.params = self._runner_params()

    # ---- the file menu ----------------------------------------------------------------------
    def guard(self, action) -> None:
        """An action that would lose the changes not saved (quit, new, open): a question first."""
        if self.state.changed():
            self.pending, self.ask_now = action, True
        else:
            action()

    def open(self, path: Path | None = None) -> None:
        if path is None:
            files = pfd.open_file("Open a workflow", str(self.state.graph.base_dir),
                                  ["etl2d workflows", "*.json", "All files", "*"]).result()
            if not files:
                return
            path = Path(files[0])
        if not Path(path).exists():
            self.state.say("error", f"{path}: no such file (removed from the recent ones)")
            self.settings.forget(path)
            return
        self.state.open(Path(path))
        if self.state.path is not None and Path(path).resolve() == self.state.path.resolve():
            self.settings.remember(path)

    def save(self, as_new: bool = False) -> bool:
        editor.sync_positions(self.state)
        if self.state.path is None or as_new:
            target = pfd.save_file("Save the workflow", str(self.state.graph.base_dir / "workflow.json"),
                                   ["etl2d workflows", "*.json"]).result()
            if not target:
                return False
            ok = self.state.save(Path(target))
        else:
            ok = self.state.save()
        if ok and self.state.path is not None:
            self.settings.remember(self.state.path)
        return ok

    def menus(self) -> None:
        st = self.state
        if imgui.begin_menu("File"):
            if imgui.menu_item_simple("New", "Ctrl+N"):
                self.guard(st.new)
            if imgui.menu_item_simple("Open...", "Ctrl+O"):
                self.guard(self.open)
            if imgui.begin_menu("Open recent", bool(self.settings.recent)):
                for p in list(self.settings.recent):
                    shown = Path(p).name
                    if imgui.menu_item_simple(f"{shown}##{p}"):
                        self.guard(lambda p=p: self.open(Path(p)))
                    imgui.set_item_tooltip(p)
                imgui.separator()
                if imgui.menu_item_simple("Clear the list"):
                    self.settings.recent = []
                    self.settings.save()
                imgui.end_menu()
            if imgui.menu_item_simple("Save", "Ctrl+S"):
                self.save()
            if imgui.menu_item_simple("Save as..."):
                self.save(as_new=True)
            imgui.separator()
            if imgui.menu_item_simple("Reload nodes"):
                self.reload()
            imgui.separator()
            if imgui.menu_item_simple("Quit"):
                self.params.app_shall_exit = True
            imgui.end_menu()
        # (Help comes after Window, below.)
        if imgui.begin_menu("Edit"):
            some = bool(st.selection) and not st.running
            if imgui.menu_item("Undo", "Ctrl+Z", False, bool(st.undo_stack) and not st.running)[0]:
                st.undo()
            if imgui.menu_item("Redo", "Ctrl+Y", False, bool(st.redo_stack) and not st.running)[0]:
                st.redo()
            imgui.separator()
            if imgui.menu_item("Copy", "Ctrl+C", False, some)[0]:
                editor.sync_positions(st)
                st.copy()
            if imgui.menu_item("Paste", "Ctrl+V", False, not st.running)[0]:
                st.paste()
            if imgui.menu_item("Duplicate", "Ctrl+D", False, some)[0]:
                editor.sync_positions(st)
                st.duplicate()
            imgui.separator()
            if imgui.menu_item("Fit the graph", "F", False, bool(st.graph.nodes))[0]:
                st.fit_now = True
            imgui.text_disabled("a drag on the background: a box around nodes")
            imgui.end_menu()
        if imgui.begin_menu("Run"):
            if imgui.menu_item("Run", "F5", False, not st.running)[0]:
                st.start_run()
            if imgui.menu_item("Stop", "Esc", False, st.running)[0]:
                st.stop.set()
            imgui.end_menu()
        if imgui.begin_menu("Window"):
            for w in self.params.docking_params.dockable_windows:
                if imgui.menu_item(w.label, "", w.is_visible)[0]:
                    w.is_visible = not w.is_visible
            imgui.separator()
            if imgui.menu_item_simple("Settings..."):
                self.open_settings = True
            if imgui.menu_item_simple("Default layout"):
                self.params.docking_params.layout_reset = True
            imgui.end_menu()
        if imgui.begin_menu("Help"):
            if imgui.menu_item_simple("Notes (the design)"):
                _browse(DOCS / "notions.en.html")
            if imgui.menu_item_simple("Reading the code"):
                _browse(DOCS / "code.en.html")
            if imgui.menu_item_simple("etl2d on GitHub"):
                webbrowser.open("https://github.com/nfr25/etl2d")
            imgui.separator()
            if imgui.menu_item_simple("About etl2d"):
                self.about = True
            imgui.end_menu()
        # The state, at the right of the bar: the file, a run going on.
        label = (st.path.name if st.path else "(new workflow)") + (" *" if st.changed() else "")
        if st.running:
            import time
            label = f"running {time.perf_counter() - st.started:.1f} s   " + label
        w = imgui.calc_text_size(label).x
        imgui.same_line(imgui.get_window_width() - w - imgui.get_style().item_spacing.x * 3)
        imgui.text_colored(GREEN if st.running else GREY, label)

    def reload(self) -> None:
        """The node files read again (§8): a node changed in an editor is used at the next run."""
        from etl2d.registry import default_registry
        st = self.state
        st.registry = default_registry()
        for inst in st.graph.nodes.values():
            inst.spec = st.registry.find(inst.type)
        for e in st.registry.errors:
            st.say("error", f"node file not loaded: {e}")
        st.say("info", f"{len(st.registry.specs)} node types")
        st.dirty_structure = True

    def shortcuts(self) -> None:
        io = imgui.get_io()
        st = self.state
        if io.want_text_input:
            return
        ctrl = io.key_ctrl
        if ctrl and imgui.is_key_pressed(imgui.Key.s, False):
            self.save()
        elif ctrl and imgui.is_key_pressed(imgui.Key.o, False):
            self.guard(self.open)
        elif ctrl and imgui.is_key_pressed(imgui.Key.n, False):
            self.guard(st.new)
        elif ctrl and imgui.is_key_pressed(imgui.Key.z, False):
            (st.redo if io.key_shift else st.undo)()
        elif ctrl and imgui.is_key_pressed(imgui.Key.y, False):
            st.redo()
        elif ctrl and imgui.is_key_pressed(imgui.Key.c, False) and not st.running:
            editor.sync_positions(st)
            st.copy()
        elif ctrl and imgui.is_key_pressed(imgui.Key.v, False) and not st.running:
            st.paste()
        elif ctrl and imgui.is_key_pressed(imgui.Key.d, False) and not st.running:
            editor.sync_positions(st)
            st.duplicate()
        elif imgui.is_key_pressed(imgui.Key.f5, False):
            st.start_run()
        elif st.running and imgui.is_key_pressed(imgui.Key.escape, False):
            st.stop.set()

    def quit_question(self) -> None:
        """Quit, New, Open with something not saved: a question first (guard())."""
        if self.params.app_shall_exit and self.state.changed() and self.pending is None:
            self.params.app_shall_exit = False
            self.guard(lambda: setattr(self.params, "app_shall_exit", True))
        if self.ask_now:
            self.ask_now = False
            imgui.open_popup("Save first?")
        if imgui.begin_popup_modal("Save first?", None, imgui.WindowFlags_.always_auto_resize)[0]:
            imgui.text("The workflow has changes that are not saved.")
            if imgui.button("Save"):
                action, self.pending = self.pending, None
                if self.save() and action is not None:   # no file chosen: nothing happens
                    action()
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Don't save"):
                action, self.pending = self.pending, None
                self.state.saved = self.state.text()
                if action is not None:
                    action()
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Cancel"):
                self.pending = None
                imgui.close_current_popup()
            imgui.end_popup()

    def about_window(self) -> None:
        """Help > About etl2d: what it is, and what it runs on (as carto2D's, its notes §71)."""
        if not self.about:
            return
        imgui.set_next_window_size(imgui.ImVec2(imgui.get_font_size() * 34, 0), imgui.Cond_.first_use_ever)
        opened, self.about = imgui.begin("About etl2d", self.about)
        if opened:
            imgui.text_unformatted("etl2d - geographic data moved and transformed by a graph of nodes, on GDAL")
            imgui.text_disabled("Copyright (c) 2026 Noel Frankinet. GNU AGPL v3 or later.")
            def table(name, rows):
                if imgui.begin_table(name, 2, imgui.TableFlags_.sizing_fixed_fit):
                    imgui.table_setup_column("a", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column("b", imgui.TableColumnFlags_.width_stretch)
                    for k, v in rows:
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.text_disabled(k)
                        imgui.table_next_column()
                        imgui.text_wrapped(str(v))
                    imgui.end_table()

            imgui.separator_text("Versions")
            table("versions", _versions(self.settings))
            imgui.separator_text("Folders")
            home = Path(__file__).resolve().parents[2]
            table("folders", [("settings", settings.folder()), ("nodes", home / "nodes"),
                              ("custom nodes", home / "custom_nodes"),
                              ("workflow", self.state.path or "(not saved yet)"),
                              ("kept files", self.state.graph.work_folder())])
            imgui.separator_text("Nodes")
            by: dict = {}
            for spec in self.state.registry.specs.values():
                by.setdefault(spec.category, []).append(spec.name)
            for cat in sorted(by):
                imgui.text_wrapped(f"{cat}: {', '.join(sorted(by[cat]))}")
        imgui.end()

    def every_frame(self) -> None:
        # Another kept output chosen (a right click, a link clicked): the Table comes to the front.
        if self.state.looked != self.last_looked:
            self.last_looked = self.state.looked
            if self.state.looked is not None:
                for w in self.params.docking_params.dockable_windows:
                    if w.label == "Table":
                        w.is_visible = True
                        w.focus_window_at_next_frame = True
        # Undo (§11): after a gesture, not during it (a node dragged is one change, not sixty).
        st = self.state
        if not st.running and not imgui.is_mouse_down(imgui.MouseButton_.left) and not imgui.is_any_item_active():
            editor.sync_positions(st)
            st.track()
        self.shortcuts()
        self.quit_question()
        self.about_window()
        settings.draw(self.settings, self.open_settings)
        self.open_settings = False

    # ---- the window --------------------------------------------------------------------------
    def _runner_params(self) -> hello_imgui.RunnerParams:
        rp = hello_imgui.RunnerParams()
        rp.app_window_params.window_title = f"etl2d {__version__}"
        rp.app_window_params.window_geometry.size = (1600, 950)
        rp.imgui_window_params.show_menu_bar = True
        rp.imgui_window_params.show_menu_app = False
        rp.imgui_window_params.show_menu_view = False   # our own "Window" menu, after File and Run
        rp.imgui_window_params.default_imgui_window_type = \
            hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
        rp.callbacks.show_menus = self.menus
        rp.callbacks.show_gui = self.every_frame
        rp.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
        rp.ini_filename = "etl2d/etl2d.ini"

        left = hello_imgui.DockingSplit("MainDockSpace", "Left", imgui.Dir.left, 0.15)
        right = hello_imgui.DockingSplit("MainDockSpace", "Right", imgui.Dir.right, 0.25)
        bottom = hello_imgui.DockingSplit("MainDockSpace", "Bottom", imgui.Dir.down, 0.22)
        rp.docking_params.docking_splits = [left, right, bottom]

        def window(label, space, fn):
            w = hello_imgui.DockableWindow()
            w.label, w.dock_space_name, w.gui_function = label, space, fn
            return w

        rp.docking_params.dockable_windows = [
            window("Nodes", "Left", lambda: _library(self.state)),
            window("Map", "MainDockSpace", lambda: self.map.draw(self.state)),     # §9: a tab beside the Graph
            window("Graph", "MainDockSpace", lambda: editor.draw(self.state)),
            window("Properties", "Right", lambda: properties.draw(self.state)),
            window("Table", "Bottom", lambda: table.draw(self.state)),   # §9: beside the Log, a tab
            window("Log", "Bottom", lambda: _log(self.state)),          # the last docked: in front
        ]
        for w in rp.docking_params.dockable_windows:   # the Graph in front, not the Map
            w.focus_window_at_next_frame = w.label in ("Graph", "Log")
        return rp

    def run(self) -> None:
        addons = immapp.AddOnsParams()
        addons.with_node_editor = True
        cfg = ed.Config()
        cfg.settings_file = ""   # positions are the workflow's own (§10), not a file beside
        addons.with_node_editor_config = cfg
        immapp.run(self.params, addons)


def main(path: str | None = None) -> int:
    App(Path(path) if path else None).run()
    return 0
