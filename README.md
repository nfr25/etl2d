# etl2d

Geographic data moved and transformed by a graph of nodes, in the spirit of FME, built on
GDAL/OGR, in Python. Nodes are found in folders at start, as ComfyUI's custom nodes: a node
is a Python class in a file. A sibling of [render2d](https://github.com/nfr25/render2d) and
[carto2D](https://github.com/nfr25/carto2d).

The design notes, `docs/notions.en.html`, come before the code: the reference. How the code
is put together, file by file, with one feature followed through it: `docs/code.en.html`.

**Status: 0.6, young but useful.** Workflows are drawn in a node editor (Dear ImGui, through
imgui-bundle) and saved as JSON files, which also run without a window from a command prompt.
Files and PostGIS in and out, attributes kept, renamed or computed (`= round(area / 10) * 10`),
clipping, reprojection, a filter on rules, a Python caller, maps printed by render2d; what passes
on a link kept and looked at in a table and on a map. The structure of the data is known on every
node before anything runs, and each reader asks its source only for the box and the columns used.

## Install (Windows)

1. Python 3.12 or later from [python.org](https://www.python.org), with "Add python.exe to PATH".
2. `setup.bat`: makes a `.venv` beside it and installs GDAL (Christoph Gohlke's wheels for
   Windows) and the rest. Nothing is installed elsewhere; delete `.venv` to start again.

## Run

```
run.bat                                the window, an empty workflow
run.bat ui examples\grand-place.json   the window, with a workflow
run.bat nodes                          the node types found
run.bat run examples\grand-place.json  a workflow, without a window
```

In the window: a click in the library (left) adds a node, or a right click in the graph; a
drag from a port to another joins them; Delete removes what is selected; F shows the whole
graph; a drag on the background selects the nodes in a box; Ctrl+C, Ctrl+V and Ctrl+D copy, paste
and duplicate them; Ctrl+Z undoes, Ctrl+Y redoes. A right click on a node disables it (Ctrl+M) or
renames it (or a double click); the background's menu makes a group around the nodes selected. A file
dropped on the window makes a Reader (a workflow dropped is opened). A click on a node's title shows its parameters at the right. Each node shows what it
receives and makes, and the readers what they are asked (a box, columns), before anything
runs. F5 runs, Esc stops; the counts go green on the nodes. Ctrl+S saves.

To look at what an output carries: a right click on it (or on a link) > Keep. At the next run
its features are also written to `job.etl2d/` beside `job.json` (a work folder, to leave out
of git: `*.etl2d/`), and the Table tab shows them. The Map tab, beside the Graph, draws them
with [render2d](https://github.com/nfr25/render2d): Window > Settings says where render2d is
(and a basemap to put under, if wanted); a drag moves the map, the wheel zooms, a double
click shows the whole file.

A workflow is a JSON file: its nodes, their parameters, the links between their ports.
Relative paths start at the workflow's folder. The exit code is 0 when all went well,
1 when a node failed during the run, 2 when the workflow cannot run (each node's error is
printed first).

## Nodes

| Node | Does |
| --- | --- |
| Reader | a file (shapefile, GeoPackage, FlatGeobuf, GeoJSON...) or a database (`PG:...`) |
| DirectoryReader | every file of a folder matching a pattern, into one flow, with `source_file` |
| Attributes | keep, drop, rename columns; add columns: a value, or computed (`= round(area / 10) * 10`, `= x(0)`, `= street \|\| ' ' \|\| number`) |
| Reprojector | from one coordinate system to another (PROJ) |
| Clipper | inside, partial (across the edge, whole or cut), outside a box |
| Filter | one output per rule (`area < 50`, `type in maison, garage`, or an expression `npoints() > 500`), plus `else`; the first rule met wins |
| PythonCaller | your `process(feature)` for each feature |
| Writer | any GDAL format that writes; create, replace, append; a failed run leaves what was there |
| MapWriter | a map drawn by [render2d](https://github.com/nfr25/render2d): PNG, SVG or a PDF page (title, scale, legend); one input per layer, each with its style or coloured by a column |

Your own nodes go in `custom_nodes/` (see its README). They run with your rights: only files
you trust.

## Tests

```
.venv\Scripts\python -m pytest tests
```

## License

Copyright (C) 2026 Noël Frankinet. GNU Affero General Public License v3 or later, as render2d
and carto2D. See `LICENSE`.
