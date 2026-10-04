#!/usr/bin/env bash
#
# Automated test suite for the deepcopy optimization.
#
#   ./run_tests.sh           full suite
#   ./run_tests.sh --quick   skip the two tests that actually execute the
#                            benchmarks (roughly 10x faster)
#
# Exit status is 0 only if every test passed. Intended to be run before any
# measurement - `make bench` depends on `verify`, and this script is what
# `make test` runs.
#
# Deliberately NOT `set -e`: every test runs even if an earlier one fails, so
# one invocation reports the complete picture.
set -uo pipefail

cd "$(dirname "$0")" || exit 1
# Same interpreter choice as the Makefile: the debug-build venv if it exists.
if [ -z "${PYTHON:-}" ]; then
    if [ -x venv/bin/python ]; then PYTHON="$PWD/venv/bin/python"; else PYTHON=python3-dbg; fi
fi
BENCH="${BENCH:-deepcopy}"
BASELINE="baseline_${BENCH}.py"
OPTIMIZED="optimized_${BENCH}.py"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0; SKIP=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  \033[33mSKIP\033[0m  %s\n' "$1"; SKIP=$((SKIP+1)); }
head_() { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }

# -----------------------------------------------------------------------------
head_ 1 "Toolchain"
# -----------------------------------------------------------------------------
for t in "$PYTHON" gcc; do
    if command -v "$t" >/dev/null 2>&1; then ok "$t present"; else bad "$t missing"; fi
done
if "$PYTHON" -c 'import sys; sys.exit(0 if hasattr(sys,"gettotalrefcount") else 1)' 2>/dev/null; then
    ok "$PYTHON is a debug build (leak checking available)"
else
    skip "$PYTHON is not a debug build - leak check will be skipped"
fi
if "$PYTHON" -c 'import pyperf' 2>/dev/null; then ok "pyperf importable"; else bad "pyperf not importable by $PYTHON"; fi

# -----------------------------------------------------------------------------
head_ 2 "C extension builds"
# -----------------------------------------------------------------------------
if (cd fastdeepcopy && "$PYTHON" setup.py build_ext --inplace) >"$TMP/build.log" 2>&1; then
    ok "fastdeepcopy compiles"
    if grep -qiE '\bwarning\b' "$TMP/build.log"; then
        skip "compiler emitted warnings (see build log below)"
        grep -iE '\bwarning\b' "$TMP/build.log" | head -5 | sed 's/^/        /'
    else
        ok "compiles without warnings"
    fi
    # NB: deliberately do not touch fastdeepcopy/.build-stamp here - that
    # stamp belongs to the Makefile, and bumping it would invalidate every
    # optimized artifact and force a full (very slow) perf rebuild.
else
    bad "fastdeepcopy failed to compile"; tail -15 "$TMP/build.log" | sed 's/^/        /'
fi
if "$PYTHON" -c 'import sys; sys.path.insert(0,"fastdeepcopy"); import fastdeepcopy' 2>/dev/null; then
    ok "fastdeepcopy imports"
else
    bad "fastdeepcopy does not import (ABI mismatch?)"
fi

# -----------------------------------------------------------------------------
head_ 3 "Baseline is the unmodified upstream benchmark"
# -----------------------------------------------------------------------------
UPSTREAM=$(find venv -path '*bm_deepcopy/run_benchmark.py' 2>/dev/null | head -1)
if [ -z "$UPSTREAM" ]; then
    skip "upstream pyperformance copy not found (no venv/) - cannot compare"
elif cmp -s "$UPSTREAM" "$BASELINE"; then
    ok "$BASELINE is byte-identical to $UPSTREAM"
else
    bad "$BASELINE DIFFERS from upstream - the baseline is not trustworthy"
    diff "$UPSTREAM" "$BASELINE" | head -15 | sed 's/^/        /'
fi

# -----------------------------------------------------------------------------
head_ 4 "Optimized measures the same workload as the baseline"
# -----------------------------------------------------------------------------
"$PYTHON" check_workload.py "$BASELINE" "$OPTIMIZED" >"$TMP/wl.log" 2>&1
while IFS= read -r line; do
    case "$line" in
        "  pass  "*) ok   "${line#  pass  }" ;;
        "  FAIL  "*) bad  "${line#  FAIL  }" ;;
    esac
done < "$TMP/wl.log"

# -----------------------------------------------------------------------------
head_ 5 "Optimized copy is semantically a real deepcopy"
# -----------------------------------------------------------------------------
if "$PYTHON" verify_deepcopy.py >"$TMP/verify.log" 2>&1; then
    n=$(grep -c '  pass  ' "$TMP/verify.log")
    ok "verify_deepcopy.py: $n assertions passed"
    grep '  skip  ' "$TMP/verify.log" | sed 's/^  skip  /        skipped: /'
else
    bad "verify_deepcopy.py reported failures"
    grep -E '  FAIL  ' "$TMP/verify.log" | sed 's/^/        /'
fi

if [ "$QUICK" = "1" ]; then
    head_ 6 "Execution tests skipped (--quick)"
    skip "benchmark smoke run"
    skip "optimized-is-faster sanity check"
else
# -----------------------------------------------------------------------------
head_ 6 "Both benchmarks actually run and report all three sub-benchmarks"
# -----------------------------------------------------------------------------
for pair in "baseline:$BASELINE" "optimized:$OPTIMIZED"; do
    tag=${pair%%:*}; script=${pair#*:}
    if "$PYTHON" "$script" -p1 -w0 -n1 -l64 -o "$TMP/$tag.json" >"$TMP/$tag.log" 2>&1; then
        names=$("$PYTHON" - "$TMP/$tag.json" <<'PY'
import json,sys
with open(sys.argv[1]) as fh:
    d = json.load(fh)
print(",".join(sorted(b["metadata"]["name"] for b in d["benchmarks"])))
PY
)
        if [ "$names" = "deepcopy,deepcopy_memo,deepcopy_reduce" ]; then
            ok "$tag runs and reports: $names"
        else
            bad "$tag reported unexpected benchmarks: $names"
        fi
    else
        bad "$tag failed to run"; tail -10 "$TMP/$tag.log" | sed 's/^/        /'
    fi
done

# -----------------------------------------------------------------------------
head_ 7 "Optimized is faster than baseline (same fixed work)"
# -----------------------------------------------------------------------------
export FW_ITERS_MAIN=200 FW_ITERS_REDUCE=20000 FW_ITERS_MEMO=2000
tb=$( { /usr/bin/time -f %e "$PYTHON" fixed_work.py --impl baseline;  } 2>&1 | tail -1 )
to=$( { /usr/bin/time -f %e "$PYTHON" fixed_work.py --impl optimized; } 2>&1 | tail -1 )
unset FW_ITERS_MAIN FW_ITERS_REDUCE FW_ITERS_MEMO
if "$PYTHON" -c "import sys; sys.exit(0 if float('$to') < float('$tb') else 1)" 2>/dev/null; then
    ok "$(printf 'baseline %ss -> optimized %ss (%.2fx faster)' "$tb" "$to" \
          "$("$PYTHON" -c "print(float('$tb')/float('$to'))")")"
else
    bad "optimized ($to s) is not faster than baseline ($tb s)"
fi
fi

# -----------------------------------------------------------------------------
printf '\n%s\n' "======================================================"
printf ' %d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIP"
printf '%s\n' "======================================================"
[ "$FAIL" -eq 0 ] || exit 1
