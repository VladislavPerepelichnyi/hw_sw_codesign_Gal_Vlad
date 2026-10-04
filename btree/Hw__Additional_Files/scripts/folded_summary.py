#!/usr/bin/env python3
"""Summarise a FlameGraph .folded file: inclusive share of samples per
Python-level function (py:: frames) and per selected CPython subsystem.
Usage: folded_summary.py FILE.folded [--top N]"""
import re, sys
from collections import Counter

path = sys.argv[1]
top = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--top" else 25
incl = Counter(); selfc = Counter(); total = 0
groups = {
    "GC (gc_collect_main & visitors)": re.compile(r"gc_collect_main|visit_decref|visit_reachable|move_unreachable|update_refs|subtract_refs|gc_collect_(with_callback|generations)|validate_list|untrack_"),
    "allocator (PyObject_Malloc/Free, _PyMem_)": re.compile(r"PyObject_Malloc|PyObject_Free|_PyMem_|pymalloc_|PyObject_GC_Del|_PyObject_GC_New|gc_alloc"),
    "list iteration (listiter_next/enum_next)": re.compile(r"listiter_next|enum_next"),
    "list.insert / ins1 / memmove": re.compile(r"list_insert|ins1|__memmove"),
}
grp = Counter()
for line in open(path):
    line = line.rstrip("\n")
    if not line: continue
    stack, n = line.rsplit(" ", 1); n = int(n); total += n
    frames = stack.split(";")
    seen = set()
    for f in frames:
        if f.startswith("py::"):
            name = f[4:].split(":")[0]
            if name not in seen:
                seen.add(name); incl[name] += n
    leaf = frames[-1]
    selfc[leaf] += n
    for g, rx in groups.items():
        if any(rx.search(f) for f in frames):
            grp[g] += n
print(f"file: {path}   total samples: {total}")
print(f"\n{'python function (inclusive)':40s} {'samples':>8s} {'%':>6s}")
for name, n in incl.most_common(top):
    print(f"{name:40s} {n:8d} {100*n/total:6.1f}")
print(f"\n{'CPython subsystem (inclusive, any frame)':40s} {'samples':>8s} {'%':>6s}")
for g, n in grp.most_common():
    print(f"{g:40s} {n:8d} {100*n/total:6.1f}")
print(f"\n{'leaf symbol (self)':40s} {'samples':>8s} {'%':>6s}")
for name, n in selfc.most_common(15):
    print(f"{name[:40]:40s} {n:8d} {100*n/total:6.1f}")
