"""Attributes: keep, drop, rename, add columns (FME's AttributeManager, notes §7)."""

from __future__ import annotations

from etl2d import Field, Input, Output, Param, SchemaError, node
from etl2d.expr import ExprError, compile_expr, is_expr, text
from etl2d.needs import Needs
from etl2d.schema import FIELD_TYPES

INTEGERS = ("Integer", "Integer64")


@node("Attributes", "Attributes")
class Attributes:
    """The columns received: keep or drop each, rename it; new columns, a constant or computed.

    A value that begins with = is computed for each feature (= area * 2, = x(0), = a || ' ' || b);
    otherwise it is a constant. A new column named as one kept replaces its value, in its place.

    columns: rows {"from": "PARENTID", "keep": false} or {"from": "STRNAMEFRE", "to": "street"};
             a column named in no row goes on as it is.
    add:     rows {"name": "source", "type": "String", "width": 20, "value": "UrbIS 2026"},
             or {"name": "area10", "type": "Integer", "value": "= round(area / 10) * 10"}.
    """

    inputs = [Input("features")]
    outputs = [Output("features")]
    params = [Param("columns", "columns", [], help="one row per column received: keep it, rename it"),
              Param("add", "rows", [], choices=("name", "type", "width", "value"),
                    help="new columns: a constant, or = an expression (= area * 2); a kept column's name replaces it")]

    def _plan(self, names: list[str]):
        """[(old name, new name)] for the columns kept, in their order, and the added ones."""
        rows = {r["from"]: r for r in self.params["columns"] or []}
        unknown = [n for n in rows if n not in names]
        if unknown:
            raise SchemaError(f"no column {', '.join(unknown)} in the input")
        kept = []
        for n in names:
            r = rows.get(n, {})
            if r.get("keep", True):
                kept.append((n, r.get("to") or n))
        added = list(self.params["add"] or [])
        kept_new = {new for _, new in kept}
        new_names = [new for _, new in kept] + [a.get("name", "") for a in added if a.get("name") not in kept_new]
        doubles = sorted({x for x in new_names if new_names.count(x) > 1})
        if doubles:
            raise SchemaError(f"two columns named {', '.join(doubles)}")
        for a in added:
            if not a.get("name"):
                raise SchemaError("a new column without a name")
            if a.get("type", "String") not in FIELD_TYPES:
                raise SchemaError(f"column {a['name']}: unknown type {a.get('type')} "
                                  f"(one of {', '.join(FIELD_TYPES)})")
        return kept, added

    def _exprs(self, names: list[str]) -> dict:
        """{new column: Expr} for the values that begin with '=', read against the columns that arrive.
        The '=' replaced by a space: the positions said are those of the field as typed."""
        out = {}
        for a in self.params["add"] or []:
            v = a.get("value")
            if is_expr(v):
                try:
                    out[a["name"]] = compile_expr(v.replace("=", " ", 1), names)
                except ExprError as e:
                    raise SchemaError(f"column {a['name']}: {e}")
        return out

    def schema(self, schemas):
        s = schemas["features"]
        kept, added = self._plan(s.names())
        self._exprs(s.names())   # their mistakes, before running (§4)
        replaced = {a["name"]: a for a in added}
        fields = []
        for old, new in kept:
            f = s.field(old)
            a = replaced.pop(new, None)
            fields.append(Field(new, a.get("type", "String"), int(a.get("width", 0) or 0)) if a else
                          Field(new, f.type, f.width))
        fields += [Field(a["name"], a.get("type", "String"), int(a.get("width", 0) or 0)) for a in added
                   if a["name"] in replaced]
        return {"features": s.with_fields(fields)}

    def needs(self, wanted):
        """§5: the columns kept that the nodes after it use, by their old names; dropped and added: not."""
        names = self.schemas_in["features"].names()
        kept, _ = self._plan(names)
        w = wanted.get("features", Needs.everything())
        cols = {old for old, new in kept if w.columns is None or new in w.columns}
        for e in self._exprs(names).values():   # what the expressions read, used or not after
            cols |= e.columns
        return {"features": Needs(w.bbox, frozenset(cols))}

    def run(self, inputs):
        names = self.schemas_in["features"].names()   # the input's structure (§4)
        kept, added = self._plan(names)
        exprs = self._exprs(names)
        consts = [(a["name"], a.get("value")) for a in added if a["name"] not in exprs]
        computed = [(a["name"], exprs[a["name"]], a.get("type", "String")) for a in added if a["name"] in exprs]

        def convert(v, kind):
            """The value to the column's type: 12.7 into an Integer: 12 (cut, as int()); a text into
            a number: read, else an error naming the feature."""
            if v is None:
                return None
            if kind in INTEGERS:
                return int(v) if isinstance(v, (int, float, bool)) else int(float(str(v).replace(",", ".")))
            if kind == "Real":
                return float(v) if isinstance(v, (int, float, bool)) else float(str(v).replace(",", "."))
            if kind == "String":
                return text(v)
            return v

        def gen():
            for f in inputs["features"]:
                old = f.attributes
                new = {n: old.get(o) for o, n in kept}
                new.update(consts)
                for name, e, kind in computed:   # they read the columns as they arrived
                    try:
                        new[name] = convert(e(old, f.geometry), kind)
                    except (ValueError, TypeError) as err:
                        raise RuntimeError(f"column {name} = {e.text.strip()}: {err} — on {f.describe()}")
                f.attributes = new
                yield f

        return {"features": gen()}
