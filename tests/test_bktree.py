from __future__ import annotations

import random

from twinlens.bktree import BKTree
from twinlens.cluster import UnionFind
from twinlens.hashing import hamming


def test_bktree_matches_brute_force():
    rng = random.Random(42)
    keys = [rng.getrandbits(64) for _ in range(600)]
    # plant near neighbours so queries have something to find
    keys += [k ^ (1 << rng.randrange(64)) ^ (1 << rng.randrange(64)) for k in keys[:150]]
    tree: BKTree[int] = BKTree()
    for i, k in enumerate(keys):
        tree.add(k, i)
    assert len(tree) == len(keys)

    for radius in (0, 3, 8):
        for q in rng.sample(keys, 40):
            got = sorted(i for _, i in tree.query(q, radius))
            want = sorted(i for i, k in enumerate(keys) if hamming(q, k) <= radius)
            assert got == want


def test_bktree_prunes_work():
    rng = random.Random(1)
    tree: BKTree[int] = BKTree()
    for i in range(3000):
        tree.add(rng.getrandbits(64), i)
    for _ in range(50):
        list(tree.query(rng.getrandbits(64), 6))
    assert tree.comparisons / 50 < 3000 * 0.5  # far fewer than a linear scan


def test_bktree_identical_keys_share_a_node():
    tree: BKTree[str] = BKTree()
    tree.add(5, "a")
    tree.add(5, "b")
    assert sorted(item for _, item in tree.query(5, 0)) == ["a", "b"]


def test_empty_tree_query():
    assert list(BKTree().query(123, 10)) == []


def test_union_find_groups():
    uf = UnionFind(6)
    uf.union(0, 1)
    uf.union(1, 2)
    uf.union(4, 5)
    assert not uf.union(0, 2)
    groups = sorted(sorted(g) for g in uf.groups())
    assert groups == [[0, 1, 2], [3], [4, 5]]
