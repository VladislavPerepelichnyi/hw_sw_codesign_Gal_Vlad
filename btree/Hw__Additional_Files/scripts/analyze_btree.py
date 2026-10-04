#!/usr/bin/env python3
"""
Research instrumentation for the pyperformance `btree` benchmark.

Runs the *unmodified* benchmark code and answers, with numbers:

  1. Where does wall time go?   (per-phase timing of run_once)
  2. How much is garbage collection?  (explicit gc.collect + automatic
     collections triggered during the build, per generation)
  3. What does the tree look like?  (nodes, height, fill, key width)
  4. How much work is the in-node key search?  (get_position calls and
     the number of tuple comparisons it performs, per phase)
  5. Function-level ranking (cProfile, relative only)

Nothing here modifies the benchmark; the counters are installed by
monkey-patching a *copy* of BNode.get_position for the counting pass only.

Usage:  analyze_btree.py [--repeat N] [--bench PATH] [--no-cprofile]
"""
import argparse
import cProfile
import gc
import importlib.util
import io
import os
import pstats
import random
import statistics
import sys
import time


# --------------------------------------------------------------------------
def default_bench_path():
    # the committed copy: <repo>/benchmark/original/bm_btree/run_benchmark.py (verbatim pyperformance 1.14.0)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "benchmark", "original", "bm_btree", "run_benchmark.py")


def load_bench(path):
    spec = importlib.util.spec_from_file_location("bm_btree", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
class GCMonitor:
    """Accumulates time spent inside automatic GC runs, per generation."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.count = [0, 0, 0]
        self.time = [0.0, 0.0, 0.0]
        self.collected = [0, 0, 0]
        self._t = None

    def __call__(self, phase, info):
        if phase == "start":
            self._t = time.perf_counter()
        else:
            g = info["generation"]
            self.count[g] += 1
            self.time[g] += time.perf_counter() - self._t
            self.collected[g] += info["collected"]

    def install(self):
        gc.callbacks.append(self)

    def remove(self):
        gc.callbacks.remove(self)

    def summary(self):
        return {
            "gen0": (self.count[0], self.time[0]),
            "gen1": (self.count[1], self.time[1]),
            "gen2": (self.count[2], self.time[2]),
            "total_time": sum(self.time),
        }


# --------------------------------------------------------------------------
def phased_run(bm, records, gcmon):
    """Re-implements run_once() with a timer around each phase.
    Same operations, same order, same RNG seeding as the benchmark."""
    N = bm.NUM_NODES
    t = {}
    gcmon.reset()

    T = time.perf_counter
    t_start = T()

    # --- make_tree ---------------------------------------------------
    ids = list(range(N))
    random.shuffle(ids)
    tree = bm.BTree()
    t0 = T()
    for node_id in ids:
        tree[node_id] = records[node_id]
    t["1 build (insert 200k)"] = T() - t0
    gc_build = gcmon.summary()

    remake_ids = range(int(N * bm.RECREATE_FRACTION))
    t0 = T()
    for node_id in remake_ids:
        del tree[node_id]
    t["2 delete 40k"] = T() - t0
    t0 = T()
    for node_id in remake_ids:
        tree[node_id] = records[node_id]
    t["3 re-insert 40k"] = T() - t0
    gc_maketree = gcmon.summary()

    # --- explicit gc.collect -----------------------------------------
    t0 = T()
    gc.collect()
    t["4 gc.collect()"] = T() - t0

    # --- full iteration ----------------------------------------------
    t0 = T()
    d_total = 0.0
    for key in tree:
        node = tree[key]
        d_total += node.d
    t["5 iterate all + lookup each"] = T() - t0

    # --- random lookups ----------------------------------------------
    t0 = T()
    num_lookup = max(200, N // 20)
    d_total = 0
    rnd = random.Random(bm.RANDOM_SEED)
    for i in range(num_lookup):
        node_id = rnd.randint(0, N)
        node = tree.get(node_id)
        if node is not None:
            d_total += node.d
    t["6 random lookups 10k"] = T() - t0

    t["TOTAL (run_once)"] = T() - t_start
    return t, gc_build, gc_maketree, tree


# --------------------------------------------------------------------------
def tree_stats(bm, tree):
    nodes = 0
    leaves = 0
    items = 0
    fill = []
    depth_hist = {}
    max_key = 0

    def walk(n, d):
        nonlocal nodes, leaves, items, max_key
        nodes += 1
        items += len(n.items)
        fill.append(len(n.items))
        depth_hist[d] = depth_hist.get(d, 0) + 1
        if n.items:
            max_key = max(max_key, n.items[-1][0])
        if n.is_leaf():
            leaves += 1
        else:
            for c in n.nodes:
                walk(c, d + 1)

    walk(tree.root, 0)
    return {
        "nodes": nodes,
        "leaves": leaves,
        "internal": nodes - leaves,
        "items": items,
        "height (levels below root)": tree.root.get_level(),
        "min_degree t": bm.BNode.minimum_degree,
        "max items/node": 2 * bm.BNode.minimum_degree - 1,
        "mean fill": statistics.mean(fill),
        "median fill": statistics.median(fill),
        "min fill": min(fill),
        "max fill": max(fill),
        "nodes per depth": dict(sorted(depth_hist.items())),
        "max key": max_key,
        "key bits needed": max_key.bit_length(),
    }


# --------------------------------------------------------------------------
class SearchCounter:
    """Replaces BNode.get_position with a counting twin (same algorithm)."""

    def __init__(self, bm):
        self.bm = bm
        self.orig = bm.BNode.get_position
        self.reset()

    def reset(self):
        self.calls = 0
        self.compares = 0      # tuple[0] >= key comparisons executed
        self.hist = {}         # items scanned before returning -> count

    def install(self):
        counter = self

        def get_position(self_node, key):
            counter.calls += 1
            scanned = 0
            for position, item in enumerate(self_node.items):
                scanned += 1
                if item[0] >= key:
                    counter.compares += scanned
                    counter.hist[scanned] = counter.hist.get(scanned, 0) + 1
                    return position
            counter.compares += scanned
            counter.hist[scanned] = counter.hist.get(scanned, 0) + 1
            return len(self_node.items)

        self.bm.BNode.get_position = get_position

    def remove(self):
        self.bm.BNode.get_position = self.orig

    def snapshot(self):
        return (self.calls, self.compares)


def counting_run(bm, records):
    """Same phases as phased_run, but records get_position work per phase."""
    N = bm.NUM_NODES
    sc = SearchCounter(bm)
    sc.install()
    per_phase = {}

    def mark(name, before):
        c1, k1 = sc.snapshot()
        per_phase[name] = (c1 - before[0], k1 - before[1])
        return (c1, k1)

    try:
        random.seed(bm.RANDOM_SEED)
        ids = list(range(N))
        random.shuffle(ids)
        tree = bm.BTree()
        s = sc.snapshot()
        for node_id in ids:
            tree[node_id] = records[node_id]
        s = mark("1 build (insert 200k)", s)
        remake_ids = range(int(N * bm.RECREATE_FRACTION))
        for node_id in remake_ids:
            del tree[node_id]
        s = mark("2 delete 40k", s)
        for node_id in remake_ids:
            tree[node_id] = records[node_id]
        s = mark("3 re-insert 40k", s)
        gc.collect()
        for key in tree:
            node = tree[key]
        s = mark("5 iterate all + lookup each", s)
        rnd = random.Random(bm.RANDOM_SEED)
        for i in range(max(200, N // 20)):
            tree.get(rnd.randint(0, N))
        s = mark("6 random lookups 10k", s)
    finally:
        sc.remove()
    return per_phase, sc


# --------------------------------------------------------------------------
def cprofile_run(bm, records):
    random.seed(bm.RANDOM_SEED)
    pr = cProfile.Profile()
    pr.enable()
    bm.run_once(False, records)
    pr.disable()
    buf = io.StringIO()
    st = pstats.Stats(pr, stream=buf)
    st.sort_stats("tottime").print_stats(18)
    return buf.getvalue()


# --------------------------------------------------------------------------
def fmt_phase_table(runs):
    keys = list(runs[0].keys())
    total_med = statistics.median(r["TOTAL (run_once)"] for r in runs)
    lines = [f"{'phase':32s} {'median s':>10s} {'min s':>10s} {'% total':>8s}"]
    for k in keys:
        vals = [r[k] for r in runs]
        med = statistics.median(vals)
        lines.append(f"{k:32s} {med:10.4f} {min(vals):10.4f} {100*med/total_med:8.1f}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--bench", default=None)
    ap.add_argument("--no-cprofile", action="store_true")
    args = ap.parse_args()

    path = args.bench or default_bench_path()
    bm = load_bench(path)
    out = []
    P = out.append

    P("=" * 78)
    P("btree benchmark research report")
    P("=" * 78)
    P(f"benchmark : {path}")
    P(f"python    : {sys.executable} {sys.version.split()[0]}")
    P(f"NUM_NODES={bm.NUM_NODES}  RECREATE_FRACTION={bm.RECREATE_FRACTION}  "
      f"min_degree={bm.BNode.minimum_degree}  gc.threshold={gc.get_threshold()}")
    P("")

    records = list(bm.make_records(bm.NUM_NODES))

    # ---- 1. reference: the benchmark's own run_once -----------------------
    ref = []
    for _ in range(args.repeat):
        random.seed(bm.RANDOM_SEED)
        ref.append(bm.run_once(False, records))
    P("1. Reference: benchmark's own run_once(gc_only=False)")
    P(f"   values: {['%.3f' % v for v in ref]}  median {statistics.median(ref):.3f} s")
    refgc = []
    for _ in range(args.repeat):
        random.seed(bm.RANDOM_SEED)
        refgc.append(bm.run_once(True, records))
    P(f"   gc_only variant (only gc.collect timed): median {statistics.median(refgc):.3f} s")
    P("")

    # ---- 2. phase timing --------------------------------------------------
    gcmon = GCMonitor()
    gcmon.install()
    runs, gcb, gcm = [], None, None
    tree = None
    for _ in range(args.repeat):
        random.seed(bm.RANDOM_SEED)
        t, gcb, gcm, tree = phased_run(bm, records, gcmon)
        runs.append(t)
    gcmon.remove()
    P("2. Per-phase timing (same ops as run_once, median of %d)" % args.repeat)
    P(fmt_phase_table(runs))
    P("")
    P("   Automatic GC triggered inside make_tree (last run):")
    for g in ("gen0", "gen1", "gen2"):
        c, s = gcm[g]
        P(f"     {g}: {c:5d} collections, {s:8.4f} s")
    P(f"     automatic GC total inside make_tree : {gcm['total_time']:.4f} s "
      f"({100*gcm['total_time']/runs[-1]['TOTAL (run_once)']:.1f}% of run_once)")
    P(f"     explicit gc.collect()               : {runs[-1]['4 gc.collect()']:.4f} s")
    P(f"     tracked objects after build         : {len(gc.get_objects())}")
    P("")

    # ---- 3. tree shape ----------------------------------------------------
    P("3. Tree shape after make_tree")
    for k, v in tree_stats(bm, tree).items():
        if isinstance(v, float):
            P(f"   {k:28s}: {v:.2f}")
        else:
            P(f"   {k:28s}: {v}")
    P("")
    del tree

    # ---- 4. in-node search work -------------------------------------------
    per_phase, sc = counting_run(bm, records)
    P("4. In-node linear key search (BNode.get_position) work per phase")
    P(f"   {'phase':32s} {'calls':>10s} {'compares':>12s} {'cmp/call':>9s}")
    tc = tk = 0
    for k, (c, cmp_) in per_phase.items():
        tc += c
        tk += cmp_
        P(f"   {k:32s} {c:10d} {cmp_:12d} {cmp_/max(c,1):9.2f}")
    P(f"   {'TOTAL':32s} {tc:10d} {tk:12d} {tk/max(tc,1):9.2f}")
    P("   items scanned per call (histogram, top 12 buckets):")
    hist = sorted(sc.hist.items())
    total_calls = sum(sc.hist.values())
    cum = 0
    for scanned, n in hist[:12]:
        cum += n
        P(f"     {scanned:3d} items : {n:9d}  ({100*n/total_calls:5.1f}%, cum {100*cum/total_calls:5.1f}%)")
    if len(hist) > 12:
        P(f"     ... up to {hist[-1][0]} items")
    P("")

    # ---- 5. cProfile -------------------------------------------------------
    if not args.no_cprofile:
        P("5. cProfile of one run_once (tottime ranking; absolute times inflated by profiler)")
        P(cprofile_run(bm, records))

    text = "\n".join(out)
    print(text)


if __name__ == "__main__":
    main()
