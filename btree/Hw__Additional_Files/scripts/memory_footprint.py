#!/usr/bin/env python3
"""Working-set measurement of the btree benchmark -> results/baseline/memory_footprint_release.txt

What is measured (CPython 3.12, 64-bit):
  * RSS of the process (psutil) at three points: before anything, after
    make_records (the 200,000 Record objects), after make_tree (the B-tree built
    on top of them).  The deltas are the memory the records and the tree
    structure really occupy, allocator overhead included.
  * sys.getsizeof over the tree's own objects: BNodes, their lists, the
    (key, record) tuples and the int key objects -- the part of the working set
    the node search touches.  Records are counted separately: the search never
    reads them.
  * the cache sizes of this host (sysfs), to put the numbers next to L1/L2/L3.

Usage: memory_footprint.py [--bench PATH] [--num-nodes N]
       (default benchmark: benchmark/original/bm_btree/run_benchmark.py)
"""
import argparse
import importlib.util
import os
import sys

MB = 2 ** 20


def default_bench_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "benchmark", "original", "bm_btree", "run_benchmark.py")


def load_bench(path):
    spec = importlib.util.spec_from_file_location("bm_btree", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rss_mb():
    import psutil
    return psutil.Process().memory_info().rss / MB


def cache_sizes():
    out = {}
    base = "/sys/devices/system/cpu/cpu0/cache"
    try:
        for idx in sorted(os.listdir(base)):
            if not idx.startswith("index"):
                continue
            typ = open(os.path.join(base, idx, "type")).read().strip()
            lvl = open(os.path.join(base, idx, "level")).read().strip()
            size = open(os.path.join(base, idx, "size")).read().strip()
            name = {"1": "L1d" if typ == "Data" else "L1i", "2": "L2", "3": "L3"}.get(lvl, "L" + lvl)
            if name != "L1i":
                out[name] = size
    except OSError:
        pass
    return out


def cpu_model():
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip().replace("(R)", "").replace("CPU ", "")
    except OSError:
        pass
    return "unknown CPU"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default=None)
    ap.add_argument("--num-nodes", type=int, default=None)
    args = ap.parse_args()
    bm = load_bench(args.bench or default_bench_path())
    n = args.num_nodes or bm.NUM_NODES

    rss0 = rss_mb()
    records = list(bm.make_records(n))
    rss1 = rss_mb()
    import random
    random.seed(bm.RANDOM_SEED)             # make_tree shuffles with the global RNG: same tree every run
    tree = bm.make_tree(n, records)
    rss2 = rss_mb()

    # ---- walk the tree: every object the node search can touch
    nodes = lists = tuples = ints = 0
    n_nodes = n_items = 0
    small_int_cache = 0                      # CPython interns -5..256: those ints cost nothing extra
    stack = [tree.root]
    while stack:
        node = stack.pop()
        n_nodes += 1
        nodes += sys.getsizeof(node)
        lists += sys.getsizeof(node.items)
        if hasattr(node, "keys"):            # optimized benchmark: the parallel keys list
            lists += sys.getsizeof(node.keys)
        for key, _rec in node.items:
            n_items += 1
            tuples += sys.getsizeof((key, None))
            if -5 <= key <= 256:
                small_int_cache += 1
            else:
                ints += sys.getsizeof(key)
        if node.nodes is not None:
            lists += sys.getsizeof(node.nodes)
            stack.extend(node.nodes)
    structure = nodes + lists + tuples + ints

    caches = cache_sizes()
    print(f"RSS: baseline {rss0:.1f} MB -> +records {rss1:.1f} MB -> +tree {rss2:.1f} MB")
    print(f"records ({n // 1000}k Record objects + attrs): {rss1 - rss0:.1f} MB")
    print(f"tree structure RSS delta:              {rss2 - rss1:.1f} MB")
    print(f"tree structure by getsizeof: {structure / MB:.1f} MB = nodes {nodes / MB:.1f} + lists {lists / MB:.1f} "
          f"+ item tuples {tuples / MB:.1f} + key int objects {ints / MB:.1f}   ({n_nodes:,} nodes, {n_items:,} items)")
    print(f"key int objects that are NOT the interned small-int cache: {n_items - small_int_cache} of {n_items}")
    print("per-compared-key pointer chain: items list -> tuple -> int object -> value  "
          f"(3 dependent loads, {sys.getsizeof((0, None))} B tuple, {sys.getsizeof(1000)} B int)")
    print(f"caches: L1d {caches.get('L1d', '?')}/core, L2 {caches.get('L2', '?')}/core, "
          f"L3 {caches.get('L3', '?')}/socket ({cpu_model()})")


if __name__ == "__main__":
    main()
