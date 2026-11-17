"""What a node really needs from the nodes before it (notes §5).

The structure goes down the graph (§4); the needs come up: from the writers to
the readers, each node says which part of the features it will use. A reader
then asks its source only for that: the features near a box (the source's
spatial index), the columns used (GDAL's ignored fields). The same as a
database pushing its filters and its columns down to the table.
"""

from __future__ import annotations

from dataclasses import dataclass

Box = tuple[float, float, float, float]   # xmin, ymin, xmax, ymax, in the port's coordinate system


@dataclass(frozen=True)
class Needs:
    bbox: Box | None = None                 # None: everywhere
    columns: frozenset[str] | None = None   # None: every column

    @staticmethod
    def everything() -> "Needs":
        return Needs()

    def union(self, other: "Needs") -> "Needs":
        """Two consumers of one output: what either needs (the larger box, both sets of columns)."""
        if self.bbox is None or other.bbox is None:
            bbox = None
        else:
            a, b = self.bbox, other.bbox
            bbox = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
        cols = None if self.columns is None or other.columns is None else self.columns | other.columns
        return Needs(bbox, cols)

    def describe(self, all_columns: list[str] | None = None) -> str:
        parts = []
        if self.bbox is not None:
            parts.append("box " + " ".join(f"{v:.0f}" for v in self.bbox))
        if self.columns is not None:
            n = len(self.columns)
            parts.append(f"{n} of {len(all_columns)} columns" if all_columns is not None else f"{n} columns")
        return ", ".join(parts) or "everything"


def intersect(a: Box | None, b: Box | None) -> Box | None:
    """The common part of two boxes; None stands for everywhere."""
    if a is None:
        return b
    if b is None:
        return a
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
