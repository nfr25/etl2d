"""Expressions: computed values without Python, as in a spreadsheet (notes §7).

    = round(area / 10) * 10
    = strnamefre || ' ' || policenum
    = if(npoints() > 500, 'detailed', 'simple')

A small parser of our own (Pratt's method: each operator has a strength, the
stronger binds first), not Python's eval: an expression can only compute. It
is read once, into a tree of small Python functions; each feature then only
calls the tree. Positions in the messages count from 1, as one reads; indexes
in the language (x(0), substr(t, 0, 3)) count from 0, as Python (§7).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from osgeo import ogr


class ExprError(Exception):
    """A mistake in an expression, with its position (1 = the first character)."""

    def __init__(self, message: str, pos: int | None = None):
        super().__init__(f"position {pos}: {message}" if pos is not None else message)
        self.pos = pos


# ---- reading: the text into tokens ---------------------------------------------------------------
KEYWORDS = {"AND", "OR", "NOT", "IS", "NULL", "IN", "TRUE", "FALSE"}
SYMBOLS = ("||", "<=", ">=", "!=", "<>", "=", "<", ">", "+", "-", "*", "/", "%", "(", ")", ",")


@dataclass
class Token:
    kind: str    # "num", "str", "name", "qname" (a "quoted" column), "kw", "op", "end"
    value: Any
    pos: int     # from 1


def tokens(text: str) -> list[Token]:
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i
            while j < n and (text[j].isdigit() or text[j] == "."):
                j += 1
            if j < n and text[j] in "eE" and j + 1 < n and (text[j + 1].isdigit() or text[j + 1] in "+-"):
                j += 2
                while j < n and text[j].isdigit():
                    j += 1
            s = text[i:j]
            try:
                out.append(Token("num", float(s) if any(x in s for x in ".eE") else int(s), i + 1))
            except ValueError:
                raise ExprError(f"'{s}' is not a number", i + 1)
            i = j
        elif c in "'\"":
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise ExprError("a quote opened here is not closed", i + 1)
                if text[j] == c:
                    if j + 1 < n and text[j + 1] == c:   # '' inside '...': one quote
                        buf.append(c)
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            out.append(Token("str" if c == "'" else "qname", "".join(buf), i + 1))
            i = j + 1
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            out.append(Token("kw", word.upper(), i + 1) if word.upper() in KEYWORDS else Token("name", word, i + 1))
            i = j
        else:
            for s in SYMBOLS:
                if text.startswith(s, i):
                    out.append(Token("op", "!=" if s == "<>" else s, i + 1))
                    i += len(s)
                    break
            else:
                raise ExprError(f"'{c}' means nothing here", i + 1)
    out.append(Token("end", None, n + 1))
    return out


# ---- the values: NULL, numbers, texts --------------------------------------------------------------
def _num(v, what: str):
    """A number from a value (a text that reads as one is accepted: shapefiles keep numbers as texts)."""
    if v is None or isinstance(v, bool):
        return None if v is None else int(v)
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"{what}: '{v}' is not a number")


def text(v) -> str | None:
    """A value as text: 103.0 is '103' (a number without decimals is written without them)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _round(x, n=0):
    """Halves away from zero, as one learnt at school (Python's round(2.5) gives 2)."""
    x, n = _num(x, "round"), int(_num(n, "round") or 0)
    if x is None:
        return None
    f = 10 ** n
    r = math.floor(abs(x) * f + 0.5) / f
    r = -r if x < 0 else r
    return int(r) if n <= 0 else r


def _compare(a, b):
    """(a, b) made comparable: numbers when both can be, texts otherwise."""
    if isinstance(a, (int, float)) and not isinstance(b, (int, float)) and b is not None:
        try:
            return a, _num(b, "")
        except ValueError:
            return text(a), str(b)
    if isinstance(b, (int, float)) and not isinstance(a, (int, float)) and a is not None:
        try:
            return _num(a, ""), b
        except ValueError:
            return str(a), text(b)
    return a, b


# ---- the geometry functions ---------------------------------------------------------------------
GEOMETRY_NAMES = {"POINT": "Point", "LINESTRING": "LineString", "POLYGON": "Polygon", "MULTIPOINT": "MultiPoint",
                  "MULTILINESTRING": "MultiLineString", "MULTIPOLYGON": "MultiPolygon",
                  "GEOMETRYCOLLECTION": "GeometryCollection"}


def points(g) -> list[tuple[float, float]]:
    """Every point, through every part and ring, in their order (a ring's last point repeats its first)."""
    if g is None:
        return []
    n = g.GetGeometryCount()
    if n:
        out = []
        for i in range(n):
            out += points(g.GetGeometryRef(i))
        return out
    return [(g.GetX(i), g.GetY(i)) for i in range(g.GetPointCount())]


def _area(g):
    return None if g is None else g.GetArea()


def _length(g):
    """A line's length; a polygon's perimeter (its rings); a point: 0."""
    if g is None:
        return None
    dim = g.GetDimension()
    if dim == 2:
        return g.Boundary().Length()
    return g.Length() if dim == 1 else 0.0


def _point(g, i, axis):
    p = points(g)
    i = int(_num(i, "x(i)"))
    if not p or not -len(p) <= i < len(p):
        return None
    return p[i][axis]


def _env(g, k):
    return None if g is None or g.IsEmpty() else g.GetEnvelope()[k]   # (xmin, xmax, ymin, ymax)


def _centroid(g, axis):
    if g is None or g.IsEmpty():
        return None
    c = g.Centroid()
    return c.GetX() if axis == 0 else c.GetY()


def _nparts(g):
    if g is None:
        return None
    t = ogr.GT_Flatten(g.GetGeometryType())
    return g.GetGeometryCount() if t in (ogr.wkbMultiPoint, ogr.wkbMultiLineString, ogr.wkbMultiPolygon,
                                         ogr.wkbGeometryCollection) else (0 if g.IsEmpty() else 1)


# name: (fewest arguments, most (None: any), the function, does it read the geometry)
# The geometry functions get the geometry first, then their arguments.
def _s(f):
    """A text function: NULL gives NULL."""
    return lambda v, *a: None if v is None else f(str(v), *a)


def _substr(t, start, count=None):
    start = int(_num(start, "substr"))
    if count is None:
        return t[start:]
    count = int(_num(count, "substr"))
    end = start + count
    return t[start:end] if end != 0 else t[start:]


def _min_max(f):
    def g(*vals):
        nums = [v for v in vals if v is not None]
        return f(nums) if nums else None
    return g


def _int(v):
    v = _num(v, "int")
    return None if v is None else int(v)


def _real(v):
    v = _num(v, "real")
    return None if v is None else float(v)


def _unary(f, what):
    def g(v):
        v = _num(v, what)
        return None if v is None else f(v)
    return g


FUNCTIONS: dict[str, tuple[int, int | None, Callable, bool]] = {
    "round": (1, 2, _round, False),
    "floor": (1, 1, _unary(lambda v: int(math.floor(v)), "floor"), False),
    "ceil": (1, 1, _unary(lambda v: int(math.ceil(v)), "ceil"), False),
    "abs": (1, 1, _unary(abs, "abs"), False),
    "sqrt": (1, 1, _unary(lambda v: math.sqrt(v) if v >= 0 else None, "sqrt"), False),
    "min": (1, None, _min_max(min), False),
    "max": (1, None, _min_max(max), False),
    "upper": (1, 1, _s(str.upper), False),
    "lower": (1, 1, _s(str.lower), False),
    "trim": (1, 1, _s(str.strip), False),
    "len": (1, 1, _s(len), False),
    "substr": (2, 3, _s(_substr), False),
    "replace": (3, 3, _s(lambda t, a, b: t.replace(text(a) or "", text(b) or "")), False),
    "starts": (2, 2, _s(lambda t, b: t.casefold().startswith((text(b) or "").casefold())), False),
    "contains": (2, 2, _s(lambda t, b: (text(b) or "").casefold() in t.casefold()), False),
    "int": (1, 1, _int, False),
    "real": (1, 1, _real, False),
    "text": (1, 1, text, False),
    "area": (0, 0, _area, True),
    "length": (0, 0, _length, True),
    "npoints": (0, 0, lambda g: None if g is None else len(points(g)), True),
    "nparts": (0, 0, _nparts, True),
    "x": (1, 1, lambda g, i: _point(g, i, 0), True),
    "y": (1, 1, lambda g, i: _point(g, i, 1), True),
    "centroid_x": (0, 0, lambda g: _centroid(g, 0), True),
    "centroid_y": (0, 0, lambda g: _centroid(g, 1), True),
    "xmin": (0, 0, lambda g: _env(g, 0), True),
    "xmax": (0, 0, lambda g: _env(g, 1), True),
    "ymin": (0, 0, lambda g: _env(g, 2), True),
    "ymax": (0, 0, lambda g: _env(g, 3), True),
    "geomtype": (0, 0, lambda g: None if g is None else GEOMETRY_NAMES.get(g.GetGeometryName(), g.GetGeometryName()), True),
}
LAZY = {"if", "coalesce"}   # their arguments computed only as needed


# ---- understanding: tokens into a tree of functions -----------------------------------------------
# The strength of each operator: the stronger binds first (* before +, AND before OR).
STRENGTH = {"OR": 1, "AND": 2, "=": 4, "!=": 4, "<": 4, "<=": 4, ">": 4, ">=": 4, "IS": 4, "IN": 4,
            "||": 5, "+": 6, "-": 6, "*": 7, "/": 7, "%": 7}
Fn = Callable[[dict, Any], Any]   # (attributes, geometry) -> value


class Parser:
    def __init__(self, text: str, columns: list[str] | None):
        self.toks, self.i = tokens(text), 0
        self.columns = columns           # None: not checked (any name is a column)
        self.used: set[str] = set()      # the columns read: asked to the reader (§5)
        self.geometry = False            # does it read the geometry

    def peek(self) -> Token:
        return self.toks[self.i]

    def take(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, kind, value, what):
        t = self.take()
        if t.kind != kind or t.value != value:
            raise ExprError(f"{what} expected" + ("" if t.kind == "end" else f", not '{t.value}'"), t.pos)
        return t

    def key(self, t: Token):
        if t.kind == "op" and t.value in STRENGTH:
            return t.value
        if t.kind == "kw" and t.value in STRENGTH:
            return t.value
        if t.kind == "kw" and t.value == "NOT" and self.toks[self.i + 1].kind == "kw" and \
                self.toks[self.i + 1].value == "IN":
            return "IN"   # NOT IN: the strength of IN
        return None

    def parse(self) -> Fn:
        if self.peek().kind == "end":
            raise ExprError("the expression is empty", self.peek().pos)
        fn = self.expr(0)
        t = self.peek()
        if t.kind != "end":
            raise ExprError(f"'{t.value}' is not expected here (an operator missing before it?)", t.pos)
        return fn

    def expr(self, floor: int) -> Fn:
        left = self.prefix()
        while True:
            t = self.peek()
            op = self.key(t)
            if op is None or STRENGTH[op] <= floor:
                return left
            left = self.infix(op, left)

    def prefix(self) -> Fn:
        t = self.take()
        if t.kind == "num" or t.kind == "str":
            v = t.value
            return lambda a, g: v
        if t.kind == "kw" and t.value in ("NULL", "TRUE", "FALSE"):
            v = {"NULL": None, "TRUE": True, "FALSE": False}[t.value]
            return lambda a, g: v
        if t.kind == "kw" and t.value == "NOT":
            inner = self.expr(3)
            return lambda a, g: not bool(inner(a, g))
        if t.kind == "op" and t.value == "-":
            inner = self.expr(8)

            def neg(a, g):
                v = _num(inner(a, g), "-")
                return None if v is None else -v
            return neg
        if t.kind == "op" and t.value == "+":
            return self.expr(8)
        if t.kind == "op" and t.value == "(":
            inner = self.expr(0)
            self.expect("op", ")", "')' closing the '(' of position %d" % t.pos)
            return inner
        if t.kind == "qname":
            return self.column(t.value, t.pos)
        if t.kind == "name":
            if self.peek().kind == "op" and self.peek().value == "(":
                return self.call(t)
            return self.column(t.value, t.pos)
        if t.kind == "end":
            prev = self.toks[self.i - 2] if self.i >= 2 else None
            raise ExprError("a value expected" + (f" after {prev.value}" if prev is not None else ""), t.pos)
        raise ExprError(f"a value expected, not '{t.value}'", t.pos)

    def column(self, name: str, pos: int) -> Fn:
        if self.columns is not None and name not in self.columns:
            near = [c for c in self.columns if c.lower() == name.lower()]
            hint = f" (did you mean {near[0]}?)" if near else ""
            raise ExprError(f"no column '{name}' arrives{hint}", pos)
        self.used.add(name)
        return lambda a, g: a.get(name)

    def call(self, t: Token) -> Fn:
        name = t.value.lower()
        self.take()   # (
        args: list[Fn] = []
        if not (self.peek().kind == "op" and self.peek().value == ")"):
            while True:
                args.append(self.expr(0))
                if self.peek().kind == "op" and self.peek().value == ",":
                    self.take()
                    continue
                break
        self.expect("op", ")", f"')' closing {t.value}(")
        if name in LAZY:
            if name == "if":
                if len(args) != 3:
                    raise ExprError("if(condition, then, else): three values", t.pos)
                c, x, y = args
                return lambda a, g: x(a, g) if c(a, g) else y(a, g)

            def coalesce(a, g):
                for f in args:
                    v = f(a, g)
                    if v is not None and v != "":
                        return v
                return None
            if not args:
                raise ExprError("coalesce(a, b, ...): at least one value", t.pos)
            return coalesce
        if name not in FUNCTIONS:
            raise ExprError(f"no function {t.value}()" + (" (a column is written without brackets)"
                                                           if self.columns and t.value in self.columns else ""), t.pos)
        lo, hi, f, geo = FUNCTIONS[name]
        if len(args) < lo or (hi is not None and len(args) > hi):
            want = f"{lo}" if lo == hi else (f"{lo} to {hi}" if hi is not None else f"at least {lo}")
            raise ExprError(f"{t.value}() takes {want} value{'s' if (hi or 2) != 1 else ''}, not {len(args)}", t.pos)
        if geo:
            self.geometry = True
            return lambda a, g: f(g, *[x(a, g) for x in args])
        return lambda a, g: f(*[x(a, g) for x in args])

    def infix(self, op: str, left: Fn) -> Fn:
        t = self.take()
        if op == "IS":
            neg = self.peek().kind == "kw" and self.peek().value == "NOT"
            if neg:
                self.take()
            self.expect("kw", "NULL", "NULL after IS" + (" NOT" if neg else ""))
            return (lambda a, g: left(a, g) is not None) if neg else (lambda a, g: left(a, g) is None)
        if op == "IN":
            neg = t.kind == "kw" and t.value == "NOT"
            if neg:
                self.take()   # IN
            self.expect("op", "(", "'(' after IN")
            items: list[Fn] = []
            while True:
                items.append(self.expr(0))
                if self.peek().kind == "op" and self.peek().value == ",":
                    self.take()
                    continue
                break
            self.expect("op", ")", "')' closing the list")

            def inside(a, g):
                v = left(a, g)
                if v is None:
                    return False
                found = any(x == y for x, y in (_compare(v, f(a, g)) for f in items))
                return found != neg
            return inside
        right = self.expr(STRENGTH[op])
        if op == "AND":
            return lambda a, g: bool(left(a, g)) and bool(right(a, g))
        if op == "OR":
            return lambda a, g: bool(left(a, g)) or bool(right(a, g))
        if op == "||":
            def join(a, g):
                x, y = left(a, g), right(a, g)
                return None if x is None or y is None else text(x) + text(y)
            return join
        if op in ("=", "!=", "<", "<=", ">", ">="):
            cmp = {"=": lambda x, y: x == y, "!=": lambda x, y: x != y, "<": lambda x, y: x < y,
                   "<=": lambda x, y: x <= y, ">": lambda x, y: x > y, ">=": lambda x, y: x >= y}[op]

            def compare(a, g):
                x, y = left(a, g), right(a, g)
                if x is None or y is None:   # nothing compares to an empty value (§7)
                    return False
                x, y = _compare(x, y)
                try:
                    return cmp(x, y)
                except TypeError:
                    return cmp(text(x), text(y))
            return compare
        # + - * / %
        def arith(a, g, op=op):
            x, y = _num(left(a, g), op), _num(right(a, g), op)
            if x is None or y is None:
                return None
            if op == "+":
                return x + y
            if op == "-":
                return x - y
            if op == "*":
                return x * y
            if y == 0:   # divided by zero: empty, not a stop
                return None
            if op == "/":
                return x / y
            return math.fmod(x, y)
        return arith


@dataclass
class Expr:
    text: str
    fn: Fn
    columns: frozenset   # the columns it reads
    geometry: bool       # whether it reads the geometry

    def __call__(self, attributes: dict, geometry=None):
        return self.fn(attributes, geometry)


def compile_expr(text: str, columns: list[str] | None = None) -> Expr:
    """An expression read once (its mistakes then, with their position); called per feature after.
    `text` without its leading '='. `columns`: the names that arrive, to check them (None: not)."""
    p = Parser(text, columns)
    fn = p.parse()
    return Expr(text, fn, frozenset(p.used), p.geometry)


def is_expr(value) -> bool:
    """A value that begins with '=' is an expression (as in a spreadsheet); otherwise a constant."""
    return isinstance(value, str) and value.lstrip().startswith("=")


def expr_text(value: str) -> str:
    return value.lstrip()[1:]
