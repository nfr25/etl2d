"""Small layers made by the tests themselves: no network, no data to ship (render2d's rule, §73)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()


def make_layer(path: Path, rows, geom_type=ogr.wkbPoint, fields=(("NAME", ogr.OFTString),),
               epsg=31370, driver="GPKG", layer=None):
    """rows: [(wkt, {field: value})]."""
    drv = ogr.GetDriverByName(driver)
    ds = drv.CreateDataSource(str(path))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    lyr = ds.CreateLayer(layer or path.stem, srs, geom_type)
    for name, t in fields:
        lyr.CreateField(ogr.FieldDefn(name, t))
    for wkt, values in rows:
        f = ogr.Feature(lyr.GetLayerDefn())
        for k, v in values.items():
            f.SetField(k, v)
        f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        lyr.CreateFeature(f)
    ds = None
    return path


def read_all(path, layer=None):
    """[(wkt, attributes)] of a written file."""
    ds = gdal.OpenEx(str(path), gdal.OF_VECTOR)
    lyr = ds.GetLayerByName(layer) if layer else ds.GetLayer(0)
    defn = lyr.GetLayerDefn()
    names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    out = []
    for f in lyr:
        g = f.GetGeometryRef()
        out.append((g.ExportToWkt() if g else None, {n: f.GetField(n) for n in names}))
    return out


@pytest.fixture
def registry():
    from etl2d.registry import default_registry
    return default_registry()


@pytest.fixture
def workflow(tmp_path, registry):
    """workflow(nodes, links) -> a Graph whose relative paths start at tmp_path."""
    from etl2d.graph import Graph

    def make(nodes, links):
        data = {"etl2d": 1, "nodes": nodes, "links": links}
        (tmp_path / "job.json").write_text(json.dumps(data))
        return Graph.from_dict(data, registry, tmp_path)

    return make
