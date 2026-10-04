#!/usr/bin/env python3
"""Hardware counters per phase of the btree loop -> results/baseline/perf_phase_counters_release.txt

perf stat can only count the whole process. This script opens the same hardware
counters itself (perf_event_open, user space only, no perf binary needed) and
reads them around each phase of run_once -- the same operations, order and RNG
seeding as the benchmark -- so cache misses can be attributed to a phase.

Counters: cycles, instructions, L1D loads / load misses, LLC loads / load misses.
Extra diagnostic rows:
  * "iterate + lookup, records NOT read": the iteration phase with the `node.d`
    access removed -- the same 200k node searches without touching the records.
  * "touch every record once": reads one attribute of every record, no tree at all.
  * "build with automatic GC disabled": the inserts without the gen-1/gen-2
    collections that CPython runs during the build.

Usage: phase_counters.py [--bench PATH] [--num-nodes N]
"""
import argparse
import ctypes
import fcntl
import gc
import importlib.util
import os
import random
import struct
import sys

SYS_perf_event_open = 298                      # x86_64
PERF_TYPE_HARDWARE, PERF_TYPE_HW_CACHE = 0, 3
EVENTS = [                                     # (name, type, config)
    ("cycles",       PERF_TYPE_HARDWARE, 0),
    ("instructions", PERF_TYPE_HARDWARE, 1),
    ("L1D loads",    PERF_TYPE_HW_CACHE, 0x00000),          # L1D | read | access
    ("L1D misses",   PERF_TYPE_HW_CACHE, 0x10000),          # L1D | read | miss
    ("LLC loads",    PERF_TYPE_HW_CACHE, 0x00002),          # LL  | read | access
    ("LLC misses",   PERF_TYPE_HW_CACHE, 0x10002),          # LL  | read | miss
]
IOC_ENABLE, IOC_DISABLE, IOC_RESET = 0x2400, 0x2401, 0x2403


class Counters:
    def __init__(self):
        libc = ctypes.CDLL(None, use_errno=True)
        self.fds = []
        self.unavailable = []
        for name, typ, cfg in EVENTS:
            attr = bytearray(128)
            # type, size, config, sample_period, sample_type, read_format, flags
            # read_format = TOTAL_TIME_ENABLED | TOTAL_TIME_RUNNING (to scale if multiplexed)
            # flags = disabled | exclude_kernel | exclude_hv  (perf_event_paranoid = 2 allows this)
            struct.pack_into("<IIQQQQQ", attr, 0, typ, 128, cfg, 0, 0, 3, 1 | (1 << 5) | (1 << 6))
            buf = ctypes.create_string_buffer(bytes(attr), 128)
            fd = libc.syscall(SYS_perf_event_open, buf, 0, -1, -1, 0)
            if fd < 0:
                e = ctypes.get_errno()
                if name in ("cycles", "instructions"):
                    sys.exit(f"perf_event_open failed for {name}: {os.strerror(e)} (errno {e}) -- no usable PMU")
                print(f"note: {name} not available on this PMU ({os.strerror(e)}); column shown as n/a", file=sys.stderr)
                self.unavailable.append(name)
                fd = -1
            self.fds.append(fd)

    def start(self):
        for fd in self.fds:
            if fd >= 0: fcntl.ioctl(fd, IOC_RESET, 0)
        for fd in self.fds:
            if fd >= 0: fcntl.ioctl(fd, IOC_ENABLE, 0)

    def stop(self):
        for fd in self.fds:
            if fd >= 0: fcntl.ioctl(fd, IOC_DISABLE, 0)
        out = []
        for fd in self.fds:
            if fd < 0:
                out.append(None); continue
            value, enabled, running = struct.unpack("<QQQ", os.read(fd, 24))
            out.append(value * enabled / running if running else 0.0)
        return out


def load_bench(path):
    spec = importlib.util.spec_from_file_location("bm_btree", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default=None)
    ap.add_argument("--num-nodes", type=int, default=None)
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bm = load_bench(args.bench or os.path.join(root, "benchmark", "original", "bm_btree", "run_benchmark.py"))
    N = args.num_nodes or bm.NUM_NODES
    ctr = Counters()
    rows = []

    def measure(name, fn):
        ctr.start()
        fn()
        rows.append((name, ctr.stop()))

    records = list(bm.make_records(N))
    random.seed(bm.RANDOM_SEED)

    # ---- the loop, phase by phase (same operations and order as run_once)
    ids = list(range(N)); random.shuffle(ids)
    tree = bm.BTree()
    def build():
        for node_id in ids:
            tree[node_id] = records[node_id]
    measure("1 build (insert 200k)", build)
    remake = range(int(N * bm.RECREATE_FRACTION))
    def delete():
        for node_id in remake:
            del tree[node_id]
    measure("2 delete 40k", delete)
    def reinsert():
        for node_id in remake:
            tree[node_id] = records[node_id]
    measure("3 re-insert 40k", reinsert)
    measure("4 gc.collect()", gc.collect)
    def iterate():
        d_total = 0.0
        for key in tree:
            node = tree[key]
            d_total += node.d
    measure("5 iterate all + lookup each (reads node.d)", iterate)
    def lookups():
        num_lookup = max(200, N // 20)
        d_total = 0
        rnd = random.Random(bm.RANDOM_SEED)
        for i in range(num_lookup):
            node_id = rnd.randint(0, N)
            node = tree.get(node_id)
            if node is not None:
                d_total += node.d
    measure("6 random lookups 10k (reads node.d)", lookups)

    # ---- diagnostics: separate the node search from the record access
    def iterate_no_record():
        for key in tree:
            node = tree[key]            # returns the record object; its memory is not read
    measure("D1 iterate all + lookup each, records NOT read", iterate_no_record)
    def touch_records():
        d_total = 0.0
        for r in records:
            d_total += r.d
    measure("D2 touch every record once (no tree)", touch_records)
    # the build again with the automatic GC switched off: what the inserts alone cost
    del tree
    gc.collect()
    tree2 = bm.BTree()
    def build_nogc():
        gc.disable()
        try:
            for node_id in ids:
                tree2[node_id] = records[node_id]
        finally:
            gc.enable()
    measure("D3 build (insert 200k) with automatic GC disabled", build_nogc)

    # ---- report
    G, M = 1e9, 1e6
    tot = [None if any(r[i] is None for _, r in rows[:6]) else sum(r[i] for _, r in rows[:6]) for i in range(len(EVENTS))]
    if ctr.unavailable:
        print("note: not available on this PMU, shown as n/a: " + ", ".join(ctr.unavailable))
    print(f"hardware counters per phase, one loop of run_once, user space only (perf_event_open; "
          f"{sys.implementation.name} {sys.version.split()[0]}, N = {N:,})")
    print()
    hdr = f"  {'phase':50s} {'cycles':>8s} {'instr':>8s} {'IPC':>5s} {'L1D ld':>8s} {'L1D miss':>9s} {'%':>5s} {'LLC ld':>8s} {'LLC miss':>9s} {'%':>5s}"
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    def f(v, scale, unit, w):
        return f"{'n/a':>{w}s}  " if v is None else f"{v / scale:{w}.{2 if scale == G else 1}f} {unit}"
    def pct(a, b, w):
        return f"{'n/a':>{w}s}" if a is None or b is None else f"{100 * a / b if b else 0:{w}.2f}"
    def line(name, r):
        cyc, ins, l1, l1m, llc, llcm = r
        ipc = f"{'n/a':>5s}" if cyc is None or ins is None else f"{ins / cyc if cyc else 0:5.2f}"
        print(f"  {name:50s} {f(cyc, G, 'G', 6)} {f(ins, G, 'G', 6)} {ipc} {f(l1, G, 'G', 6)} {f(l1m, M, 'M', 7)} "
              f"{pct(l1m, l1, 5)} {f(llc, M, 'M', 6)} {f(llcm, M, 'M', 7)} {pct(llcm, llc, 5)}")
    for name, r in rows[:6]:
        line(name, r)
    line("total, phases 1-6 (= the timed loop)", tot)
    print()
    print("  diagnostics (not part of the loop):")
    for name, r in rows[6:]:
        line(name, r)
    print()
    if any(r[5] is None for _, r in rows):
        print("  (LLC events not available on this PMU: no per-phase LLC-miss attribution)")
        return
    print(f"  LLC misses per loop: build {rows[0][1][5] / M:.2f} M (inserts alone, GC off: {rows[8][1][5] / M:.2f} M, "
          f"so ~{(rows[0][1][5] - rows[8][1][5]) / M:.2f} M are the automatic collections), delete {rows[1][1][5] / M:.2f} M, "
          f"re-insert {rows[2][1][5] / M:.2f} M, gc.collect {rows[3][1][5] / M:.2f} M, iterate {rows[4][1][5] / M:.2f} M "
          f"(node searches alone, D1: {rows[6][1][5] / M:.2f} M), random lookups {rows[5][1][5] / M:.2f} M; "
          f"touching every record once in allocation order costs {rows[7][1][5] / M:.2f} M (prefetched).")


if __name__ == "__main__":
    main()
