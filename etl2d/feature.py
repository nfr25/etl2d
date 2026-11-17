"""A feature in etl2d (notes §6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from osgeo import ogr


@dataclass
class Feature:
    """One feature on its way through the graph.

    The geometry stays GDAL's own (ogr.Geometry): reading, reprojecting,
    clipping and writing are GDAL operations, so it never changes form between
    nodes. The attributes are a plain dictionary, what a Python caller expects.
    The structure is not repeated here: it is the link's schema (§4).
    """

    attributes: dict[str, Any] = field(default_factory=dict)
    geometry: ogr.Geometry | None = None
    fid: int | None = None
    source: str = ""   # the file it came from

    def copy(self) -> "Feature":
        """An independent copy: the geometry cloned, the attributes copied."""
        return Feature(dict(self.attributes), self.geometry.Clone() if self.geometry is not None else None,
                       self.fid, self.source)

    def describe(self) -> str:
        """What an error message shows about it: where it came from, and its values."""
        where = f"fid {self.fid}" if self.fid is not None else "a feature"
        if self.source:
            where += f" of {self.source}"
        return f"{where}: {self.attributes}"
