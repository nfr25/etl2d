"""Kept outputs: what passes is also written to a FlatGeobuf, to look at (notes §9)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator

from osgeo import gdal, ogr, osr

from etl2d.feature import Feature
from etl2d.schema import FIELD_TYPES, Schema

gdal.UseExceptions()
ogr.UseExceptions()

GEOMETRIES = {"Point": ogr.wkbPoint, "LineString": ogr.wkbLineString, "Polygon": ogr.wkbPolygon,
              "MultiPoint": ogr.wkbMultiPoint, "MultiLineString": ogr.wkbMultiLineString,
              "MultiPolygon": ogr.wkbMultiPolygon}


def keeper(features: Iterable[Feature], schema: Schema, path: Path, counter: list[int]) -> Iterator[None]:
    """A sink: each feature written to path, one yield per feature (pulled with the writers, §9).

    Written to n3-outside.tmp.fgb, renamed n3-outside.fgb at the end: a run
    that fails or is stopped leaves the previous file as it was.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.fgb")
    tmp.unlink(missing_ok=True)
    srs = None
    if schema.crs:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(schema.crs)
    ds = gdal.GetDriverByName("FlatGeobuf").Create(str(tmp), 0, 0, 0, gdal.GDT_Unknown)
    lyr = ds.CreateLayer(path.stem, srs, GEOMETRIES.get(schema.geometry, ogr.wkbUnknown),
                         ["SPATIAL_INDEX=YES"])   # the index: render2d asks a box of it (§9)
    for f in schema.fields:
        fd = ogr.FieldDefn(f.name, FIELD_TYPES.get(f.type, ogr.OFTString))
        if f.width:
            fd.SetWidth(f.width)
        lyr.CreateField(fd)
    defn = lyr.GetLayerDefn()
    index = {defn.GetFieldDefn(i).GetName(): i for i in range(defn.GetFieldCount())}
    done = False
    try:
        for feat in features:
            out = ogr.Feature(defn)
            for name, value in feat.attributes.items():
                i = index.get(name)
                if i is None:
                    continue
                if value is None:
                    out.SetFieldNull(i)
                else:
                    out.SetField(i, value)
            if feat.geometry is not None:
                out.SetGeometry(feat.geometry)
            lyr.CreateFeature(out)
            counter[0] += 1
            yield
        done = True
    finally:
        lyr = None
        ds = None   # closed: the index written, everything on the disk
        if done:
            os.replace(tmp, path)
        else:
            tmp.unlink(missing_ok=True)


def file_view(path: Path):
    """(map CRS 'EPSG:n', extent (x0, y0, x1, y1) in it) of a kept file.

    A file in degrees is shown in EPSG:3857 (render2d does the same: its notes
    §70); its extent is converted.
    """
    ds = gdal.OpenEx(str(path), gdal.OF_VECTOR)
    lyr = ds.GetLayer(0)
    x0, x1, y0, y1 = lyr.GetExtent()
    srs = lyr.GetSpatialRef()
    if srs is None:
        return None, (x0, y0, x1, y1)
    srs = srs.Clone()
    srs.AutoIdentifyEPSG()
    if srs.IsProjected():
        code = srs.GetAuthorityCode(None)
        return (f"EPSG:{code}" if code else None), (x0, y0, x1, y1)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    web = osr.SpatialReference()
    web.ImportFromEPSG(3857)
    web.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    t = osr.CoordinateTransformation(srs, web)
    xs, ys = [], []
    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        X, Y, _ = t.TransformPoint(x, y)
        xs.append(X)
        ys.append(Y)
    return "EPSG:3857", (min(xs), min(ys), max(xs), max(ys))
