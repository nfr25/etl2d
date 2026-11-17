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
         "Attributes": (0.40, 0.36, 0.20), "Python": (0.36, 0.25, 0.48), "Filters": (0.16, 0.42, 0.44),
         "Relations": (0.50, 0.27, 0.42)}
BAND_OTHER = (0.32, 0.32, 0.36)   # custom categories
FLASH = 0.8   # seconds a new link stays bright: the moment of joining, seen
_flash: dict = {}   # Link -> time it was made
_rights: dict = {}  # node -> the right edge of its widest line at the last frame, its outputs not counted:
                    # where its output ports go (at the edge, not just after their name)

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
    ins = inst.inputs()
    return (nid, ins[k - 1], "in") if 0 <= k - 1 < len(ins) else None


GROUP_BASE = 900000   # a group's id in the editor: GROUP_BASE + its place in the list (nodes are below)
_group_sizes: dict = {}   # group's place -> (width, height) of its area, read while it is drawn
_group_offsets: dict = {}  # group's place -> what its node adds to its area (title, margins)
GROUP_COLOURS = {"Blue": (0.25, 0.40, 0.60), "Green": (0.25, 0.50, 0.30), "Ochre": (0.55, 0.45, 0.20),
                 "Red": (0.55, 0.25, 0.25), "Violet": (0.42, 0.30, 0.55), "Grey": (0.40, 0.40, 0.42)}


def sync_positions(state: State) -> None:
    """The editor's positions back into the workflow (before saving): nodes, and groups with their size."""
    for nid, inst in state.graph.nodes.items():
        if nid in state.placed:
            p = ed.get_node_position(ed.NodeId(nid))
            inst.pos = (round(p.x), round(p.y))
    for i, g in enumerate(state.graph.groups):
        gid = ed.NodeId(GROUP_BASE + i)
        if GROUP_BASE + i in state.placed:
            p = ed.get_node_position(gid)
            g["pos"] = [round(p.x), round(p.y)]
            size = _group_sizes.get(i)   # its area as the editor has it (resized by its corner)
            if size is not None and size[0] > 0 and size[1] > 0:
                g["size"] = [round(size[0]), round(size[1])]


def _group(state: State, i: int, g: dict) -> None:
    """A group: a coloured rectangle with a title, behind nodes; moved with the nodes in it (§11)."""
    gid = GROUP_BASE + i
    first = gid not in state.placed
    if first:
        ed.set_node_position(ed.NodeId(gid), imgui.ImVec2(*g["pos"]))
        state.placed.add(gid)
        _group_offsets.pop(i, None)
    r_, g_, b_ = (list(g.get("colour", (0.3, 0.4, 0.5))) + [0.3, 0.4, 0.5])[:3]
    ed.push_style_color(ed.StyleColor.node_bg, imgui.ImVec4(r_, g_, b_, 0.22))
    ed.push_style_color(ed.StyleColor.node_border, imgui.ImVec4(r_, g_, b_, 0.9))
    ed.begin_node(ed.NodeId(gid))
    imgui.text(g.get("title") or "group")
    ed.group(imgui.ImVec2(*g.get("size", (300, 200))))
    ed.end_node()
    if first:   # group() gives only the size of a new group: one the editor knew already gets it here
        ed.set_group_size(ed.NodeId(gid), imgui.ImVec2(*g.get("size", (300, 200))))
    # Its area as the editor has it (resized by its corner): the node's size, less what its title and
    # margins add, measured once while the area is still the one given.
    n = ed.get_node_size(ed.NodeId(gid))
    if n.x > 0:
        if i not in _group_offsets:
            _group_offsets[i] = (n.x - g["size"][0], n.y - g["size"][1])
        ox, oy = _group_offsets[i]
        _group_sizes[i] = (n.x - ox, n.y - oy)
    ed.pop_style_color(2)


def _node(state: State, inst) -> None:
    ed.begin_node(ed.NodeId(inst.id))
    if inst.off:   # §11: disabled, or fed by a node that is: pale
        imgui.push_style_var(imgui.StyleVar_.alpha, 0.45)
    imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + 230)
    imgui.text(inst.title or inst.type)   # its own name, if given (§11), else its type
    imgui.same_line()
    imgui.text_colored(imgui.ImVec4(0.85, 0.85, 0.85, 1.0), f"{inst.type}  #{inst.id}" if inst.title else f"#{inst.id}")
    title_bottom = imgui.get_item_rect_max().y
    rights = [imgui.get_item_rect_max().x]   # the right edge of each line but the outputs (see _rights)
    imgui.dummy(imgui.ImVec2(0, 2))
    if inst.spec is None:
        imgui.text_colored(RED, "unknown node type")
    else:
        # Inputs in a column at the left, outputs at the right: the links come in and go out.
        imgui.begin_group()
        inputs = inst.inputs()
        for i, name in enumerate(inputs):
            ed.begin_pin(ed.PinId(pin_in(inst.id, i)), ed.PinKind.input)
            ed.pin_pivot_alignment(imgui.ImVec2(0.0, 0.5))
            imgui.text(f"o {name}")
            ed.end_pin()
        imgui.end_group()
        if inputs:
            rights.append(imgui.get_item_rect_max().x)
        outputs = inst.outputs()
        if inputs and outputs:
            imgui.same_line(0, 40)
        width = max((imgui.calc_text_size(f"{name} o").x for name in outputs), default=0)
        # The outputs against the right edge, as wide as the widest other line was at the last frame.
        shift = _rights.get(inst.id, 0) - (imgui.get_cursor_screen_pos().x + width)
        if outputs and shift > 1:
            imgui.dummy(imgui.ImVec2(shift, 0))
            imgui.same_line(0, 0)
        imgui.begin_group()
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
        def line(colour, text):
            imgui.text_colored(colour, text)
            rights.append(imgui.get_item_rect_max().x)

        if inst.off:
            line(GREY, inst.off + ("  (Ctrl+M: enable)" if inst.disabled else ""))
        elif inst.error:
            line(RED, inst.error)
        else:
            first = next(iter(inst.schemas.values()), None) or next(iter(inst.given.values()), None)
            if first is not None:
                line(GREY, first.describe())
            if not inputs and "features" in inst.wanted:
                cols = first.names() if first is not None else None
                line(GREY, "asks: " + inst.wanted["features"].describe(cols))
        for note in inst.notes[:3]:
            line(YELLOW, note)
        if len(inst.notes) > 3:
            line(YELLOW, f"... {len(inst.notes) - 3} more (Properties)")
        c = state.counts(inst.id)
        if c is not None:
            n_in, outs = c
            text = (f"in {n_in:,}" if inputs else "") + \
                   ("  " if inputs and outs else "") + \
                   "  ".join(f"{p} {n:,}" for p, n in outs.items())
            line(GREEN, text or "-")
    _rights[inst.id] = max(rights)
    imgui.pop_text_wrap_pos()
    if inst.off:
        imgui.pop_style_var()
    ed.end_node()
    # The band behind the title, drawn under the node's content, rounded at the top as the node.
    nid = ed.NodeId(inst.id)
    pos, size = ed.get_node_position(nid), ed.get_node_size(nid)
    if size.x > 0:
        r_, g_, b_ = BANDS.get(inst.spec.category, BAND_OTHER) if inst.spec else (0.55, 0.20, 0.20)
        if inst.off:
            r_, g_, b_ = (0.30, 0.30, 0.32)
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

    for i, g in enumerate(state.graph.groups):   # first: behind the nodes
        _group(state, i, g)
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
        ins = state.graph.nodes[link.dst].inputs()
        if link.dst_port not in ins:
            continue   # a layer just removed: the link goes at the next refresh
        j = ins.index(link.dst_port)
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
        if state.fit == 1:   # at opening: the whole graph shown, centred (the nodes measured by now)
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
        gone_groups = []
        while ed.query_deleted_node(nid):
            if nid.id() >= GROUP_BASE:   # a group: removed, its nodes stay
                if ed.accept_deleted_item():
                    gone_groups.append(nid.id() - GROUP_BASE)
                continue
            if ed.accept_deleted_item():
                state.graph.remove_node(nid.id())
                state.placed.discard(nid.id())
                if state.selected == nid.id():
                    state.selected = None
                state.dirty_structure = True
        if gone_groups:
            sync_positions(state)
            state.graph.groups = [g for i, g in enumerate(state.graph.groups) if i not in gone_groups]
            state.placed = {n for n in state.placed if n < GROUP_BASE}   # the ids of those after changed
            _group_sizes.clear()
            _group_offsets.clear()
        ed.end_delete()

    # The node shown in Properties: the one selected.
    sel = [n for n in ed.get_selected_nodes() if n.id() < GROUP_BASE]   # groups are not nodes
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
    # Right click on a node: disable, rename (§11); on a group: rename, colour, remove.
    nid_ = ed.NodeId()
    if ed.show_node_context_menu(nid_):
        state.menu_node = nid_.id()
        imgui.open_popup("group" if nid_.id() >= GROUP_BASE else "node")
    dbl = ed.get_double_clicked_node()
    if dbl and dbl.id():
        state.menu_node = dbl.id()
        _start_rename(state)
    if imgui.begin_popup("node"):
        inst = state.graph.nodes.get(state.menu_node)
        if inst is not None:
            imgui.text_colored(GREY, inst.label())
            if imgui.menu_item("Enable" if inst.disabled else "Disable", "Ctrl+M", False, not state.running)[0]:
                state.toggle_disabled([inst.id])
            if imgui.menu_item_simple("Rename..."):
                _start_rename(state)
        imgui.end_popup()
    if imgui.begin_popup("group"):
        i = state.menu_node - GROUP_BASE
        if 0 <= i < len(state.graph.groups):
            g = state.graph.groups[i]
            imgui.text_colored(GREY, g.get("title") or "group")
            if imgui.menu_item_simple("Rename..."):
                _start_rename(state)
            if imgui.begin_menu("Colour"):
                for name, rgb in GROUP_COLOURS.items():
                    if imgui.menu_item_simple(name):
                        sync_positions(state)
                        g["colour"] = list(rgb)
                imgui.end_menu()
            if imgui.menu_item_simple("Remove the group (its nodes stay)"):
                sync_positions(state)
                state.graph.groups.pop(i)
                state.placed = {n for n in state.placed if n < GROUP_BASE}
                _group_sizes.clear()
                _group_offsets.clear()
            _group_offsets.clear()
        imgui.end_popup()
    if state.renaming is not None:
        imgui.open_popup("rename")
        state.renaming_open, state.renaming = True, None
    if imgui.begin_popup("rename"):
        if state.renaming_open:
            imgui.set_keyboard_focus_here()
            state.renaming_open = False
        imgui.set_next_item_width(imgui.get_font_size() * 16)
        done, state.rename_text = imgui.input_text("##rename", state.rename_text,
                                                    imgui.InputTextFlags_.enter_returns_true)
        imgui.text_disabled("Enter: done; empty: its type again" if state.menu_node < GROUP_BASE else "Enter: done")
        if done:
            sync_positions(state)
            if state.menu_node >= GROUP_BASE:
                i = state.menu_node - GROUP_BASE
                if 0 <= i < len(state.graph.groups):
                    state.graph.groups[i]["title"] = state.rename_text.strip()
            elif state.menu_node in state.graph.nodes:
                state.graph.nodes[state.menu_node].title = state.rename_text.strip()
            imgui.close_current_popup()
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
    imgui.separator()
    if imgui.menu_item_simple("A group" + (" around the selected nodes" if state.selection else "")):
        add_group(state, at)


def add_group(state: State, at) -> None:
    """A group (§11): around the selected nodes (their box plus a margin), else 400 x 250 at the click."""
    sync_positions(state)
    sel = [state.graph.nodes[n] for n in state.selection if n in state.graph.nodes]
    if sel:
        x0 = min(n.pos[0] for n in sel) - 30
        y0 = min(n.pos[1] for n in sel) - 50
        x1 = max(n.pos[0] + ed.get_node_size(ed.NodeId(n.id)).x for n in sel) + 30
        y1 = max(n.pos[1] + ed.get_node_size(ed.NodeId(n.id)).y for n in sel) + 30
        pos, size = [round(x0), round(y0)], [round(x1 - x0), round(y1 - y0 - 20)]
    else:
        pos, size = [round(at[0]), round(at[1])], [400, 250]
    state.graph.groups.append({"title": "group", "pos": pos, "size": size,
                               "colour": list(GROUP_COLOURS["Blue"])})
    state.menu_node = GROUP_BASE + len(state.graph.groups) - 1
    _start_rename(state)


def _start_rename(state: State) -> None:
    """The small window where a node's or a group's name is typed (§11)."""
    n = state.menu_node
    if n >= GROUP_BASE:
        i = n - GROUP_BASE
        state.rename_text = state.graph.groups[i].get("title", "") if 0 <= i < len(state.graph.groups) else ""
    elif n in state.graph.nodes:
        state.rename_text = state.graph.nodes[n].title or state.graph.nodes[n].type
    else:
        return
    state.renaming = n


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
