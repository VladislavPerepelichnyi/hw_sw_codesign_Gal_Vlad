# deepcopy slides

Seven Beamer frames covering the optimization work. No title slide and no
introduction — a colleague covers those.

## Build

```bash
pdflatex slides_deepcopy.tex     # run twice so the frame numbers settle
```

Builds clean with TeX Live's `beamer`, `booktabs`, `listings`, `xcolor`,
`graphicx`, `tikz`.

## Frames

| # | Topic |
|---|---|
| 1 | The benchmark and the module's architecture |
| 2 | How the built-in `copy.deepcopy` works |
| 3 | Overhead analysis: flame graph and `perf` counters |
| 4 | The `fastdeepcopy` C module and its refcount/GC obligations |
| 5 | Exact-type fast path, and delegation to `copy.py` for custom classes |
| 6 | Optimization at the Python class level (`__deepcopy__`) |
| 7 | Performance evaluation |

## Swapping in your own images

Every figure is a placeholder box drawn by `\imgph{height}{caption}`. Above
each one sits a commented-out `\includegraphics` line. To use a real picture,
drop the file in `figures/`, uncomment the `\graphicspath` line in the
preamble, then replace the `\imgph{...}` line with the `\includegraphics` one.

Five placeholders, in frame order:

| Frame | Suggested source |
|---|---|
| 1 | module architecture / repo layout diagram |
| 2 | dispatch and recursion flow diagram |
| 3 | `perf_results/deepcopy/pyspy_flamegraph_baseline.svg` |
| 5 | fast path vs. delegation diagram |
| 7 | `perf_results/deepcopy/pyspy_flamegraph_opt.svg` |

The flame graphs are SVG; `pdflatex` needs PDF or PNG, so convert first, e.g.
`rsvg-convert -f pdf -o figures/flamegraph_baseline.pdf ../perf_results/deepcopy/pyspy_flamegraph_baseline.svg`
(or `inkscape --export-type=pdf`). Note the native `flamegraph_*.svg` files are
6–8 MB and very dense — the py-spy ones read far better on a projector.

Placeholder boxes are deliberately short so the frames do not overflow. After
inserting a real image, adjust the `width`/`height` and recompile — the build
is clean of `Overfull \vbox` warnings today, so any new one means the frame no
longer fits.

## Numbers

Every figure quoted in the slides comes from the committed artifacts in
`../perf_results/deepcopy/` and `../compare_report/deepcopy/`, and matches
`../DEEPCOPY_REPORT.md`.
