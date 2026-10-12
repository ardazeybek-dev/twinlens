"""A Burkhard-Keller tree for radius queries in the Hamming metric.

Comparing every photo with every other one is O(n²) — 50k photos means over a
billion comparisons. A BK-tree files each item under its parent at edge label
``d(parent, item)``. Thanks to the triangle inequality, a query for everything
within ``r`` of ``q`` only needs to descend into children whose edge label lies
in ``[d(q, node) - r, d(q, node) + r]``, which prunes most of the tree for the
small radii we use.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Generic, TypeVar

from .hashing import hamming

T = TypeVar("T")


class _Node(Generic[T]):
    __slots__ = ("key", "items", "children")

    def __init__(self, key: int, item: T) -> None:
        self.key = key
        self.items: list[T] = [item]
        self.children: dict[int, _Node[T]] = {}


class BKTree(Generic[T]):
    def __init__(self, distance: Callable[[int, int], int] = hamming) -> None:
        self._distance = distance
        self._root: _Node[T] | None = None
        self._size = 0
        self.comparisons = 0  # distance evaluations during queries, for diagnostics

    def __len__(self) -> int:
        return self._size

    def add(self, key: int, item: T) -> None:
        self._size += 1
        if self._root is None:
            self._root = _Node(key, item)
            return
        node = self._root
        while True:
            d = self._distance(key, node.key)
            if d == 0:
                node.items.append(item)  # identical hashes share one node
                return
            child = node.children.get(d)
            if child is None:
                node.children[d] = _Node(key, item)
                return
            node = child

    def extend(self, pairs: Iterable[tuple[int, T]]) -> None:
        for key, item in pairs:
            self.add(key, item)

    def query(self, key: int, radius: int) -> Iterator[tuple[int, T]]:
        """Yield ``(distance, item)`` for every stored item within ``radius`` of ``key``."""
        if self._root is None:
            return
        stack = [self._root]
        while stack:
            node = stack.pop()
            d = self._distance(key, node.key)
            self.comparisons += 1
            if d <= radius:
                for item in node.items:
                    yield d, item
            lo, hi = d - radius, d + radius
            for edge, child in node.children.items():
                if lo <= edge <= hi:
                    stack.append(child)
