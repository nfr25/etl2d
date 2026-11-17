"""Geometry: reproject; clip by a box (notes §7)."""

from __future__ import annotations

from osgeo import ogr, osr

from etl2d import Input, Output, Param, SchemaError, node
from etl2d.needs import Needs, intersect
from etl2d.run import split

osr.UseExceptions()


def srs_of(text: str, what: str) -> osr.SpatialReference:
    """'EPSG:3812', a WKT, a PROJ string... in x = east, y = north order whatever the authority says."""
    srs = osr.SpatialReference()
    try:
        srs.SetFromUserInput(text)
    except RuntimeError as e:
        raise SchemaError(f"{what}: '{text}' is not a known coordinate system ({e})")
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


@node("Reprojector", "Geometry")
class Reprojector:
    """From the input's coordinate system (or the one forced) to another, by PROJ."""

    inputs = [Input("features")]
    outputs = [Output("features")]
    params = [Param("target", "crs", "EPSG:3812", help="the system wanted"),
              Param("source", "crs", "", help="empty: the input's; or forced, when a file says nothing or lies")]

    def _systems(self, s):
        src_text = self.params["source"] or s.crs
        if not src_text:
            raise SchemaError("the input declares no coordinate system: give one in 'source'")
        return srs_of(src_text, "source"), srs_of(self.params["target"], "target")

    def schema(self, schemas):
        s = schemas["features"]
        src, dst = self._systems(s)
        try:
            osr.CoordinateTransformation(src, dst)
        except RuntimeError as e:
            raise SchemaError(f"no way from {s.crs_name() or 'the source'} to {self.params['target']}: {e}")
        return {"features": s.with_crs(dst.ExportToWkt())}

    def needs(self, wanted):
        """§5: the box asked after it, taken back into the source's system.

        Its edges are followed (21 points each), not only its corners: a
        straight edge in one system is slightly curved in another. Then 1 % of
        margin around, so nothing near the edge is lost; the exact test is done after.
        """
        w = wanted.get("features", Needs.everything())
        if w.bbox is None:
            return {"features": w}
        src, dst = self._systems(self.schemas_in["features"])
        back = osr.CoordinateTransformation(dst, src)
        xmin, ymin, xmax, ymax = w.bbox
        pts = []
        for k in range(21):
            t = k / 20
            for x, y in ((xmin + t * (xmax - xmin), ymin), (xmin + t * (xmax - xmin), ymax),
                         (xmin, ymin + t * (ymax - ymin)), (xmax, ymin + t * (ymax - ymin))):
                pts.append(back.TransformPoint(x, y)[:2])
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        mx, my = (max(xs) - min(xs)) * 0.01, (max(ys) - min(ys)) * 0.01
        return {"features": Needs((min(xs) - mx, min(ys) - my, max(xs) + mx, max(ys) + my), w.columns)}

    def run(self, inputs):
        src, dst = self._systems(self.schemas_in["features"])

        def gen():
            ct = osr.CoordinateTransformation(src, dst)
            for f in inputs["features"]:
                if f.geometry is not None:
                    f.geometry.Transform(ct)
                yield f

        return {"features": gen()}


@node("Clipper", "Geometry")
class Clipper:
    """Features compared to a box: inside, across its edge (partial), outside. Across: whole or cut."""

    inputs = [Input("features")]
    outputs = [Output("inside"), Output("partial"), Output("outside")]
    params = [Param("box", "bbox", None, help="xmin, ymin, xmax, ymax, in the input's system"),
              Param("cut", "bool", True, help="the features across the edge cut at the box; else whole")]

    def _box(self):
        b = self.params["box"]
        if not b or len(b) != 4:
            raise SchemaError("no box: four numbers, xmin ymin xmax ymax")
        xmin, ymin, xmax, ymax = map(float, b)
        if xmin >= xmax or ymin >= ymax:
            raise SchemaError(f"the box is empty: {b} (xmin < xmax and ymin < ymax)")
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)):
            ring.AddPoint_2D(x, y)
        poly = ogr.Geometry(ogr.wkbPolygon)
        poly.AddGeometry(ring)
        return poly

    def schema(self, schemas):
        self._box()
        s = schemas["features"].with_count(None)   # how many go where is known only by running
        return {"inside": s, "partial": s, "outside": s}

    def needs(self, wanted):
        """§5: only what touches its box, unless its 'outside' is used (then everything).

        Its outputs' needs together: the columns any of them uses; the box
        asked after inside and partial (the larger), cut down to its own box.
        """
        if not wanted:   # no output joined: nothing goes on
            return {"features": Needs(None, frozenset())}
        together = None
        for w in wanted.values():
            together = w if together is None else together.union(w)
        if "outside" in wanted:
            return {"features": Needs(None, together.columns)}
        e = self._box().GetEnvelope()   # (xmin, xmax, ymin, ymax)
        return {"features": Needs(intersect((e[0], e[2], e[1], e[3]), together.bbox), together.columns)}

    def run(self, inputs):
        box, cut = self._box(), bool(self.params["cut"])
        env = box.GetEnvelope()   # (xmin, xmax, ymin, ymax): a quick test before the exact one

        def route(f):
            g = f.geometry
            if g is None or g.IsEmpty():
                return "outside"
            gx0, gx1, gy0, gy1 = g.GetEnvelope()
            if gx1 < env[0] or gx0 > env[1] or gy1 < env[2] or gy0 > env[3]:
                return "outside"            # far from the box: no exact test needed
            if g.Within(box):
                return "inside"
            if g.Intersects(box):
                if cut:
                    f.geometry = g.Intersection(box)
                return "partial"
            return "outside"

        return split(inputs["features"], route, ["inside", "partial", "outside"], self.ctx.connected)
