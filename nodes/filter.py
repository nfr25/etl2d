"""The Filter: one output per rule, plus 'else' (notes §7; FME's TestFilter)."""

from __future__ import annotations

from etl2d import Input, Output, Param, SchemaError, node
from etl2d.needs import Needs
from etl2d.run import split

OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "not in", "contains", "starts with", "is empty", "is not empty")
NO_VALUE = ("is empty", "is not empty")
NUMBERS = ("Integer", "Integer64", "Real")


def _number(text, rule) -> float:
    if str(text if text is not None else "").strip() == "":
        raise SchemaError(f"rule '{rule}': no value yet (a number)")
    try:
        return float(str(text).strip().replace(",", "."))
    except ValueError:
        raise SchemaError(f"rule '{rule}': a number is expected, not '{text}'")


def _test(op: str, value, numeric: bool, rule: str):
    """The comparison of one rule, made once: a function of a feature's value, true or false.

    An empty value (NULL, or "" for is empty) makes every comparison false, but
    'is empty': such features go on to the next rule, or to 'else' (§7).
    """
    if op == "is empty":
        return lambda v: v is None or v == ""
    if op == "is not empty":
        return lambda v: not (v is None or v == "")
    if op in ("in", "not in"):
        items = [x.strip() for x in str(value or "").split(",") if x.strip()]
        if numeric:
            nums = {_number(x, rule) for x in items}
            inside = lambda v: float(v) in nums
        else:
            texts = set(items)
            inside = lambda v: str(v) in texts
        if op == "in":
            return lambda v: v is not None and inside(v)
        return lambda v: v is not None and not inside(v)
    if op in ("contains", "starts with"):
        t = str(value or "").casefold()
        if op == "contains":
            return lambda v: v is not None and t in str(v).casefold()
        return lambda v: v is not None and str(v).casefold().startswith(t)
    # = != < <= > >=: as numbers on a column of numbers, as text otherwise
    if numeric:
        x = _number(value, rule)
        conv = float
    else:
        x = str(value if value is not None else "")
        conv = str
    cmp = {"=": lambda a: a == x, "!=": lambda a: a != x, "<": lambda a: a < x, "<=": lambda a: a <= x,
           ">": lambda a: a > x, ">=": lambda a: a >= x}[op]
    return lambda v: v is not None and cmp(conv(v))


@node("Filter", "Filters")
class Filter:
    """Each feature out on the first rule it meets, top to bottom, else on 'else'.

    A rule: a name (its output's), a column, a comparison, a value. Comparisons:
    = != < <= > >= (as numbers on a column of numbers), in / not in (a list, with
    commas), contains / starts with (capitals ignored), is empty / is not empty.
    An empty value (NULL) makes every comparison false but 'is empty'.
    """

    inputs = [Input("features")]
    outputs = [Output("else")]   # the fixed one; the rules' come first (outputs_for)
    params = [Param("rules", "rules", [], help="name, column, comparison, value; the first rule met wins")]

    @staticmethod
    def outputs_for(params) -> list[str]:
        """§7: the outputs from the parameters: one per rule, in their order, then 'else'."""
        return [str(r.get("name", "")) for r in (params.get("rules") or [])] + ["else"]

    def _rules(self, schema):
        """[(name, column, test)], checked against the structure received (§4)."""
        rules = self.params.get("rules") or []
        seen, out = set(), []
        types = {f.name: f.type for f in schema.fields}
        for i, r in enumerate(rules, 1):
            name = str(r.get("name", "")).strip()
            if not name:
                raise SchemaError(f"rule {i} has no name (it names its output)")
            if name == "else" or name in seen:
                raise SchemaError(f"two outputs named '{name}' (rule {i})")
            seen.add(name)
            col, op = r.get("column", ""), r.get("op", "=")
            if col not in types:
                raise SchemaError(f"rule '{name}': no column '{col}' arrives"
                                  + (f" (it has: {', '.join(types)})" if types else ""))
            if op not in OPS:
                raise SchemaError(f"rule '{name}': no comparison '{op}'")
            numeric = types[col] in NUMBERS and op not in ("contains", "starts with")
            out.append((name, col, _test(op, r.get("value", ""), numeric, name)))
        return out

    def schema(self, schemas):
        s = schemas["features"]
        rules = self._rules(s)
        s = s.with_count(None)   # how many go where is known only by running
        return {name: s for name, _, _ in rules} | {"else": s}

    def needs(self, wanted):
        """§5: its rules' columns, and what the nodes after it need of its outputs together."""
        if not wanted:   # no output joined: nothing goes on
            return {"features": Needs(None, frozenset())}
        together = None
        for w in wanted.values():
            together = w if together is None else together.union(w)
        read = {str(r.get("column", "")) for r in (self.params.get("rules") or [])}
        cols = None if together.columns is None else together.columns | read
        return {"features": Needs(together.bbox, cols)}

    def run(self, inputs):
        rules = self._rules(self.schemas_in["features"])

        def route(f):
            a = f.attributes
            for name, col, test in rules:
                if test(a.get(col)):
                    return name
            return "else"

        return split(inputs["features"], route, [n for n, _, _ in rules] + ["else"], self.ctx.connected)
