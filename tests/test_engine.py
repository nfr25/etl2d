"""The engine without a window: the notes' examples (docs/notions.en.html)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from osgeo import ogr

from conftest import make_layer, read_all
from etl2d.graph import GraphError
from etl2d.run import run

ROOT = Path(__file__).resolve().parent.parent


def addresses(tmp_path):
    """Five 'addresses' around the Grand-Place, with UrbIS-like columns (§4)."""
    rows = [("POINT (148900 170700)", {"PARENTID": "https://databrussels.be/id/address/1", "STRNAMEFRE": "Grand-Place", "POLICENUM": "1"}),
            ("POINT (148950 170720)", {"PARENTID": "https://databrussels.be/id/address/2", "STRNAMEFRE": "rue au Beurre", "POLICENUM": "12"}),
            ("POINT (149000 170650)", {"PARENTID": "https://databrussels.be/id/address/3", "STRNAMEFRE": "rue Charles Buls", "POLICENUM": ""}),
            ("POINT (150500 170700)", {"PARENTID": "https://databrussels.be/id/address/4", "STRNAMEFRE": "rue Royale", "POLICENUM": "3"}),
            ("POINT (148100 169900)", {"PARENTID": "https://databrussels.be/id/address/5", "STRNAMEFRE": "rue Haute", "POLICENUM": "100"})]
    fields = (("PARENTID", ogr.OFTString), ("STRNAMEFRE", ogr.OFTString), ("POLICENUM", ogr.OFTString))
    return make_layer(tmp_path / "addresses.gpkg", rows, fields=fields)


def test_schema_travels_before_data(tmp_path, workflow):
    """§4: the reader's 3 columns, one dropped, one renamed, one added: known without reading."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Attributes", "params": {
                      "columns": [{"from": "PARENTID", "keep": False}, {"from": "STRNAMEFRE", "to": "street"}],
                      "add": [{"name": "source", "type": "String", "width": 20, "value": "UrbIS 2026"}]}},
                  {"id": 3, "type": "Reprojector", "params": {"target": "EPSG:3812"}},
                  {"id": 4, "type": "Writer", "params": {"path": "out.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"], [3, "features", 4, "features"]])
    assert g.propagate()
    s1 = g.nodes[1].schemas["features"]
    assert s1.names() == ["PARENTID", "STRNAMEFRE", "POLICENUM"] and s1.count == 5 and s1.crs_name() == "EPSG:31370"
    assert g.nodes[2].schemas["features"].names() == ["street", "POLICENUM", "source"]
    assert g.nodes[3].schemas["features"].crs_name() == "EPSG:3812"
    assert not (tmp_path / "out.gpkg").exists()   # nothing run


def test_shapefile_names_said_before_running(tmp_path, workflow):
    """§4: a column of 11 characters into a shapefile: an error in the writer, before any run."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Attributes", "params": {"columns": [{"from": "STRNAMEFRE", "to": "street_name"}]}},
                  {"id": 3, "type": "Writer", "params": {"path": "out.shp"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"]])
    assert not g.propagate()
    assert "10 characters" in g.nodes[3].error and "street_name" in g.nodes[3].error


def test_reproject_grand_place(tmp_path, workflow):
    """§7: (148,900; 170,700) in Lambert 72 -> about (648,899.61; 670,700.27) in Lambert 2008."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Reprojector", "params": {"target": "EPSG:3812"}},
                  {"id": 3, "type": "Writer", "params": {"path": "out.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"]])
    r = run(g)
    assert r.ok, r.error
    wkt = read_all(tmp_path / "out.gpkg")[0][0]
    x, y = map(float, wkt[wkt.index("(") + 1:-1].split())
    # The 500,000 m of design, and a datum shift of some decimetres (PROJ's choice may vary by a few cm).
    assert abs(x - 648899.61) < 0.5 and abs(y - 670700.27) < 0.5


def test_clipper_outputs_and_the_pavement(tmp_path, workflow):
    """§7: a 200 m pavement leaving the box by 30 m: partial, 170 m once cut; a point on the edge: partial."""
    rows = [("LINESTRING (1000 500, 1200 500)", {"NAME": "pavement"}),     # box x 900..1170: 30 m out
            ("LINESTRING (950 600, 1000 600)", {"NAME": "inside"}),
            ("LINESTRING (3000 3000, 3100 3000)", {"NAME": "far"}),
            ("POINT (900 700)", {"NAME": "on the edge"})]
    make_layer(tmp_path / "lines.gpkg", rows, geom_type=ogr.wkbUnknown)
    for cut, length in ((True, 170.0), (False, 200.0)):
        g = workflow([{"id": 1, "type": "Reader", "params": {"source": "lines.gpkg"}},
                      {"id": 2, "type": "Clipper", "params": {"box": [900, 400, 1170, 800], "cut": cut}},
                      {"id": 3, "type": "Writer", "params": {"path": "in.gpkg", "mode": "replace"}},
                      {"id": 4, "type": "Writer", "params": {"path": "partial.gpkg", "mode": "replace"}},
                      {"id": 5, "type": "Writer", "params": {"path": "out.gpkg", "mode": "replace"}}],
                     [[1, "features", 2, "features"], [2, "inside", 3, "features"],
                      [2, "partial", 4, "features"], [2, "outside", 5, "features"]])
        r = run(g)
        assert r.ok, r.error
        assert r.stats[2].features_out == {"inside": 1, "partial": 2, "outside": 1}
        partial = {a["NAME"]: w for w, a in read_all(tmp_path / "partial.gpkg")}
        assert set(partial) == {"pavement", "on the edge"}
        assert ogr.CreateGeometryFromWkt(partial["pavement"]).Length() == pytest.approx(length)
        assert [a["NAME"] for _, a in read_all(tmp_path / "in.gpkg")] == ["inside"]
        assert [a["NAME"] for _, a in read_all(tmp_path / "out.gpkg")] == ["far"]


def test_unjoined_output_keeps_nothing(tmp_path, workflow):
    """§3: an output joined to nothing (outside, here) does not pile its features up.

    A Python caller in between needs everything (§5), so the clip really sees the 2,000.
    """
    rows = [(f"POINT ({x} 0)", {"NAME": str(x)}) for x in range(0, 2000)]
    make_layer(tmp_path / "pts.gpkg", rows)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "pts.gpkg"}},
                  {"id": 2, "type": "PythonCaller"},
                  {"id": 3, "type": "Clipper", "params": {"box": [-1, -1, 9.5, 1]}},
                  {"id": 4, "type": "Writer", "params": {"path": "in.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"], [3, "inside", 4, "features"]])
    r = run(g)
    assert r.ok and r.stats[4].features_in == 10 and r.stats[3].features_in == 2000


def test_directory_reader_union_and_source_file(tmp_path, workflow):
    """§7: two municipalities' deliveries, one with a column more: the union, source_file, the difference said."""
    d = tmp_path / "urbis"
    d.mkdir()
    make_layer(d / "evere.shp", [("POINT (1 1)", {"NAME": "a"})], driver="ESRI Shapefile")
    make_layer(d / "ixelles.shp", [("POINT (2 2)", {"NAME": "b", "EXTRA": 7}), ("POINT (3 3)", {"NAME": "c", "EXTRA": 8})],
               fields=(("NAME", ogr.OFTString), ("EXTRA", ogr.OFTInteger)), driver="ESRI Shapefile")
    (d / "notes.txt").write_text("not a shapefile")
    g = workflow([{"id": 1, "type": "DirectoryReader", "params": {"folder": "urbis", "pattern": "*.shp"}},
                  {"id": 2, "type": "Writer", "params": {"path": "out/sub/all.gpkg"}}],
                 [[1, "features", 2, "features"]])
    assert g.propagate()
    assert g.nodes[1].schemas["features"].names() == ["NAME", "EXTRA", "source_file"]
    assert g.nodes[1].schemas["features"].count == 3
    assert any("evere.shp: no column EXTRA" in n for n in g.nodes[1].notes)
    r = run(g)
    assert r.ok, r.error
    rows = read_all(tmp_path / "out" / "sub" / "all.gpkg")   # the folders made when needed
    assert [(a["NAME"], a["EXTRA"], a["source_file"]) for _, a in rows] == \
           [("a", None, "evere.shp"), ("b", 7, "ixelles.shp"), ("c", 8, "ixelles.shp")]


def test_bbox_asked_to_the_source(tmp_path, workflow):
    """§7: the readers' bbox: only the features near the box are read (the clip then sees 3, not 5)."""
    addresses(tmp_path)
    d = tmp_path / "dir"
    d.mkdir()
    (tmp_path / "addresses.gpkg").rename(d / "addresses.gpkg")
    for reader in ({"type": "Reader", "params": {"source": "dir/addresses.gpkg", "bbox": [148500, 170400, 149300, 171000]}},
                   {"type": "DirectoryReader", "params": {"folder": "dir", "pattern": "*.gpkg",
                                                          "bbox": [148500, 170400, 149300, 171000]}}):
        g = workflow([{"id": 1, **reader},
                      {"id": 2, "type": "Clipper", "params": {"box": [148500, 170400, 149300, 171000]}},
                      {"id": 3, "type": "Writer", "params": {"path": "gp.gpkg", "mode": "replace"}}],
                     [[1, "features", 2, "features"], [2, "inside", 3, "features"]])
        r = run(g)
        assert r.ok, r.error
        assert r.stats[1].features_out["features"] == 3 and r.stats[3].features_in == 3


def test_python_caller(tmp_path, workflow):
    """§7: drops the addresses without a number, adds a column; an error names the feature."""
    addresses(tmp_path)
    code = ("def process(feature):\n"
            "    if not feature.attributes['POLICENUM']:\n"
            "        return None\n"
            "    feature.attributes['name_length'] = len(feature.attributes['STRNAMEFRE'])\n"
            "    return feature\n")
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "PythonCaller", "params": {"code": code,
                                                               "new_columns": [{"name": "name_length", "type": "Integer"}]}},
                  {"id": 3, "type": "Writer", "params": {"path": "out.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"]])
    r = run(g)
    assert r.ok, r.error
    rows = read_all(tmp_path / "out.gpkg")
    assert len(rows) == 4 and rows[0][1]["name_length"] == len("Grand-Place")

    bad = "def process(feature):\n    return int(feature.attributes['POLICENUM']) and feature\n"
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "PythonCaller", "params": {"code": bad}},
                  {"id": 3, "type": "Writer", "params": {"path": "bad.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"]])
    r = run(g)
    assert not r.ok
    assert "#2 PythonCaller" in r.error and "ValueError" in r.error and "rue Charles Buls" in r.error
    assert not (tmp_path / "bad.gpkg").exists()   # rolled back, and the file it had created removed


def test_writer_modes(tmp_path, workflow):
    """§7: create refused when it exists; replace; append. Into a shapefile too (several files)."""
    addresses(tmp_path)
    (tmp_path / "out.yaml").write_text("a style beside: never touched")

    def job(mode, path):
        return workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                         {"id": 2, "type": "Writer", "params": {"path": path, "mode": mode}}],
                        [[1, "features", 2, "features"]])

    for path in ("out.gpkg", "out.shp"):
        assert run(job("create", path)).ok
        g = job("create", path)
        assert not g.propagate() and "exists" in g.nodes[2].error
        assert run(job("append", path)).ok
        assert len(read_all(tmp_path / path)) == 10
        assert run(job("replace", path)).ok
        assert len(read_all(tmp_path / path)) == 5
    assert (tmp_path / "out.yaml").read_text() == "a style beside: never touched"
    assert not list(tmp_path.glob(".etl2d-*"))   # no temporary folder left


def test_fan_out(tmp_path, workflow):
    """§9: one output joined to two writers: each gets every feature."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Writer", "params": {"path": "a.gpkg"}},
                  {"id": 3, "type": "Writer", "params": {"path": "b.fgb"}}],
                 [[1, "features", 2, "features"], [1, "features", 3, "features"]])
    r = run(g)
    assert r.ok, r.error
    assert len(read_all(tmp_path / "a.gpkg")) == 5 and len(read_all(tmp_path / "b.fgb")) == 5


def test_graph_errors(tmp_path, registry, workflow):
    """§10: an unknown node is kept and shown; a loop and a bad link are refused."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "SomeoneElsesNode", "params": {"x": 1}}],
                 [[1, "features", 2, "features"]])
    assert not g.propagate() and "not found" in g.nodes[2].error
    assert g.to_dict()["nodes"][1] == {"id": 2, "type": "SomeoneElsesNode", "pos": [0, 0], "params": {"x": 1}}
    with pytest.raises(GraphError, match="loop"):
        workflow([{"id": 1, "type": "Attributes"}, {"id": 2, "type": "Attributes"}],
                 [[1, "features", 2, "features"], [2, "features", 1, "features"]])
    with pytest.raises(GraphError, match="no output 'inside'"):
        workflow([{"id": 1, "type": "Reader"}, {"id": 2, "type": "Writer"}], [[1, "inside", 2, "features"]])


def test_broken_custom_node(tmp_path):
    """§8: a custom node file with a syntax error is listed; the others load."""
    from etl2d.registry import default_registry
    d = tmp_path / "custom"
    d.mkdir()
    (d / "broken.py").write_text("def oops(:\n")
    (d / "good.py").write_text("from etl2d import node, Input, Output\n"
                               "@node('Upper', 'Text')\n"
                               "class Upper:\n"
                               "    inputs = [Input('features')]\n"
                               "    outputs = [Output('features')]\n")
    reg = default_registry([d])
    assert "Upper" in reg.specs and "Reader" in reg.specs
    assert any(e.startswith("broken.py: SyntaxError") for e in reg.errors)


def test_command_line(tmp_path):
    """§10: python -m etl2d run job.json, exit 0, the counts printed; a broken workflow: exit 2."""
    addresses(tmp_path)
    job = {"etl2d": 1,
           "nodes": [{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                     {"id": 2, "type": "Clipper", "params": {"box": [148500, 170400, 149300, 171000]}},
                     {"id": 3, "type": "Writer", "params": {"path": "grand-place.gpkg"}}],
           "links": [[1, "features", 2, "features"], [2, "inside", 3, "features"]]}
    (tmp_path / "job.json").write_text(json.dumps(job))
    p = subprocess.run([sys.executable, "-m", "etl2d", "run", str(tmp_path / "job.json")],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "3 features read" in p.stdout and "3 written" in p.stdout   # §5: the clip's box asked to the source
    job["nodes"][2]["params"]["path"] = "x.shp"
    job["nodes"].append({"id": 4, "type": "Attributes", "params": {"columns": [{"from": "NOPE"}]}})
    job["links"].append([1, "features", 4, "features"])
    (tmp_path / "bad.json").write_text(json.dumps(job))
    p = subprocess.run([sys.executable, "-m", "etl2d", "run", str(tmp_path / "bad.json")],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 2 and "no column NOPE" in p.stdout


# ---- §5: the needs come up ---------------------------------------------------------------

def test_needs_columns(tmp_path, workflow):
    """Attributes drops PARENTID and renames STRNAMEFRE: the reader reads 2 of its 3 columns."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Attributes", "params": {
                      "columns": [{"from": "PARENTID", "keep": False}, {"from": "STRNAMEFRE", "to": "street"}],
                      "add": [{"name": "source", "value": "UrbIS"}]}},
                  {"id": 3, "type": "Writer", "params": {"path": "out.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"]])
    r = run(g)
    assert r.ok, r.error
    assert g.nodes[1].wanted["features"].columns == {"STRNAMEFRE", "POLICENUM"}
    rows = read_all(tmp_path / "out.gpkg")
    assert rows[1][1] == {"street": "rue au Beurre", "POLICENUM": "12", "source": "UrbIS"}


def test_needs_outside_used_means_everything(tmp_path, workflow):
    """The clip's 'outside' joined: it needs every feature, no box asked."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Clipper", "params": {"box": [148500, 170400, 149300, 171000]}},
                  {"id": 3, "type": "Writer", "params": {"path": "in.gpkg"}},
                  {"id": 4, "type": "Writer", "params": {"path": "out.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "inside", 3, "features"], [2, "outside", 4, "features"]])
    r = run(g)
    assert r.ok and g.nodes[1].wanted["features"].bbox is None
    assert r.stats[3].features_in == 3 and r.stats[4].features_in == 2


def test_needs_box_through_the_reprojector(tmp_path, workflow):
    """A box in Lambert 2008 after the reprojector reaches the reader in Lambert 72, 1 % wider."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Reprojector", "params": {"target": "EPSG:3812"}},
                  {"id": 3, "type": "Clipper", "params": {"box": [648500, 670400, 649300, 671000]}},
                  {"id": 4, "type": "Writer", "params": {"path": "gp.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "features", 3, "features"], [3, "inside", 4, "features"]])
    r = run(g)
    assert r.ok, r.error
    xmin, ymin, xmax, ymax = g.nodes[1].wanted["features"].bbox
    # 800 m x 600 m: 8 m and 6 m of margin; the datum shift, a few decimetres.
    assert abs(xmin - (148500 - 8)) < 1 and abs(ymin - (170400 - 6)) < 1
    assert abs(xmax - (149300 + 8)) < 1 and abs(ymax - (171000 + 6)) < 1
    assert r.stats[1].features_out["features"] == 3 and r.stats[4].features_in == 3


def test_needs_fan_out_union(tmp_path, workflow):
    """One reader, two clips with different boxes: the reader is asked the box around both."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Clipper", "params": {"box": [148800, 170600, 149100, 170800]}},
                  {"id": 3, "type": "Clipper", "params": {"box": [150000, 170500, 151000, 171000]}},
                  {"id": 4, "type": "Writer", "params": {"path": "a.gpkg"}},
                  {"id": 5, "type": "Writer", "params": {"path": "b.gpkg"}}],
                 [[1, "features", 2, "features"], [1, "features", 3, "features"],
                  [2, "inside", 4, "features"], [3, "inside", 5, "features"]])
    r = run(g)
    assert r.ok, r.error
    assert g.nodes[1].wanted["features"].bbox == (148800, 170500, 151000, 171000)
    assert r.stats[4].features_in == 3 and r.stats[5].features_in == 1


def test_needs_python_caller(tmp_path, workflow):
    """A Python caller without 'reads' needs everything; with them, only those (and what follows uses)."""
    addresses(tmp_path)
    code = "def process(feature):\n    feature.attributes['n'] = len(feature.attributes['STRNAMEFRE'])\n    return feature\n"
    for reads, expected in (([], None), (["STRNAMEFRE"], {"STRNAMEFRE"})):
        g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                      {"id": 2, "type": "PythonCaller", "params": {"code": code, "reads": reads,
                                                                   "new_columns": [{"name": "n", "type": "Integer"}]}},
                      {"id": 3, "type": "Attributes", "params": {"columns": [{"from": "PARENTID", "keep": False},
                                                                             {"from": "STRNAMEFRE", "keep": False},
                                                                             {"from": "POLICENUM", "keep": False}]}},
                      {"id": 4, "type": "Writer", "params": {"path": "n.gpkg", "mode": "replace"}}],
                     [[1, "features", 2, "features"], [2, "features", 3, "features"], [3, "features", 4, "features"]])
        r = run(g)
        assert r.ok, r.error
        assert g.nodes[1].wanted["features"].columns == expected
        assert read_all(tmp_path / "n.gpkg")[0][1] == {"n": len("Grand-Place")}


def test_stop_leaves_nothing(tmp_path, workflow):
    """§11: Stop (Esc in the window) in the middle of a run: the file is not made, no temporary layer left."""
    addresses(tmp_path)

    class StopSoon:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 2

    for path in ("out.gpkg", "out.shp"):
        g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                      {"id": 2, "type": "Writer", "params": {"path": path}}],
                     [[1, "features", 2, "features"]])
        r = run(g, stop=StopSoon())
        assert not r.ok and "stopped" in r.error
        assert not (tmp_path / path).exists(), path
        assert not list(tmp_path.glob(".etl2d-*"))


def test_keep_outputs(tmp_path, registry, workflow):
    """§9: a kept output is written to job.etl2d/; keeping 'outside' makes the reader read everything."""
    addresses(tmp_path)
    nodes = [{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
             {"id": 2, "type": "Clipper", "params": {"box": [148500, 170400, 149300, 171000]}},
             {"id": 3, "type": "Writer", "params": {"path": "inside.gpkg"}}]
    links = [[1, "features", 2, "features"], [2, "inside", 3, "features"]]
    g = workflow(nodes, links)
    g.propagate(); g.plan_needs()
    assert g.nodes[1].wanted["features"].bbox is not None     # the clip's box asked to the source

    g.keep = {(2, "inside"), (2, "outside")}                   # outside joined to nothing, kept
    g.work_dir = tmp_path / "job.etl2d"
    g.propagate(); g.plan_needs()
    assert g.nodes[1].wanted["features"].bbox is None          # outside looked at: everything read
    r = run(g)
    assert r.ok, r.error
    assert r.kept == {(2, "inside"): 3, (2, "outside"): 2}
    assert len(read_all(tmp_path / "job.etl2d" / "n2-outside.fgb")) == 2
    assert len(read_all(tmp_path / "job.etl2d" / "n2-inside.fgb")) == 3
    assert len(read_all(tmp_path / "inside.gpkg")) == 3      # the writer as before
    assert not list((tmp_path / "job.etl2d").glob("*.tmp.fgb"))

    # In the file, and back.
    from etl2d.graph import Graph
    d = g.to_dict()
    assert d["keep"] == [[2, "inside"], [2, "outside"]]
    assert Graph.from_dict(d, registry, tmp_path).keep == g.keep
    d["keep"] = [[2, "nowhere"]]
    with pytest.raises(GraphError):
        Graph.from_dict(d, registry, tmp_path)

    # Stopped: the previous kept file stays as it was.
    class StopSoon:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 2

    (tmp_path / "inside.gpkg").unlink()
    r = run(g, stop=StopSoon())
    assert not r.ok and r.kept == {}
    assert len(read_all(tmp_path / "job.etl2d" / "n2-outside.fgb")) == 2
    assert not list((tmp_path / "job.etl2d").glob("*.tmp.fgb"))


def test_copy_paste(tmp_path, registry, workflow):
    """§11: nodes copied with their parameters and the links between them, pasted under new numbers."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}, "pos": [40, 60]},
                  {"id": 2, "type": "Clipper", "params": {"box": [148500, 170400, 149300, 171000]}, "pos": [330, 60]},
                  {"id": 3, "type": "Writer", "params": {"path": "a.gpkg"}, "pos": [620, 60]}],
                 [[1, "features", 2, "features"], [2, "inside", 3, "features"]])
    g.keep = {(2, "outside")}
    clip = json.loads(json.dumps(g.extract([2, 3])))   # through the clipboard's text
    assert [n["pos"] for n in clip["nodes"]] == [[0, 0], [290, 0]]
    assert clip["links"] == [[2, "inside", 3, "features"]]   # the link from #1 stays behind
    new = g.insert(clip, registry, at=(330, 300))
    assert new == [4, 5]
    assert g.nodes[4].pos == (330, 300) and g.nodes[5].pos == (620, 300)
    assert g.nodes[4].params["box"] == [148500, 170400, 149300, 171000]
    g.nodes[4].params["box"][0] = 0                      # a copy: the original keeps its box
    assert g.nodes[2].params["box"][0] == 148500
    assert any(l.src == 4 and l.dst == 5 for l in g.links) and (4, "outside") in g.keep
    assert not g.propagate() and "not joined" in g.nodes[4].error   # its input: to join
    with pytest.raises(GraphError):
        g.insert({"text": "not nodes"}, registry)


def test_kept_file_view(tmp_path):
    """§9: the Map's system and first view of a kept file; a file in degrees is shown in EPSG:3857."""
    from etl2d.keep import file_view
    p = make_layer(tmp_path / "a.gpkg", [("POINT (148900 170700)", {"NAME": "a"}),
                                         ("POINT (149000 170650)", {"NAME": "b"})])
    crs, ext = file_view(p)
    assert crs == "EPSG:31370" and ext == (148900, 170650, 149000, 170700)
    p = make_layer(tmp_path / "b.gpkg", [("POINT (4.3525 50.8467)", {"NAME": "Grand-Place"})], epsg=4326)
    crs, (x0, y0, x1, y1) = file_view(p)
    assert crs == "EPSG:3857" and abs(x0 - 484_520) < 100 and abs(y0 - 6_594_100) < 300


def test_three_layers_into_one_geopackage(tmp_path, workflow):
    """§7: three readers, three writers into the same GeoPackage, one layer each: they take turns."""
    nodes, links = [], []
    for i, name in enumerate(("parcels", "buildings", "addresses")):
        make_layer(tmp_path / f"{name}.gpkg", [(f"POINT ({148900 + k} 170700)", {"NAME": f"{name} {k}"})
                                               for k in range(50 + i)])
        nodes += [{"id": 10 + i, "type": "Reader", "params": {"source": f"{name}.gpkg"}},
                  {"id": 20 + i, "type": "Writer", "params": {"path": "out/bxl.gpkg", "layer": name, "mode": "replace"}}]
        links.append([10 + i, "features", 20 + i, "features"])
    r = run(workflow(nodes, links))
    assert r.ok, r.error
    ds = ogr.Open(str(tmp_path / "out" / "bxl.gpkg"))
    assert sorted((ds.GetLayer(i).GetName(), ds.GetLayer(i).GetFeatureCount()) for i in range(ds.GetLayerCount())) == \
        [("addresses", 52), ("buildings", 51), ("parcels", 50)]
    # Each with its spatial index, made at the end (not GDAL's during the transaction: it fails in
    # recent GDAL after the rename), filled, and no leftover of the temporary layers.
    import sqlite3
    con = sqlite3.connect(tmp_path / "out" / "bxl.gpkg")
    tables = {t for (t,) in con.execute("select name from sqlite_master where type='table'")}
    for name, n in (("addresses", 52), ("buildings", 51), ("parcels", 50)):
        assert con.execute(f'select count(*) from "rtree_{name}_geom"').fetchone()[0] == n
    assert not [t for t in tables if "etl2d_tmp" in t]
    con.close()


def buildings(tmp_path):
    """Seven buildings around the Grand-Place, with an area and a type; one area empty."""
    rows = [(30, "maison"), (120, "maison"), (49.9, "garage"), (200, "immeuble"), (650, "immeuble"), (None, "annexe"),
            (50, "Maison de maître")]
    fields = (("area", ogr.OFTReal), ("type", ogr.OFTString), ("NAME", ogr.OFTString))
    return make_layer(tmp_path / "buildings.gpkg",
                      [(f"POINT ({148900 + i} 170700)", {"area": a, "type": t, "NAME": f"b{i}"} if a is not None
                        else {"type": t, "NAME": f"b{i}"}) for i, (a, t) in enumerate(rows)],
                      fields=fields)


def test_filter_first_rule_wins(tmp_path, workflow):
    """§7: small (< 50), medium (< 200), else; 30 m² is small although < 200 too; an empty area: else."""
    buildings(tmp_path)
    rules = [{"name": "small", "column": "area", "op": "<", "value": "50"},
             {"name": "medium", "column": "area", "op": "<", "value": "200"}]
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
                  {"id": 2, "type": "Filter", "params": {"rules": rules}},
                  {"id": 3, "type": "Writer", "params": {"path": "small.gpkg"}},
                  {"id": 4, "type": "Writer", "params": {"path": "medium.gpkg"}},
                  {"id": 5, "type": "Writer", "params": {"path": "else.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "small", 3, "features"], [2, "medium", 4, "features"],
                  [2, "else", 5, "features"]])
    assert g.nodes[2].outputs() == ["small", "medium", "else"]
    g.propagate(); g.plan_needs()
    assert g.nodes[1].wanted["features"].columns is None   # the writers want everything
    r = run(g)
    assert r.ok, r.error
    assert r.stats[2].features_out == {"small": 2, "medium": 2, "else": 3}
    assert sorted(a["area"] for _, a in read_all(tmp_path / "small.gpkg")) == [30, 49.9]
    assert sorted(a["area"] for _, a in read_all(tmp_path / "medium.gpkg")) == [50, 120]   # 50: not < 50
    assert sorted((a["area"] is None, a["type"]) for _, a in read_all(tmp_path / "else.gpkg")) == \
        [(False, "immeuble"), (False, "immeuble"), (True, "annexe")]


def test_filter_comparisons(tmp_path, registry, workflow):
    """§7: in, starts with (capitals ignored), is empty; the errors said before running."""
    buildings(tmp_path)

    def counts(rule):
        g = workflow([{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
                      {"id": 2, "type": "Filter", "params": {"rules": [dict(name="yes", **rule)]}},
                      {"id": 3, "type": "Writer", "params": {"path": f"out{len(list(tmp_path.glob('out*')))}.gpkg"}}],
                     [[1, "features", 2, "features"], [2, "yes", 3, "features"]])
        r = run(g)
        assert r.ok, r.error
        return r.stats[2].features_out["yes"]

    assert counts({"column": "type", "op": "in", "value": "garage, annexe"}) == 2
    assert counts({"column": "type", "op": "starts with", "value": "MAISON"}) == 3
    assert counts({"column": "area", "op": "is empty", "value": ""}) == 1
    assert counts({"column": "area", "op": ">=", "value": "200"}) == 2
    assert counts({"column": "area", "op": "!=", "value": "30"}) == 5    # the empty one: no comparison true

    def error(rules):
        g = workflow([{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
                      {"id": 2, "type": "Filter", "params": {"rules": rules}}],
                     [[1, "features", 2, "features"]])
        g.propagate()
        return g.nodes[2].error

    assert "no column 'surface'" in error([{"name": "a", "column": "surface", "op": "<", "value": "1"}])
    assert "a number is expected" in error([{"name": "a", "column": "area", "op": "<", "value": "abc"}])
    assert "two outputs named 'else'" in error([{"name": "else", "column": "area", "op": "<", "value": "1"}])


def test_filter_ports_follow_the_rules(tmp_path, workflow):
    """§7: a rule renamed: its links follow; a rule removed: its links go, and it is said."""
    buildings(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
                  {"id": 2, "type": "Filter", "params": {"rules": [
                      {"name": "small", "column": "area", "op": "<", "value": "50"}]}},
                  {"id": 3, "type": "Writer", "params": {"path": "s.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "small", 3, "features"]])
    g.keep = {(2, "small")}
    g.nodes[2].params["rules"][0]["name"] = "tiny"
    g.rename_output(2, "small", "tiny")
    assert [l.src_port for l in g.outputs_of(2)] == ["tiny"] and g.keep == {(2, "tiny")}
    assert g.propagate()
    g.nodes[2].params["rules"] = []
    gone = g.prune_ports()
    assert len(gone) == 2 and not g.outputs_of(2) and not g.keep
    assert g.nodes[2].outputs() == ["else"]


def test_filter_needs(tmp_path, workflow):
    """§5, §7: after the Filter only NAME is used; its rule reads area: the reader asks NAME and area."""
    buildings(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
                  {"id": 2, "type": "Filter", "params": {"rules": [
                      {"name": "small", "column": "area", "op": "<", "value": "50"}]}},
                  {"id": 3, "type": "Attributes", "params": {"columns": [{"from": "area", "keep": False},
                                                                         {"from": "type", "keep": False}]}},
                  {"id": 4, "type": "Writer", "params": {"path": "names.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "small", 3, "features"], [3, "features", 4, "features"]])
    g.propagate(); g.plan_needs()
    assert g.nodes[1].wanted["features"].columns == frozenset({"NAME", "area"})
    r = run(g)
    assert r.ok, r.error
    assert sorted(a["NAME"] for _, a in read_all(tmp_path / "names.gpkg")) == ["b0", "b2"]


def test_two_writers_one_layer_refused(tmp_path, workflow):
    """§7: two Writers on the same layer of the same file (the layer left empty: the file's name): said before running."""
    addresses(tmp_path)
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "addresses.gpkg"}},
                  {"id": 2, "type": "Writer", "params": {"path": "out.gpkg"}},
                  {"id": 3, "type": "Writer", "params": {"path": "out.gpkg", "layer": "OUT"}}],
                 [[1, "features", 2, "features"], [1, "features", 3, "features"]])
    assert not g.propagate()
    assert "#2 Writer writes layer 'out' of out.gpkg too" in g.nodes[3].error
    g.nodes[3].params["layer"] = "addresses"
    assert g.propagate()


def test_mapwriter(tmp_path, workflow):
    """§7: the printer: a PNG and a PDF page drawn by render2d; said before running when render2d is missing."""
    import os
    import shutil as sh
    buildings(tmp_path)
    nodes = [{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
             {"id": 2, "type": "MapWriter", "params": {"path": "out/b.png", "render2d": str(tmp_path / "nowhere")}}]
    g = workflow(nodes, [[1, "features", 2, "features"]])
    assert not g.propagate() and "render2d not found" in g.nodes[2].error
    exe = os.environ.get("ETL2D_TEST_RENDER2D") or sh.which("render2d")
    if not exe:
        pytest.skip("render2d not found (ETL2D_TEST_RENDER2D or the PATH): the drawing itself not tried")
    nodes[1]["params"]["render2d"] = exe
    nodes.append({"id": 3, "type": "MapWriter", "params": {"path": "out/b.pdf", "page": "A4-landscape", "scale": 500,
                                                            "title": "Seven buildings", "render2d": exe}})
    g = workflow(nodes, [[1, "features", 2, "features"], [1, "features", 3, "features"]])
    r = run(g)
    assert r.ok, r.error
    assert (tmp_path / "out" / "b.png").read_bytes()[:4] == b"\x89PNG"
    assert (tmp_path / "out" / "b.pdf").read_bytes()[:4] == b"%PDF"
    nodes[2]["params"].update(path="out/b.png", page="none")   # two maps into one file: refused
    g = workflow(nodes, [[1, "features", 2, "features"], [1, "features", 3, "features"]])
    assert not g.propagate() and "too" in g.nodes[3].error


def test_filter_expression(tmp_path, workflow):
    """§7: a rule that is an expression: several columns, the geometry; its columns asked to the reader."""
    buildings(tmp_path)
    rules = [{"name": "big_house", "op": "expression", "value": "area >= 100 AND starts(type, 'maison')"},
             {"name": "point", "op": "expression", "value": "= geomtype() = 'Point' AND area IS NULL"}]
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
                  {"id": 2, "type": "Filter", "params": {"rules": rules}},
                  {"id": 3, "type": "Attributes", "params": {"columns": [{"from": "area", "keep": False},
                                                                         {"from": "type", "keep": False}]}},
                  {"id": 4, "type": "Writer", "params": {"path": "big.gpkg"}}],
                 [[1, "features", 2, "features"], [2, "big_house", 3, "features"], [3, "features", 4, "features"]])
    assert g.propagate(), [n.error for n in g.nodes.values()]
    g.plan_needs()
    assert g.nodes[1].wanted["features"].columns == frozenset({"NAME", "area", "type"})
    r = run(g)
    assert r.ok, r.error
    assert r.stats[2].features_out == {"big_house": 1}     # 120 m² maison; "Maison de maître" has 50 m²
    assert [a["NAME"] for _, a in read_all(tmp_path / "big.gpkg")] == ["b1"]
    g.nodes[2].params["rules"] = [{"name": "x", "op": "expression", "value": "area >"}]
    assert not g.propagate() and "rule 'x': position 7: a value expected" in g.nodes[2].error


def test_mapwriter_layers_ports(tmp_path, workflow):
    """§7, decision 10: one input per layer; renamed, its link follows; removed, its link goes;
    a workflow of 0.5 (no layers, its style and name) read as one layer 'features'."""
    buildings(tmp_path)
    fake = tmp_path / "render2d"
    fake.write_text("")   # found (a file): enough to see the structure; nothing is drawn here
    rules = [{"name": "small", "column": "area", "op": "<", "value": "50"}]
    g = workflow([{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
                  {"id": 2, "type": "Filter", "params": {"rules": rules}},
                  {"id": 3, "type": "MapWriter", "params": {"path": "m.png", "render2d": str(fake), "layers": [
                      {"name": "large"}, {"name": "small", "colour": "categorize", "column": "type"}]}}],
                 [[1, "features", 2, "features"], [2, "else", 3, "large"], [2, "small", 3, "small"]])
    assert g.nodes[3].inputs() == ["large", "small"]
    assert g.propagate(), [n.error for n in g.nodes.values()]
    g.nodes[3].params["layers"][1]["column"] = "nowhere"
    assert not g.propagate() and "no column 'nowhere' arrives to colour by" in g.nodes[3].error
    g.nodes[3].params["layers"][1].update(name="tiny", column="type")
    g.rename_input(3, "small", "tiny")
    assert g.propagate() and any(l.dst_port == "tiny" for l in g.inputs_of(3))
    g.nodes[3].params["layers"] = [{"name": "large"}]
    assert len(g.prune_ports()) == 1 and [l.dst_port for l in g.inputs_of(3)] == ["large"]

    old = workflow([{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
                    {"id": 2, "type": "MapWriter", "params": {"path": "m.png", "render2d": str(fake),
                                                              "style": "", "name": "my buildings"}}],
                   [[1, "features", 2, "features"]])
    assert old.nodes[2].inputs() == ["features"] and old.propagate()


def test_disabled_nodes(tmp_path, workflow):
    """§11: a node disabled: it and the nodes it alone feeds are left out, without an error; their files
    untouched; the nodes before no longer asked for what only it needed. Titles and groups in the file."""
    buildings(tmp_path)
    nodes = [{"id": 1, "type": "Reader", "params": {"source": "buildings.gpkg"}},
             {"id": 2, "type": "Filter", "params": {"rules": [{"name": "small", "column": "area", "op": "<", "value": "50"}]},
              "disabled": True, "title": "Small ones"},
             {"id": 3, "type": "Writer", "params": {"path": "small.gpkg"}},
             {"id": 4, "type": "Attributes", "params": {"columns": [{"from": "type", "keep": False},
                                                                    {"from": "area", "keep": False}]}},
             {"id": 5, "type": "Writer", "params": {"path": "names.gpkg"}}]
    links = [[1, "features", 2, "features"], [2, "small", 3, "features"], [1, "features", 4, "features"],
             [4, "features", 5, "features"]]
    g = workflow(nodes, links)
    g.groups = [{"title": "Cleaning", "pos": [0, 0], "size": [400, 300], "colour": [0.3, 0.5, 0.3]}]
    assert g.propagate()
    assert g.nodes[2].off == "disabled" and "fed by #2 Small ones, off" in g.nodes[3].off
    g.plan_needs()
    assert g.nodes[1].wanted["features"].columns == frozenset({"NAME"})   # the Filter's area: not asked
    r = run(g)
    assert r.ok, r.error
    assert not (tmp_path / "small.gpkg").exists() and (tmp_path / "names.gpkg").exists()
    d = g.to_dict()
    assert d["nodes"][1]["disabled"] is True and d["nodes"][1]["title"] == "Small ones"
    assert d["groups"][0]["title"] == "Cleaning"
    from etl2d.graph import Graph
    from etl2d.registry import default_registry
    back = Graph.from_dict(d, default_registry(), tmp_path)
    assert back.nodes[2].disabled and back.nodes[2].title == "Small ones" and back.groups == g.groups
