"""The structure that travels before the data (notes §4)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from osgeo import ogr, osr

# OGR's field types, by the names etl2d shows and writes in workflows.
FIELD_TYPES = {
    "Integer": ogr.OFTInteger,
    "Integer64": ogr.OFTInteger64,
    "Real": ogr.OFTReal,
    "String": ogr.OFTString,
    "Date": ogr.OFTDate,
    "DateTime": ogr.OFTDateTime,
    "Time": ogr.OFTTime,
    "Binary": ogr.OFTBinary,
}
_NAME_OF_OGR = {v: k for k, v in FIELD_TYPES.items()}


class SchemaError(Exception):
    """A node cannot work with the structure it receives: said before any run."""


@dataclass(frozen=True)
class Field:
    name: str
    type: str = "String"     # one of FIELD_TYPES
    width: int = 0           # 0: as the format decides

    def __str__(self) -> str:
        return f"{self.name} {self.type}" + (f"({self.width})" if self.width else "")


@dataclass(frozen=True)
class Schema:
    """Columns, geometry type, coordinate system, and the count when known."""

    fields: tuple[Field, ...] = ()
    geometry: str = "Unknown"   # OGR's name: Point, LineString, Polygon, MultiPolygon...
    crs: str = ""               # WKT; "" = none declared
    count: int | None = None    # features, when the source knows it without reading

    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    def field(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def with_fields(self, fields) -> "Schema":
        return replace(self, fields=tuple(fields))

    def with_crs(self, crs: str) -> "Schema":
        return replace(self, crs=crs)

    def with_count(self, count: int | None) -> "Schema":
        return replace(self, count=count)

    def crs_name(self) -> str:
        """'EPSG:31370', else the system's name, else ''."""
        if not self.crs:
            return ""
        srs = osr.SpatialReference()
        if srs.ImportFromWkt(self.crs) != 0:
            return "?"
        code, name = srs.GetAuthorityCode(None), srs.GetAuthorityName(None)
        return f"{name}:{code}" if code and name else (srs.GetName() or "?")

    def describe(self) -> str:
        """One line, for the log and for a link's tooltip."""
        parts = [f"{len(self.fields)} columns", self.geometry]
        if self.crs:
            parts.append(self.crs_name())
        if self.count is not None:
            parts.append(f"{self.count:,} features")
        return ", ".join(parts)


def schema_of_layer(layer: ogr.Layer, count: bool = True) -> Schema:
    """A layer's structure, read from its header: no feature read."""
    defn = layer.GetLayerDefn()
    fields = []
    for i in range(defn.GetFieldCount()):
        fd = defn.GetFieldDefn(i)
        fields.append(Field(fd.GetName(), _NAME_OF_OGR.get(fd.GetType(), "String"), fd.GetWidth()))
    srs = layer.GetSpatialRef()
    n = layer.GetFeatureCount(False) if count else -1
    return Schema(tuple(fields), ogr.GeometryTypeToName(layer.GetGeomType()).replace(" ", ""),
                  srs.ExportToWkt() if srs is not None else "", n if n >= 0 else None)
