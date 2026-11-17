"""Expressions (notes §7): the language alone, then in Attributes."""

from __future__ import annotations

import pytest
from osgeo import ogr

from etl2d.expr import ExprError, compile_expr


def ev(text, attrs=None, geom=None, columns=None):
    return compile_expr(text, columns)(attrs or {}, geom)


def test_arithmetic_and_rounding():
    a = {"area": 103.0}
    assert ev("area * 2", a) == 206
    assert ev("round(area / 10) * 10", a) == 100           # 10.3 rounded 10, times 10
    assert ev("round(2.5)") == 3 and ev("round(-2.5)") == -3   # halves away from zero (Python's gives 2)
    assert ev("round(103.156, 2)") == 103.16
    assert ev("1 + 2 * 3") == 7 and ev("(1 + 2) * 3") == 9 and ev("-2 * 3") == -6
    assert ev("7 % 3") == 1 and ev("1 / 0") is None          # divided by zero: empty
    assert ev("floor(2.7)") == 2 and ev("ceil(2.1)") == 3 and ev("sqrt(16)") == 4


def test_texts_and_nulls():
    a = {"strnamefre": "Grand-Place", "policenum": "14", "boxnumber": None, "area": None}
    assert ev("strnamefre || ' ' || policenum", a) == "Grand-Place 14"
    assert ev("strnamefre || ' ' || boxnumber", a) is None   # joined with an empty value: empty
    assert ev("coalesce(boxnumber, '-')", a) == "-"
    assert ev("area * 2", a) is None                         # a calculation with NULL: NULL, not an error
    assert ev("area > 10", a) is False and ev("area IS NULL", a) is True
    assert ev("upper(substr(strnamefre, 0, 5))", a) == "GRAND"   # from 0: the first five letters
    assert ev("substr(strnamefre, -5)", a) == "Place"
    assert ev("len(strnamefre)", a) == 11 and ev("replace(strnamefre, '-', ' ')", a) == "Grand Place"
    assert ev("text(103.0) || ' m2'") == "103 m2"
    assert ev("'it''s'") == "it's"
    assert ev("policenum + 1", a) == 15                      # a text that reads as a number: a number


def test_logic_and_choice():
    a = {"area": 250.0, "type": "maison"}
    assert ev("if(area >= 200, 'large', 'small')", a) == "large"
    assert ev("type IN ('maison', 'garage') AND NOT area < 100", a) is True
    assert ev("type NOT IN ('maison')", a) is False
    assert ev("starts(type, 'MAI') OR 1 = 0", a) is True
    assert ev("type = 'maison' AND area > 300 OR area < 300", a) is True   # AND before OR
    assert ev("if(area > 1000, 1 / 0, 'fine')", a) == "fine"      # if computes only the branch taken


def test_geometry():
    """A ring of 5 points (4 corners, the last one closing it), a 10 x 5 rectangle."""
    g = ogr.CreateGeometryFromWkt("MULTIPOLYGON (((0 0,10 0,10 5,0 5,0 0)),((20 20,21 20,21 21,20 20)))")
    assert ev("npoints()", geom=g) == 9 and ev("nparts()", geom=g) == 2
    assert ev("x(0)", geom=g) == 0 and ev("x(1)", geom=g) == 10 and ev("y(2)", geom=g) == 5
    assert ev("x(4) = x(0)", geom=g) is True                  # the ring closed: its last point is its first
    assert ev("x(-1)", geom=g) == 20 and ev("x(30)", geom=g) is None
    assert ev("area()", geom=g) == 50.5
    line = ogr.CreateGeometryFromWkt("LINESTRING (0 0,3 4)")
    assert ev("length()", geom=line) == 5
    assert ev("length()", geom=ogr.CreateGeometryFromWkt("POLYGON ((0 0,10 0,10 5,0 5,0 0))")) == 30   # perimeter
    assert ev("geomtype()", geom=g) == "MultiPolygon"
    assert ev("xmax() - xmin()", geom=g) == 21
    assert compile_expr("area() * 2").geometry and not compile_expr("area * 2").geometry


def test_mistakes_said_with_their_place():
    cols = ["area", "type"]
    with pytest.raises(ExprError, match=r"position 8: a value expected, not '\*'"):
        compile_expr("area * * 2", cols)
    with pytest.raises(ExprError, match="no column 'surface' arrives"):
        compile_expr("surface * 2", cols)
    with pytest.raises(ExprError, match="did you mean area"):
        compile_expr("AREA * 2", cols)
    with pytest.raises(ExprError, match="no function areaa"):
        compile_expr("areaa()", cols)
    with pytest.raises(ExprError, match="closing"):
        compile_expr("round(area", cols)
    with pytest.raises(ExprError, match="not closed"):
        compile_expr("'maison", cols)
    with pytest.raises(ExprError, match="takes 3"):
        compile_expr("replace(type, 'a')", cols)
    assert compile_expr("round(area) || type", cols).columns == {"area", "type"}


def test_attributes_computed(tmp_path, workflow):
    """§7: = expressions in Attributes, a kept column replaced in its place, the columns asked to the reader."""
    from conftest import make_layer, read_all
    from etl2d.run import run
    make_layer(tmp_path / "b.gpkg", [("POLYGON ((0 0,10 0,10 5,0 5,0 0))", {"area": 103.0, "NAME": "a"}),
                                     ("POLYGON ((0 0,1 0,1 1,0 0))", {"NAME": "b"})],
               geom_type=ogr.wkbPolygon, fields=(("area", ogr.OFTReal), ("NAME", ogr.OFTString)))
    add = [{"name": "area10", "type": "Integer", "value": "= round(area / 10) * 10"},
           {"name": "corners", "type": "Integer", "value": "= npoints() - 1"},
           {"name": "label", "type": "String", "value": "= NAME || ': ' || text(round(area()))"},
           {"name": "NAME", "type": "String", "value": "= upper(NAME)"},
           {"name": "source", "type": "String", "value": "UrbIS 2026"}]
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "b.gpkg"}},
                  {"id": 2, "type": "Attributes", "params": {"columns": [{"from": "area", "keep": False}], "add": add}},
                  {"id": 3, "type": "Writer", "params": {"path": "out.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"]])
    assert g.propagate(), [n.error for n in g.nodes.values()]
    assert g.nodes[2].schemas["features"].names() == ["NAME", "area10", "corners", "label", "source"]
    g.plan_needs()
    assert g.nodes[1].wanted["features"].columns == frozenset({"NAME", "area"})   # area dropped, but read
    r = run(g)
    assert r.ok, r.error
    rows = sorted((a for _, a in read_all(tmp_path / "out.gpkg")), key=lambda a: a["NAME"])
    assert rows[0] == {"NAME": "A", "area10": 100, "corners": 4, "label": "a: 50", "source": "UrbIS 2026"}
    assert rows[1]["area10"] is None and rows[1]["label"] == "b: 1"   # no area: empty, not an error; area() 0.5 rounded up

    g.nodes[2].params["add"] = [{"name": "x", "type": "Real", "value": "= area * * 2"}]
    assert not g.propagate()
    assert "column x: position 10: a value expected, not '*'" in g.nodes[2].error   # the place in the field
