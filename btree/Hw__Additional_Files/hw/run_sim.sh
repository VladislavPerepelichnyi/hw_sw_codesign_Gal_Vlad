#!/usr/bin/env bash
# Lint the RTL and run the self-checking testbench with Verilator.
# Usage: hw/run_sim.sh [NV] [fixed]   (NV = number of vectors, default: all; "fixed" = the 31-lane reference design)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."                     # Hw__Additional_Files/
# Python: the repo-root venv (../.venv), or the one a level above the repo (../../.venv)
if   [ -x ../.venv/bin/python ];    then PY=../.venv/bin/python
elif [ -x ../../.venv/bin/python ]; then PY=../../.venv/bin/python
else                                  PY=python3; fi
# Verilator: VERILATOR_ROOT if the caller set it (in-place source build), else tools/verilator-src
# (built by hw/run_hw.sh), else whatever `verilator` is on PATH.
if [ -n "${VERILATOR_ROOT:-}" ] && [ -x "$VERILATOR_ROOT/bin/verilator" ]; then
  VL="$VERILATOR_ROOT/bin/verilator"
elif [ -x tools/verilator-src/bin/verilator_bin ]; then
  export VERILATOR_ROOT="$(pwd)/tools/verilator-src"; VL="$VERILATOR_ROOT/bin/verilator"
else
  VL=verilator
fi
# Verilator build directories (git-ignored); the testbench binaries read hw/vectors/ relative to this folder.
SIM="tools/sim"; mkdir -p "$SIM"
NV="${1:-$(cat hw/vectors/nsu_vectors.meta)}"
VARIANT="${2:-nsu}"
[ -s hw/vectors/nsu_vectors.hex ] || "$PY" hw/gen_vectors.py

if [ "$VARIANT" = "fixed" ]; then
  echo "==> lint (nsu_top_fixed + nsu_core, -Wall)"
  "$VL" --lint-only -Wall --top-module nsu_top_fixed hw/rtl/nsu_core.sv hw/rtl/nsu_top_fixed.sv
  echo "==> build testbench (fixed 31-lane design)"
  "$VL" --binary --timing -Wall -Wno-fatal -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL \
        --top-module tb_nsu_fixed -Mdir "$SIM/obj_dir_fixed" -o tb_nsu_fixed hw/rtl/nsu_core.sv hw/rtl/nsu_top_fixed.sv hw/tb/tb_nsu_fixed.sv \
        > "$SIM/obj_dir_fixed_build.log" 2>&1 || { tail -n 40 "$SIM/obj_dir_fixed_build.log"; exit 1; }
  echo "==> simulate ($NV vectors)"
  "$SIM/obj_dir_fixed/tb_nsu_fixed" +NV="$NV" | tee hw/sim_result_fixed.txt
  grep -q '^PASS' hw/sim_result_fixed.txt
  exit 0
fi

echo "==> lint (nsu_top, -Wall)"
"$VL" --lint-only -Wall --top-module nsu_top hw/rtl/nsu_top.sv

echo "==> build testbench (NSU, streaming early-out)"
"$VL" --binary --timing -Wall -Wno-fatal -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL \
      --top-module tb_nsu -Mdir "$SIM/obj_dir" -o tb_nsu hw/rtl/nsu_top.sv hw/tb/tb_nsu.sv \
      > "$SIM/obj_dir_build.log" 2>&1 || { tail -n 40 "$SIM/obj_dir_build.log"; exit 1; }

echo "==> simulate ($NV vectors)"
"$SIM/obj_dir/tb_nsu" +NV="$NV" | tee hw/sim_result.txt
grep -q '^PASS' hw/sim_result.txt
