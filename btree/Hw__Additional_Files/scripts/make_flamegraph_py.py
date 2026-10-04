#!/usr/bin/env python3
"""
Readable, Python-level flame graph from a perf .folded file.

The raw graph from `perf script | stackcollapse-perf.pl | flamegraph.pl` is
hard to read for CPython code: every Python call is wrapped in 4-5
interpreter frames (_PyEval_EvalFrameDefault, PyObject_Vectorcall, ...),
frame names carry the full file path, and the stack starts 15 frames below
the benchmark in libc / Py_BytesMain.  Stacks here were up to 128 frames deep.

This script:
  * keeps only the `py::` frames (Python functions) and shortens
    `py::BNode.search:/long/path/run_benchmark.py` to `BNode.search`
  * re-roots every stack at --root (default run_bench, so the timed
    run_once and the untimed make_records appear side by side)
  * drops frames narrower than --minwidth % so slivers don't clutter
  * writes an SVG whose width is 100 % of the browser window (click a
    frame to zoom into it, click the title or "Reset Zoom" to go back,
    Ctrl-F / the "Search" link highlights by name)

Usage: make_flamegraph_py.py IN.folded OUT.svg --title "..." [--root run_bench] [--minwidth 0.3]
       (also writes OUT.folded next to the svg: Python-only stacks, loadable in speedscope.app)
"""
import argparse, os, re, subprocess, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
FLAMEGRAPH = os.path.join(os.path.dirname(HERE), "tools", "FlameGraph", "flamegraph.pl")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folded"); ap.add_argument("svg")
    ap.add_argument("--title", default="Python-level flame graph")
    ap.add_argument("--subtitle", default="Python frames only; click to zoom, click title to reset")
    ap.add_argument("--root", default="run_bench", help="function name to re-root at ('' = keep all)")
    ap.add_argument("--minwidth", default="0.3", help="hide frames narrower than this %% of total")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--keep-c-leaf", action="store_true", help="append the leaf C symbol under the last Python frame")
    a = ap.parse_args()

    out = Counter(); total = kept = 0
    for line in open(a.folded):
        line = line.rstrip("\n")
        if not line: continue
        stack, n = line.rsplit(" ", 1); n = int(n); total += n
        frames = stack.split(";")
        py = [re.sub(r"^py::([^:]*):.*$", r"\1", f) for f in frames if f.startswith("py::")]
        if a.root:
            if a.root not in py: continue
            py = py[py.index(a.root):]
        if a.keep_c_leaf and not frames[-1].startswith("py::"):
            py.append("[C] " + frames[-1])
        if not py: continue
        kept += n; out[";".join(py)] += n

    if not out:
        print(f"{a.svg}: no py:: frames in the profile (CPython < 3.12 has no -X perf); "
              f"Python-only flame graph not written, use the _full.svg", file=sys.stderr)
        return
    folded_py = os.path.splitext(a.svg)[0] + ".folded"
    with open(folded_py, "w") as f:
        for k, v in out.most_common(): f.write(f"{k} {v}\n")

    svg = subprocess.run(["perl", FLAMEGRAPH, "--title", a.title, "--subtitle", a.subtitle,
                          "--width", str(a.width), "--minwidth", a.minwidth, "--fontsize", "12",
                          "--countname", "samples", "--nametype", "Function:"],
                         input=open(folded_py).read(), capture_output=True, text=True, check=True).stdout
    # make it fit the window: drop the fixed pixel size, keep the viewBox so it scales
    svg = re.sub(r'<svg version="1.1" width="\d+" height="\d+"', '<svg version="1.1" width="100%"', svg, count=1)
    open(a.svg, "w").write(svg)
    depth = max(k.count(";") + 1 for k in out) if out else 0
    print(f"{a.svg}: {kept/total*100:.1f}% of samples kept under root '{a.root}', "
          f"{len(out)} unique Python stacks, max depth {depth} (raw file had up to "
          f"{max(l.count(';')+1 for l in open(a.folded) if l.strip())})")

if __name__ == "__main__":
    main()
