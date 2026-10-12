"""Group twins together and decide which shot in each group is worth keeping."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .bktree import BKTree
from .hashing import from_hex, hamming
from .imaging import PhotoRecord

# dHash is noisier than pHash; a genuine twin may sit a few bits further away.
DHASH_SLACK = 8

_COPY_NAME = re.compile(r"(\bcopy\b|kopya|\(\d+\)|[-_ ]\d{1,2}$|-edited|\bedit\b)", re.IGNORECASE)


class UnionFind:
    """Disjoint-set forest with path halving and union by size."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        parent = self.parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        return True

    def groups(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.parent)):
            out.setdefault(self.find(i), []).append(i)
        return list(out.values())


@dataclass
class Member:
    record: PhotoRecord
    score: float
    keep: bool
    distance: int  # pHash distance to the chosen keeper
    reasons: list[str] = field(default_factory=list)


@dataclass
class Cluster:
    id: int
    kind: str  # "exact" (byte-identical) or "similar"
    members: list[Member]

    @property
    def keeper(self) -> Member:
        return next(m for m in self.members if m.keep)

    @property
    def reclaimable(self) -> int:
        return sum(m.record.size for m in self.members if not m.keep)


def is_twin(a: PhotoRecord, b: PhotoRecord, threshold: int) -> bool:
    if a.sha == b.sha:
        return True
    pd = hamming(from_hex(a.phash), from_hex(b.phash))
    dd = hamming(from_hex(a.dhash), from_hex(b.dhash))
    return pd <= threshold and dd <= threshold + DHASH_SLACK


def find_groups(records: list[PhotoRecord], threshold: int) -> tuple[list[list[int]], int]:
    """Return index groups of size >= 2, plus the number of hash comparisons spent."""
    uf = UnionFind(len(records))
    by_sha: dict[str, int] = {}
    for i, rec in enumerate(records):
        first = by_sha.setdefault(rec.sha, i)
        if first != i:
            uf.union(first, i)

    tree: BKTree[int] = BKTree()
    for i in by_sha.values():
        tree.add(from_hex(records[i].phash), i)
    for i in by_sha.values():
        for _, j in tree.query(from_hex(records[i].phash), threshold):
            if j > i and is_twin(records[i], records[j], threshold):
                uf.union(i, j)

    groups = [sorted(g) for g in uf.groups() if len(g) > 1]
    groups.sort(key=lambda g: records[g[0]].path)
    return groups, tree.comparisons


def _rank(members: list[PhotoRecord]) -> list[tuple[float, list[str]]]:
    max_px = max(r.pixels for r in members) or 1
    max_sharp = max(r.sharpness for r in members) or 1.0
    max_size = max(r.size for r in members) or 1
    ranked = []
    for r in members:
        score = 0.45 * r.pixels / max_px + 0.40 * r.sharpness / max_sharp + 0.15 * r.size / max_size
        reasons = []
        if r.pixels == max_px:
            reasons.append("highest resolution")
        if r.sharpness == max_sharp:
            reasons.append("sharpest")
        if r.size == max_size:
            reasons.append("least compressed")
        if r.taken:
            score += 0.03
            reasons.append("original EXIF date")
        if _COPY_NAME.search(Path(r.path).stem):
            score -= 0.02
        ranked.append((round(score, 4), reasons))
    return ranked


def build_clusters(records: list[PhotoRecord], threshold: int) -> tuple[list[Cluster], int]:
    groups, comparisons = find_groups(records, threshold)
    clusters: list[Cluster] = []
    for cid, group in enumerate(groups, start=1):
        recs = [records[i] for i in group]
        ranked = _rank(recs)
        # Highest score wins; ties go to the oldest capture/mtime, then the shortest path.
        best = max(
            range(len(recs)),
            key=lambda k: (ranked[k][0], -(recs[k].mtime), -len(recs[k].path)),
        )
        keeper_hash = from_hex(recs[best].phash)
        members = [
            Member(
                record=rec,
                score=ranked[k][0],
                keep=k == best,
                distance=hamming(keeper_hash, from_hex(rec.phash)),
                reasons=ranked[k][1] if k == best else [],
            )
            for k, rec in enumerate(recs)
        ]
        members.sort(key=lambda m: (not m.keep, -m.score, m.record.path))
        kind = "exact" if len({r.sha for r in recs}) == 1 else "similar"
        clusters.append(Cluster(id=cid, kind=kind, members=members))
    clusters.sort(key=lambda c: -c.reclaimable)
    for cid, c in enumerate(clusters, start=1):
        c.id = cid
    return clusters, comparisons
