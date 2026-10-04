# Flame graphs — baseline vs optimized (course VM, same settings)

Produced by `./script_btree.sh flamegraph` (baseline, release + debug interpreter)
and `./script_btree.sh compare` (optimized, release + debug) with
`perf record -e cycles:u -c 2000000 -g -- python -X perf scripts/profile_btree.py --loops 2`,
in the same VM with the same settings, so the parts of the program that were not
changed (record generation, garbage collection) can be compared between graphs.

The VM's release interpreter (deadsnakes CPython 3.12.15) is built without frame
pointers, so perf cannot walk its Python frames: the release recordings show C
symbols only and no `*_release_python.svg` is produced. The debug interpreter
keeps frame pointers, so the Python-level graphs come from it. The bare-metal
set in `../host_bare_metal/flamegraphs/` has release-build Python graphs
(Ubuntu's build keeps frame pointers).

| file | what |
|---|---|
| `baseline_debug_python.svg`    | **use this** — unmodified benchmark, Python functions only, fits the window |
| `optimized_debug_python.svg`   | **use this** — bisect on the keys list, Python functions only |
| `baseline_debug_full.svg`, `optimized_debug_full.svg` | same recordings, every CPython frame (debug build: adds validate_list etc.) |
| `baseline_release_full.svg`, `optimized_release_full.svg` | release build, C symbols (interpreter loop, list iteration, GC, bisect) |
| `*.folded`         | collapsed stacks (load in https://www.speedscope.app for pan/zoom) |
| `*.summary.txt`    | inclusive % per Python function and per CPython subsystem (scripts/folded_summary.py) |
| `*.perf_report.txt`, `*.perf_report_flat.txt` | perf's own call-graph and flat reports |
| `*.record.log`     | the driver's timing line of the recording (`*.perf.data` is not committed) |

How to read: width = share of CPU samples of that recording. Click a frame to
zoom, click the title to reset, "Search" (top right) highlights a function by
name. Compare `BNode.get_position` (baseline) with `BNode.get_position_opt`
(optimized): that is the change. `make_records` is outside the timed loop and
identical in both; its width differs only because the total shrank. The debug
build spends extra time in its own checks (validate_list, _Py_DECREF_DecRefTotal),
which inflates the GC and allocator frames on both sides equally.

`optimized_debug_*` was recorded in a second run of the compare stage in the same
VM (the first run recorded the optimized version on the release interpreter only);
the timing files in this folder are from the first, complete run.

Speed-up (pyperf, measured, see `timing_back_to_back.txt` in this folder):
the two benchmark versions run one after the other with 10 processes x 3 values each.
