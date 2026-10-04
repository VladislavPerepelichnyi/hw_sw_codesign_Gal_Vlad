# Measurement sets

`baseline/`, `optimized/`, `flamegraphs/` — produced by `script_btree.sh` inside the
course VM (QEMU/KVM on naranja4: 1 vCPU Intel Xeon E5-2630 v3 @ 2.40 GHz, 4 GB,
Ubuntu 22.04.5, CPython 3.12.15 from the deadsnakes archive, python3.12-dbg for
the symbol-level profiles). These are the numbers the report quotes.

`host_bare_metal/` — the same three folders produced by the same script on a
bare-metal host with the same CPU model (naranja13, 2 sockets, Ubuntu 24.04,
CPython 3.12.3). Kept because (1) the virtual PMU does not expose the LLC
events, so the LLC-miss attribution in report section 2.5 comes from here, and
(2) Ubuntu's release interpreter keeps frame pointers, so its release-build
flame graphs carry Python function names (`*_release_python.svg`), which the
VM's release build cannot provide (it has debug-build ones instead).
`host_bare_metal/baseline/BASELINE_FINDINGS.md` is the analysis log of that run.

Same code, same workload in both; the VM's interpreter build is about 1.6x
faster per loop (3.11 s vs 5.06 s baseline), the speed-up of the optimization
is the same within noise (1.60x vs 1.65x).
