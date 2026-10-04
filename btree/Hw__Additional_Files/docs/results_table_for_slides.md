# btree — final results (course VM: 1 vCPU Xeon E5-2630 v3, Ubuntu 22.04, CPython 3.12.15; pyperf 10 processes × 3 values)

## Software optimization: linear scan → binary search (bisect_left in C) over a per-node key list

| metric                                   | baseline          | optimized          | change            |
|------------------------------------------|-------------------|--------------------|-------------------|
| benchmark loop time (mean ± std dev)     | 3.11 s ± 0.05     | 1.94 s ± 0.03      | **1.60× faster, −37.6 %** |
| back to back, same session               | 3.07 s ± 0.03     | 1.94 s ± 0.03      | 1.59× faster      |
| bare-metal cross-check (CPython 3.12.3)  | 5.06 s ± 0.03     | 3.07 s ± 0.02      | 1.65× faster      |
| course threshold                         |                   |                    | 7 %  (met 5×)     |
| in-node search, time per call            | 1 008 ns          | 352 ns             | 2.9× faster (bare metal: 1 668 → 513 ns) |
| in-node search, share of the loop        | 50 %              | ~12 %              |                   |
| key comparisons per call (avg, 20 keys)  | 10.6 interpreted  | ~4–5 in C          |                   |
| calls to the search per loop             | 1.93 M            | 1.93 M             | unchanged         |
| garbage-collection work (gc-only variant)| 139 ms            | 150 ms             | unchanged (+10 ms for 10 150 extra lists) |
| lines changed in the benchmark           | —                 | +27 / −6 of 436    | one method + mirrored key list |
| tree / workload / results                | —                 | identical          | verified node by node at N = 200 000 |

## Hardware: Node Search Unit (NSU) — `nsrch rd, rs1 = node address, rs2 = query`

| metric                                   | value                                              |
|------------------------------------------|----------------------------------------------------|
| what it replaces                         | BNode.get_position (bisect_left semantics)         |
| parallelism                              | 16 lanes × 32-bit compare on each arriving cache line (16 keys); stops at the first line holding a key ≥ query |
| pipeline                                 | 2 stages per line: compare + valid mask (S1) → popcount-16, running position, found (S2) |
| instruction latency (simulated)          | 7–9 cycles issue → writeback, average 7.7 (3-cycle L1; 1.47 of the 2 lines needed on average) |
| occupancy                                | one instruction in flight; busy 9.3 cycles (drains the pre-requested line) |
| node block in memory                     | 32 × 32-bit entries = 128 B = 2 cache lines (key count + 31 keys); keys ≥ 2³² take the software path |
| area / power estimate (28 nm)            | ~3.5 k gate-eq. + ~220 flops, ~0.003–0.005 mm²; < 1 mW active at 1 GHz |
| target clock                             | ≥ 1.5 GHz ASIC (28 nm), ~400 MHz FPGA (32-bit comparators) |
| verification                             | Verilator, lint clean; 3 210 vectors (3 000 from the real tree) pass; latency histogram 7:1707 8:824 9:679 |
| projected loop time under CPython (est.) | VM: ≈ 1.81 s, ≈ 1.72× — bounded by the 157 ns Python call overhead per search and by the 1.56 s of the loop outside the search (Amdahl limit 1.99×); bare metal: ≈ 2.98 s, ≈ 1.69× (206 ns, 2.57 s, limit 1.96×) |
| where it really pays off                 | compiled callers: 7–9 cycles vs 20–40 cycles of branchy binary search per node |
| fixed 31-lane design it evolved from (also simulated) | waits for both lines, 31 comparators + 31 key registers: 10 cycles constant, ~6–7 k gate-eq. + ~1.2 k flops; same bandwidth; all 3 210 vectors pass (`sim_result_fixed.txt`) |

## Hardware counters, baseline, one benchmark loop

Course VM (in-process counters, `results/baseline/perf_phase_counters_release.txt`): 7.08 G cycles, 16.17 G instructions, **IPC 2.28**, L1 D miss rate 2.54 %, L2 demand-read miss 43.8 %; the LLC events are not virtualised. The LLC figures below are from the bare-metal host with the same CPU (`results/host_bare_metal/baseline/perf_stat_btree_rel.txt`, CPython 3.12.3).

| counter                 | short   | exact value        | derived                                   |
|-------------------------|---------|--------------------|-------------------------------------------|
| cycles                  | 15.4 G  | 15,435,490,470     | 6.4 s at 2.4 GHz (perf-instrumented run)  |
| instructions            | 22.3 G  | 22,290,154,106     | IPC 1.44 → instruction-bound, not memory-bound |
| L1 D-cache loads        | 6.50 G  | 6,497,042,780      |                                           |
| L1 D-cache load misses  | 141 M   | 140,619,376        | miss rate 2.16 %                          |
| LLC loads               | 52.4 M  | 52,428,135         |                                           |
| LLC load misses (→ DRAM)| 10.5 M  | 10,480,968         | 20.0 % of LLC loads; one DRAM access per ~1,470 cycles |
| branch instructions     | 3.94 G  | 3,940,387,170      |                                           |
| branch misses           | 12.4 M  | 12,363,758         | miss rate 0.31 %                          |

G = 10⁹ (billion), M = 10⁶ (million). Counters are multiplexed by perf (8 events on 4 counters), so each is a scaled estimate over ~50 % of the run — the percentages in the right column are the reliable figures.

### L2 (bare-metal host, `results/host_bare_metal/baseline/perf_stat_l2_btree_rel.txt`, Haswell `l2_rqsts` events; VM: 106.3 M reads, 43.8 % miss)

| counter                                  | short  | exact value  | derived                         |
|------------------------------------------|--------|--------------|---------------------------------|
| L2 demand data reads (L1 D misses)       | 107 M  | 107,385,919  |                                 |
| L2 demand data read hits                 | 65.5 M | 65,465,518   | 61.0 % hit                      |
| L2 demand data read misses (→ LLC)       | 41.8 M | 41,846,879   | 39.0 % miss rate on data reads  |
| L2 references, all types (data, code, prefetch, write-back) | 249 M | 249,270,869 |                    |
| L2 misses, all types                     | 87.8 M | 87,832,269   | 35.2 % miss rate overall        |

Cache-level summary, one baseline loop (~15 G cycles): L1 D 6.5 G loads, 2.2 % miss → L2 107 M data reads, 39 % miss → LLC 52 M loads, 20 % miss → DRAM 10.5 M lines. Each level filters the traffic by roughly two orders of magnitude before the next; per 1,000 L1 loads about 16 reach L2, 8 reach the LLC and under 2 reach DRAM.

## Flame graph comparison — Python frames, baseline vs optimized (bare-metal release build, same host and settings, 2 loops each)

Cycles attributed to each frame (inclusive, G = 10⁹), from the bare-metal release profiles `results/host_bare_metal/flamegraphs/*_release.summary.txt` (the VM's release build has no frame pointers; its debug-build profiles rank the functions identically).

| frame                                   | baseline | optimized | change  | share of run_once: base → opt |
|-----------------------------------------|---------:|----------:|--------:|:-----------------------------:|
| run_once (the timed loop)               | 24.31 G  | 16.86 G   | −31 %   | 100 % → 100 %                 |
| make_tree (inserts + recreate)          | 15.89 G  | 11.88 G   | −25 %   | 65 % → 70 %                   |
| BNode.insert_item                       | 12.47 G  |  8.44 G   | −32 %   | 51 % → 50 %                   |
| **BNode.get_position → get_position_opt** | **12.17 G** | **2.26 G** | **−81 %** | **50 % → 13 %**         |
| BTree.__getitem__ (lookups)             |  6.94 G  |  3.20 G   | −54 %   | 29 % → 19 %                   |
| BNode.search                            |  6.69 G  |  2.96 G   | −56 %   | 28 % → 18 %                   |
| BNode.delete                            |  1.39 G  |  1.28 G   |  −8 %   |  6 % →  8 %                   |
| BNode.is_full                           |  1.66 G  |  1.64 G   |  −1 %   |  7 % → 10 %                   |
| BTree.__iter__                          |  0.50 G  |  0.52 G   |  +5 %   |  2 % →  3 %                   |
| make_records (untimed, unchanged)       |  2.40 G  |  2.39 G   |   0 %   | outside the loop              |
| GC (gc_collect_main + visitors)         |  2.14 G  |  2.56 G   | +19 % * |  9 % → 15 %                   |
| allocator (PyObject_Malloc / Free)      |  0.84 G  |  0.43 G   | −49 %   |  3 % →  3 %                   |
| list iteration (listiter_next, enum_next) | 2.58 G |  0.09 G   | −97 %   | 11 % →  0.5 %                 |

\* GC: same object graph on both sides; GC cycles vary by about ±10 % between recordings of the same version (2.14–2.53 G seen for the baseline), so this row is noise, not an effect of the change.

Reading: the whole loop lost 8.2 G cycles; 10.0 G of that is get_position (the rest of the loop grew slightly
under perf's overhead). Frames that only *contain* the search — insert_item, search, __getitem__ — shrink by
the search's share and nothing more. Frames that never touch the search — delete, is_full, __iter__, GC,
make_records — are unchanged within noise (their *percentages* rise because the total shrank). The list-iteration
machinery (enumerate over the tuple list) disappears almost entirely: it was the scan itself. The allocator halves
because the scan no longer creates a position/int pair per iteration.
