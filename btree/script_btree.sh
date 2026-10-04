#!/usr/bin/env bash
# script_btree.sh — the execution script for the btree benchmark (Project.pdf, "Execution script").
# All paths are relative to this script's directory; nothing depends on the user's home.
# The script works inside "Hw__Additional_Files/" (benchmark/, results/, scripts/, tests/, tools/).
#
# Stages, in the order the project asks for them:
#   ./script_btree.sh setup       1. environment setup and dependency installation
#                                    apt packages (python3-venv, python3-dbg, perl, git, perf) when root or sudo is available,
#                                    .venv (release) + .venv_dbg (python3-dbg), pyperformance 1.14.0 / pyperf 2.10.0,
#                                    pyperformance's own benchmark venv, FlameGraph clone
#   ./script_btree.sh benchmark   2. benchmark execution with pyperformance and the other profiling tools
#                                    pyperformance run, 10-process reference run, per-phase analysis, in-node
#                                    search micro-benchmark, working-set measurement     -> results/baseline/
#   ./script_btree.sh flamegraph  3. flame graph and performance data generation
#                                    perf stat counters (IPC, caches, L2), counters per phase of the loop, perf record + flame graphs of the
#                                    baseline on the release and the debug interpreter  -> results/baseline/, results/flamegraphs/
#   ./script_btree.sh compare     4. post-optimization benchmark execution with performance comparison
#                                    generate the optimized benchmark, equivalence test, pyperformance run,
#                                    10-process run, pyperf compare_to, flame graphs (release + debug), back-to-back timing
#                                                                                        -> results/optimized/, results/flamegraphs/
#   ./script_btree.sh all         the four stages in order (default)
#
# The hardware proposal (SystemVerilog NSU) has its own flow: hw/run_hw.sh (see hw/NSU_DESIGN.md).
#
# Requirements: Ubuntu 24.04 (python3.12) or 22.04 (python3.10): python3, python3-venv, python3-dbg, perl, git, perf.
#   Python frames in the perf profiles (py::...) need CPython >= 3.12 (-X perf); on older interpreters the
#   flame graphs still work but show interpreter frames only.
# perf notes for this host:
#   * kernel.perf_event_paranoid=2  -> user-space sampling only (cycles:u), no sudo needed
#   * kernel.perf_event_max_sample_rate=1 -> `-F <hz>` is silently clamped to 1 Hz; we
#     therefore sample by period (`-c 2000000` ~= 1.2 kHz at 2.4 GHz) which is not clamped.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # repository root (this script's directory)
WORK="$ROOT/Hw__Additional_Files"                      # HW files and additional files (Project.pdf layout)
cd "$WORK"                                              # every relative path below is inside this folder

# Virtual environments: inside the repository (created by `setup`, git-ignored) or,
# if they already exist one level up (the original workspace layout), there.
if   [ -x "$ROOT/.venv/bin/python" ];    then WS="$ROOT"
elif [ -x "$ROOT/../.venv/bin/python" ]; then WS="$(cd "$ROOT/.." && pwd)"
else                                          WS="$ROOT"; fi
# Base interpreters for the two venvs: CPython 3.12 when present (its -X perf support is what puts
# Python function names into the perf profiles), else the system python3. Override with PYTHON= / PYTHON_DBG=.
PY_BASE="${PYTHON:-$(command -v python3.12 || command -v python3)}"
PY_DBG_BASE="${PYTHON_DBG:-$(command -v python3.12-dbg || command -v python3-dbg || true)}"
PY_REL="$WS/.venv/bin/python"
PY_DBG="$WS/.venv_dbg/bin/python3-dbg"
[ -x "$PY_DBG" ] || PY_DBG="$WS/.venv_dbg/bin/python"          # a 3.12 debug venv names its binary 'python' 
TOOLS="tools"                                           # FlameGraph clone (git-ignored)
FG="$TOOLS/FlameGraph"
OUT="results/baseline"
FLAME="results/flamegraphs"
PERIOD=2000000
LOOPS=2

# perf: Ubuntu's /usr/bin/perf is a version-matching wrapper; prefer a binary that actually runs
PERF="${PERF:-perf}"
if ! "$PERF" version >/dev/null 2>&1; then
  PERF=""
  for cand in /usr/local/sbin/perf /usr/local/bin/perf $(ls -1d /usr/lib/linux-tools-*/perf 2>/dev/null | sort -V -r) /usr/bin/perf; do
    if [ -x "$cand" ] && "$cand" version >/dev/null 2>&1; then PERF="$cand"; break; fi
  done
fi
perf_ok() { [ -n "$PERF" ] && "$PERF" version >/dev/null 2>&1; }

log() { printf '\n==> %s\n' "$*"; }

# generated logs / reports embed absolute paths of this checkout; make them relative so the
# repository never contains a user's home directory
scrub_paths() { for f in "$@"; do [ -f "$f" ] && sed -i "s#$WORK/#./#g; s#$ROOT/#../#g; s#$WS/#../../#g; s#$HOME/#~/#g" "$f"; done; return 0; }

# The benchmark code lives in this repository: benchmark/original/bm_btree (verbatim pyperformance 1.14.0
# bm_btree) and benchmark/optimized/bm_btree. pyperformance only provides the harness; nothing is read
# from the benchmark copy installed inside the pyperformance package.
BORIG="benchmark/original"
BENCH_ORIG="$BORIG/bm_btree/run_benchmark.py"

# ============================================================ 1. environment setup and dependency installation
make_venv() {   # $1 = base interpreter, $2 = venv directory
  local hint="install the venv package first (Debian/Ubuntu: sudo apt install python3-venv python3-dbg, i.e. python3.X-venv for your python3), then re-run setup"
  if [ -x "$2/bin/python" ] && ! "$2/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "removing $2: it exists but has no pip"; rm -rf "$2"
  fi
  if [ -x "$2/bin/python" ] && [ "$("$2/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])')" != "$("$1" -c 'import sys;print("%d.%d"%sys.version_info[:2])')" ]; then
    echo "removing $2: built from another Python than $1"; rm -rf "$2"
  fi
  if [ ! -x "$2/bin/python" ]; then
    command -v "$1" >/dev/null || { echo "ERROR: $1 not found (Debian/Ubuntu: sudo apt install python3 python3-dbg)" >&2; exit 1; }
    "$1" -m venv "$2" || { rm -rf "$2"; echo "ERROR: '$1 -m venv' failed; $hint" >&2; exit 1; }
    # Debian/Ubuntu without python3-venv: the directory is created but pip is missing
    "$2/bin/python" -m pip --version >/dev/null 2>&1 \
      || { rm -rf "$2"; echo "ERROR: $1 created a venv without pip; $hint" >&2; exit 1; }
  fi
}

install_system_packages() {   # Debian/Ubuntu: everything the stages need; as root directly, otherwise via passwordless sudo
  command -v apt-get >/dev/null || { echo "no apt-get on this system: make sure python3 (3.12 preferred), python3-venv, python3-dbg, perl, git and perf are installed"; return 0; }
  local SUDO=""
  if [ "$(id -u)" -ne 0 ]; then
    if sudo -n true 2>/dev/null; then SUDO="sudo -n"; else
      echo "not root and no passwordless sudo: skipping apt. Make sure these are installed: python3 (3.12 preferred) python3-venv python3-dbg python3-pip perl git perf"; return 0
    fi
  fi
  # CPython 3.12 is wanted for the profiles; on Ubuntu 22.04 it comes from the deadsnakes archive
  if ! command -v python3.12 >/dev/null && grep -q 'VERSION_ID="22.04"' /etc/os-release 2>/dev/null; then
    $SUDO apt-get install -y -q software-properties-common >/dev/null 2>&1 || true
    $SUDO add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1 && $SUDO apt-get update -q >/dev/null 2>&1 \
      && echo "added ppa:deadsnakes/ppa for python3.12" || echo "could not add the deadsnakes archive; staying on the system python3"
  fi
  local pyver; pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  local essential="python3 python3-venv python${pyver}-venv python3-dbg python3-pip perl git curl"
  if command -v python3.12 >/dev/null || apt-cache policy python3.12 2>/dev/null | grep -q Candidate: ; then
    essential="$essential python3.12 python3.12-venv python3.12-dbg"
  fi
  local optional="linux-tools-common linux-tools-generic linux-tools-$(uname -r)"      # perf; the kernel-specific one may not exist
  log "1. setup: system packages (apt-get): $essential"
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -q >/dev/null 2>&1 || true
  $SUDO apt-get install -y -q $essential 2>&1 | grep -E "^(Setting up|E:)" || true
  for p in $optional; do $SUDO apt-get install -y -q "$p" >/dev/null 2>&1 && echo "installed $p" || echo "not available: $p (perf is optional; the flamegraph stage skips recordings without it)"; done
}

do_setup() {
  install_system_packages
  log "1. setup: virtual environments"
  echo "base interpreters: $PY_BASE ($("$PY_BASE" --version 2>&1)), debug: ${PY_DBG_BASE:-none}"
  make_venv "$PY_BASE" "$WS/.venv"
  if [ -n "$PY_DBG_BASE" ]; then make_venv "$PY_DBG_BASE" "$WS/.venv_dbg"; else echo "no debug interpreter found: the debug profile will be skipped"; fi
  [ -x "$WS/.venv_dbg/bin/python3-dbg" ] || PY_DBG="$WS/.venv_dbg/bin/python"
  for py in "$PY_REL" "$PY_DBG"; do
    [ -x "$py" ] || continue
    "$py" -m pip install -q --upgrade pip
    "$py" -m pip install -q "pyperformance==1.14.0" "pyperf==2.10.0" psutil
  done
  log "1. setup: pyperformance's private benchmark venv (otherwise the first 'run' creates it)"
  "$PY_REL" -m pyperformance venv create >/dev/null 2>&1 || true   # no-op if it already exists
  "$PY_REL" -m pyperformance venv show 2>&1 | tail -n 1            # -> venv/cpython3.12-<hash>/ (git-ignored)
  log "1. setup: FlameGraph"
  [ -d "$FG" ] || git clone -q --depth 1 https://github.com/brendangregg/FlameGraph "$FG"
  mkdir -p "$OUT" "$FLAME"
  perf_ok && echo "perf: $PERF" || echo "perf: not found (stage 3 will skip the recordings)"
}

# ============================================================ 2. benchmark execution (pyperformance + other profiling tools)
do_benchmark() {
  mkdir -p "$OUT"
  log "2. benchmark: pyperformance run (release interpreter, suite defaults)"
  rm -f "$OUT/btree_baseline_default.json" "$OUT/btree_baseline_rigorous.json"
  "$PY_REL" -m pyperformance run --manifest "$BORIG/MANIFEST" -b btree -o "$OUT/btree_baseline_default.json" 2>&1 | tee "$OUT/pyperformance_default.log"

  log "2. benchmark: reference run, 10 processes x 3 values (the baseline side of pyperf compare_to; ~3 min)"
  "$PY_REL" "$BENCH_ORIG" -p 10 -n 3 -w 1 -l 1 -o "$OUT/btree_baseline_rigorous.json" 2>&1 | tee "$OUT/btree_baseline_rigorous.log"

  log "2. benchmark: per-phase analysis (cProfile, GC, tree shape) and in-node search micro-benchmark"
  "$PY_REL" scripts/analyze_btree.py --repeat 3 --bench "$BENCH_ORIG" > "$OUT/analysis_btree_release.txt"
  "$PY_REL" scripts/microbench_search.py > "$OUT/microbench_search_release.txt"
  log "2. benchmark: working set (RSS of records and tree, getsizeof of the tree structure, cache sizes)"
  "$PY_REL" scripts/memory_footprint.py > "$OUT/memory_footprint_release.txt"

  scrub_paths "$OUT"/*.log "$OUT"/*.json "$OUT"/analysis_btree_release.txt
  log "2. benchmark done -> $OUT/ (findings: $OUT/BASELINE_FINDINGS.md)"
}

# ============================================================ 3. flame graph and performance data generation
record_and_flame() {   # $1 python, $2 tag (baseline_release|baseline_debug|optimized_release), $3 title, $4 benchmark path
  local py="$1" tag="$2" title="$3" bench="$4" extra=(--bench "$4")
  mkdir -p "$FLAME"
  if ! perf_ok; then
    log "perf is not available on this host: skipping the $tag recording (existing $FLAME/${tag}_* files are kept)"
    return 0
  fi
  log "perf record ($tag): $py -X perf scripts/profile_btree.py --loops $LOOPS ${extra[*]}"
  "$PERF" record -e cycles:u -c "$PERIOD" -g -o "$FLAME/${tag}.perf.data" -- \
      "$py" -X perf scripts/profile_btree.py --loops "$LOOPS" "${extra[@]}" 2>&1 | tee "$FLAME/${tag}.record.log"
  "$PERF" report -i "$FLAME/${tag}.perf.data" --stdio --children  > "$FLAME/${tag}.perf_report.txt"
  "$PERF" report -i "$FLAME/${tag}.perf.data" --stdio --no-children --sort symbol > "$FLAME/${tag}.perf_report_flat.txt"
  "$PERF" script -i "$FLAME/${tag}.perf.data" | "$FG/stackcollapse-perf.pl" > "$FLAME/${tag}.folded"
  scrub_paths "$FLAME/${tag}.folded" "$FLAME/${tag}.perf_report.txt" "$FLAME/${tag}.perf_report_flat.txt" "$FLAME/${tag}.record.log"
  "$FG/flamegraph.pl" --title "$title (full stacks)" --width 1800 "$FLAME/${tag}.folded" > "$FLAME/${tag}_full.svg"
  "$PY_REL" scripts/make_flamegraph_py.py "$FLAME/${tag}.folded" "$FLAME/${tag}_python.svg" --title "$title - Python functions only"
  "$PY_REL" scripts/folded_summary.py "$FLAME/${tag}.folded" > "$FLAME/${tag}.summary.txt"
  echo "flame graphs: $FLAME/${tag}_python.svg (readable)  $FLAME/${tag}_full.svg (full stacks)"
}

perf_stat_counters() {   # hardware counters over one loop of the baseline (user space only; paranoid=2)
  if ! perf_ok; then log "perf is not available: skipping perf stat (existing $OUT/perf_stat_*.txt are kept)"; return 0; fi
  log "3. flamegraph: perf stat — cycles, instructions, L1 D / LLC loads and misses, branches (one loop)"
  "$PERF" stat -e cycles:u,instructions:u,L1-dcache-loads,L1-dcache-load-misses,LLC-loads,LLC-load-misses,branch-instructions:u,branch-misses:u \
      -o "$OUT/perf_stat_btree_rel.txt" -- "$PY_REL" scripts/profile_btree.py --loops 1 --bench "$BENCH_ORIG" > /dev/null 2>&1 \
      || echo "perf stat (core events) failed; see $OUT/perf_stat_btree_rel.txt"
  log "3. flamegraph: perf stat — L2 demand data reads / hits / misses (Haswell l2_rqsts events; one loop)"
  "$PERF" stat -e l2_rqsts.all_demand_data_rd:u,l2_rqsts.demand_data_rd_hit:u,l2_rqsts.demand_data_rd_miss:u,l2_rqsts.references:u,l2_rqsts.miss:u \
      -o "$OUT/perf_stat_l2_btree_rel.txt" -- "$PY_REL" scripts/profile_btree.py --loops 1 --bench "$BENCH_ORIG" > /dev/null 2>&1 \
      || echo "perf stat (l2_rqsts events) failed — these event names are Intel Haswell specific; see $OUT/perf_stat_l2_btree_rel.txt"
  scrub_paths "$OUT/perf_stat_btree_rel.txt" "$OUT/perf_stat_l2_btree_rel.txt"
}

phase_counters() {        # the same counters read around each phase of the loop (perf_event_open, no perf binary needed)
  log "3. flamegraph: hardware counters per phase of the loop (cycles, instructions, L1D, LLC)"
  "$PY_REL" scripts/phase_counters.py --bench "$BENCH_ORIG" > "$OUT/perf_phase_counters_release.txt" \
      || echo "phase counters failed (perf_event_open refused?); see $OUT/perf_phase_counters_release.txt"
}

do_flamegraph() {
  perf_stat_counters
  phase_counters
  record_and_flame "$PY_REL" baseline_release "btree BASELINE (release 3.12, cycles)" "$BENCH_ORIG"
  if [ -x "$PY_DBG" ]; then
    record_and_flame "$PY_DBG" baseline_debug "btree BASELINE (python3-dbg, cycles)" "$BENCH_ORIG"
  else
    log "no debug interpreter venv: skipping the baseline_debug recording"
  fi
  log "3. flamegraph done -> $FLAME/ (see $FLAME/README.md), counters in $OUT/perf_stat_*.txt"
}

# ============================================================ 4. post-optimization benchmark execution with performance comparison
do_compare() {
  local O="results/optimized" BOPT="benchmark/optimized"; mkdir -p "$O" "$BOPT"
  log "4. compare: generate benchmark/optimized/bm_btree/run_benchmark.py from benchmark/original (in-node search -> bisect on a keys list)"
  "$PY_REL" scripts/make_optimized.py
  diff -u benchmark/original/bm_btree/run_benchmark.py "$BOPT/bm_btree/run_benchmark.py" > "benchmark/btree_optimization.diff" || true
  echo "diff written to benchmark/btree_optimization.diff ($(grep -c '^[+-][^+-]' "benchmark/btree_optimization.diff") changed lines)"

  log "4. compare: equivalence test — optimized tree must be identical to the original, node for node"
  "$PY_REL" tests/test_btree_equivalence.py

  log "4. compare: pyperformance run of the optimized benchmark (same harness, same benchmark name, via manifest)"
  rm -f "$O/btree_opt_default.json" "$O/btree_opt_rigorous.json"
  "$PY_REL" -m pyperformance run --manifest "$BOPT/MANIFEST" -b btree -o "$O/btree_opt_default.json" 2>&1 | tee "$O/pyperformance_opt_default.log"

  log "4. compare: 10 processes x 3 values, same settings as the baseline reference run (~2 min)"
  "$PY_REL" "$BOPT/bm_btree/run_benchmark.py" -p 10 -n 3 -w 1 -l 1 -o "$O/btree_opt_rigorous.json" 2>&1 | tee "$O/btree_opt_rigorous.log"

  log "4. compare: pyperf compare_to against the baseline"
  "$PY_REL" -m pyperf compare_to "$OUT/btree_baseline_default.json"  "$O/btree_opt_default.json"  --table | tee "$O/compare_default.txt"
  "$PY_REL" -m pyperf compare_to "$OUT/btree_baseline_rigorous.json" "$O/btree_opt_rigorous.json" --table | tee "$O/compare_rigorous.txt"

  record_and_flame "$PY_REL" optimized_release "btree OPTIMIZED - bisect on keys list (release 3.12, cycles)" "$BOPT/bm_btree/run_benchmark.py"
  if [ -x "$PY_DBG" ]; then     # the debug interpreter keeps frame pointers: Python-level attribution even for builds that omit them
    record_and_flame "$PY_DBG" optimized_debug "btree OPTIMIZED - bisect on keys list (python3-dbg, cycles)" "$BOPT/bm_btree/run_benchmark.py"
  fi

  log "4. compare: timing back to back — baseline then optimized in one session, 10 processes x 3 values each (~5 min)"
  rm -f "$FLAME/timing_baseline.json" "$FLAME/timing_optimized.json"
  "$PY_REL" "$BENCH_ORIG"                      -p 10 -n 3 -w 1 -l 1 -o "$FLAME/timing_baseline.json"  > "$FLAME/timing_baseline.log"  2>&1
  "$PY_REL" "$BOPT/bm_btree/run_benchmark.py"  -p 10 -n 3 -w 1 -l 1 -o "$FLAME/timing_optimized.json" > "$FLAME/timing_optimized.log" 2>&1
  {
    echo "baseline and optimized run back to back, $(date), 10 processes x 3 values each, $("$PY_REL" -c 'import platform;print(platform.python_implementation(), platform.python_version())')"
    echo
    "$PY_REL" -m pyperf compare_to "$FLAME/timing_baseline.json" "$FLAME/timing_optimized.json" --table
    echo; echo "--- baseline ---";  "$PY_REL" -m pyperf stats "$FLAME/timing_baseline.json"  | grep -E 'Mean|Median|Minimum|Maximum|outlier'
    echo "--- optimized ---";       "$PY_REL" -m pyperf stats "$FLAME/timing_optimized.json" | grep -E 'Mean|Median|Minimum|Maximum|outlier'
  } > "$FLAME/timing_back_to_back.txt"
  cat "$FLAME/timing_back_to_back.txt"

  scrub_paths "$O"/*.log "$O"/*.json "$O"/compare_*.txt "$FLAME"/timing_*.log "$FLAME"/timing_*.json "$FLAME"/timing_back_to_back.txt
  log "4. compare done -> $O/compare_rigorous.txt, $FLAME/timing_back_to_back.txt, $FLAME/optimized_release_python.svg"
}

case "${1:-all}" in
  setup)      do_setup ;;
  benchmark)  do_benchmark ;;
  flamegraph) do_flamegraph ;;
  compare)    do_compare ;;
  all)        do_setup; do_benchmark; do_flamegraph; do_compare ;;
  *) echo "usage: $0 {setup|benchmark|flamegraph|compare|all}" >&2; exit 2 ;;
esac
