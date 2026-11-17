"""The SpatialRelator: which reference each feature falls in (notes §7; FME's SpatialRelator)."""

from __future__ import annotations

from osgeo import ogr

from etl2d import Field, Input, Output, Param, SchemaError, node
from etl2d.index import Grid
from etl2d.needs import Needs
from etl2d.run import split

ogr.UseExceptions()

TESTS = ("inside", "point inside", "intersects", "contains")
SEVERAL = ("first", "each")


def _test(name: str):
    """The test of a feature's geometry g (or its point p) against a reference r."""
    return {"inside": lambda g, p, r: g.Within(r),
            "point inside": lambda g, p, r: p.Within(r),
            "intersects": lambda g, p, r: g.Intersects(r),
            "contains": lambda g, p, r: g.Contains(r)}[name]


@node("SpatialRelator", "Relations")
class SpatialRelator:
    """Each feature tested against the references (read whole first, in a grid): matched, with the
    reference's columns copied (and the count), or unmatched, unchanged.

    Tests: inside (wholly: points in polygons), point inside (a point of the feature's own surface:
    polygons drawn by other hands, buildings to parcels), intersects (a point shared, a touching
    edge too), contains (the reference wholly inside the feature: with the count, how many
    addresses each parcel holds). Several met: the first (in the reference's order), or each
    (the feature out once per reference).
    """

    inputs = [Input("features"), Input("reference")]
    outputs = [Output("matched"), Output("unmatched")]
    params = [Param("test", "choice", "inside", choices=TESTS,
                    help="inside, point inside (buildings to parcels), intersects, contains"),
              Param("columns", "names", [], input="reference", help="the reference's columns copied onto the feature"),
              Param("prefix", "str", "", help="put before the copied columns' names: parcel_ gives parcel_capakey"),
              Param("several", "choice", "first", choices=SEVERAL,
                    help="several references met: the first (in the reference's order), or each (one feature per reference)"),
              Param("count", "str", "", help="a column with how many references were met; empty: none")]

    # --- structure (§4) ---

    def _copied(self, features, reference) -> list[tuple[str, Field]]:
        """[(name on the reference, field on the output)], checked."""
        prefix = str(self.params.get("prefix") or "")
        names = [str(n) for n in (self.params.get("columns") or [])]
        out, seen = [], set(features.names())
        for n in names:
            f = reference.field(n)
            if f is None:
                raise SchemaError(f"no column '{n}' arrives on the reference"
                                  + (f" (it has: {', '.join(reference.names())})" if reference.fields else ""))
            new = prefix + n
            if new in seen:
                raise SchemaError(f"a column '{new}' arrives on the features too: give a prefix (parcel_ gives parcel_{n})")
            seen.add(new)
            out.append((n, Field(new, f.type, f.width)))
        count = str(self.params.get("count") or "").strip()
        if count and count in seen:
            raise SchemaError(f"the count column '{count}' arrives already: give it another name")
        return out

    def schema(self, schemas):
        s, r = schemas["features"], schemas["reference"]
        if str(self.params.get("test")) not in TESTS:
            raise SchemaError(f"no test '{self.params.get('test')}'")
        if str(self.params.get("several")) not in SEVERAL:
            raise SchemaError(f"no choice '{self.params.get('several')}' for several")
        if not s.same_crs(r):
            raise SchemaError(f"the features are in {s.crs_name()}, the reference in {r.crs_name()}: "
                              f"a Reprojector before one of them")
        fields = list(s.fields) + [f for _, f in self._copied(s, r)]
        count = str(self.params.get("count") or "").strip()
        if count:
            fields.append(Field(count, "Integer"))
        return {"matched": s.with_fields(fields).with_count(None), "unmatched": s.with_count(None)}

    # --- needs (§5) ---

    def needs(self, wanted):
        """From the features: what its outputs need, without the columns it adds. From the reference:
        the copied columns only, in the same box (a reference whose box misses it cannot be met)."""
        if not wanted:
            return {"features": Needs(None, frozenset()), "reference": Needs(None, frozenset())}
        together = None
        for w in wanted.values():
            together = w if together is None else together.union(w)
        prefix = str(self.params.get("prefix") or "")
        names = [str(n) for n in (self.params.get("columns") or [])]
        added = {prefix + n for n in names} | {str(self.params.get("count") or "").strip()}
        cols = None if together.columns is None else together.columns - added
        return {"features": Needs(together.bbox, cols), "reference": Needs(together.bbox, frozenset(names))}

    # --- run ---

    def run(self, inputs):
        s = self.schemas_in["features"]
        copied = self._copied(s, self.schemas_in["reference"])
        count = str(self.params.get("count") or "").strip()
        test = _test(str(self.params["test"]))
        point = self.params["test"] == "point inside"
        each = self.params.get("several") == "each"
        log = self.ctx.log

        def tagged():
            """(port, feature), the reference read whole first, at the first feature asked."""
            geoms, values, boxes, skipped = [], [], [], 0
            for f in inputs["reference"]:
                g = f.geometry
                if g is None or g.IsEmpty():
                    skipped += 1
                    continue
                geoms.append(g)
                values.append(tuple(f.attributes.get(n) for n, _ in copied))
                boxes.append(g.GetEnvelope())
            grid = Grid(boxes)
            log(f"reference {len(geoms):,}, cell {grid.side:,.1f} m"
                + (f", {skipped:,} without geometry left out" if skipped else ""))
            for f in inputs["features"]:
                g = f.geometry
                if g is None or g.IsEmpty():
                    yield "unmatched", f
                    continue
                p = g.PointOnSurface() if point else None
                met = [i for i in grid.query((p or g).GetEnvelope()) if test(g, p, geoms[i])]
                if not met:
                    yield "unmatched", f
                    continue
                for k, i in enumerate(met if each else met[:1]):
                    out = f if k == len(met) - 1 or not each else f.copy()
                    for (_, fld), v in zip(copied, values[i]):
                        out.attributes[fld.name] = v
                    if count:
                        out.attributes[count] = len(met)
                    yield "matched", out

        ports = split(tagged(), lambda t: t[0], ["matched", "unmatched"], self.ctx.connected)
        return {name: (t[1] for t in it) for name, it in ports.items()}
