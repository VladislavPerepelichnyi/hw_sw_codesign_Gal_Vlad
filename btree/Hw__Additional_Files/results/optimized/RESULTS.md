# btree optimization — results (2026-09-19)

## The change

`benchmark/optimized/bm_btree/run_benchmark.py` is generated from the installed
pyperformance 1.14.0 `bm_btree` by `scripts/make_optimized.py`; the full
diff is `btree_optimization.diff`. What changes:

| where | change |
|---|---|
| imports | `from bisect import bisect_left` |
| `BNode.__slots__`, `__init__` | new `keys` list (plain ints), parallel to `items` |
| `BNode.get_position` | replaced by `get_position_opt`: `return bisect_left(self.keys, key)`; `get_position = get_position_opt` keeps the three call sites (search, insert_item, delete) untouched |
| `insert_item`, `split_child`, `delete` (7 cases + root shrink) | one mirrored line per mutation of `items`, so `keys[i] == items[i][0]` always holds |

Everything else — records, shuffled insert order, the 20 % delete /
re-insert phase, `gc.collect()`, iteration, lookups, the pyperf runner
settings, the `--gc-only` option and the benchmark name — is byte-identical.
The `--gc-only` variant is *not* faster (150 ms vs 139 ms for the explicit
collection: the 10 150 extra list objects cost the collector ~10 ms), which
confirms the gain comes from the search and not from doing less GC work.

## Equivalence

`tests/test_btree_equivalence.py` builds both versions from the same records
in the same order and compares them node by node (keys, values, child
counts, height, iteration order) after build, delete, re-insert and
value-replacement, checks `keys` against `items` in every node, and runs
10 000 lookups plus out-of-range misses on both. Passed at N = 200 000.

## Timing (course VM: 1 vCPU Xeon E5-2630 v3, Ubuntu 22.04, CPython 3.12.15)

| run | baseline | optimized | pyperf compare_to |
|---|---|---|---|
| pyperformance default (1 proc × 3 values) | 3.19 s ± 0.10 | 1.94 s ± 0.00 | **1.64× faster** |
| 10 processes × 3 values (30 values) | 3.11 s ± 0.05 | 1.94 s ± 0.03 | **1.60× faster** |
| bare-metal cross-check (naranja13, CPython 3.12.3), 30 values | 5.06 s ± 0.03 | 3.07 s ± 0.02 | 1.65× faster |

Speed-up 37.6 % on the loop in the course VM; the course threshold is 7 %.

Back-to-back confirmation (both versions run one after the other in the VM,
10 × 3 values each, `../flamegraphs/timing_back_to_back.txt`):
baseline 3.07 s ± 0.03, optimized 1.94 s ± 0.03, **1.59× faster** — agrees
with the first measurement within 1.5 %. The bare-metal set is in
`../host_bare_metal/optimized/` (5.03 s → 3.09 s back to back, 1.63×).

Commands (also in `script_btree.sh compare`):

```
.venv/bin/python -m pyperformance run --manifest benchmark/optimized/MANIFEST -b btree -o results/optimized/btree_opt_default.json
.venv/bin/python benchmark/optimized/bm_btree/run_benchmark.py -p 10 -n 3 -w 1 -l 1 -o results/optimized/btree_opt_rigorous.json
.venv/bin/python -m pyperf compare_to results/baseline/btree_baseline_rigorous.json results/optimized/btree_opt_rigorous.json --table
```

The manifest run needs an explicit `version` in `pyproject.toml`; outside the
pyperformance tree the original's `dynamic = ["version"]` cannot be resolved.

## Profile after (perf, `-X perf`, release)

| Python function | before (inclusive % of samples) | after |
|---|---|---|
| BNode.get_position / get_position_opt | 45.3 | 12.3 |
| BNode.insert_item | 46.2 | 43.3 |
| BNode.search | 24.4 | 14.9 |
| make_records (untimed, unchanged) | 8.9 | 12.6 (2.42 vs 2.40 G cycles) |
| GC | 7.9 | 11.8 (same absolute work, smaller total) |

Flame graphs (recorded back to back with the baseline): `../flamegraphs/optimized_release_python.svg`
(`_full.svg` for all frames); summary `../flamegraphs/optimized_release.summary.txt`.
The remaining 12 % in `get_position_opt` is the Python call/return around
one C call — the floor that the hardware node-search unit targets next.
