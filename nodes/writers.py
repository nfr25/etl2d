"""The writer: any GDAL format that writes, a file or a database (notes §7)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from osgeo import gdal, ogr, osr

from etl2d import Input, Param, SchemaError, node
from etl2d.schema import FIELD_TYPES

gdal.UseExceptions()
ogr.UseExceptions()

# A file's extension -> its driver, when the format is not given.
DRIVERS = {".gpkg": "GPKG", ".shp": "ESRI Shapefile", ".fgb": "FlatGeobuf", ".geojson": "GeoJSON",
           ".json": "GeoJSON", ".csv": "CSV", ".gml": "GML", ".kml": "KML", ".sqlite": "SQLite"}
# A shapefile is several files; only these are touched (never "parcels.yaml" beside "parcels.shp").
SHAPEFILE_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")
GEOMETRIES = {"Point": ogr.wkbPoint, "LineString": ogr.wkbLineString, "Polygon": ogr.wkbPolygon,
              "MultiPoint": ogr.wkbMultiPoint, "MultiLineString": ogr.wkbMultiLineString,
              "MultiPolygon": ogr.wkbMultiPolygon}


@node("Writer", "Writers")
class Writer:
    """Writes the features: create (refused if it exists), replace, or append.

    Into a GeoPackage or PostGIS, in one transaction; into a file format without
    transactions (shapefile, FlatGeobuf, GeoJSON), in a temporary folder moved
    in place at the end. Either way a run that fails half way leaves what was there.
    """

    inputs = [Input("features")]
    outputs = []
    params = [Param("path", "path", "", help="a file, or a connection PG:host=... dbname=..."),
              Param("format", "str", "", help="a GDAL driver name; empty: from the extension"),
              Param("layer", "str", "", help="the layer or table; empty: the file's name"),
              Param("mode", "choice", "create", choices=("create", "replace", "append"))]

    # ---- where, what --------------------------------------------------------------------
    def _target(self):
        p = str(self.params["path"] or "")
        if not p:
            raise SchemaError("no path chosen")
        is_db = p.upper().startswith("PG:")
        path = p if is_db else str(self.ctx.path(p))
        fmt = self.params["format"] or ("PostgreSQL" if is_db else DRIVERS.get(Path(path).suffix.lower(), ""))
        if not fmt:
            raise SchemaError(f"which format for '{Path(path).suffix}'? give it in 'format'")
        driver = gdal.GetDriverByName(fmt)
        if driver is None:
            raise SchemaError(f"no GDAL driver '{fmt}'")
        md = driver.GetMetadata() or {}
        if md.get(gdal.DCAP_VECTOR) != "YES" or (md.get(gdal.DCAP_CREATE) != "YES" and not is_db):
            raise SchemaError(f"GDAL cannot write {fmt}")
        layer = self.params["layer"] or (Path(path).stem if not is_db else "")
        if not layer:
            raise SchemaError("which table? give it in 'layer'")
        return path, fmt, driver, layer, is_db

    def schema(self, schemas):
        s = schemas["features"]
        path, fmt, driver, layer, is_db = self._target()
        if fmt == "ESRI Shapefile":   # its DBF of 1983: names of 10 characters at most
            long = [f.name for f in s.fields if len(f.name.encode()) > 10]
            if long:
                raise SchemaError(f"a shapefile allows 10 characters per column name: "
                                  f"{', '.join(long)} (rename them before, in Attributes)")
        mode = self.params["mode"]
        if mode not in ("create", "replace", "append"):
            raise SchemaError(f"mode '{mode}': create, replace or append")
        if mode == "create" and not is_db and Path(path).exists():
            if fmt != "GPKG" or self._has_layer(path, layer):
                raise SchemaError(f"{Path(path).name} exists (mode create): choose replace or append")
        self.ctx.log(f"writes {s.describe()} to {layer}")
        return {}

    @staticmethod
    def _has_layer(path, layer) -> bool:
        try:
            ds = gdal.OpenEx(path, gdal.OF_VECTOR)
            return ds is not None and ds.GetLayerByName(layer) is not None
        except RuntimeError:
            return False

    def writes(self):
        """(where, layer) it writes, for the graph: two Writers on the same layer of the same file
        are refused before running (each would replace or mix the other's features)."""
        path, fmt, driver, layer, is_db = self._target()
        where = " ".join(path.split()) if is_db else str(Path(path).resolve()).lower()
        return where, layer.lower(), (layer, path if is_db else Path(path).name)

    def target(self) -> str | None:
        """For run(): writers into the same GeoPackage or SQLite file take turns (one writes,
        then the next): the file allows one transaction at a time. None: no need to wait."""
        path, fmt, driver, layer, is_db = self._target()
        return str(Path(path).resolve()).lower() if fmt in ("GPKG", "SQLite") else None

    # ---- writing ----------------------------------------------------------------------
    def run(self, inputs):
        s = self.schemas_in["features"]
        path, fmt, driver, layer_name, is_db = self._target()
        mode = self.params["mode"]
        transactional = fmt in ("GPKG", "PostgreSQL", "SQLite")

        def drop(ds, name):
            for i in range(ds.GetLayerCount()):
                if ds.GetLayer(i).GetName() == name:
                    ds.DeleteLayer(i)
                    return

        def make_layer(ds, name, options=()):
            if mode == "append":
                lyr = ds.GetLayerByName(name)
                if lyr is not None:
                    return lyr
            srs = None
            if s.crs:
                srs = osr.SpatialReference()
                srs.ImportFromWkt(s.crs)
            lyr = ds.CreateLayer(name, srs, GEOMETRIES.get(s.geometry, ogr.wkbUnknown), list(options))
            for f in s.fields:
                fd = ogr.FieldDefn(f.name, FIELD_TYPES.get(f.type, ogr.OFTString))
                if f.width:
                    fd.SetWidth(f.width)
                lyr.CreateField(fd)
            return lyr

        def write(lyr):
            defn = lyr.GetLayerDefn()
            index = {defn.GetFieldDefn(i).GetName(): i for i in range(defn.GetFieldCount())}
            for feat in inputs["features"]:
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
                yield

        def gen_transactional():
            """A GeoPackage, SQLite or PostGIS.

            append: the features added in one transaction, rolled back on error.
            create, replace: written into a new layer under a temporary name, in
            one transaction; only when all is written is the old layer dropped and
            the new one renamed. A failure leaves the old layer, and drops the new.
            """
            created_file = not is_db and not Path(path).exists()
            if created_file:
                Path(path).parent.mkdir(parents=True, exist_ok=True)   # out/ made when needed
            ds = (driver.Create(path, 0, 0, 0, gdal.GDT_Unknown) if created_file
                  else gdal.OpenEx(path, gdal.OF_VECTOR | gdal.OF_UPDATE))
            old = ds.GetLayerByName(layer_name)
            if mode == "append" and old is not None:
                lyr, tmp_name = old, None
            else:
                tmp_name = layer_name + "_etl2d_tmp"
                if ds.GetLayerByName(tmp_name) is not None:   # a run killed before: its leftover
                    drop(ds, tmp_name)
                if fmt == "GPKG":
                    # The spatial index (an R-tree) made at the end, once, on the final layer: GDAL's
                    # own, built during a transaction then a rename, fails in recent versions ("UNIQUE
                    # constraint failed: rtree_..._etl2d_tmp_geom.id"). A leftover of a killed run first.
                    for sql in (f'DROP TABLE IF EXISTS "rtree_{tmp_name}_geom"',
                                f"DELETE FROM gpkg_extensions WHERE lower(table_name) = lower('{tmp_name}')"):
                        try:
                            ds.ExecuteSQL(sql)
                        except RuntimeError:
                            pass
                lyr = make_layer(ds, tmp_name, ["SPATIAL_INDEX=NO"] if fmt == "GPKG" else [])
                lyr.SyncToDisk()   # a GeoPackage creates its table lazily: now, outside the transaction
            done = False
            ds.StartTransaction()
            try:
                yield from write(lyr)
                done = True
            finally:
                if done:
                    ds.CommitTransaction()
                    if tmp_name is not None:
                        if ds.GetLayerByName(layer_name) is not None:
                            drop(ds, layer_name)
                        ds.GetLayerByName(tmp_name).Rename(layer_name)
                        final = ds.GetLayerByName(layer_name)
                        if fmt == "GPKG" and final.GetGeomType() != ogr.wkbNone:
                            ds.ExecuteSQL(f"SELECT CreateSpatialIndex('{layer_name}', "
                                          f"'{final.GetGeometryColumn() or 'geom'}')")
                    ds = None
                else:
                    try:   # an error, or the run stopped: nothing kept. Never hide the first error.
                        ds.RollbackTransaction()
                        if tmp_name is not None:
                            drop(ds, tmp_name)
                    except RuntimeError:
                        pass
                    ds = None
                    if created_file:
                        Path(path).unlink(missing_ok=True)

        def parts(target: Path) -> list[Path]:
            names = [target.stem + ext for ext in SHAPEFILE_PARTS] if fmt == "ESRI Shapefile" else [target.name]
            return [target.parent / n for n in names if (target.parent / n).is_file()]

        def gen_files():
            """Written beside, in a temporary folder; moved in place only when all went well."""
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(prefix=".etl2d-", dir=target.parent))
            done = False
            try:
                tmp_path = tmp / target.name
                if mode == "append" and target.exists():
                    for f in parts(target):
                        shutil.copy2(f, tmp / f.name)
                    ds = gdal.OpenEx(str(tmp_path), gdal.OF_VECTOR | gdal.OF_UPDATE)
                else:
                    ds = driver.Create(str(tmp_path), 0, 0, 0, gdal.GDT_Unknown)
                yield from write(make_layer(ds, layer_name))
                ds = None   # closed: everything on the disk
                done = True
            finally:
                if done:
                    for f in parts(target):   # the old file and, for a shapefile, its side files
                        f.unlink()
                    for f in tmp.iterdir():
                        shutil.move(str(f), target.parent / f.name)
                shutil.rmtree(tmp, ignore_errors=True)

        return gen_transactional() if transactional else gen_files()
