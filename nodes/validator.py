"""The GeometryValidator: valid, repaired, invalid (notes §7)."""

from __future__ import annotations

from osgeo import gdal, ogr

from etl2d import Field, Input, Output, Param, SchemaError, node
from etl2d.needs import Needs
from etl2d.run import split

gdal.UseExceptions()
ogr.UseExceptions()

MULTI = {"Point": "MultiPoint", "LineString": "MultiLineString", "Polygon": "MultiPolygon"}
MULTI_OGR = {0: ogr.wkbMultiPoint, 1: ogr.wkbMultiLineString, 2: ogr.wkbMultiPolygon}
DIM_NAMES = {0: "point", 1: "line", 2: "polygon"}


class Reasons:
    """GEOS's reason for an invalid geometry, through SpatiaLite (ST_IsValidReason), when GDAL has it."""

    def __init__(self):
        self.ds = gdal.GetDriverByName("Memory").Create("", 0, 0, 0, gdal.GDT_Unknown)
        self.ok = self._ask(ogr.CreateGeometryFromWkt("POLYGON ((0 0,1 1,1 0,0 1,0 0))")) is not None

    def _ask(self, g):
        try:
            sql = f"SELECT ST_IsValidReason(ST_GeomFromWKB(X'{g.ExportToWkb().hex()}'))"
            r = self.ds.ExecuteSQL(sql, dialect="SQLITE")
            try:
                f = r.GetNextFeature()
                return f.GetField(0) if f is not None else None
            finally:
                self.ds.ReleaseResultSet(r)
        except RuntimeError:
            return None

    def __call__(self, g) -> str:
        if not self.ok:
            return "invalid (no reason: this GDAL has no SpatiaLite)"
        return self._ask(g) or "invalid"


def parts_of(g, dim: int) -> list:
    """The parts of a geometry of one dimension, out of collections and multis (copies)."""
    out = []
    n = g.GetGeometryCount()
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t in (ogr.wkbGeometryCollection, ogr.wkbMultiPoint, ogr.wkbMultiLineString, ogr.wkbMultiPolygon):
        for i in range(n):
            out += parts_of(g.GetGeometryRef(i), dim)
    elif not g.IsEmpty() and g.GetDimension() == dim:
        out.append(g.Clone())
    return out


@node("GeometryValidator", "Geometry")
class GeometryValidator:
    """Each geometry checked (GEOS's IsValid): valid; repaired (MakeValid, its own dimension kept,
    made Multi); invalid (none, empty, nothing left after the repair). A column says what was wrong,
    with its place: Self-intersection[5 5]."""

    inputs = [Input("features")]
    outputs = [Output("valid"), Output("repaired"), Output("invalid")]
    params = [Param("repair", "bool", True, help="repair what can be (MakeValid); off: every invalid one to 'invalid'"),
              Param("reason", "str", "reason", help="the column added to repaired and invalid: what was wrong")]

    def _reason_col(self, s) -> str:
        name = str(self.params["reason"] or "").strip()
        if not name:
            raise SchemaError("no name for the reason column")
        if name in s.names():
            raise SchemaError(f"a column '{name}' arrives already: give the reason column another name")
        return name

    def schema(self, schemas):
        s = schemas["features"]
        col = self._reason_col(s)
        bad = s.with_fields(list(s.fields) + [Field(col, "String", 254)]).with_count(None)
        repaired = bad
        if s.geometry in MULTI:   # a Polygon may come back as two: a MultiPolygon
            from dataclasses import replace
            repaired = replace(bad, geometry=MULTI[s.geometry])
        return {"valid": s.with_count(None), "repaired": repaired, "invalid": bad}

    def needs(self, wanted):
        """§5: what its outputs need together; it reads no column (its reason column is its own)."""
        if not wanted:
            return {"features": Needs(None, frozenset())}
        together = None
        for w in wanted.values():
            together = w if together is None else together.union(w)
        col = str(self.params["reason"] or "")
        cols = None if together.columns is None else together.columns - {col}
        return {"features": Needs(together.bbox, cols)}

    def run(self, inputs):
        col = self._reason_col(self.schemas_in["features"])
        repair = bool(self.params["repair"])
        reasons = Reasons()

        def route(f):
            g = f.geometry
            if g is None:
                f.attributes[col] = "no geometry"
                return "invalid"
            if g.IsEmpty():
                f.attributes[col] = "empty geometry"
                return "invalid"
            if g.IsValid():
                return "valid"
            why = reasons(g)
            f.attributes[col] = why
            if not repair:
                return "invalid"
            dim = g.GetDimension()
            try:
                fixed = g.MakeValid()
            except RuntimeError as e:
                f.attributes[col] = f"{why}; not repaired: {e}"
                return "invalid"
            parts = parts_of(fixed, dim) if fixed is not None else []
            if not parts:
                f.attributes[col] = f"{why}; no {DIM_NAMES.get(dim, 'part')} left after the repair"
                return "invalid"
            multi = ogr.Geometry(MULTI_OGR[dim])
            for p in parts:
                multi.AddGeometry(p)
            f.geometry = multi
            return "repaired"

        return split(inputs["features"], route, ["valid", "repaired", "invalid"], self.ctx.connected)
