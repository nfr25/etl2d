"""etl2d's window: the node library, the graph, the properties, the log (notes §11)."""

from __future__ import annotations

from pathlib import Path

from imgui_bundle import hello_imgui, imgui, immapp
from imgui_bundle import imgui_node_editor as ed
from imgui_bundle import portable_file_dialogs as pfd

from etl2d import __version__
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
        self.ask_quit = False
        self.settings = settings.Settings()
        self.map = MapView(self.settings)
        self.open_settings = False
        self.last_looked = self.state.looked   # the Table comes to the front when it changes, not at start
        self.params = self._runner_params()

    # ---- the file menu ----------------------------------------------------------------------
    def open(self) -> None:
        files = pfd.open_file("Open a workflow", str(self.state.graph.base_dir),
                              ["etl2d workflows", "*.json", "All files", "*"]).result()
        if files:
            self.state.open(Path(files[0]))

    def save(self, as_new: bool = False) -> bool:
        editor.sync_positions(self.state)
        if self.state.path is None or as_new:
            target = pfd.save_file("Save the workflow", str(self.state.graph.base_dir / "workflow.json"),
                                   ["etl2d workflows", "*.json"]).result()
            if not target:
                return False
            return self.state.save(Path(target))
        return self.state.save()

    def menus(self) -> None:
        st = self.state
        if imgui.begin_menu("File"):
            if imgui.menu_item_simple("New", "Ctrl+N"):
                st.new()
            if imgui.menu_item_simple("Open...", "Ctrl+O"):
                self.open()
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
            self.open()
        elif ctrl and imgui.is_key_pressed(imgui.Key.n, False):
            st.new()
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
        """Closing with something not saved: a question first."""
        if self.params.app_shall_exit and self.state.changed() and not self.ask_quit:
            self.params.app_shall_exit = False
            self.ask_quit = True
            imgui.open_popup("Save first?")
        if imgui.begin_popup_modal("Save first?", None, imgui.WindowFlags_.always_auto_resize)[0]:
            imgui.text("The workflow has changes that are not saved.")
            if imgui.button("Save"):
                if self.save():
                    self.params.app_shall_exit = True
                else:
                    self.ask_quit = False   # no file chosen: the window stays, the question comes again next time
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Don't save"):
                self.state.saved = self.state.text()
                self.params.app_shall_exit = True
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Cancel"):
                self.ask_quit = False
                imgui.close_current_popup()
            imgui.end_popup()

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
