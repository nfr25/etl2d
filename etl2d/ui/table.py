"""The Table panel: what a kept output carried, read back from its file (notes §9)."""

from __future__ import annotations

import time
from pathlib import Path

from imgui_bundle import imgui
from osgeo import gdal, ogr

from etl2d.ui.state import State

GREY = imgui.ImVec4(0.62, 0.62, 0.62, 1.0)
YELLOW = imgui.ImVec4(1.0, 0.85, 0.35, 1.0)
RED = imgui.ImVec4(1.0, 0.45, 0.40, 1.0)

NAMES = {"POINT": "Point", "LINESTRING": "LineString", "POLYGON": "Polygon", "MULTIPOINT": "MultiPoint",
         "MULTILINESTRING": "MultiLineString", "MULTIPOLYGON": "MultiPolygon",
         "GEOMETRYCOLLECTION": "GeometryCollection"}
ROWS = 1000   # the first rows only: a look, not a copy of the file


def points(g: ogr.Geometry) -> int:
    """The number of points of a geometry, its parts and rings included."""
    n = g.GetGeometryCount()
    if n:
        return sum(points(g.GetGeometryRef(i)) for i in range(n))
    return max(g.GetPointCount(), 1 if g.GetGeometryType() in (ogr.wkbPoint, ogr.wkbPoint25D) else 0)


def describe_geometry(g: ogr.Geometry | None) -> str:
    """'Point', 'Polygon, 14 points': what a row's geometry is, in a few words."""
    if g is None:
        return "(none)"
    name = NAMES.get(g.GetGeometryName(), g.GetGeometryName().title())
    if name == "Point":
        return name
    return f"{name}, {points(g):,} points"


class TableData:
    """A kept file read back: its count, its columns, its first rows. Read again when it changes."""

    def __init__(self):
        self.path: Path | None = None
        self.mtime = 0.0
        self.checked = 0.0
        self.count = 0
        self.columns: list[str] = []
        self.rows: list[list[str]] = []
        self.error = ""

    def follow(self, path: Path) -> None:
        now = time.monotonic()
        if path == self.path and now - self.checked < 1.0:
            return
        self.checked = now
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if path == self.path and mtime == self.mtime:
            return
        self.path, self.mtime = path, mtime
        self.count, self.columns, self.rows, self.error = 0, [], [], ""
        if not mtime:
            return
        try:
            ds = gdal.OpenEx(str(path), gdal.OF_VECTOR)
            lyr = ds.GetLayer(0)
            defn = lyr.GetLayerDefn()
            self.columns = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
            self.count = lyr.GetFeatureCount()
            for f in lyr:
                values = [describe_geometry(f.GetGeometryRef())]
                for i in range(len(self.columns)):
                    v = None if f.IsFieldNull(i) else f.GetField(i)
                    values.append("" if v is None else str(v))
                self.rows.append(values)
                if len(self.rows) >= ROWS:
                    break
        except RuntimeError as e:
            self.error = str(e)


_data = TableData()


def draw(state: State) -> None:
    g = state.graph
    kept = sorted(k for k in g.keep if k[0] in g.nodes)
    if not kept:
        imgui.text_colored(GREY, "nothing kept: a right click on an output (or a link) > Keep, then F5")
        return
    if state.looked not in kept:
        state.looked = kept[0]

    def label(k):
        return f"#{k[0]} {g.nodes[k[0]].type}: {k[1]}"

    imgui.set_next_item_width(imgui.get_font_size() * 18)
    if imgui.begin_combo("##kept", label(state.looked)):
        for k in kept:
            if imgui.selectable(label(k), k == state.looked)[0]:
                state.looked = k
        imgui.end_combo()
    imgui.same_line()

    path = g.kept_path(*state.looked)
    _data.follow(path)
    if not _data.mtime:
        imgui.text_colored(GREY, "not written yet: F5 runs the workflow")
        return
    if _data.error:
        imgui.text_colored(RED, _data.error)
        return
    when = time.strftime("%H:%M:%S", time.localtime(_data.mtime))
    shown = f"the first {len(_data.rows):,} of " if len(_data.rows) < _data.count else ""
    imgui.text(f"{shown}{_data.count:,} features, written at {when}")
    if imgui.is_item_hovered():
        imgui.set_tooltip(str(path))
    # The last run did not finish after this file was written: it is the run before's.
    if state.result is not None and not state.result.ok and _data.mtime < state.started_wall:
        imgui.same_line()
        imgui.text_colored(YELLOW, "(old: the last run did not finish)")

    n = len(_data.columns) + 1
    flags = (imgui.TableFlags_.borders | imgui.TableFlags_.row_bg | imgui.TableFlags_.scroll_x |
             imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable | imgui.TableFlags_.sizing_fixed_fit)
    if imgui.begin_table("rows", min(n, 64), flags):
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column("geometry")
        for c in _data.columns[:63]:
            imgui.table_setup_column(c)
        imgui.table_headers_row()
        clipper = imgui.ListClipper()
        clipper.begin(len(_data.rows))
        while clipper.step():
            for r in range(clipper.display_start, clipper.display_end):
                imgui.table_next_row()
                for v in _data.rows[r][:64]:
                    imgui.table_next_column()
                    imgui.text_unformatted(v)
        imgui.end_table()
