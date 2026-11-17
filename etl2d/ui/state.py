"""What the window holds: the workflow, its file, the run going on (notes §11)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from imgui_bundle import imgui

from etl2d.graph import Graph, GraphError
from etl2d.registry import Registry, default_registry
from etl2d.run import RunResult, run


@dataclass
class State:
    registry: Registry = field(default_factory=default_registry)
    graph: Graph = field(default_factory=Graph)
    path: Path | None = None          # the workflow's file; None: never saved
    saved: str = ""                   # its text when read or saved: "changed?"
    selected: int | None = None       # the node shown in Properties
    log: list[tuple[str, str]] = field(default_factory=list)   # (kind, text): info, ok, error
    dirty_structure: bool = True      # §4: propagate again at the next frame
    placed: set[int] = field(default_factory=set)   # nodes whose position was given to the editor
    add_at: tuple[float, float] | None = None       # where the right click was, for "add node"
    select_new: list[int] = field(default_factory=list)   # nodes just added or pasted, to select in the editor
    selection: list[int] = field(default_factory=list)    # the nodes selected in the editor (for Copy)
    mouse_canvas: tuple[float, float] | None = None       # the mouse in the graph, where Paste puts nodes
    clip: str = ""                                        # the last Copy, if the system's clipboard fails
    fit_now: bool = False                                 # Edit > Fit: the whole graph in view at the next frame
    undo_stack: list = field(default_factory=list)        # the workflow's text before each change (§11)
    redo_stack: list = field(default_factory=list)
    last: str = ""                                        # the text as it was at the last look
    view_center: tuple[float, float] = (40.0, 80.0)  # canvas point in the middle of the graph view
    fit: int = 4                                    # frames before showing the whole graph (0: done)
    looked: tuple[int, str] | None = None            # §9: the kept output shown in the Table
    menu_output: tuple[int, str] = (0, "")           # the output whose menu is open (right click)
    menu_node: int = 0                               # the node (or group) whose menu is open
    renaming: int | None = None                      # a node or group to rename: the small window opens
    renaming_open: bool = False
    rename_text: str = ""
    dropped: list = field(default_factory=list)      # files dropped on the window, handled at the next frame

    # the run, in a thread
    running: bool = False
    live: dict = field(default_factory=dict)
    result: RunResult | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    started: float = 0.0
    started_wall: float = 0.0          # time.time() at the start: compared to files' times (§9)

    def __post_init__(self):
        for e in self.registry.errors:
            self.say("error", f"node file not loaded: {e}")
        self.saved = self.text()

    # ---- messages -----------------------------------------------------------------------
    def say(self, kind: str, text: str) -> None:
        self.log.append((kind, time.strftime("%H:%M:%S ") + text))
        del self.log[:-500]

    # ---- the file -------------------------------------------------------------------------
    def text(self) -> str:
        return json.dumps(self.graph.to_dict(), indent=1, ensure_ascii=False)

    def changed(self) -> bool:
        return self.text() != self.saved

    def title(self) -> str:
        name = self.path.name if self.path else "(new workflow)"
        return f"etl2d - {name}" + (" *" if self.changed() else "")

    def new(self) -> None:
        self.graph = Graph(base_dir=Path.cwd())
        self.path, self.selected, self.result, self.looked = None, None, None, None
        self.placed.clear()
        self.saved = self.last = self.text()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty_structure = True

    def open(self, path: Path) -> None:
        try:
            g = Graph.load(path, self.registry)
        except GraphError as e:
            self.say("error", str(e))
            return
        self.graph, self.path, self.selected, self.result = g, Path(path), None, None
        self.looked = min(g.keep) if g.keep else None   # §9: the Table shows the first kept output
        self.placed.clear()
        self.fit = 4
        if len({n.pos for n in g.nodes.values()}) < len(g.nodes):   # a file written by hand: no positions
            self.layout()
        self.saved = self.last = self.text()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty_structure = True
        self.say("info", f"opened {path}")

    def save(self, path: Path | None = None) -> bool:
        path = Path(path) if path else self.path
        if path is None:
            return False
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        old_base = self.graph.base_dir
        new_base = path.resolve().parent
        if self.path is None and old_base != new_base:
            self.say("info", f"relative paths now start at {new_base}")
        self.graph.base_dir = new_base
        self.graph.work_dir = path.resolve().with_suffix(".etl2d")   # §9: job.etl2d/ beside job.json
        text = self.text()
        path.write_text(text, encoding="utf-8")
        self.path, self.saved = path, text
        self.dirty_structure = True
        self.say("ok", f"saved {path}")
        return True

    def layout(self) -> None:
        """Nodes placed by their depth in the graph: readers at the left, writers at the right."""
        depth: dict[int, int] = {}
        for nid in self.graph.order():
            srcs = [l.src for l in self.graph.inputs_of(nid)]
            depth[nid] = 1 + max((depth[s] for s in srcs), default=-1)
        rows: dict[int, int] = {}
        for nid in self.graph.order():
            d = depth[nid]
            self.graph.nodes[nid].pos = (40 + d * 290, 60 + rows.get(d, 0) * 200)
            rows[d] = rows.get(d, 0) + 1
        self.placed.clear()

    # ---- structure (§4, §5) -----------------------------------------------------------------
    def refresh(self) -> None:
        """At most once a frame, after a change: every node's structure and error."""
        if not self.dirty_structure or self.running:
            return
        self.dirty_structure = False
        for gone in self.graph.prune_ports():   # a Filter's rule removed: its links (§7)
            self.say("info", gone)
        self.graph.propagate()
        if not any(n.error for n in self.graph.nodes.values()):
            self.graph.plan_needs()

    # ---- running (§3), in a thread so the window stays alive ----------------------------------
    def start_run(self) -> None:
        if self.running:
            return
        self.refresh()
        bad = [n for n in self.graph.nodes.values() if n.error]
        if bad:
            for n in bad:
                self.say("error", f"{n.label()}: {n.error}")
            self.say("error", "the workflow cannot run")
            return
        if not self.graph.nodes:
            return
        self.running, self.result, self.live = True, None, {}
        self.stop.clear()
        self.started = time.perf_counter()
        self.started_wall = time.time()
        self.say("info", "run started")

        def work():
            r = run(self.graph, live=self.live, stop=self.stop)
            self.result, self.running = r, False
            if r.ok:
                rate = r.read() / r.seconds if r.seconds > 0 else 0
                kept = f", {len(r.kept)} output{'s' if len(r.kept) > 1 else ''} kept" if r.kept else ""
                self.say("ok", f"{r.seconds:.2f} s: {r.read():,} features read ({rate:,.0f}/s), {r.written():,} written{kept}")
            else:
                self.say("error", r.error)
            for (nid, port), count in sorted(r.kept.items()):   # §9
                self.say("info", f"kept #{nid} {port}: {count:,} in {self.graph.kept_path(nid, port)}")

        threading.Thread(target=work, daemon=True).start()

    # ---- undo, redo (§11): the workflow's text before each change ----------------------------------
    UNDO_DEPTH = 200

    def track(self) -> None:
        """Called each frame, when no gesture is going on (no button held, no field being typed in):
        the workflow changed since the last look? Its text before goes on the undo stack. Any change
        is caught this way, wherever it was made: a node moved, a link, a parameter, a paste."""
        now = self.text()
        if now != self.last:
            self.undo_stack.append(self.last)
            del self.undo_stack[:-self.UNDO_DEPTH]
            self.redo_stack.clear()
            self.last = now

    def _restore(self, text: str) -> None:
        try:
            g = Graph.from_dict(json.loads(text), self.registry, self.graph.base_dir)
        except (GraphError, ValueError) as e:
            self.say("error", f"undo: {e}")
            return
        g.work_dir = self.graph.work_dir
        self.graph, self.last = g, text
        self.placed.clear()   # the positions given to the editor again
        if self.selected not in g.nodes:
            self.selected = None
        self.dirty_structure = True

    def undo(self) -> None:
        if self.undo_stack and not self.running:
            self.redo_stack.append(self.last)
            self._restore(self.undo_stack.pop())

    def redo(self) -> None:
        if self.redo_stack and not self.running:
            self.undo_stack.append(self.last)
            self._restore(self.redo_stack.pop())

    # ---- copy and paste (§11): through the system's clipboard, as JSON -------------------------
    def copy(self) -> int:
        """The selected nodes to the clipboard; how many."""
        data = self.graph.extract(self.selection)
        if data:
            self.clip = json.dumps(data, indent=1, ensure_ascii=False)
            imgui.set_clipboard_text(self.clip)
        return len(data.get("nodes", []))

    def paste(self, offset: tuple[float, float] | None = None) -> None:
        """The clipboard's nodes added: where the mouse is in the graph, else in the middle of the view."""
        data = None
        for text in (imgui.get_clipboard_text() or "", self.clip):   # the system's; else our own copy
            try:
                data = json.loads(text)
                if isinstance(data, dict) and "nodes" in data:
                    break
            except ValueError:
                pass
            data = None
        if data is None:
            return   # no nodes in the clipboard: nothing, silently
        at = self.mouse_canvas or self.view_center
        if offset is not None:
            at = offset
        try:
            new = self.graph.insert(data, self.registry, at)
        except GraphError as e:
            self.say("error", f"paste: {e}")
            return
        self.select_new = new
        self.selected = new[0] if new else self.selected
        self.dirty_structure = True
        self.say("info", f"pasted {len(new)} node{'s' if len(new) > 1 else ''}")

    def duplicate(self) -> None:
        """Copy and paste in one: beside the originals (40 down, 40 right)."""
        if not self.selection:
            return
        x0 = min(self.graph.nodes[i].pos[0] for i in self.selection if i in self.graph.nodes)
        y0 = min(self.graph.nodes[i].pos[1] for i in self.selection if i in self.graph.nodes)
        if self.copy():
            self.paste((x0 + 40, y0 + 40))

    def toggle_disabled(self, ids) -> None:
        """§11: switch nodes off (nothing goes through them), or on again (Ctrl+M, a right click)."""
        nodes = [self.graph.nodes[i] for i in ids if i in self.graph.nodes]
        if not nodes or self.running:
            return
        off = not all(n.disabled for n in nodes)   # some on: all off; all off: all on
        for n in nodes:
            n.disabled = off
        self.dirty_structure = True
        self.say("info", ("disabled " if off else "enabled ") + ", ".join(n.label() for n in nodes))

    def toggle_keep(self, nid: int, port: str) -> None:
        """§9: keep an output (its features written to the work folder at the next run), or stop."""
        k = (nid, port)
        if k in self.graph.keep:
            self.graph.keep.discard(k)
            if self.looked == k:
                self.looked = None
        else:
            self.graph.keep.add(k)
            self.looked = k
            if self.path is None:
                self.say("info", f"not saved yet: kept files go to {self.graph.work_folder()}")
        self.dirty_structure = True   # §5: a kept output needs everything

    def counts(self, nid: int):
        """(in, {port: out}) of a node: live during a run, then the last result's."""
        if nid in self.live:
            c_in, outs = self.live[nid]
            return c_in[0], {p: c[0] for p, c in outs.items()}
        if self.result and nid in self.result.stats:
            st = self.result.stats[nid]
            return st.features_in, dict(st.features_out)
        return None
