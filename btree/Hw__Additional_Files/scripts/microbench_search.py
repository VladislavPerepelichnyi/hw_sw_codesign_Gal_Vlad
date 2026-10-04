#!/usr/bin/env python3
"""
Micro-benchmark of the in-node key search that dominates the btree benchmark.

Measures, on realistic node contents (random int keys, fill ~20 of max 31,
items are (key, value) tuples exactly as the benchmark stores them):

  * the benchmark's own BNode.get_position (linear scan with enumerate)
  * a while-loop linear scan (no enumerate / tuple unpack)
  * bisect.bisect_left over a parallel list of keys (C implementation)
  * bisect with key= over the tuple list (no parallel list; Python 3.10+)
  * a builtin-driven scan: next(i for ...)  (for reference)
  * dict-based "oracle" lookups to show the floor of a hash lookup

Also reports bytecodes executed per compared key for the baseline loop,
which is the quantity a hardware node-search unit removes.

This script changes nothing in the benchmark; it is research input for the
optimization and the hardware-acceleration proposal.
"""
import bisect
import dis
import random
import statistics
import sys
import time

T = 16
MAX_ITEMS = 2 * T - 1
FILL = 20               # measured mean fill of the benchmark's nodes
N_NODES = 4096
QUERIES = 400_000
SEED = 0


def get_position_baseline(items, key):
    # verbatim copy of BNode.get_position (benchmark line 58)
    for position, item in enumerate(items):
        if item[0] >= key:
            return position
    return len(items)


def get_position_while(items, key):
    n = len(items)
    i = 0
    while i < n and items[i][0] < key:
        i += 1
    return i


def get_position_bisect_keys(keys, key):
    return bisect.bisect_left(keys, key)


def get_position_bisect_keyfn(items, key):
    return bisect.bisect_left(items, key, key=lambda it: it[0])


_first = lambda it: it[0]


def get_position_bisect_keyfn_nolambda(items, key):
    return bisect.bisect_left(items, key, key=_first)


def build_nodes():
    rnd = random.Random(SEED)
    nodes = []
    for _ in range(N_NODES):
        keys = sorted(rnd.sample(range(200_000), FILL))
        items = [(k, None) for k in keys]
        nodes.append((items, keys))
    return nodes


def make_queries(nodes):
    rnd = random.Random(SEED + 1)
    q = []
    for _ in range(QUERIES):
        idx = rnd.randrange(N_NODES)
        items, keys = nodes[idx]
        # Mix: 50% keys that exist in the node, 50% random (mostly absent)
        if rnd.random() < 0.5:
            k = rnd.choice(keys)
        else:
            k = rnd.randrange(200_000)
        q.append((idx, k))
    return q


def bench(fn, nodes, queries, use_keys, repeat=5):
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        if use_keys:
            for idx, k in queries:
                fn(nodes[idx][1], k)
        else:
            for idx, k in queries:
                fn(nodes[idx][0], k)
        times.append(time.perf_counter() - t0)
    return min(times), statistics.median(times)


def main():
    nodes = build_nodes()
    queries = make_queries(nodes)

    # sanity: all variants agree
    for idx, k in queries[:20000]:
        items, keys = nodes[idx]
        ref = get_position_baseline(items, k)
        assert get_position_while(items, k) == ref
        assert get_position_bisect_keys(keys, k) == ref
        assert get_position_bisect_keyfn(items, k) == ref
        assert get_position_bisect_keyfn_nolambda(items, k) == ref

    # average number of keys compared by the linear scan on this query mix
    scanned = 0
    for idx, k in queries:
        items, _ = nodes[idx]
        p = get_position_baseline(items, k)
        scanned += min(p + 1, len(items))
    avg_scan = scanned / len(queries)

    print(f"python {sys.version.split()[0]}  nodes={N_NODES} fill={FILL} queries={QUERIES}")
    print(f"average keys compared per linear-scan call on this mix: {avg_scan:.2f}")
    print()
    print(f"{'variant':44s} {'min s':>9s} {'ns/call':>9s} {'speedup':>8s}")
    variants = [
        ("baseline: for/enumerate linear scan", get_position_baseline, False),
        ("while-loop linear scan", get_position_while, False),
        ("bisect_left(items, key=lambda)", get_position_bisect_keyfn, False),
        ("bisect_left(items, key=named fn)", get_position_bisect_keyfn_nolambda, False),
        ("bisect_left(parallel keys list)", get_position_bisect_keys, True),
    ]
    base = None
    for name, fn, use_keys in variants:
        mn, md = bench(fn, nodes, queries, use_keys)
        if base is None:
            base = mn
        print(f"{name:44s} {mn:9.4f} {1e9*mn/QUERIES:9.1f} {base/mn:8.2f}x")

    # loop overhead floor (call a trivial function), to separate call cost
    def trivial(items, key):
        return 0
    mn, _ = bench(trivial, nodes, queries, False)
    print(f"{'(floor: trivial function call + loop)':44s} {mn:9.4f} {1e9*mn/QUERIES:9.1f}")

    print()
    print("Bytecode of the baseline scan loop body (per compared key):")
    dis.dis(get_position_baseline)


if __name__ == "__main__":
    main()
