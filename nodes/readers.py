"""Readers: a file or a database; every file of a folder (notes §7)."""

from __future__ import annotations

from pathlib import Path

from osgeo import gdal, ogr

from etl2d import Feature, Field, Input, Output, Param, Schema, SchemaError, node
from etl2d.needs import Needs, intersect
from etl2d.schema import schema_of_layer

gdal.UseExceptions()
ogr.UseExceptions()


def open_layer(source: str, layer: str = ""):
    """(dataset, layer). `source` is a path or a GDAL connection string (PG:...)."""
    try:
        ds = gdal.OpenEx(source, gdal.OF_VECTOR)
    except RuntimeError as e:
        raise SchemaError(f"cannot open {source}: {e}")
    if ds is None:
        raise SchemaError(f"cannot open {source}")
    lyr = ds.GetLayerByName(layer) if layer else ds.GetLayer(0)
    if lyr is None:
        names = [ds.GetLayer(i).GetName() for i in range(ds.GetLayerCount())]
        raise SchemaError(f"no layer '{layer}' in {source} (it has: {', '.join(names)})" if layer
                          else f"{source} has no vector layer")
    return ds, lyr


def features_of(lyr, source: str, bbox=None, extra: dict | None = None, columns=None):
    """The features of a layer, one by one, as etl2d Features.

    bbox: only those near it (the source's spatial index). columns: only these
    are read (§5: GDAL is told to ignore the others), None: all.
    """
    defn = lyr.GetLayerDefn()
    names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    if columns is not None:
        ignored = [n for n in names if n not in columns]
        lyr.SetIgnoredFields(ignored + ["OGR_STYLE"])
        wanted = [(i, n) for i, n in enumerate(names) if n in columns]
    else:
        wanted = list(enumerate(names))
    if bbox:
        lyr.SetSpatialFilterRect(*bbox)
    lyr.ResetReading()
    for f in lyr:
        attrs = {name: f.GetField(i) for i, name in wanted}
        if extra:
            attrs.update(extra)
        g = f.GetGeometryRef()
        yield Feature(attrs, g.Clone() if g is not None else None, f.GetFID(), source)


def asked(params_bbox, wanted: Needs | None):
    """§5: the box given in the reader's own parameter and the one the nodes after it need: both."""
    w = wanted or Needs.everything()
    box = intersect(tuple(params_bbox) if params_bbox else None, w.bbox)
    return box, w.columns


def source_label(source: str) -> str:
    """A file's name, or a connection without its password."""
    if source.upper().startswith("PG:"):
        return " ".join(p for p in source.split() if not p.lower().startswith("password="))
    return Path(source).name


@node("Reader", "Readers")
class Reader:
    """A file (shapefile, GeoPackage, FlatGeobuf, GeoJSON...) or a database (PG:host=... dbname=...)."""

    inputs = []
    outputs = [Output("features")]
    params = [Param("source", "path", "", help="a file, or a connection: PG:host=... dbname=... (no password here)"),
              Param("layer", "str", "", help="when the source holds several; empty: the first"),
              Param("bbox", "bbox", None, help="only the features near this box, asked to the source's index")]

    def _source(self) -> str:
        s = str(self.params["source"] or "")
        if not s:
            raise SchemaError("no source chosen")
        return s if s.upper().startswith("PG:") else str(self.ctx.path(s))

    def schema(self, schemas):
        ds, lyr = open_layer(self._source(), self.params["layer"])
        s = schema_of_layer(lyr, count=not self.params["bbox"])
        return {"features": s}

    def run(self, inputs):
        source = self._source()

        box, columns = asked(self.params["bbox"], self.wanted.get("features"))

        def gen():
            ds, lyr = open_layer(source, self.params["layer"])   # ds kept alive by this frame
            yield from features_of(lyr, source_label(source), box, columns=columns)

        return {"features": gen()}


@node("DirectoryReader", "Readers")
class DirectoryReader:
    """Every file of a folder matching a pattern, read in turn into one flow, with a column source_file."""

    inputs = []
    outputs = [Output("features")]
    params = [Param("folder", "path", ""), Param("pattern", "str", "*.shp"),
              Param("recursive", "bool", False, help="the sub-folders too"),
              Param("layer", "str", "", help="when each file holds several; empty: the first"),
              Param("bbox", "bbox", None, help="only the features near this box, asked to each file's index")]

    def _files(self) -> list[Path]:
        folder = self.ctx.path(self.params["folder"] or ".")
        if not folder.is_dir():
            raise SchemaError(f"no folder {folder}")
        pattern = self.params["pattern"] or "*"
        files = sorted(folder.rglob(pattern) if self.params["recursive"] else folder.glob(pattern))
        files = [f for f in files if f.is_file()]
        if not files:
            raise SchemaError(f"no file '{pattern}' in {folder}")
        return files

    def schema(self, schemas):
        """The union of the files' columns, in the order first met; the differences said, file by file."""
        files = self._files()
        fields: list[Field] = []
        geometry, crs, count, first, first_geometry = None, None, 0, None, None
        per_file = {}
        for f in files:
            ds, lyr = open_layer(str(f), self.params["layer"])
            s = schema_of_layer(lyr, count=not self.params["bbox"])
            per_file[f.name] = s
            for fd in s.fields:
                known = next((x for x in fields if x.name == fd.name), None)
                if known is None:
                    fields.append(fd)
                elif known.type != fd.type:
                    self.ctx.log(f"{f.name}: column {fd.name} is {fd.type}, {known.type} elsewhere")
            if first is None:
                first, geometry, crs, first_geometry = f.name, s.geometry, s.crs, s.geometry
            else:
                if s.geometry != first_geometry:   # compared to the first file, said once per file
                    self.ctx.log(f"{f.name}: geometry {s.geometry}, {first_geometry} in {first}")
                    geometry = "Unknown"
                if s.crs != crs:
                    self.ctx.log(f"{f.name}: another coordinate system ({s.crs_name()}) than {first}")
            count = None if count is None or s.count is None else count + s.count
        names = [fd.name for fd in fields]
        for name, s in per_file.items():
            missing = [n for n in names if n not in s.names()]
            if missing:
                self.ctx.log(f"{name}: no column {', '.join(missing)} (left empty)")
        if "source_file" not in names:
            fields.append(Field("source_file", "String", 254))
        self.ctx.log(f"{len(files)} file" + ("s" if len(files) > 1 else ""))
        return {"features": Schema(tuple(fields), geometry or "Unknown", crs or "", count)}

    def run(self, inputs):
        files = self._files()
        box, columns = asked(self.params["bbox"], self.wanted.get("features"))

        def gen():
            for f in files:
                ds, lyr = open_layer(str(f), self.params["layer"])
                yield from features_of(lyr, f.name, box, extra={"source_file": f.name}, columns=columns)
                del lyr, ds

        return {"features": gen()}
