"""python -m etl2d: the window; python -m etl2d run job.json: a workflow run without one (notes §10, §11).

Exit code: 0 all right, 1 a node failed during the run, 2 the workflow cannot run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from etl2d import __version__
from etl2d.graph import Graph, GraphError
from etl2d.registry import default_registry
from etl2d.run import run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="etl2d", description="Geographic data through a graph of nodes.")
    ap.add_argument("--version", action="version", version=f"etl2d {__version__}")
    ap.add_argument("--nodes", action="append", default=[], help="one more folder of custom nodes")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="runs a workflow file")
    r.add_argument("workflow", nargs="?")
    sub.add_parser("nodes", help="lists the node types found")
    u = sub.add_parser("ui", help="the window (the default, with no command)")
    u.add_argument("workflow", nargs="?")
    args = ap.parse_args(argv)
    if args.cmd == "run" and not args.workflow:
        print("run needs a workflow: etl2d.bat run job.json (etl2d.bat alone, or etl2d.bat ui, opens the window)",
              file=sys.stderr)
        return 2

    reg = default_registry([Path(p) for p in args.nodes])
    for e in reg.errors:
        print(f"node file not loaded: {e}", file=sys.stderr)

    if args.cmd == "nodes":
        for spec in sorted(reg.specs.values(), key=lambda s: (s.category, s.name)):
            ins = ", ".join(i.name for i in spec.inputs) or "-"
            outs = ", ".join(o.name for o in spec.outputs) or "-"
            print(f"{spec.category:12} {spec.name:16} in: {ins:10} out: {outs:24} {Path(spec.file).name}")
        return 0
    if args.cmd in (None, "ui"):
        from etl2d.ui.app import main as ui_main   # imgui-bundle only for the window
        return ui_main(getattr(args, "workflow", None))
    if args.cmd != "run":
        ap.print_help()
        return 2

    try:
        g = Graph.load(args.workflow, reg)
    except GraphError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ok = g.propagate()   # §4: the structures first, every node's error at once
    if ok:
        g.plan_needs()   # §5: then what each reader will ask its source
    for nid in g.order():
        n = g.nodes[nid]
        line = f"{n.label():24} " + (f"({n.off})" if n.off else f"ERROR {n.error}" if n.error else
                                     "; ".join(f"{p}: {s.describe()}" for p, s in n.schemas.items()) or "-")
        print(line)
        if ok and n.spec is not None and not n.spec.inputs and "features" in n.wanted:
            cols = n.schemas["features"].names() if "features" in n.schemas else None
            print(f"{'':24}   asked to the source: {n.wanted['features'].describe(cols)}")
        for note in n.notes:
            print(f"{'':24}   {note}")
    if not ok:
        print("the workflow cannot run (see the errors above)", file=sys.stderr)
        return 2

    said = {nid: list(n.notes) for nid, n in g.nodes.items()}
    result = run(g)
    print()
    for nid in g.order():
        st = result.stats.get(nid)
        if st is None:
            continue
        outs = ", ".join(f"{p} {c:,}" for p, c in st.features_out.items())
        print(f"{g.nodes[nid].label():24} in {st.features_in:,}" + (f"  out {outs}" if outs else ""))
        for note in g.nodes[nid].notes:   # what a node said while running (the SpatialRelator: its reference)
            if note not in said.get(nid, []):
                print(f"{'':24}   {note}")
    for (nid, port), count in sorted(result.kept.items()):   # §9
        print(f"kept {g.nodes[nid].label()} {port}: {count:,} in {g.kept_path(nid, port)}")
    rate = result.read() / result.seconds if result.seconds > 0 else 0.0
    print(f"{result.seconds:.2f} s: {result.read():,} features read ({rate:,.0f}/s), "
          f"{result.written():,} written" + (f", {len(result.kept)} kept" if result.kept else ""))
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
