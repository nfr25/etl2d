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
COLOURS = ("none", "categorize", "graduate")
METHODS = ("equal", "quantiles", "natural")
PAGES = ("none", "A4-landscape", "A4-portrait", "A3-landscape", "A3-portrait")


@node("MapWriter", "Writers")
class MapWriter:
    """The features drawn by render2d into a map: PNG, SVG or PDF (by the file's extension).

    One input per layer (the top of the list drawn on top), each with its style,
    or coloured by a column: categorize (a colour per value) or graduate (classes
    of a number). An image of the area (bbox, size), or a printed page (page: A4
    or A3, a scale, a title, a scale bar, a north arrow, the legend); a basemap
    drawn under, if given. A failed run leaves the old map.
    """

    inputs = [Input("features")]   # the fixed one; the layers' (inputs_for) replace it
    outputs = []
    params = [Param("path", "path", "", help="the map: out/buildings.png, .svg or .pdf"),
              Param("layers", "layers", [], help="one input per layer, the top drawn on top: its name (and legend), "
                                                 "a style, or a colour by a column"),
              Param("basemap", "path", "", help="drawn under: basemaps/urbis-ortho-2024.xml, a GeoTIFF...; empty: none"),
              Param("bbox", "bbox", None, help="the area drawn, in the features' system; empty: all of them, plus 5 %"),
              Param("size", "str", "1600", help="pixels: 1600 (the height from the area) or 1600x1200"),
              Param("page", "choice", "none", choices=PAGES, help="a printed page (PDF or SVG); none: an image"),
              Param("scale", "int", 0, help="with a page: 2500 for 1:2500; 0: the area fitted"),
              Param("title", "str", "", help="with a page: written at its top"),
              Param("render2d", "path", "", help="render2d's program; empty: the window's setting, else the PATH")]

    @staticmethod
    def layers_of(params) -> list[dict]:
        """The layers, top first. A workflow of 0.5 or 0.6 (no 'layers', its 'style' and 'name'):
        one layer 'features', its style and its legend kept (§7)."""
        rows = [dict(r) for r in (params.get("layers") or [])]
        if not rows:
            rows = [{"name": "features", "style": params.get("style", "") or "",
                     "legend": params.get("name", "") or ""}]
        return rows

    @staticmethod
    def inputs_for(params) -> list[str]:
        """§7: one input per layer, in the order of the list."""
        return [str(r.get("name", "")) for r in MapWriter.layers_of(params)]

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
        seen = set()
        for i, lay in enumerate(self.layers_of(self.params), 1):
            name = str(lay.get("name", "")).strip()
            if not name:
                raise SchemaError(f"layer {i} has no name (it names its input and its legend line)")
            if name in seen:
                raise SchemaError(f"two layers named '{name}'")
            seen.add(name)
            st = str(lay.get("style", "") or "")
            if st and not self.ctx.path(st).exists():
                raise SchemaError(f"layer '{name}': no style {self.ctx.path(st)}")
            colour = lay.get("colour", "none") or "none"
            if colour not in COLOURS:
                raise SchemaError(f"layer '{name}': colour by '{colour}'? none, categorize or graduate")
            if colour != "none":
                col, s = lay.get("column", ""), schemas.get(name)
                if s is not None and col not in s.names():
                    raise SchemaError(f"layer '{name}': no column '{col}' arrives to colour by")
                if colour == "graduate":
                    f = s.field(col) if s is not None else None
                    if f is not None and f.type not in ("Integer", "Integer64", "Real"):
                        raise SchemaError(f"layer '{name}': graduate needs a column of numbers; {col} is {f.type}")
                    if not 2 <= int(lay.get("classes", 5) or 5) <= 10:
                        raise SchemaError(f"layer '{name}': 2 to 10 classes")
                    if (lay.get("method", "equal") or "equal") not in METHODS:
                        raise SchemaError(f"layer '{name}': method equal, quantiles or natural")
        if page != "none" and out.suffix.lower() == ".png":
            raise SchemaError("a page is written as .pdf or .svg, not .png")
        for p in ("basemap",):
            v = str(self.params[p] or "")
            if v and not v.upper().startswith(("PG:", "WMS:", "<")) and not self.ctx.path(v).exists():
                raise SchemaError(f"no {p} {self.ctx.path(v)}")
        size = str(self.params["size"] or "1600").lower().replace(" ", "")
        if not all(x.isdigit() for x in size.split("x")) or size.count("x") > 1:
            raise SchemaError(f"size '{self.params['size']}': 1600 or 1600x1200")
        n = len(self.layers_of(self.params))
        what = next(iter(schemas.values())).describe() if n == 1 and schemas else f"{n} layers"
        self.ctx.log(f"draws {what} to {out.name}" + (f", page {page}" if page != "none" else ""))
        return {}

    def needs(self, wanted):
        """§5: its box, when given, asked to the readers; every column (the style may use any)."""
        b = self.params["bbox"]
        box = tuple(map(float, b)) if b else None
        return {name: Needs(box, None) for name in self.inputs_for(self.params)}

    def writes(self):
        """For the graph: two MapWriters on the same file are refused (§7)."""
        return str(self._out().resolve()).lower(), "", ("map", self._out().name)

    def run(self, inputs):
        out, exe = self._out(), self._exe()
        layers = self.layers_of(self.params)
        flags = 0x08000000 if os.name == "nt" else 0   # no console flashing

        def render2d(args, what):
            r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=600, creationflags=flags)
            if r.returncode != 0:
                said = (r.stderr or r.stdout or "").strip().splitlines()
                raise RuntimeError(f"render2d ({what}): " + (said[-1] if said else f"exit {r.returncode}"))

        def gen():
            work = Path(tempfile.mkdtemp(prefix="etl2d-map-"))
            try:
                # Each layer to its FlatGeobuf, named as its legend line; the inputs read in turn, one
                # feature from each, so that the queues of a Filter feeding two layers stay short (§9).
                files, writers = [], []
                for i, lay in enumerate(layers):
                    legend = str(lay.get("legend") or lay["name"])
                    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in legend).strip() or "layer"
                    (work / str(i)).mkdir()
                    f = work / str(i) / f"{safe}.fgb"
                    files.append(f)
                    writers.append(keeper(inputs[lay["name"]], self.schemas_in[lay["name"]], f, [0]))
                active = list(writers)
                while active:
                    for w in list(active):
                        try:
                            next(w)
                        except StopIteration:
                            active.remove(w)
                        yield
                views = [file_view(f) for f in files]
                crs = next((c for c, _ in views if c), None)
                # A layer coloured by a column: its style made first, render2d alone on it (it colours
                # only the last source it is given).
                styles = []
                for i, (lay, f) in enumerate(zip(layers, files)):
                    colour = lay.get("colour", "none") or "none"
                    if colour == "none":
                        st = str(lay.get("style", "") or "")
                        styles.append(str(self.ctx.path(st)) if st else None)
                        continue
                    st = work / f"style{i}.yaml"
                    args = [str(f)]
                    if colour == "categorize":
                        args += ["--categorize", str(lay.get("column"))]
                    else:
                        args += ["--graduate", str(lay.get("column")), "--classes", str(int(lay.get("classes", 5) or 5)),
                                 "--method", str(lay.get("method", "equal") or "equal")]
                    render2d(args + ["--write-style", str(st)], f"the classes of {lay['name']}")
                    styles.append(str(st))
                args = []
                if self.params["basemap"]:
                    b = str(self.params["basemap"])
                    args.append(b if b.upper().startswith(("PG:", "WMS:", "<")) else str(self.ctx.path(b)))
                for f, st in reversed(list(zip(files, styles))):   # the bottom of the list first: under
                    args.append(str(f))
                    if st:
                        args += ["--style", st]
                if crs:
                    args += ["--crs", crs]
                page = self.params["page"] or "none"
                if self.params["bbox"]:
                    bbox = list(map(float, self.params["bbox"]))
                else:   # all the layers' extent, plus 5 % (a point alone: 50 units around)
                    x0 = min(v[1][0] for v in views); y0 = min(v[1][1] for v in views)
                    x1 = max(v[1][2] for v in views); y1 = max(v[1][3] for v in views)
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
                render2d(args + ["-o", str(tmp)], "the map")
                if not tmp.exists():
                    raise RuntimeError("render2d wrote no map")
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp), str(out.with_name(out.name + ".tmp")))
                os.replace(out.with_name(out.name + ".tmp"), out)   # the old map replaced only now
                self.ctx.log(f"{out.name} drawn")
            finally:
                shutil.rmtree(work, ignore_errors=True)

        return gen()
