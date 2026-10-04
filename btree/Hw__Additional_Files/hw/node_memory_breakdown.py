#!/usr/bin/env python3
"""How one B-tree node is laid out in memory today vs. the hardware node block
-> hw/node_memory_breakdown.txt

Builds the real 200k-entry tree with the OPTIMIZED benchmark (BNode has the
`keys` list), picks a typical leaf and a typical internal node, and measures
every object they refer to with sys.getsizeof.  The last row of each table is
the contiguous 128-byte block (count + 31 keys, 32-bit entries) the NSU reads instead of the
scattered objects.

Usage: node_memory_breakdown.py [--num-nodes N]
"""
import argparse
import importlib.util
import os
import sys
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BENCH = os.path.join(ROOT, "benchmark", "optimized", "bm_btree", "run_benchmark.py")
MB = 2 ** 20


def load_bench(path):
    spec = importlib.util.spec_from_file_location("bm_btree_opt", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def record_size(rec):
    """the Record object, its __dict__ and the attribute values it owns"""
    return sys.getsizeof(rec) + sys.getsizeof(rec.__dict__) + sum(sys.getsizeof(v) for v in rec.__dict__.values())


def all_nodes(tree):
    stack = [tree.root]
    while stack:
        node = stack.pop()
        yield node
        if node.nodes is not None:
            stack.extend(node.nodes)


def table(title, node):
    n = len(node.items)
    children = 0 if node.nodes is None else len(node.nodes)
    rows = []

    def row(part, count, each, total, where):
        rows.append((part, count, each, total, where))

    bnode = sys.getsizeof(node)
    items_l = sys.getsizeof(node.items)
    keys_l = sys.getsizeof(node.keys)
    row("BNode object (3 slots: items, keys, nodes)", 1, bnode, bnode, "separate heap object")
    row(f"items list object (header + {n} pointers)", 1, items_l, items_l, "separate heap object")
    row(f"keys list object (header + {n} pointers)", 1, keys_l, keys_l, "separate heap object")
    book = bnode + items_l + keys_l
    allocs = 3
    if children:
        nodes_l = sys.getsizeof(node.nodes)
        row(f"nodes list object (header + {children} pointers)", 1, nodes_l, nodes_l, "separate heap object")
        book += nodes_l
        allocs += 1
    tup = sum(sys.getsizeof(it) for it in node.items)
    ints = sum(sys.getsizeof(k) for k in node.keys)
    recs = sum(record_size(r) for _k, r in node.items)
    row("(key, record) tuple", n, tup // n, tup, "separate heap objects, scattered")
    row("int key object (shared by tuple and keys list)", n, ints // n, ints, "separate heap objects, scattered")
    row("Record object + its __dict__ + attribute values", n, recs // n, recs, "separate heap objects, scattered")
    row(f"node bookkeeping (BNode + {allocs - 1} lists)", "", "", book, f"{allocs} allocations, not contiguous")
    row("everything the node refers to", "", "", book + tup + ints + recs, f"~{allocs + 2 * n + 1} allocations")
    blk = sys.getsizeof(array("I", [0] * 32)) - sys.getsizeof(array("I"))   # payload: 32 x 4 B
    row("HARDWARE node block: count + 31 keys, array(I) 32-bit", 1, blk, blk, "ONE contiguous chunk, 128 B aligned = 2 lines")

    kind = "leaf" if children == 0 else f"{children} children"
    print(f"\n{title}: {n} items, {kind}")
    print(f"  {'part':52s} {'count':>5s} {'bytes each':>10s} {'total':>8s}  where")
    for part, count, each, total, where in rows:
        print(f"  {part:52s} {str(count):>5s} {str(each):>10s} {total:>8d}  {where}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--num-nodes", type=int, default=None)
    args = ap.parse_args()
    bm = load_bench(BENCH)
    n = args.num_nodes or bm.NUM_NODES
    records = list(bm.make_records(n))
    import random
    random.seed(bm.RANDOM_SEED)             # make_tree shuffles with the global RNG: same tree every run
    tree = bm.make_tree(n, records)
    nodes = list(all_nodes(tree))

    # typical = the most common fill (minimum fill, 15 items, is by far the most frequent)
    leaf = next(x for x in nodes if x.nodes is None and len(x.items) == 15)
    internal = next((x for x in nodes if x.nodes is not None and len(x.items) == 15),
                    next(x for x in nodes if x.nodes is not None))
    table("typical leaf", leaf)
    table("typical internal node", internal)

    tuples = sum(sys.getsizeof(it) for x in nodes for it in x.items)
    ints = sum(sys.getsizeof(k) for x in nodes for k in x.keys if not -5 <= k <= 256)
    lists = sum(sys.getsizeof(x.items) + sys.getsizeof(x.keys) + (sys.getsizeof(x.nodes) if x.nodes is not None else 0)
                for x in nodes)
    bnodes = sum(sys.getsizeof(x) for x in nodes)
    print(f"\nwhole tree: {len(nodes):,} nodes; tree structure by getsizeof {(tuples + ints + lists + bnodes) / MB:.1f} MB "
          f"(tuples {tuples / MB:.1f} MB, int keys {ints / MB:.1f} MB, items + keys + nodes lists {lists / MB:.1f} MB, "
          f"BNodes {bnodes / MB:.1f} MB); records: 68 MB of RSS, see results/baseline/memory_footprint_release.txt")
    print(f"hardware blocks for all nodes: {len(nodes):,} x 128 B = {len(nodes) * 128 / 1e6:.1f} MB, replacing the keys lists")


if __name__ == "__main__":
    main()
