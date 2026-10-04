#!/usr/bin/env python3
"""
Profiling driver for the pyperformance `btree` benchmark.

pyperformance/pyperf spawn worker sub-processes and sanitise their
environment, which makes it awkward to attach `perf` with Python-level
symbols.  This driver imports the *unmodified* benchmark module and runs
its timed workload in-process, so:

    perf record -c 2000000 -g -- python3 -X perf scripts/profile_btree.py --loops 3

captures exactly the code pyperformance times, with Python frames visible
(via the 3.12 perf trampoline) alongside CPython internals.

Usage:
    profile_btree.py [--loops N] [--gc-only] [--bench PATH]
"""
import argparse
import importlib.util
import os
import sys
import time


def default_bench_path():
    # Locate bm_btree/run_benchmark.py inside the pyperformance install of
    # whichever interpreter is running this script.
    # the committed copy: <repo>/benchmark/original/bm_btree/run_benchmark.py (verbatim pyperformance 1.14.0)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "benchmark", "original", "bm_btree", "run_benchmark.py")


def load_bench(path):
    spec = importlib.util.spec_from_file_location("bm_btree", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loops", type=int, default=3)
    ap.add_argument("--gc-only", action="store_true")
    ap.add_argument("--bench", default=None, help="path to run_benchmark.py (default: benchmark/original)")
    args = ap.parse_args()

    path = args.bench or default_bench_path()
    bm = load_bench(path)
    print(f"benchmark module : {path}", file=sys.stderr)
    print(f"python           : {sys.executable} {sys.version.split()[0]}", file=sys.stderr)
    tramp = getattr(sys, "is_stack_trampoline_active", None)          # CPython >= 3.12 (-X perf)
    print(f"perf trampoline  : {tramp() if tramp else 'n/a (CPython < 3.12: no py:: frames in the profile)'}", file=sys.stderr)
    print(f"loops            : {args.loops}", file=sys.stderr)

    t0 = time.perf_counter()
    total = bm.run_bench(args.loops, args.gc_only)
    wall = time.perf_counter() - t0
    print(f"timed section    : {total:.3f} s over {args.loops} loops "
          f"({total/args.loops:.3f} s/loop); wall {wall:.3f} s", file=sys.stderr)


if __name__ == "__main__":
    main()
