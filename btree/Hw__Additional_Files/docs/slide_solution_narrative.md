# How the speed-up was achieved — slide narrative (animation order)

All numbers measured on the host (Xeon E5-2630 v3, CPython 3.12.3); sources in brackets.

## Step 1 — the hot function (reveal first)
Every insert, delete and lookup walks 4 nodes root→leaf and, in each node, calls

    def get_position(self, key):
        for position, item in enumerate(self.items):   # items = list of (key, record) tuples
            if item[0] >= key:
                return position
        return len(self.items)

It scans the node's sorted keys from the left until the first key ≥ query.
- called 1.93 M times per benchmark loop                         [analyze_btree.py]
- compares 10.6 keys per call on average (node holds 15–31)       [analyze_btree.py]
- = 20.5 M key comparisons per loop
- 1,008 ns per call → ~1.55 s of the 3.11 s loop = 50 %  (VM)      [microbench, perf]

## Step 2 — why one comparison is so expensive (reveal the cost of ONE iteration)
Each `item[0] >= key` runs through the CPython bytecode interpreter:
- 11 bytecodes per key: FOR_ITER, UNPACK_SEQUENCE, STORE_FAST ×2, LOAD_FAST, LOAD_CONST,
  BINARY_SUBSCR, LOAD_FAST, COMPARE_OP, POP_JUMP_IF_TRUE, JUMP_BACKWARD    [dis output]
- 3 dependent pointer loads per key: items list → tuple → int object → value
- ≈ 130 ns ≈ 310 cycles per compared key (all cache hits — the cost is instructions, not memory:
  IPC 2.3 in the VM / 1.44 bare metal, L1 miss 2.5 %)                                         [perf stat]
So a node search is ~11 × 130 ns + 206 ns call overhead ≈ 1,600 ns.

## Step 3 — the change (reveal the diff: 1 import, 1 slot, 1 method, mirrored lines)
    from bisect import bisect_left
    class BNode:
        __slots__ = ['items', 'keys', 'nodes']      # + a plain list of the keys
        def get_position_opt(self, key):
            return bisect_left(self.keys, key)       # binary search, in C
        get_position = get_position_opt

- `keys` holds only the int keys, in the same sorted order as `items`
- every place that edits `items` edits `keys` the same way (one line each: insert, split, 7 delete cases)
- 27 lines added, 6 removed, out of 436; nothing else in the benchmark touched   [btree_optimization.diff]

## Step 4 — the three reasons it is faster (reveal one by one)
1. **Fewer comparisons.** Binary search halves the range each step:
   log2(20) ≈ 4–5 probes instead of ~11 sequential compares.
2. **Each comparison is C, not bytecode.** bisect_left is a compiled C function in the standard library:
   no FOR_ITER / UNPACK_SEQUENCE / BINARY_SUBSCR, no per-iteration dispatch — ≈ 72 ns per probe
   instead of 130 ns.
3. **The interpreter runs once per call, not once per key.** Python executes one call (≈ 206 ns fixed)
   and gets the answer back; the loop no longer exists in Python.
   Plain int list, not the tuple list: bisect on tuples would call back into Python per probe
   (measured only 1.40×); the int list gives 2.86× (3.25× bare metal).                [microbench_search.py]

## Step 5 — the result chain (reveal as arrows)
per call:   1,008 ns  →  352 ns   (2.9× faster; bare metal 1,668 → 513)                    [microbench]
per loop:   1.93 M calls × 1.09 µs saved ≈ 2.1 s saved
hotspot:    50 % of the loop  →  12 % (what remains is mostly the 206 ns Python call)  [flame graphs]
benchmark:  3.11 s  →  1.94 s  =  1.60× faster, −37.6 %  (course VM, pyperf, 10 processes × 3 values;
            back to back 3.07 → 1.94 s, 1.59×; bare-metal cross-check 5.06 → 3.07 s, 1.65×)    [timing_back_to_back.txt]
threshold:  7 % required → met 5× over

## Step 6 — what did NOT change (reveal last, one line each)
- same tree: 200,000 keys, 10,150 nodes, height 3, same fill — verified node by node   [test_btree_equivalence.py]
- same workload: inserts, deletes, re-inserts, gc.collect(), iteration, lookups
- same (key, record) tuples and records → same object graph; GC work unchanged
  (gc-only variant 139 ms → 150 ms, +10,150 small lists)
- only the algorithm inside one method changed: linear scan → binary search

## Optional bridge to the hardware slide
The remaining 352 ns are 157 ns of Python call + ~195 ns of C search (bare metal: 513 = 206 + ~310).
The NSU instruction removes the C search (each arriving cache line compared with 8 lanes, early exit; 7–12-cycle instruction);
the 157 ns remain → ≈ 160 ns per call, loop ≈ 1.81 s (1.72×). Amdahl: 1.56 s of the loop is
not the search at all (limit 1.99×).
