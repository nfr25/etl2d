"""A grid of boxes: which references may meet a box (notes §7, SpatialRelator).

No library: the references' boxes written in the cells of a regular grid. The
cell's side is the extent's larger side divided by the square root of the
count: 1,202 parcels over 781 m give cells of 22.5 m. A query reads the cells
its box covers and keeps the references whose box meets it; GEOS decides then.
"""

from __future__ import annotations

import math

Box = tuple[float, float, float, float]   # GDAL's order: xmin, xmax, ymin, ymax


class Grid:
    def __init__(self, boxes: list[Box]):
        self.boxes = boxes
        self.cells: dict[tuple[int, int], list[int]] = {}
        if not boxes:
            self.x0 = self.y0 = 0.0
            self.side = 1.0
            self.nx = self.ny = 0
            return
        self.x0 = min(b[0] for b in boxes)
        self.y0 = min(b[2] for b in boxes)
        width = max(b[1] for b in boxes) - self.x0
        height = max(b[3] for b in boxes) - self.y0
        self.side = max(width, height) / math.sqrt(len(boxes)) or 1.0   # one point only: any side
        self.nx, self.ny = self._ix(self.x0 + width), self._iy(self.y0 + height)   # the last cells
        for i, b in enumerate(boxes):
            for cx in range(self._ix(b[0]), self._ix(b[1]) + 1):
                for cy in range(self._iy(b[2]), self._iy(b[3]) + 1):
                    self.cells.setdefault((cx, cy), []).append(i)

    def _ix(self, x: float) -> int:
        return int((x - self.x0) // self.side)

    def _iy(self, y: float) -> int:
        return int((y - self.y0) // self.side)

    def entries(self) -> int:
        return sum(len(c) for c in self.cells.values())

    def query(self, box: Box) -> list[int]:
        """The references whose box meets this one, in their order of insertion
        (the reading order: 'first' gives the same answer at every run)."""
        if not self.cells:
            return []
        found = set()
        boxes = self.boxes
        # kept within the grid: a box larger than the references does not read empty cells
        for cx in range(max(0, self._ix(box[0])), min(self.nx, self._ix(box[1])) + 1):
            for cy in range(max(0, self._iy(box[2])), min(self.ny, self._iy(box[3])) + 1):
                for i in self.cells.get((cx, cy), ()):
                    b = boxes[i]
                    if b[0] <= box[1] and box[0] <= b[1] and b[2] <= box[3] and box[2] <= b[3]:
                        found.add(i)
        return sorted(found)
