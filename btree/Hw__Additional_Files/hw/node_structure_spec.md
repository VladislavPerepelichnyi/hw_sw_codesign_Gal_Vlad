# B-tree node structure — for the slide illustration

All sizes measured on CPython 3.12 (64-bit) with the optimized benchmark.
Every box below is a separate memory allocation. Arrows are pointers (8 B each).

## Tree level
- 200,000 entries, 10,150 nodes, height 3: root -> 26 nodes -> 511 nodes -> 9,612 leaves
- minimum degree t = 16: a node holds 15..31 entries (root may hold fewer); typical 20
- an internal node with n entries has n+1 children

## Objects of ONE node (software, as implemented)

1. BNode object — 56 B
   fields: items -> (list A), keys -> (list B), nodes -> (list C) or None for a leaf
   (it holds three pointers and nothing else)

2. list A "items" — 56 B header + 8 B per entry  (176 B for 15 entries, 304 B for 31)
   contents: n pointers, one to each tuple

3. list B "keys" — same size as A
   contents: n pointers, one to each int key object (the same int objects the tuples point to)

4. list C "nodes" — internal nodes only, 56 B header + 8 B per child (n+1 pointers to child BNodes)

5. tuple (key, record) — 56 B each, n per node
   contents: pointer to the int key object, pointer to the Record object

6. int key object — 28 B each, n per node
   contents: the key value (0..199,999). Pointed to by BOTH the tuple and list B.

7. Record object — ~380–430 B each including its attribute dict (the bytes field is 0..99 B), n per node
   contents: 6 attributes (a int, b str, c bytes, d float, e str, f bool/None). The value payload.

Pointer graph for one entry i:
   BNode.items -> list A -> [i] -> tuple -> (int key, Record)
   BNode.keys  -> list B -> [i] -> int key            (same int object)
   BNode.nodes -> list C -> [i] -> child BNode        (internal only)

Totals for a 15-entry leaf: 3 + 15*3 = 48 objects, ~7.2 KB (records are 5.6 KB of it)
Totals for a 15-entry internal node: 4 + 15*3 + (16 children pointed to) ; ~8.3 KB

Nothing about a node is one contiguous chunk. Only the three lists are contiguous
arrays of pointers.

## Hardware node block (what the NSU reads) — ONE contiguous chunk

- array of 32 entries x 4 B = 128 B, 128 B aligned = exactly 2 cache lines of 64 B
- entry 0  = count n (0..31)
- entry 1..31 = keys k0..k30 as plain 32-bit unsigned numbers (no int objects, no pointers;
  the benchmark's keys are < 200,000, keys >= 2^32 would take the software path)
- unused entries (n+1..31) are don't-care
- it REPLACES list B (the keys list). It contains no pointers to records or children;
  those stay in list A and list C in Python.
- instruction: nsrch rd, rs1 = address of this block, rs2 = query
  -> rd = position (0..n) and found flag. Python then uses items[position] (list A -> tuple -> Record).
- per tree: 10,150 blocks x 128 B = 1.3 MB

## What the search touches
- software today: BNode -> list A -> tuple -> int key, for ~11 of the n entries (three pointer hops each)
- software fix (bisect): BNode -> list B -> int key, ~4-5 probes, in C
- hardware: 4 line reads of the block, all 31 keys compared at once
