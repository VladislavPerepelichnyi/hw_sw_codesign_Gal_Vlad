# HW/SW Co-Design Final Project — pyperformance benchmarks

Analysis, software optimization and hardware acceleration proposals for two
benchmarks from the [pyperformance](https://github.com/python/pyperformance)
suite. For each benchmark we:

- profiled it with pyperformance/pyperf, `perf` and flame graphs to find the
  bottleneck;
- optimized it without changing the measured workload, and verified the result;
- proposed and implemented a hardware accelerator for a key part of it in
  (System)Verilog, with a testbench.

The two benchmarks live in **separate, independent folders**. Each one has its
own report, run script, AI prompt log and README with full instructions, and
runs on its own.

| Folder | Benchmark | Software optimization | Hardware proposal | Result |
|---|---|---|---|---|
| [`deepcopy/`](deepcopy/) | `deepcopy` — `copy.deepcopy` of dicts, lists, tuples and user classes | C extension for the container walk + `__deepcopy__` hooks | Memo cache: pointer→pointer lookup table on a Wishbone bus (Verilog) | 4.74× faster (geometric mean of 3 sub-benchmarks) |
| [`btree/`](btree/) | `btree` — a 200,000-entry pure-Python B-tree, stressing the garbage collector | Faster in-node key search | Node Search Unit: an `nsrch` instruction for in-node search (SystemVerilog) | 1.60× faster |

## Layout

```
README.md                  this file
deepcopy/                  pyperformance "deepcopy"   -> see deepcopy/README.md
├── report_deepcopy.txt
├── script_deepcopy.sh
├── prompt.txt
├── README.md
└── Hw_Additional_Files/
btree/                     pyperformance "btree"      -> see btree/README.md
├── report_btree.txt
├── script_btree.sh
├── prompt.txt
├── README.md
└── Hw__Additional_Files/
```

## Running

Both run on the course Ubuntu 22.04 QEMU image as root. Each script installs
what it needs:

```bash
git clone https://github.com/VladislavPerepelichnyi/final_project_HW-SW_co_design.git
cd final_project_HW-SW_co_design

./deepcopy/script_deepcopy.sh     # full deepcopy pipeline  (deepcopy/README.md)
./btree/script_btree.sh           # full btree pipeline     (btree/README.md)
```

Each script works relative to its own folder, so it can be started from
anywhere. Each folder's README lists the individual steps.

## Authors

- `deepcopy/` — Vladislav Perepelichnyi
- `btree/` — Gal Dvir
