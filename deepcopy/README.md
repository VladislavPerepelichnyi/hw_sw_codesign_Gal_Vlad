# HW/SW Co-Design Final Project — `deepcopy` benchmark

Optimization, analysis and a hardware acceleration proposal for the
[pyperformance](https://github.com/python/pyperformance) `deepcopy` benchmark.

## Repository layout

| Path | Contents |
|---|---|
| `report_deepcopy.txt` | **The report**: overview, profiling, optimizations, before/after comparison, hardware proposal, conclusion |
| `script_deepcopy.sh` | One script that reproduces everything from a clean VM (`--help` for single steps) |
| `prompt.txt` | Prompts given to the AI assistant |
| `Hw_Additional_Files/` | Everything else (below) |
| `Hw_Additional_Files/Makefile` | Every step as a make target (`make -C Hw_Additional_Files help`) |
| `Hw_Additional_Files/baseline_deepcopy.py` | The upstream benchmark, byte-identical (checked by the tests) |
| `Hw_Additional_Files/optimized_deepcopy.py` | Same workload, optimized copy path |
| `Hw_Additional_Files/fastdeepcopy/` | The C extension (`fastdeepcopymodule.c`) |
| `Hw_Additional_Files/hw/` | Hardware proposal: memo-cache RTL (`memo_cache.v`) and its testbench |
| `Hw_Additional_Files/run_tests.sh`, `verify_deepcopy.py`, `check_workload.py`, `fixed_work.py` | Correctness suite, same-workload proof, fixed-work driver for `perf stat` |
| `Hw_Additional_Files/perf_results/deepcopy/` | Raw results: pyperf JSON, `perf report`, `perf stat`, flame graphs |
| `Hw_Additional_Files/compare_report/deepcopy/` | Collected before/after tables and flame graphs |
| `Hw_Additional_Files/DEEPCOPY_REPORT.md` | Longer write-up of the optimization, in plain language |
| `Hw_Additional_Files/presentation/` | Beamer slides (LaTeX source + PDF) |

## Installation and running

Target: the course Ubuntu 22.04 QEMU image, logged in as **root**.

```bash
git clone https://github.com/VladislavPerepelichnyi/final_project_HW-SW_co_design.git
cd final_project_HW-SW_co_design/deepcopy
./script_deepcopy.sh
```

With no arguments the script runs every step in order:

| # | Step | What it does |
|---|---|---|
| 1 | `deps` | `apt update && apt upgrade`; installs gcc/make, `python3-dbg`, `python3-dev`, `python3-venv`, `perf` for the running kernel, `iverilog` |
| 2 | `venv` | `Hw_Additional_Files/venv` from `python3-dbg`, with pinned `pyperf`, `pyperformance`, `py-spy` |
| 3 | `flamegraph_tool` | clones Brendan Gregg's FlameGraph scripts |
| 4 | `build` | compiles the `fastdeepcopy` C extension |
| 5 | `test` | correctness suite (must pass before any number is trusted) |
| 6 | `pyperformance` | the stock benchmark through `pyperformance run` (reference) |
| 7 | `measure` | pyperf timing, baseline vs optimized |
| 8 | `counters` | `perf stat` on identical fixed work, baseline vs optimized |
| 9 | `flamegraph` | `perf` (DWARF) and `py-spy` flame graphs, both versions |
| 10 | `report` | collects everything into `compare_report/deepcopy/` and prints the comparison |
| 11 | `hw_sim` | simulates the accelerator RTL against its testbench |

Single steps or a tail of the pipeline:

```bash
./script_deepcopy.sh --help              # steps, outputs, timings
./script_deepcopy.sh test measure        # only these steps
./script_deepcopy.sh --from measure      # from a step to the end
make -C Hw_Additional_Files help         # the same steps as make targets
```

Each step's output is also saved to `Hw_Additional_Files/logs/<step>.log`. A full run
takes about an hour on the course VM, about 48 minutes of it in `flamegraph`
(`perf` DWARF unwinding).

## Results (summary)

| Sub-benchmark | Baseline | Optimized | Speedup |
|---|---|---|---|
| `deepcopy` | 2.00 ms | 372 µs | **5.39×** |
| `deepcopy_reduce` | 18.2 µs | 4.73 µs | **3.85×** |
| `deepcopy_memo` | 233 µs | 45.4 µs | **5.13×** |
| Geometric mean | | | **4.74×** |

On identical fixed work (340,000 copies), instructions drop from 196 K to
44 K per copy (−77.5 %) at unchanged IPC. The stock benchmark run through
`pyperformance` itself matches the baseline within noise.

All numbers are from `python3-dbg` (CPython 3.10 debug build) on the course
VM. They come from a run of `./script_deepcopy.sh` on a fresh clone and are
committed under `Hw_Additional_Files/`.

**How it works.** Stock `copy.deepcopy` is pure Python. It pays a Python
function call (frame setup, dispatch-table lookup, memo lookup) for **every
element** it visits, even an integer it returns unchanged. Most of the
baseline profile is the interpreter's call overhead.

The optimization changes how the copy is done, never what is copied:

- **`fastdeepcopy`** walks `dict`, `list`, `tuple` and immutable values in C,
  with the same memo, keep-alive and tuple-reuse semantics. It delegates
  every other type to `copy.deepcopy`.
- **`__deepcopy__` hooks** on the benchmark's two user classes replace the
  generic `__reduce_ex__` reconstruction.

`run_tests.sh` proves three things before anything is measured:

- the result is still a real deep copy (47 assertions, plus a reference-leak check);
- the workload is unchanged (an AST comparison against the upstream file);
- the optimized version is faster on identical fixed work.

**Hardware proposal.** A memo-cache: a 256-entry, 4-way set-associative
pointer→pointer table on a Wishbone bus. It replaces the memo dict's
lookups and inserts with two or three uncached register accesses. The RTL is
in `Hw_Additional_Files/hw/memo_cache.v`. The report covers its interface,
architecture, block diagram, the expected gain and its limits.
