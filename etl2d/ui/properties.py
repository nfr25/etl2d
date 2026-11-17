"""The Properties panel: the selected node's parameters as widgets (notes §8, §11)."""

from __future__ import annotations

from imgui_bundle import imgui, portable_file_dialogs as pfd

from etl2d.schema import FIELD_TYPES
from etl2d.ui.editor import GREY, RED, YELLOW
from etl2d.ui.state import State

TYPES = list(FIELD_TYPES)


def draw(state: State) -> None:
    inst = state.graph.nodes.get(state.selected) if state.selected is not None else None
    if inst is None:
        imgui.text_colored(GREY, "select a node (a click on its title)")
        return
    imgui.text(f"{inst.type}  #{inst.id}")
    # Its own name (§11): typed here, or a double click on its title in the graph.
    imgui.set_next_item_width(-1)
    c, t = imgui.input_text_with_hint("##title", "its name (empty: its type)", inst.title)
    if c:
        inst.title = t
    if inst.off:
        imgui.text_colored(GREY, inst.off + ("  (Ctrl+M, or a right click on it: enable)" if inst.disabled else ""))
    if inst.spec is None:
        imgui.text_colored(RED, "unknown node type: its parameters are kept as they are")
        return
    if inst.spec.help:
        imgui.push_text_wrap_pos(0)
        first, _, rest = inst.spec.help.strip().partition("\n\n")
        imgui.text_colored(GREY, first)     # the first paragraph; the rest under the mouse
        imgui.pop_text_wrap_pos()
        if rest:
            imgui.set_item_tooltip(rest)
    if inst.error:
        imgui.push_text_wrap_pos(0)
        imgui.text_colored(RED, inst.error)
        imgui.pop_text_wrap_pos()
    imgui.separator()
    disabled = state.running
    if disabled:
        imgui.begin_disabled()
    changed = False
    for p in inst.spec.params:
        imgui.push_id(p.name)
        changed |= _param(state, inst, p)
        imgui.pop_id()
    if disabled:
        imgui.end_disabled()
    if changed:
        state.dirty_structure = True
    # What it receives and makes (§4), what it says.
    imgui.separator()
    for port, s in inst.given.items():
        _schema(f"in: {port}", s)
    for port, s in inst.schemas.items():
        _schema(f"out: {port}", s)
    for note in inst.notes:
        imgui.push_text_wrap_pos(0)
        imgui.text_colored(YELLOW, note)
        imgui.pop_text_wrap_pos()


def _schema(label: str, s) -> None:
    if imgui.tree_node(label):
        imgui.push_text_wrap_pos(0)
        imgui.text_colored(GREY, s.describe())
        imgui.pop_text_wrap_pos()
        for f in s.fields:
            imgui.text(str(f))
        imgui.tree_pop()


def _help(p) -> None:
    if p.help:
        imgui.set_item_tooltip(p.help)


def _param(state: State, inst, p) -> bool:
    v = inst.params.get(p.name, p.default)
    t = p.type
    changed = False
    imgui.text(p.name)
    _help(p)
    imgui.set_next_item_width(-1)
    if t in ("str", "crs"):
        changed, nv = _typed(inst, p, imgui.input_text("##v", str(v or "")))
    elif t == "path":
        imgui.set_next_item_width(-imgui.get_frame_height() * 1.6)
        changed, nv = _typed(inst, p, imgui.input_text("##v", str(v or "")))
        imgui.same_line()
        if imgui.button("..."):
            files = pfd.open_file("Choose a file", str(state.graph.base_dir)).result()
            if files:
                nv, changed = _relative(state, files[0]), True
    elif t == "int":
        changed, nv = imgui.input_int("##v", int(v or 0))
    elif t == "float":
        changed, nv = imgui.input_float("##v", float(v or 0.0))
    elif t == "bool":
        changed, nv = imgui.checkbox("##v", bool(v))
    elif t == "choice":
        items = list(p.choices)
        idx = items.index(v) if v in items else 0
        changed, idx = imgui.combo("##v", idx, items)
        nv = items[idx]
    elif t == "bbox":
        box = list(v) if v else [0.0, 0.0, 0.0, 0.0]
        changed, box = imgui.input_float4("##v", [float(x) for x in box], "%.1f")
        nv = box if any(box) else None
        if imgui.small_button("clear"):
            nv, changed = None, True
    elif t == "code":
        changed, nv = _typed(inst, p, imgui.input_text_multiline(
            "##v", str(v or ""), imgui.ImVec2(-1, imgui.get_text_line_height() * 12)))
    elif t == "columns":
        changed, nv = _columns(inst, list(v or []))
    elif t == "rows":
        changed, nv, typed = _rows([dict(x) for x in (v or [])], list(p.choices))
        if typed and not changed:   # a text being typed: kept, propagated when the field is left
            inst.params[p.name] = nv
    elif t == "names":
        changed, nv = _names(inst, list(v or []))
    elif t == "rules":
        changed, nv = _rules(state, inst, [dict(r) for r in (v or [])])
    elif t == "layers":
        rows = inst.spec.cls.layers_of(inst.params) if hasattr(inst.spec.cls, "layers_of") else list(v or [])
        changed, nv, typed = _layers(state, inst, [dict(r) for r in rows])
        if typed and not changed:
            inst.params[p.name] = nv
    else:
        imgui.text_colored(GREY, f"{v!r} (no widget for '{t}')")
        return False
    if changed:
        inst.params[p.name] = nv
    imgui.spacing()
    return changed


def _typed(inst, p, edited_value) -> tuple[bool, str]:
    """A text being typed: kept at each key, but 'changed' (propagate again, §4) only when the field
    is left or Enter pressed. Otherwise a connection PG:host=... would be tried at each letter."""
    edited, nv = edited_value
    if edited:
        inst.params[p.name] = nv
    return imgui.is_item_deactivated_after_edit(), nv


def _relative(state: State, path: str) -> str:
    from pathlib import Path
    try:
        return str(Path(path).resolve().relative_to(state.graph.base_dir.resolve())).replace("\\", "/")
    except ValueError:
        return path


def _columns(inst, rows: list) -> tuple[bool, list]:
    """Attributes: every column received, a box to keep it, its new name (§7)."""
    s = next(iter(inst.given.values()), None)
    if s is None:
        imgui.text_colored(GREY, "join an input first: its columns come here")
        return False, rows
    by = {r["from"]: dict(r) for r in rows}
    changed = False
    if imgui.begin_table("cols", 3, imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.sizing_stretch_prop):
        imgui.table_setup_column("keep", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("column")
        imgui.table_setup_column("new name")
        imgui.table_headers_row()
        for f in s.fields:
            r = by.get(f.name, {"from": f.name})
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.push_id(f.name)
            c1, keep = imgui.checkbox("##k", r.get("keep", True))
            imgui.table_next_column()
            imgui.text(f.name) if keep else imgui.text_disabled(f.name)
            imgui.set_item_tooltip(str(f))
            imgui.table_next_column()
            imgui.set_next_item_width(-1)
            c2, to = imgui.input_text("##to", r.get("to", ""))
            imgui.pop_id()
            if c1 or c2:
                changed = True
                r = {"from": f.name}
                if not keep:
                    r["keep"] = False
                if to:
                    r["to"] = to
                by[f.name] = r
        imgui.end_table()
    if changed:   # only the rows that say something: a column untouched goes on as it is
        rows = [r for r in by.values() if len(r) > 1]
    return changed, rows


def _rows(rows: list, keys: list) -> tuple[bool, list, bool]:
    """A small table of rows; 'type' is a list of OGR's types; '+' adds, 'x' removes.
    Texts (a name, a value, an expression = ...) are propagated when their field is left (§4)."""
    changed, typed = False, False
    remove = None
    if rows and imgui.begin_table("rows", len(keys) + 1, imgui.TableFlags_.sizing_stretch_prop):
        for k in keys:
            if k in ("type", "width"):   # a type name and a number: fixed widths, the text gets the rest
                imgui.table_setup_column(k, imgui.TableColumnFlags_.width_fixed, imgui.get_font_size() * (5.5 if k == "type" else 2.5))
            else:   # a value may be an expression (= ...): three times the room of a name
                imgui.table_setup_column(k, imgui.TableColumnFlags_.width_stretch, 3.0 if k == "value" else 1.0)
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed)
        imgui.table_headers_row()
        for i, r in enumerate(rows):
            imgui.table_next_row()
            imgui.push_id(i)
            for k in keys:
                imgui.table_next_column()
                imgui.set_next_item_width(-1)
                imgui.push_id(k)
                if k == "type":
                    cur = TYPES.index(r.get("type", "String")) if r.get("type", "String") in TYPES else 0
                    c, cur = imgui.combo("##", cur, TYPES)
                    if c:
                        r["type"], changed = TYPES[cur], True
                elif k == "width":
                    c, w = imgui.input_int("##", int(r.get("width", 0) or 0), 0)
                    if c:
                        r["width"], changed = max(0, w), True
                else:
                    c, txt = imgui.input_text("##", str(r.get(k, "") or ""))
                    if c:
                        r[k], typed = txt, True
                    changed |= imgui.is_item_deactivated_after_edit()
                    if k == "value":
                        imgui.set_item_tooltip("a constant, or = an expression: = area * 2, = x(0), "
                                               "= a || ' ' || b (notes §7)")
                imgui.pop_id()
            imgui.table_next_column()
            if imgui.small_button("x"):
                remove = i
            imgui.pop_id()
        imgui.end_table()
    if remove is not None:
        rows.pop(remove)
        changed = True
    if imgui.small_button("+"):
        rows.append({"name": f"column{len(rows) + 1}", "type": "String"})
        changed = True
    return changed, rows, typed


_renaming: dict = {}   # (node, row) -> the rule's name when its field was entered

OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "not in", "contains", "starts with", "is empty", "is not empty",
       "expression")


def _rules(state: State, inst, rules: list) -> tuple[bool, list]:
    """The Filter's rules (§7): name, column (from those received), comparison, value; up, down, remove.

    A name or a value typed: kept at each key (a renamed rule's links follow at
    once), propagated when the field is left. A choice in a list, a rule added,
    moved or removed: at once.
    """
    s = next(iter(inst.given.values()), None)
    cols = s.names() if s is not None else []
    if s is None:
        imgui.text_colored(GREY, "join an input first: its columns come in the lists")
    changed, typed = False, False
    action = None   # ("up" | "down" | "x", row)
    # Two lines per rule, the panel being narrow: its name and the buttons; then column, comparison, value.
    for i, r in enumerate(rules):
        imgui.push_id(i)
        bw = imgui.calc_text_size("^").x + imgui.get_style().frame_padding.x * 2
        buttons = bw * 3 + 4 + imgui.get_style().item_spacing.x
        imgui.set_next_item_width(imgui.get_content_region_avail().x - buttons)
        c, name = imgui.input_text("##name", str(r.get("name", "")))
        imgui.set_item_tooltip("the rule's name: the name of its output")
        if imgui.is_item_activated():   # the name before typing: its links are on it
            _renaming[(inst.id, i)] = str(r.get("name", ""))
        if c:
            r["name"], typed = name, True
        if imgui.is_item_deactivated():
            old = _renaming.pop((inst.id, i), None)
            new = str(r.get("name", ""))
            if old is not None and new != old:
                others = {str(x.get("name")) for j, x in enumerate(rules) if j != i} | {"else"}
                if new in others or not new.strip():   # taken, or empty: back to the old name
                    r["name"] = old
                    state.say("error", f"#{inst.id} Filter: '{new}' is " +
                              ("taken by another output" if new.strip() else "empty") + f"; the rule keeps '{old}'")
                else:   # its output renamed: its links follow
                    state.graph.rename_output(inst.id, old, new)
                changed = True
        imgui.same_line()
        if imgui.small_button("^") and i > 0:
            action = ("up", i)
        imgui.same_line(0, 2)
        if imgui.small_button("v") and i < len(rules) - 1:
            action = ("down", i)
        imgui.same_line(0, 2)
        if imgui.small_button("x"):
            action = ("x", i)
        w = imgui.get_content_region_avail().x
        sp = imgui.get_style().item_spacing.x
        is_expr = r.get("op") == "expression"
        if not is_expr:   # an expression names its columns itself: no column to choose
            imgui.set_next_item_width(w * 0.38)
            col = r.get("column", "")
            items = cols if col in cols or not col else cols + [col]   # a column gone: kept, shown (red on the node)
            if imgui.begin_combo("##col", col or "(column)"):
                for name_ in items:
                    if imgui.selectable(name_, name_ == col)[0]:
                        r["column"], changed = name_, True
                imgui.end_combo()
            imgui.same_line()
        imgui.set_next_item_width(w * 0.27)
        op = r.get("op", "=")
        k = OPS.index(op) if op in OPS else 0
        c, k = imgui.combo("##op", k, list(OPS))
        if c:
            r["op"], changed = OPS[k], True
        if r.get("op") not in ("is empty", "is not empty"):
            if is_expr:   # the expression on the rest of the line: it is longer than a value
                imgui.same_line()
                imgui.set_next_item_width(-1)
            else:
                imgui.same_line()
                imgui.set_next_item_width(w * 0.35 - 2 * sp)
            c, val = imgui.input_text("##val", str(r.get("value", "") if r.get("value") is not None else ""))
            if c:
                r["value"], typed = val, True
            changed |= imgui.is_item_deactivated_after_edit()
            if is_expr:
                imgui.set_item_tooltip("true or false: npoints() > 500, area() > 200 AND type = 'maison' (notes §7)")
        imgui.dummy(imgui.ImVec2(0, 4))
        imgui.pop_id()
    imgui.text_colored(GREY, "else: what no rule takes")
    if action is not None:
        what, i = action
        if what == "x":
            rules.pop(i)   # its links go at the refresh, said in the log (graph.prune_ports)
        else:
            j = i - 1 if what == "up" else i + 1
            rules[i], rules[j] = rules[j], rules[i]
        changed = True
    if imgui.small_button("+ rule"):
        names = {str(r.get("name")) for r in rules}
        n = len(rules) + 1
        while f"rule{n}" in names:
            n += 1
        rules.append({"name": f"rule{n}", "column": cols[0] if cols else "", "op": "=", "value": ""})
        changed = True
    if typed and not changed:   # kept without propagating yet (see _typed)
        inst.params["rules"] = rules
    return changed, rules


COLOURS = ("none", "categorize", "graduate")
METHODS = ("equal", "quantiles", "natural")


def _layers(state: State, inst, layers: list) -> tuple[bool, list, bool]:
    """The MapWriter's layers (§7), top first (drawn on top): name (its input, its legend line) with
    ^ v x; its style; its colour by a column (categorize, graduate: classes, method)."""
    changed, typed, action = False, False, None
    for i, lay in enumerate(layers):
        imgui.push_id(i)
        bw = imgui.calc_text_size("^").x + imgui.get_style().frame_padding.x * 2
        imgui.set_next_item_width(imgui.get_content_region_avail().x - bw * 3 - 4 - imgui.get_style().item_spacing.x)
        c, name = imgui.input_text("##name", str(lay.get("name", "")))
        imgui.set_item_tooltip("the layer's name: its input, and its line in the legend")
        if imgui.is_item_activated():
            _renaming[(inst.id, "layer", i)] = str(lay.get("name", ""))
        if c:
            lay["name"], typed = name, True
            lay.pop("legend", None)   # a layer of 0.5 or 0.6 renamed: its name is now its legend
        if imgui.is_item_deactivated():
            old = _renaming.pop((inst.id, "layer", i), None)
            new = str(lay.get("name", ""))
            if old is not None and new != old:
                others = {str(x.get("name")) for j, x in enumerate(layers) if j != i}
                if new in others or not new.strip():
                    lay["name"] = old
                    state.say("error", f"#{inst.id} MapWriter: '{new}' is " +
                              ("taken by another layer" if new.strip() else "empty") + f"; the layer keeps '{old}'")
                else:   # its input renamed: its link follows
                    state.graph.rename_input(inst.id, old, new)
                changed = True
        imgui.same_line()
        if imgui.small_button("^") and i > 0:
            action = ("up", i)
        imgui.same_line(0, 2)
        if imgui.small_button("v") and i < len(layers) - 1:
            action = ("down", i)
        imgui.same_line(0, 2)
        if imgui.small_button("x") and len(layers) > 1:   # a map keeps one layer at least
            action = ("x", i)
        w = imgui.get_content_region_avail().x
        colour = lay.get("colour", "none") or "none"
        imgui.set_next_item_width(w * 0.34)
        k = COLOURS.index(colour) if colour in COLOURS else 0
        c, k = imgui.combo("##colour", k, list(COLOURS))
        imgui.set_item_tooltip("none: its style (or render2d's); categorize: a colour per value; "
                               "graduate: classes of a number, light to dark")
        if c:
            lay["colour"], changed = COLOURS[k], True
        imgui.same_line()
        if (lay.get("colour", "none") or "none") == "none":
            imgui.set_next_item_width(-imgui.get_frame_height() * 1.6)
            c, st = imgui.input_text("##style", str(lay.get("style", "") or ""))
            imgui.set_item_tooltip("a YSLD style; empty: render2d's built-in one")
            if c:
                lay["style"], typed = st, True
            changed |= imgui.is_item_deactivated_after_edit()
            imgui.same_line()
            if imgui.button("..."):
                files = pfd.open_file("A style", str(state.graph.base_dir), ["YSLD styles", "*.yaml *.yml", "All files", "*"]).result()
                if files:
                    lay["style"], changed = _relative(state, files[0]), True
        else:
            s = inst.given.get(str(lay.get("name", "")))
            cols = s.names() if s is not None else []
            col = lay.get("column", "")
            imgui.set_next_item_width(-1)
            if imgui.begin_combo("##col", col or ("(column)" if cols else "(join it first)")):
                for c_ in cols:
                    if imgui.selectable(c_, c_ == col)[0]:
                        lay["column"], changed = c_, True
                imgui.end_combo()
            if lay.get("colour") == "graduate":
                imgui.set_next_item_width(w * 0.34)
                c, n = imgui.input_int("classes##n", int(lay.get("classes", 5) or 5))
                if c:
                    lay["classes"], changed = max(2, min(10, n)), True
                imgui.same_line()
                imgui.set_next_item_width(-1)
                m = lay.get("method", "equal") or "equal"
                k = METHODS.index(m) if m in METHODS else 0
                c, k = imgui.combo("##method", k, list(METHODS))
                if c:
                    lay["method"], changed = METHODS[k], True
        imgui.dummy(imgui.ImVec2(0, 4))
        imgui.pop_id()
    imgui.text_colored(GREY, "the top of the list is drawn on top")
    if action is not None:
        what, i = action
        if what == "x":
            layers.pop(i)   # its link goes at the refresh, said in the log
        else:
            j = i - 1 if what == "up" else i + 1
            layers[i], layers[j] = layers[j], layers[i]
        changed = True
    if imgui.small_button("+ layer"):
        names = {str(x.get("name")) for x in layers}
        n = len(layers) + 1
        while f"layer{n}" in names:
            n += 1
        layers.append({"name": f"layer{n}"})
        changed = True
    return changed, layers, typed


def _names(inst, names: list) -> tuple[bool, list]:
    """Some of the input's columns, ticked."""
    s = next(iter(inst.given.values()), None)
    if s is None:
        imgui.text_colored(GREY, "join an input first")
        return False, names
    changed = False
    for f in s.fields:
        c, on = imgui.checkbox(f.name, f.name in names)
        if c:
            changed = True
            names = [n for n in names if n != f.name] + ([f.name] if on else [])
    return changed, names
