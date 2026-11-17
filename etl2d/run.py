"""Running a workflow: features pulled one by one through generators (notes §3, §9)."""

from __future__ import annotations

import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from etl2d.feature import Feature
from etl2d.graph import Graph
from etl2d.keep import keeper


class RunError(Exception):
    """A node failed during the run; the message names the node and, when known, the feature."""


def split(features: Iterable[Feature], route: Callable[[Feature], str | None], ports: list[str],
          connected: set[str]) -> dict[str, Iterator[Feature]]:
    """One input, several outputs, still one by one.

    `route(f)` names the output a feature goes to (None: dropped). When an
    output is asked for its next feature, features are read from the input
    and routed until one is for it; those for the other outputs wait in their
    queue. Outputs joined to nothing keep no queue: their features are dropped
    (otherwise they would pile up, never asked for).
    """
    source = iter(features)
    queues = {p: deque() for p in ports}
    done = [False]

    def pull(port: str) -> Iterator[Feature]:
        q = queues[port]
        while True:
            if q:
                yield q.popleft()
                continue
            if done[0]:
                return
            try:
                f = next(source)
            except StopIteration:
                done[0] = True
                continue
            target = route(f)
            if target is not None and target in connected:
                queues[target].append(f)

    return {p: pull(p) for p in ports}


@dataclass
class NodeStats:
    features_in: int = 0
    features_out: dict[str, int] = field(default_factory=dict)


@dataclass
class RunResult:
    ok: bool
    seconds: float
    stats: dict[int, NodeStats]
    error: str = ""
    kept: dict[tuple[int, str], int] = field(default_factory=dict)   # §9: features written per kept output

    def read(self) -> int:
        """Features the readers gave (the nodes without input)."""
        return sum(sum(s.features_out.values()) for s in self.stats.values()
                   if s.features_in == 0 and s.features_out)

    def written(self) -> int:
        """Features that reached the sinks (the writers)."""
        return sum(s.features_in for nid, s in self.stats.items() if not s.features_out)


def _counted(it: Iterable[Feature], counter: list[int]) -> Iterator[Feature]:
    for f in it:
        counter[0] += 1
        yield f


def run(graph: Graph, log: Callable[[str], None] = lambda m: None, live: dict | None = None,
        stop=None) -> RunResult:
    """Runs the whole workflow (the structure checked first: graph.propagate()).

    live: filled at the start with, per node, its counters ([in], {port: [out]}),
    read by the window while the run goes on (§11). stop: an object with
    is_set() (a threading.Event): checked between two features; the writers
    then roll back, as on an error.
    """
    t0 = time.perf_counter()
    stats: dict[int, NodeStats] = {}
    outputs: dict[tuple[int, str], list[Iterator[Feature]]] = {}   # (node, port) -> one iterator per consumer
    counters: dict[tuple[int, str], list[int]] = {}
    sinks: list[tuple[int, Iterator, str | None]] = []   # (node, generator, file shared with others or None)
    kept_counts: dict[tuple[int, str], list[int]] = {}

    if not graph.propagate():
        bad = [f"{n.label()}: {n.error}" for n in graph.nodes.values() if n.error]
        return RunResult(False, 0.0, stats, "the workflow cannot run:\n  " + "\n  ".join(bad))
    graph.plan_needs()   # §5: the readers ask their sources only for what is used

    def guarded(it: Iterable, label: str) -> Iterator:
        """A node's failure, named: which node, and what it said."""
        try:
            yield from it
        except RunError:
            raise
        except Exception as e:
            raise RunError(f"{label}: {type(e).__name__}: {e}") from e

    for nid in graph.order():
        inst = graph.nodes[nid]
        if inst.off:   # §11: disabled, or fed by a node that is: not run, its files untouched
            continue
        st = stats[nid] = NodeStats()
        obj = graph.instantiate(inst)
        inputs = {}
        for link in graph.inputs_of(nid):
            inputs[link.dst_port] = outputs[(link.src, link.src_port)].pop()
        in_counter = [0]
        if inputs:   # every input counted into the same "in"
            inputs = {k: _counted(v, in_counter) for k, v in inputs.items()}
        result = obj.run(inputs)
        if inst.spec.is_sink:
            key = obj.target() if hasattr(obj, "target") else None
            sinks.append((nid, guarded(result, inst.label()), key))
        else:
            for port in inst.outputs():
                consumers = [l for l in graph.consumers(nid) if l.src_port == port]
                kept = (nid, port) in graph.keep
                n = len(consumers) + kept
                if not n:
                    continue
                c = counters[(nid, port)] = [0]
                it = guarded(_counted(result[port], c), inst.label())
                # §9: one output, several nodes: each gets every feature (a queue in memory).
                its = list(itertools.tee(it, n)) if n > 1 else [it]
                if kept:   # §9: one more consumer, writing to the work folder
                    kc = kept_counts[(nid, port)] = [0]
                    sinks.append((nid, guarded(keeper(its.pop(), inst.schemas[port], graph.kept_path(nid, port), kc),
                                               f"{inst.label()} (kept {port})"), None))
                outputs[(nid, port)] = its
        st._in = in_counter   # read at the end
        if live is not None:
            live[nid] = (in_counter, {p: c for (src, p), c in counters.items() if src == nid})

    # Pull through every sink in turn: the queues of a fan-out stay short when
    # the branches go at the same pace (§9). Writers into the same GeoPackage
    # take turns instead: only the first of them still active is pulled (the
    # file allows one transaction at a time); the next starts when it is done.
    error = ""
    active = list(sinks)
    try:
        while active:
            if stop is not None and stop.is_set():
                raise RunError("stopped")
            first_of: dict = {}
            for item in active:
                if item[2] is not None:
                    first_of.setdefault(item[2], item)
            for item in list(active):
                if item[2] is not None and first_of[item[2]] is not item:
                    continue   # waits for the writer before it into the same file
                try:
                    next(item[1])
                except StopIteration:
                    active.remove(item)
    except RunError as e:
        error = str(e)
        for _, it, _ in active:   # the other writers roll back (their generators' finally)
            it.close()

    for nid, st in stats.items():
        st.features_in = st._in[0]
        del st._in
        for (src, port), c in counters.items():
            if src == nid:
                st.features_out[port] = c[0]
    seconds = time.perf_counter() - t0
    return RunResult(not error, seconds, stats, error,
                     {k: c[0] for k, c in kept_counts.items()} if not error else {})
