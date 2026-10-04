#!/usr/bin/env python3
"""
Equivalence test: the optimized btree benchmark must build exactly the same
tree as the original, node for node, and answer every query identically.

Both modules are loaded from their files, driven with the same records and
the same shuffled key order (same RANDOM_SEED), through the same operations
the benchmark performs: insert all keys, delete the lowest 20%, re-insert
them, iterate, and look up. After every phase the two trees are compared
structurally, and the optimized tree's `keys` list is checked against its
`items` list in every node.

Run (from "Hw__Additional_Files/"):  ../.venv/bin/python tests/test_btree_equivalence.py [--num-nodes N]
"""
import argparse
import importlib.util
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def original_path():
    return os.path.join(ROOT, "benchmark", "original", "bm_btree", "run_benchmark.py")


def structure(node):
    """Nested tuple of (keys, children) - values compared by identity of record.a"""
    keys = tuple(item[0] for item in node.items)
    vals = tuple(item[1].a for item in node.items)
    if node.is_leaf():
        return (keys, vals, None)
    return (keys, vals, tuple(structure(c) for c in node.nodes))


def check_keys_mirror(node, path="root"):
    """In the optimized tree, node.keys must equal [item[0] for item in node.items] everywhere."""
    expect = [item[0] for item in node.items]
    assert node.keys == expect, f"{path}: keys {node.keys[:5]}... != items {expect[:5]}..."
    assert expect == sorted(expect), f"{path}: items not sorted"
    if not node.is_leaf():
        assert len(node.nodes) == len(node.items) + 1, f"{path}: child count"
        for i, c in enumerate(node.nodes):
            check_keys_mirror(c, f"{path}/{i}")


def compare(orig_tree, opt_tree, phase):
    a, b = structure(orig_tree.root), structure(opt_tree.root)
    assert a == b, f"[{phase}] tree structures differ"
    check_keys_mirror(opt_tree.root)
    assert len(orig_tree) == len(opt_tree), f"[{phase}] len differs"
    assert orig_tree.root.get_level() == opt_tree.root.get_level(), f"[{phase}] height differs"
    assert list(orig_tree) == list(opt_tree), f"[{phase}] iteration order differs"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--num-nodes", type=int, default=None, help="override NUM_NODES (default: benchmark's 200000)")
    args = ap.parse_args()

    orig = load(original_path(), "bm_orig")
    opt = load(os.path.join(ROOT, "benchmark", "optimized", "bm_btree", "run_benchmark.py"), "bm_opt")
    if args.num_nodes:
        orig.NUM_NODES = opt.NUM_NODES = args.num_nodes
    N = orig.NUM_NODES

    assert opt.BNode.get_position is opt.BNode.get_position_opt, "get_position must alias get_position_opt"
    assert "bisect_left" in opt.BNode.get_position_opt.__code__.co_names, "get_position_opt must use bisect_left"

    records = list(orig.make_records(N))          # both trees share the same record objects

    # --- phase 1: build in the same shuffled order (make_tree uses random.shuffle after seed) ---
    random.seed(orig.RANDOM_SEED)
    ids = list(range(N)); random.shuffle(ids)
    t_orig, t_opt = orig.BTree(), opt.BTree()
    for k in ids:
        t_orig[k] = records[k]; t_opt[k] = records[k]
    compare(t_orig, t_opt, "build")

    # --- phase 2: delete lowest 20% ---
    remake = range(int(N * orig.RECREATE_FRACTION))
    for k in remake:
        del t_orig[k]; del t_opt[k]
    compare(t_orig, t_opt, "delete")

    # --- phase 3: re-insert ---
    for k in remake:
        t_orig[k] = records[k]; t_opt[k] = records[k]
    compare(t_orig, t_opt, "re-insert")

    # --- phase 4: lookups, hits and misses, including keys beyond the range ---
    rnd = random.Random(orig.RANDOM_SEED)
    hits = misses = 0
    for _ in range(max(200, N // 20)):
        k = rnd.randint(-5, N + 5)
        a, b = t_orig.get(k), t_opt.get(k)
        assert a is b, f"lookup {k}: {a} vs {b}"
        assert (k in t_orig) == (k in t_opt)
        if a is None: misses += 1
        else: hits += 1
    for k in (0, 1, N // 2, N - 1):
        assert t_orig[k] is t_opt[k]
    for k in (-1, N, N + 12345):
        for t in (t_orig, t_opt):
            try:
                t[k]; assert False, "expected KeyError"
            except KeyError:
                pass

    # --- phase 5: replace values for existing keys (insert_item's 'same key' branches) ---
    for k in (5, N // 3, N - 2):
        t_orig[k] = records[(k + 1) % N]; t_opt[k] = records[(k + 1) % N]
    compare(t_orig, t_opt, "replace")

    # --- phase 6: the benchmark's own timed function runs on both and returns a float ---
    random.seed(orig.RANDOM_SEED); assert isinstance(orig.run_once(False, records), float)
    random.seed(opt.RANDOM_SEED);  assert isinstance(opt.run_once(False, records), float)

    print(f"OK: original and optimized trees identical through build/delete/re-insert/replace "
          f"(N={N}, {len(t_opt)} items, height {t_opt.root.get_level()}, "
          f"{hits} lookup hits, {misses} misses, all equal).")


if __name__ == "__main__":
    main()
