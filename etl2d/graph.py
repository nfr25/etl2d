"""A workflow: nodes, their parameters, the links between their ports (notes §10)."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etl2d.needs import Needs
from etl2d.registry import NodeSpec, Registry
from etl2d.schema import Schema, SchemaError


class GraphError(Exception):
    """The workflow file itself is wrong: unreadable, a link to a missing node, a cycle."""


@dataclass
class Context:
    """What a node may use besides its parameters: where relative paths start, the log."""

    base_dir: Path = field(default_factory=Path.cwd)
    messages: list[str] = field(default_factory=list)
    connected: set[str] = field(default_factory=set)   # this node's output ports that lead somewhere

    def path(self, p: str | Path) -> Path:
        """A path of a parameter: relative ones start at the workflow's folder (§10)."""
        p = Path(p)
        return p if p.is_absolute() else (self.base_dir / p)

    def log(self, message: str) -> None:
        self.messages.append(message)


@dataclass
class NodeInstance:
    id: int
    type: str
    params: dict[str, Any]
    pos: tuple[float, float] = (0.0, 0.0)
    spec: NodeSpec | None = None          # None: its type was not found (kept, shown in red)
    schemas: dict[str, Schema] = field(default_factory=dict)   # its outputs' structures (§4)
    given: dict[str, Schema] = field(default_factory=dict)     # its inputs' structures
    wanted: dict[str, Needs] = field(default_factory=dict)     # §5: what its consumers need of each output
    asks: dict[str, Needs] = field(default_factory=dict)       # §5: what it needs of each input
    error: str = ""                       # why it cannot run, said before any run
    notes: list[str] = field(default_factory=list)   # what the node says (differences between files...)

    def label(self) -> str:
        return f"#{self.id} {self.type}"

    def inputs(self) -> list[str]:
        """Its input ports now: fixed, or made from its parameters (§7, the MapWriter's layers)."""
        return self.spec.input_names(self.params) if self.spec is not None else []

    def outputs(self) -> list[str]:
        """Its output ports now: fixed, or made from its parameters (§7, the Filter)."""
        return self.spec.output_names(self.params) if self.spec is not None else []


@dataclass(frozen=True)
class Link:
    src: int
    src_port: str
    dst: int
    dst_port: str


@dataclass
class Graph:
    nodes: dict[int, NodeInstance] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)
    base_dir: Path = field(default_factory=Path.cwd)
    raw: dict = field(default_factory=dict)   # the file as read: unknown keys written back as they were
    keep: set[tuple[int, str]] = field(default_factory=set)   # §9: outputs whose features are kept in a file
    work_dir: Path | None = None   # §9: where the kept files go: job.etl2d/ beside job.json

    def work_folder(self) -> Path:
        """job.etl2d/ beside job.json; a workflow not saved yet: a folder in the temporary folder."""
        return self.work_dir or Path(tempfile.gettempdir()) / "etl2d-untitled.etl2d"

    def kept_path(self, nid: int, port: str) -> Path:
        return self.work_folder() / f"n{nid}-{port}.fgb"

    # ---- reading and writing -------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict, registry: Registry, base_dir: Path | None = None) -> "Graph":
        if not isinstance(data, dict) or "nodes" not in data:
            raise GraphError("not an etl2d workflow: no 'nodes'")
        g = cls(base_dir=Path(base_dir) if base_dir else Path.cwd(), raw=data)
        for n in data.get("nodes", []):
            try:
                nid, ntype = int(n["id"]), str(n["type"])
            except (KeyError, TypeError, ValueError):
                raise GraphError(f"a node without id or type: {n}")
            if nid in g.nodes:
                raise GraphError(f"two nodes numbered {nid}")
            spec = registry.find(ntype)
            params = {}
            if spec is not None:
                params = {p.name: p.default for p in spec.params}
            params.update(n.get("params", {}))
            inst = NodeInstance(nid, ntype, params, tuple(n.get("pos", (0, 0))), spec)
            if spec is None:
                inst.error = f"node type '{ntype}' not found (a custom node missing on this computer?)"
            g.nodes[nid] = inst
        for item in data.get("links", []):
            try:
                link = Link(int(item[0]), str(item[1]), int(item[2]), str(item[3]))
            except (IndexError, TypeError, ValueError):
                raise GraphError(f"a link must be [from, port, to, port]: {item}")
            for end, port, side in ((link.src, link.src_port, "outputs"), (link.dst, link.dst_port, "inputs")):
                if end not in g.nodes:
                    raise GraphError(f"link {item}: no node {end}")
                inst = g.nodes[end]
                if inst.spec is not None:
                    ports = inst.outputs() if side == "outputs" else inst.inputs()
                    if port not in ports:
                        raise GraphError(f"link {item}: {inst.label()} has no {side[:-1]} '{port}'")
            if any(l.dst == link.dst and l.dst_port == link.dst_port for l in g.links):
                raise GraphError(f"link {item}: input '{link.dst_port}' of node {link.dst} is already joined")
            g.links.append(link)
        for item in data.get("keep", []):
            try:
                nid, port = int(item[0]), str(item[1])
            except (IndexError, TypeError, ValueError):
                raise GraphError(f"a kept output must be [node, port]: {item}")
            if nid not in g.nodes:
                raise GraphError(f"keep {item}: no node {nid}")
            if g.nodes[nid].spec is not None and port not in g.nodes[nid].outputs():
                raise GraphError(f"keep {item}: {g.nodes[nid].label()} has no output '{port}'")
            g.keep.add((nid, port))
        g.order()   # a cycle is refused here
        return g

    @classmethod
    def load(cls, path: str | Path, registry: Registry) -> "Graph":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise GraphError(f"{path}: {e}")
        g = cls.from_dict(data, registry, path.resolve().parent)
        g.work_dir = path.resolve().with_suffix(".etl2d")
        return g

    def to_dict(self) -> dict:
        out = dict(self.raw)
        out["etl2d"] = 1
        out["nodes"] = [{"id": n.id, "type": n.type, "pos": list(n.pos), "params": n.params}
                        for n in self.nodes.values()]
        out["links"] = [[l.src, l.src_port, l.dst, l.dst_port] for l in self.links]
        if self.keep:
            out["keep"] = [[nid, port] for nid, port in sorted(self.keep)]
        else:
            out.pop("keep", None)
        return out

    # ---- editing (the window, §11) ---------------------------------------------------
    def add_node(self, registry: Registry, ntype: str, pos=(0.0, 0.0)) -> NodeInstance:
        spec = registry.find(ntype)
        nid = max(self.nodes, default=0) + 1
        params = {p.name: p.default for p in spec.params} if spec else {}
        inst = NodeInstance(nid, ntype, params, tuple(pos), spec)
        self.nodes[nid] = inst
        return inst

    def remove_node(self, nid: int) -> None:
        self.links = [l for l in self.links if l.src != nid and l.dst != nid]
        self.keep = {k for k in self.keep if k[0] != nid}
        self.nodes.pop(nid, None)

    def can_link(self, link: Link) -> str:
        """'' when the link may be made, else why not (said in the window)."""
        if link.src == link.dst:
            return "a node cannot read its own output"
        if any(l.dst == link.dst and l.dst_port == link.dst_port for l in self.links):
            return f"input '{link.dst_port}' is already joined"
        trial = Graph(dict(self.nodes), self.links + [link], self.base_dir)
        try:
            trial.order()
        except GraphError:
            return "this link would make a loop"
        return ""

    def add_link(self, link: Link) -> str:
        why = self.can_link(link)
        if not why:
            self.links.append(link)
        return why

    def remove_link(self, link: Link) -> None:
        self.links = [l for l in self.links if l != link]

    def rename_input(self, nid: int, old: str, new: str) -> None:
        """An input renamed (a MapWriter's layer, §7): its link follows."""
        self.links = [Link(l.src, l.src_port, l.dst, new) if l.dst == nid and l.dst_port == old else l
                      for l in self.links]

    def rename_output(self, nid: int, old: str, new: str) -> None:
        """An output renamed (a Filter's rule, §7): its links and its keeping follow."""
        self.links = [Link(l.src, new, l.dst, l.dst_port) if l.src == nid and l.src_port == old else l
                      for l in self.links]
        if (nid, old) in self.keep:
            self.keep.discard((nid, old))
            self.keep.add((nid, new))

    def prune_ports(self) -> list[str]:
        """Links and kept outputs on outputs that no longer exist (a rule removed): removed; what, said."""
        gone = []
        for l in list(self.links):
            inst = self.nodes.get(l.src)
            if inst is not None and inst.spec is not None and l.src_port not in inst.outputs():
                self.links.remove(l)
                gone.append(f"link {inst.label()} {l.src_port} > #{l.dst} removed: no output '{l.src_port}' any more")
        for l in list(self.links):
            inst = self.nodes.get(l.dst)
            if inst is not None and inst.spec is not None and l.dst_port not in inst.inputs():
                self.links.remove(l)
                gone.append(f"link #{l.src} {l.src_port} > {inst.label()} {l.dst_port} removed: "
                            f"no input '{l.dst_port}' any more")
        for nid, port in list(self.keep):
            inst = self.nodes.get(nid)
            if inst is not None and inst.spec is not None and port not in inst.outputs():
                self.keep.discard((nid, port))
                gone.append(f"{inst.label()} {port}: no longer kept, no output '{port}' any more")
        return gone

    # ---- copy and paste (the window, §11) -------------------------------------------------
    def extract(self, ids) -> dict:
        """Some nodes as a small workflow: their parameters, the links and kept outputs between them.

        Positions relative to the top-left one. What the clipboard holds (as JSON).
        """
        ids = [i for i in ids if i in self.nodes]
        if not ids:
            return {}
        x0 = min(self.nodes[i].pos[0] for i in ids)
        y0 = min(self.nodes[i].pos[1] for i in ids)
        chosen = set(ids)
        return {"etl2d": 1,
                "nodes": [{"id": i, "type": self.nodes[i].type,
                           "pos": [self.nodes[i].pos[0] - x0, self.nodes[i].pos[1] - y0],
                           "params": self.nodes[i].params} for i in ids],
                "links": [[l.src, l.src_port, l.dst, l.dst_port] for l in self.links
                          if l.src in chosen and l.dst in chosen],
                "keep": [[n, p] for n, p in sorted(self.keep) if n in chosen]}

    def insert(self, data: dict, registry: Registry, at=(0.0, 0.0)) -> list[int]:
        """A small workflow (extract(), or a whole workflow file) added under new numbers at `at`.

        Its links between its own nodes come along; links to nodes outside it do not.
        Returns the new numbers. Raises GraphError when data is not a workflow.
        """
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not data["nodes"]:
            raise GraphError("not etl2d nodes")
        part = Graph.from_dict(json.loads(json.dumps(data)), registry, self.base_dir)   # checked, and a deep copy
        x0 = min(n.pos[0] for n in part.nodes.values())
        y0 = min(n.pos[1] for n in part.nodes.values())
        new: dict[int, int] = {}
        for old, n in part.nodes.items():
            inst = self.add_node(registry, n.type, (at[0] + n.pos[0] - x0, at[1] + n.pos[1] - y0))
            inst.params.update(n.params)
            if inst.spec is None:
                inst.error = n.error
            new[old] = inst.id
        for l in part.links:
            self.links.append(Link(new[l.src], l.src_port, new[l.dst], l.dst_port))
        self.keep |= {(new[n], p) for n, p in part.keep}
        return list(new.values())

    # ---- structure --------------------------------------------------------------------
    def inputs_of(self, nid: int) -> list[Link]:
        return [l for l in self.links if l.dst == nid]

    def outputs_of(self, nid: int) -> list[Link]:
        return [l for l in self.links if l.src == nid]

    def kept_ports(self, nid: int) -> set[str]:
        return {port for n, port in self.keep if n == nid}

    def order(self) -> list[int]:
        """The nodes, each after every node it reads from (Kahn's algorithm). A cycle is an error."""
        waiting = {nid: len({l.src for l in self.inputs_of(nid)}) for nid in self.nodes}
        ready = sorted(nid for nid, w in waiting.items() if w == 0)
        out = []
        while ready:
            nid = ready.pop(0)
            out.append(nid)
            for dst in sorted({l.dst for l in self.outputs_of(nid)}):
                waiting[dst] -= 1
                if waiting[dst] == 0:
                    ready.append(dst)
        if len(out) != len(self.nodes):
            raise GraphError("the links make a loop: a node would read its own output")
        return out

    def instantiate(self, inst: NodeInstance):
        """A fresh object of the node's class, with its parameters and context."""
        obj = inst.spec.cls()
        obj.params = dict(inst.params)
        obj.schemas_in = dict(inst.given)   # what run() may need of its inputs' structure
        obj.wanted = dict(inst.wanted)      # §5: what the nodes after it need of its outputs
        obj.ctx = Context(self.base_dir, inst.notes,
                          {l.src_port for l in self.outputs_of(inst.id)} | self.kept_ports(inst.id))
        return obj

    def plan_needs(self) -> None:
        """§5: the needs, from the writers up to the readers (after propagate()).

        Each node gets `wanted` (per output: the union of what its consumers
        ask) and `asks` (per input: from its needs(), or everything when it has
        none). An output joined to nothing is wanted by nobody: absent. A kept
        output (§9) is wanted whole.
        """
        for nid in reversed(self.order()):
            inst = self.nodes[nid]
            wanted: dict[str, Needs] = {}
            for link in self.outputs_of(nid):
                asked = self.nodes[link.dst].asks.get(link.dst_port, Needs.everything())
                wanted[link.src_port] = wanted[link.src_port].union(asked) if link.src_port in wanted else asked
            for port in self.kept_ports(nid):   # §9: a kept output is looked at whole: everything
                wanted[port] = Needs.everything()
            inst.wanted = wanted
            inst.asks = {}
            if inst.spec is None or inst.error or not inst.inputs():
                continue
            obj = self.instantiate(inst)
            asks = {}
            if hasattr(obj, "needs"):
                try:
                    asks = obj.needs(wanted) or {}
                except Exception as e:   # a node's bug: it then needs everything, and says so
                    inst.notes.append(f"needs(): {type(e).__name__}: {e}; everything read")
                    asks = {}
            inst.asks = {name: asks.get(name, Needs.everything()) for name in inst.inputs()}

    def propagate(self) -> bool:
        """§4: every node's output structures, from the readers down, without reading data.

        Each node gets `schemas` or an `error` (its own, or "waits for #3").
        True when every node can run.
        """
        ok = True
        for nid in self.order():
            inst = self.nodes[nid]
            inst.schemas, inst.given, inst.notes[:] = {}, {}, []
            if inst.spec is None:
                ok = False
                continue
            inst.error = ""
            given: dict[str, Schema] = {}
            for link in self.inputs_of(nid):
                src = self.nodes[link.src]
                if link.src_port not in src.schemas:
                    inst.error = f"waits for {src.label()}"
                    break
                given[link.dst_port] = src.schemas[link.src_port]
            if not inst.error:
                missing = [name for name in inst.inputs() if name not in given]
                if missing:
                    inst.error = "input not joined: " + ", ".join(missing)
            inst.given = given
            if not inst.error:
                try:
                    result = self.instantiate(inst).schema(given) or {}
                    inst.schemas = dict(result)
                except SchemaError as e:
                    inst.error = str(e)
                except Exception as e:   # a node's bug, said as such
                    inst.error = f"{type(e).__name__}: {e}"
            if inst.error:
                ok = False
        # Two writers on the same layer of the same file: the second would replace or mix the first's.
        seen: dict = {}
        for nid in self.order():
            inst = self.nodes[nid]
            if inst.error or inst.spec is None or not inst.spec.is_sink:
                continue
            obj = self.instantiate(inst)
            if not hasattr(obj, "writes"):
                continue
            try:
                where, layer, (shown_layer, shown_file) = obj.writes()
            except Exception:
                continue
            first, first_layer = seen.setdefault((where, layer), (inst, shown_layer))
            if first is not inst:
                inst.error = (f"{first.label()} writes layer '{first_layer}' of {shown_file} too: "
                              f"give each Writer its own layer")
                ok = False
        return ok
