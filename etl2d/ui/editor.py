"""The graph: nodes, their ports, the links (imgui-node-editor, notes §11)."""

from __future__ import annotations

import time

from imgui_bundle import imgui
from imgui_bundle import imgui_node_editor as ed

from etl2d.graph import Link
from etl2d.ui.state import State

RED = imgui.ImVec4(1.0, 0.45, 0.40, 1.0)
GREY = imgui.ImVec4(0.62, 0.62, 0.62, 1.0)
GREEN = imgui.ImVec4(0.45, 0.85, 0.50, 1.0)
YELLOW = imgui.ImVec4(1.0, 0.85, 0.35, 1.0)
CYAN = imgui.ImVec4(0.40, 0.80, 1.0, 1.0)     # §9: a kept output, its links

# The title band of a node: one colour per category, to see the role of a node at a glance.
BANDS = {"Readers": (0.20, 0.42, 0.30), "Writers": (0.47, 0.27, 0.22), "Geometry": (0.22, 0.33, 0.52),
         "Attributes": (0.40, 0.36, 0.20), "Python": (0.36, 0.25, 0.48), "Filters": (0.16, 0.42, 0.44)}
BAND_OTHER = (0.32, 0.32, 0.36)   # custom categories
FLASH = 0.8   # seconds a new link stays bright: the moment of joining, seen
_flash: dict = {}   # Link -> time it was made

# A link's id must stay the same from frame to frame: one number per link, kept.
_link_ids: dict[Link, int] = {}
_next_link_id = [1]


def link_id(link: Link) -> int:
    if link not in _link_ids:
        _link_ids[link] = _next_link_id[0]
        _next_link_id[0] += 1
    return _link_ids[link]


def pin_in(nid: int, i: int) -> int:
    return nid * 1000 + 1 + i


def pin_out(nid: int, i: int) -> int:
    return nid * 1000 + 500 + i


def port_of(state: State, pin: int):
    """(node id, port name, 'in' or 'out') of a pin number, or None."""
    nid, k = divmod(pin, 1000)
    inst = state.graph.nodes.get(nid)
    if inst is None or inst.spec is None:
        return None
    if k >= 500:
        outs = inst.outputs()
        return (nid, outs[k - 500], "out") if k - 500 < len(outs) else None
    ins = inst.spec.inputs
    return (nid, ins[k - 1].name, "in") if 0 <= k - 1 < len(ins) else None


def sync_positions(state: State) -> None:
    """The editor's positions back into the workflow (before saving)."""
    for nid, inst in state.graph.nodes.items():
        if nid in state.placed:
            p = ed.get_node_position(ed.NodeId(nid))
            inst.pos = (round(p.x), round(p.y))


def _node(state: State, inst) -> None:
    ed.begin_node(ed.NodeId(inst.id))
    imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + 230)
    imgui.text(inst.type)
    imgui.same_line()
    imgui.text_colored(imgui.ImVec4(0.85, 0.85, 0.85, 1.0), f"#{inst.id}")
    title_bottom = imgui.get_item_rect_max().y
    imgui.dummy(imgui.ImVec2(0, 2))
    if inst.spec is None:
        imgui.text_colored(RED, "unknown node type")
    else:
        # Inputs in a column at the left, outputs at the right: the links come in and go out.
        imgui.begin_group()
        for i, p in enumerate(inst.spec.inputs):
            ed.begin_pin(ed.PinId(pin_in(inst.id, i)), ed.PinKind.input)
            ed.pin_pivot_alignment(imgui.ImVec2(0.0, 0.5))
            imgui.text(f"o {p.name}")
            ed.end_pin()
        imgui.end_group()
        outputs = inst.outputs()
        if inst.spec.inputs and outputs:
            imgui.same_line(0, 40)
        imgui.begin_group()
        width = max((imgui.calc_text_size(f"{name} o").x for name in outputs), default=0)
        for i, name in enumerate(outputs):
            label = f"{name} o"
            pad = width - imgui.calc_text_size(label).x
            if pad > 0:
                imgui.dummy(imgui.ImVec2(pad, 0))
                imgui.same_line(0, 0)
            ed.begin_pin(ed.PinId(pin_out(inst.id, i)), ed.PinKind.output)
            ed.pin_pivot_alignment(imgui.ImVec2(1.0, 0.5))
            if (inst.id, name) in state.graph.keep:
                imgui.text_colored(CYAN, label)   # §9: kept
            else:
                imgui.text(label)
            ed.end_pin()
        imgui.end_group()
        # What it receives or makes (§4), what it asks its source (§5).
        if inst.error:
            imgui.text_colored(RED, inst.error)
        else:
            first = next(iter(inst.schemas.values()), None) or next(iter(inst.given.values()), None)
            if first is not None:
                imgui.text_colored(GREY, first.describe())
            if not inst.spec.inputs and "features" in inst.wanted:
                cols = first.names() if first is not None else None
                imgui.text_colored(GREY, "asks: " + inst.wanted["features"].describe(cols))
        for note in inst.notes[:3]:
            imgui.text_colored(YELLOW, note)
        if len(inst.notes) > 3:
            imgui.text_colored(YELLOW, f"... {len(inst.notes) - 3} more (Properties)")
        c = state.counts(inst.id)
        if c is not None:
            n_in, outs = c
            text = (f"in {n_in:,}" if inst.spec.inputs else "") + \
                   ("  " if inst.spec.inputs and outs else "") + \
                   "  ".join(f"{p} {n:,}" for p, n in outs.items())
            imgui.text_colored(GREEN, text or "-")
    imgui.pop_text_wrap_pos()
    ed.end_node()
    # The band behind the title, drawn under the node's content, rounded at the top as the node.
    nid = ed.NodeId(inst.id)
    pos, size = ed.get_node_position(nid), ed.get_node_size(nid)
    if size.x > 0:
        r_, g_, b_ = BANDS.get(inst.spec.category, BAND_OTHER) if inst.spec else (0.55, 0.20, 0.20)
        dl = ed.get_node_background_draw_list(nid)
        rounding = ed.get_style().node_rounding
        dl.add_rect_filled(imgui.ImVec2(pos.x + 1, pos.y + 1), imgui.ImVec2(pos.x + size.x - 1, title_bottom + 4),
                           imgui.color_convert_float4_to_u32(imgui.ImVec4(r_, g_, b_, 1.0)), rounding,
                           imgui.ImDrawFlags_.round_top)


def draw(state: State) -> None:
    """The Graph panel. Its changes mark the structure to propagate again (§4)."""
    state.refresh()
    hovered = imgui.is_window_hovered(imgui.HoveredFlags_.child_windows)   # the mouse over the graph
    # Screen positions, taken before the canvas: inside it, ImGui's positions are the canvas's.
    mouse_screen = imgui.get_mouse_pos()
    wp, ws = imgui.get_window_pos(), imgui.get_window_size()
    ed.begin("graph", imgui.ImVec2(0, 0))

    for nid, inst in list(state.graph.nodes.items()):
        if nid not in state.placed:
            ed.set_node_position(ed.NodeId(nid), imgui.ImVec2(*inst.pos))
            state.placed.add(nid)
        _node(state, inst)
    if state.select_new:   # nodes just added or pasted: they become the selection
        ed.clear_selection()
        for nid in state.select_new:
            if nid in state.graph.nodes:
                ed.select_node(ed.NodeId(nid), True)
        state.select_new = []

    for link in state.graph.links:
        src = state.graph.nodes[link.src].spec
        dst = state.graph.nodes[link.dst].spec
        if src is None or dst is None:
            continue
        outs = state.graph.nodes[link.src].outputs()
        if link.src_port not in outs:
            continue   # a rule just removed: the link goes at the next refresh
        i = outs.index(link.src_port)
        j = [p.name for p in dst.inputs].index(link.dst_port)
        made = _flash.get(link)
        if made is not None and time.monotonic() - made < FLASH:   # just joined: bright and thick
            ed.link(ed.LinkId(link_id(link)), ed.PinId(pin_out(link.src, i)), ed.PinId(pin_in(link.dst, j)), GREEN, 4.0)
        elif (link.src, link.src_port) in state.graph.keep:   # §9: a kept output's links, thicker
            ed.link(ed.LinkId(link_id(link)), ed.PinId(pin_out(link.src, i)), ed.PinId(pin_in(link.dst, j)), CYAN, 3.0)
        else:
            ed.link(ed.LinkId(link_id(link)), ed.PinId(pin_out(link.src, i)), ed.PinId(pin_in(link.dst, j)))

    # The whole graph in view, when it does not fit (after opening a file); F does it any time.
    if state.fit_now:   # Edit > Fit the graph
        state.fit_now = False
        ed.navigate_to_content(0.3)
    if state.fit and ws.x > 200 and ws.y > 200:   # counted only while the Graph is shown, at its size
        state.fit -= 1
        if state.fit == 0:
            right = max((ed.get_node_position(ed.NodeId(n)).x + ed.get_node_size(ed.NodeId(n)).x
                         for n in state.graph.nodes), default=0)
            far = ed.screen_to_canvas(imgui.ImVec2(wp.x + ws.x, wp.y))
            if right > far.x:
                ed.navigate_to_content(0.0)

    # A new link, dragged from a port to another.
    if not state.running and ed.begin_create():
        a, b = ed.PinId(), ed.PinId()
        if ed.query_new_link(a, b) and a and b:
            pa, pb = port_of(state, a.id()), port_of(state, b.id())
            if pa and pb and pa[2] == "in":
                pa, pb = pb, pa   # dragged from an input: the same link
            why = ""
            if not pa or not pb or pa[2] != "out" or pb[2] != "in":
                why = "an output to an input"
            else:
                why = state.graph.can_link(Link(pa[0], pa[1], pb[0], pb[1]))
            if why:   # over a port that refuses: red, and why
                ed.reject_new_item(RED, 2.0)
                imgui.set_tooltip(why)
            else:     # over a port that accepts: green, and what would pass (§4)
                s = state.graph.nodes[pa[0]].schemas.get(pa[1])
                imgui.set_tooltip(f"{pa[1]} > {pb[1]}" + (f": {s.describe()}" if s is not None else ""))
                if ed.accept_new_item(GREEN, 3.0):
                    link = Link(pa[0], pa[1], pb[0], pb[1])
                    state.graph.add_link(link)
                    _flash[link] = time.monotonic()
                    state.dirty_structure = True
        ed.end_create()

    # Deleting: a selected link or node, with the Delete key.
    if not state.running and ed.begin_delete():
        lid = ed.LinkId()
        while ed.query_deleted_link(lid):
            if ed.accept_deleted_item():
                for link, k in list(_link_ids.items()):
                    if k == lid.id():
                        state.graph.remove_link(link)
                        state.dirty_structure = True
        nid = ed.NodeId()
        while ed.query_deleted_node(nid):
            if ed.accept_deleted_item():
                state.graph.remove_node(nid.id())
                state.placed.discard(nid.id())
                if state.selected == nid.id():
                    state.selected = None
                state.dirty_structure = True
        ed.end_delete()

    # The node shown in Properties: the one selected.
    sel = ed.get_selected_nodes()
    state.selection = [n.id() for n in sel]
    if sel:
        state.selected = sel[0].id()
    # A link of a kept output selected: the Table shows it (§9).
    for lid in ed.get_selected_links():
        for link, k in _link_ids.items():
            if k == lid.id() and (link.src, link.src_port) in state.graph.keep:
                state.looked = (link.src, link.src_port)

    # The middle of what is in view: where a node from the library goes.
    c = ed.screen_to_canvas(imgui.ImVec2(wp.x + ws.x * 0.4, wp.y + ws.y * 0.4))
    state.view_center = (c.x, c.y)

    # Right click on the background: add a node there.
    mouse = ed.screen_to_canvas(mouse_screen)
    state.mouse_canvas = (mouse.x, mouse.y) if hovered else None
    ed.suspend()
    if ed.show_background_context_menu():
        imgui.open_popup("add node")
        state.add_at = (mouse.x, mouse.y)
    if imgui.begin_popup("add node"):
        _add_menu(state, state.add_at)
        imgui.end_popup()
    # Right click on an output port, or on a link: keep that output (§9).
    pin, lid = ed.PinId(), ed.LinkId()
    if ed.show_pin_context_menu(pin):
        p = port_of(state, pin.id())
        if p and p[2] == "out":
            state.menu_output = (p[0], p[1])
            imgui.open_popup("output")
    if ed.show_link_context_menu(lid):
        for link, k in _link_ids.items():
            if k == lid.id():
                state.menu_output = (link.src, link.src_port)
                imgui.open_popup("output")
    if imgui.begin_popup("output"):
        nid, port = state.menu_output
        kept = (nid, port) in state.graph.keep
        imgui.text_colored(GREY, f"#{nid} {port}")
        if imgui.menu_item("Keep", "", kept, not state.running)[0]:
            state.toggle_keep(nid, port)
        if imgui.menu_item("Show in the Table", "", False, kept)[0]:
            state.looked = (nid, port)
        imgui.end_popup()
    # A link under the mouse: its structure (§4).
    hovered = ed.get_hovered_link()
    if hovered:
        for link, k in _link_ids.items():
            if k == hovered.id() and link.src in state.graph.nodes:
                s = state.graph.nodes[link.src].schemas.get(link.src_port)
                if s is not None:
                    imgui.set_tooltip(f"{link.src_port}: {s.describe()}\n" + "\n".join(str(f) for f in s.fields[:25]) +
                                      ("\n..." if len(s.fields) > 25 else ""))
    ed.resume()
    ed.end()


def _add_menu(state: State, at) -> None:
    by_cat: dict[str, list] = {}
    for spec in state.registry.specs.values():
        by_cat.setdefault(spec.category, []).append(spec)
    for cat in sorted(by_cat):
        if imgui.begin_menu(cat):
            for spec in sorted(by_cat[cat], key=lambda s: s.name):
                if imgui.menu_item_simple(spec.name):
                    add_node(state, spec.name, at)
            imgui.end_menu()


def add_node(state: State, ntype: str, at=None) -> None:
    """A node added where the right click was, or in the middle of the view (library click)."""
    sync_positions(state)
    if at is None:
        x, y = state.view_center
        # Not on top of another node (about 200 x 100 each): lower, until the place is free.
        while any(abs(n.pos[0] - x) < 200 and abs(n.pos[1] - y) < 100 for n in state.graph.nodes.values()):
            y += 60
        at = (x, y)
    inst = state.graph.add_node(state.registry, ntype, (round(at[0]), round(at[1])))
    state.selected = inst.id
    state.select_new = [inst.id]
    state.dirty_structure = True
