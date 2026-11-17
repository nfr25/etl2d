"""The MapWriter: the features drawn by render2d into a PNG, an SVG or a PDF (notes §7: the printer)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from etl2d import Input, Param, SchemaError, node
from etl2d.keep import file_view, keeper
from etl2d.needs import Needs
from etl2d.render import find_render2d

FORMATS = (".png", ".svg", ".pdf")
PAGES = ("none", "A4-landscape", "A4-portrait", "A3-landscape", "A3-portrait")


@node("MapWriter", "Writers")
class MapWriter:
    """The features drawn by render2d into a map: PNG, SVG or PDF (by the file's extension).

    An image of the area (bbox, size), or a printed page (page: A4 or A3, a
    scale, a title, a scale bar, a north arrow, the legend). A style (YSLD) and
    a basemap drawn under, if given. A failed run leaves the old map.
    """

    inputs = [Input("features")]
    outputs = []
    params = [Param("path", "path", "", help="the map: out/buildings.png, .svg or .pdf"),
              Param("style", "path", "", help="a YSLD style; empty: render2d's built-in one"),
              Param("basemap", "path", "", help="drawn under: basemaps/urbis-ortho-2024.xml, a GeoTIFF...; empty: none"),
              Param("bbox", "bbox", None, help="the area drawn, in the features' system; empty: all of them, plus 5 %"),
              Param("size", "str", "1600", help="pixels: 1600 (the height from the area) or 1600x1200"),
              Param("page", "choice", "none", choices=PAGES, help="a printed page (PDF or SVG); none: an image"),
              Param("scale", "int", 0, help="with a page: 2500 for 1:2500; 0: the area fitted"),
              Param("title", "str", "", help="with a page: written at its top"),
              Param("name", "str", "features", help="the layer's name in the legend"),
              Param("render2d", "path", "", help="render2d's program; empty: the window's setting, else the PATH")]

    def _out(self) -> Path:
        p = str(self.params["path"] or "")
        if not p:
            raise SchemaError("no path chosen: out/map.png, .svg or .pdf")
        out = self.ctx.path(p)
        if out.suffix.lower() not in FORMATS:
            raise SchemaError(f"'{out.suffix}': a map is .png, .svg or .pdf")
        return out

    def _exe(self) -> str:
        exe = find_render2d(str(self.params["render2d"] or ""))
        if exe is None:
            raise SchemaError("render2d not found: give its path here, or in Window > Settings, or put it on the PATH")
        return exe

    def schema(self, schemas):
        out, page = self._out(), self.params["page"] or "none"
        self._exe()
        if page != "none" and out.suffix.lower() == ".png":
            raise SchemaError("a page is written as .pdf or .svg, not .png")
        for p in ("style", "basemap"):
            v = str(self.params[p] or "")
            if v and not v.upper().startswith(("PG:", "WMS:", "<")) and not self.ctx.path(v).exists():
                raise SchemaError(f"no {p} {self.ctx.path(v)}")
        size = str(self.params["size"] or "1600").lower().replace(" ", "")
        if not all(x.isdigit() for x in size.split("x")) or size.count("x") > 1:
            raise SchemaError(f"size '{self.params['size']}': 1600 or 1600x1200")
        self.ctx.log(f"draws {schemas['features'].describe()} to {out.name}" + (f", page {page}" if page != "none" else ""))
        return {}

    def needs(self, wanted):
        """§5: its box, when given, asked to the readers; every column (the style may use any)."""
        b = self.params["bbox"]
        return {"features": Needs(tuple(map(float, b)) if b else None, None)}

    def writes(self):
        """For the graph: two MapWriters on the same file are refused (§7)."""
        return str(self._out().resolve()).lower(), "", ("map", self._out().name)

    def run(self, inputs):
        out, exe, schema = self._out(), self._exe(), self.schemas_in["features"]
        name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (self.params["name"] or "features")).strip()

        def gen():
            work = Path(tempfile.mkdtemp(prefix="etl2d-map-"))
            try:
                data = work / f"{name or 'features'}.fgb"   # its name: the legend's
                yield from keeper(inputs["features"], schema, data, [0])
                crs, (x0, y0, x1, y1) = file_view(data)
                args = [exe]
                if self.params["basemap"]:
                    b = str(self.params["basemap"])
                    args.append(b if b.upper().startswith(("PG:", "WMS:", "<")) else str(self.ctx.path(b)))
                args.append(str(data))
                if self.params["style"]:
                    args += ["--style", str(self.ctx.path(self.params["style"]))]
                if crs:
                    args += ["--crs", crs]
                page = self.params["page"] or "none"
                if self.params["bbox"]:
                    bbox = list(map(float, self.params["bbox"]))
                else:   # the features' extent, plus 5 % (a point alone: 50 units around)
                    mx, my = max((x1 - x0) * 0.05, 50 if x1 == x0 else 0), max((y1 - y0) * 0.05, 50 if y1 == y0 else 0)
                    bbox = [x0 - mx, y0 - my, x1 + mx, y1 + my]
                if page != "none":
                    args += ["--page", page]
                    if self.params["scale"]:
                        args += ["--scale", str(int(self.params["scale"]))]
                    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
                    args += ["--center", f"{cx:.3f}", f"{cy:.3f}"]
                    if not self.params["scale"]:
                        args += ["--bbox", *(f"{v:.3f}" for v in bbox)]
                    if self.params["title"]:
                        args += ["--title", str(self.params["title"])]
                else:
                    args += ["--bbox", *(f"{v:.3f}" for v in bbox), "--size", str(self.params["size"] or "1600")]
                tmp = work / ("map" + out.suffix.lower())
                args += ["-o", str(tmp)]
                r = subprocess.run(args, capture_output=True, text=True, timeout=600,
                                   creationflags=0x08000000 if os.name == "nt" else 0)   # no console flashing
                if r.returncode != 0 or not tmp.exists():
                    said = (r.stderr or r.stdout or "").strip().splitlines()
                    raise RuntimeError("render2d: " + (said[-1] if said else f"exit {r.returncode}"))
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp), str(out.with_name(out.name + ".tmp")))
                os.replace(out.with_name(out.name + ".tmp"), out)   # the old map replaced only now
                self.ctx.log(f"{out.name} drawn")
            finally:
                shutil.rmtree(work, ignore_errors=True)

        return gen()
