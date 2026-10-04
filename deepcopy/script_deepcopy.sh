#!/usr/bin/env bash
#
# script_deepcopy.sh - reproduce the deepcopy optimization from a clean VM.
#
#   ./script_deepcopy.sh                  every step below, in order
#   ./script_deepcopy.sh test measure     only the named steps, in that order
#   ./script_deepcopy.sh --from measure   from a step to the end
#   ./script_deepcopy.sh --list           step names only
#   ./script_deepcopy.sh --help           this help
#
# Target: Ubuntu 22.04 (the course QEMU image), run as root. Every step is a
# Makefile target in Hw_Additional_Files/, so each one can also be run by hand
# with `make -C Hw_Additional_Files <target>` (see `make help` there).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
HW="$ROOT/Hw_Additional_Files"
BENCH=deepcopy
OUT="$HW/perf_results/$BENCH"
REP="$HW/compare_report/$BENCH"
LOGDIR="$HW/logs"

# ---- steps: name | what it does | the make target(s) it runs ----------------
STEPS=(deps venv flamegraph_tool build test pyperformance measure counters flamegraph report hw_sim)

describe() {
    case "$1" in
        deps)            echo "apt update && upgrade; install gcc/make, python3-dbg, python3-dev, venv, perf" ;;
        venv)            echo "Hw_Additional_Files/venv from python3-dbg + pyperf, pyperformance, py-spy (pinned)" ;;
        flamegraph_tool) echo "clone Brendan Gregg's FlameGraph scripts" ;;
        build)           echo "compile the fastdeepcopy C extension" ;;
        test)            echo "correctness suite: real deepcopy, same workload, no leaks, faster" ;;
        pyperformance)   echo "stock benchmark through pyperformance itself (reference run)" ;;
        measure)         echo "pyperf timing, baseline vs optimized (the before/after numbers)" ;;
        counters)        echo "perf stat on identical fixed work, baseline vs optimized" ;;
        flamegraph)      echo "perf (dwarf) and py-spy flame graphs, baseline and optimized" ;;
        report)          echo "collect tables + flame graphs into compare_report/ and print them" ;;
        hw_sim)          echo "simulate the memo-cache accelerator RTL (hw/) against its testbench" ;;
    esac
}

usage() {
    sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "Steps (default: all, in this order):"
    local i=1
    for s in "${STEPS[@]}"; do
        printf '  %2d  %-16s %s\n' "$i" "$s" "$(describe "$s")"
        i=$((i + 1))
    done
    cat <<EOF

Outputs:
  Hw_Additional_Files/compare_report/$BENCH/   time_comparison*.txt, hw_stat_comparison.txt, *.svg
  Hw_Additional_Files/perf_results/$BENCH/     raw pyperf JSON, perf reports, perf stat
  Hw_Additional_Files/logs/                     one log per step of the last run

Measurement steps always re-measure: they delete their own previous outputs
first (the committed results are overwritten by a fresh run).

Time on the course VM (1 vCPU, debug interpreter), about 1 hour in total:
deps+venv ~4 min, test ~2 min, pyperformance ~2 min, measure ~2 min,
counters ~1 min, flamegraph ~48 min (perf DWARF unwinding is slow).
EOF
}

# ---- helpers -------------------------------------------------------------------
rule() { printf '\n\033[1m==== [%s] %s\033[0m\n' "$1" "$2"; }
mk()   { make -C "$HW" BENCH="$BENCH" "$@"; }

run_step() {
    local s="$1" log="$LOGDIR/$1.log"
    rule "$s" "$(describe "$s")"
    mkdir -p "$LOGDIR"
    # Each step logs to its own file and to the terminal; with pipefail a
    # failing make fails the step, and set -e stops the pipeline there.
    "step_$s" 2>&1 | tee "$log"
}

as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"
    elif command -v sudo >/dev/null 2>&1; then sudo "$@"
    else echo "step needs root (no sudo available)"; return 1
    fi
}

# ---- step bodies -------------------------------------------------------------
step_deps()            { as_root make -C "$HW" deps; }
step_venv()            { mk venv; }
step_flamegraph_tool() { mk flamegraph_tool; }
step_build()           { mk ext; }
step_test()            { mk test; }
step_pyperformance()   { rm -f "$OUT/pyperformance_baseline.json"; mk pyperformance; }
step_measure() {
    rm -f "$OUT/${BENCH}_baseline.json" "$OUT/${BENCH}_optimized.json"
    mk compare
}
step_counters() {
    rm -f "$OUT/perf_fixed_baseline.txt" "$OUT/perf_fixed_opt.txt"
    mk fixedstat
}
step_flamegraph() {
    rm -f "$OUT"/perf_*.data "$OUT"/flamegraph_*.svg "$OUT"/pyspy_flamegraph_*.svg
    mk flamegraph opt_flamegraph pyspy opt_pyspy
}
step_hw_sim()          { mk hw_sim; }
step_report() {
    rm -rf "$REP"
    mk report
    echo
    echo "------------------------------------------------------------------"
    echo " Before/after timing (pyperf, $BENCH)"
    echo "------------------------------------------------------------------"
    cat "$REP/time_comparison.txt"
    if [ -f "$REP/time_comparison_pyperformance.txt" ]; then
        echo
        echo " Reference: pyperformance run of the stock benchmark vs both scripts"
        cat "$REP/time_comparison_pyperformance.txt"
    fi
    echo
    echo "Flame graphs: $REP/*.svg"
    echo "Counters:     $REP/hw_stat_comparison.txt"
}

# ---- argument parsing ------------------------------------------------------------
is_step() { local s; for s in "${STEPS[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }

selected=()
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --list)    printf '%s\n' "${STEPS[@]}"; exit 0 ;;
        --from)
            [ $# -ge 2 ] && is_step "$2" || { echo "--from needs a step name (see --list)"; exit 2; }
            on=0
            for s in "${STEPS[@]}"; do [ "$s" = "$2" ] && on=1; [ $on = 1 ] && selected+=("$s"); done
            shift 2 ;;
        *)
            is_step "$1" || { echo "unknown step: $1 (see --help)"; exit 2; }
            selected+=("$1"); shift ;;
    esac
done
[ ${#selected[@]} -gt 0 ] || selected=("${STEPS[@]}")

start=$(date +%s)
for s in "${selected[@]}"; do
    run_step "$s"
done
printf '\n\033[1mDone: %s  (%d s)\033[0m\n' "${selected[*]}" "$(( $(date +%s) - start ))"
