#!/usr/bin/env bash
# run_hw.sh — the hardware proposal's own flow (independent of script_btree.sh).
#
#   hw/run_hw.sh          model check, NSU vectors from the real benchmark tree, software-side check,
#                         block diagram, Verilator build if needed, lint + simulation of both variants
#
# Run from "Hw__Additional_Files/" or from anywhere (the script changes into that folder).
# Python: the repository-root .venv (../.venv) or the one a level above the repository (../../.venv),
# created by ./script_btree.sh setup. Verilator: VERILATOR_ROOT if set, else tools/verilator-src
# (built here from source, needs g++, make, autoconf, flex, bison, perl), else `verilator` on PATH.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."                       # Hw__Additional_Files/
if   [ -x ../.venv/bin/python ];    then PY=../.venv/bin/python
elif [ -x ../../.venv/bin/python ]; then PY=../../.venv/bin/python
else                                     PY=python3; fi
log() { printf '\n==> %s\n' "$*"; }

log "hardware: model check, vectors from the real tree, block diagram, lint, simulate (hw/NSU_DESIGN.md)"
"$PY" hw/model/nsu_model.py                 # bit-exact Python model of the unit, checked against bisect_left
"$PY" hw/gen_vectors.py                     # 3,210 (node, query) vectors -> hw/vectors/
"$PY" hw/sw/nsu_get_position.py             # software side: node block + nsrch intrinsic, checked against the model
"$PY" -c 'import cairosvg' 2>/dev/null || "$PY" -m pip install -q cairosvg 2>/dev/null || true   # PNG export (optional)
"$PY" hw/node_memory_breakdown.py > hw/node_memory_breakdown.txt   # one node in memory today vs the 256 B hardware block
"$PY" hw/make_block_diagram.py              # hw/block_diagram_system.svg (+ .png when cairosvg is installed)

if [ -z "${VERILATOR_ROOT:-}" ] && [ ! -x tools/verilator-src/bin/verilator_bin ] && ! command -v verilator >/dev/null; then
  if command -v apt-get >/dev/null; then                       # the build toolchain, when we are allowed to install it
    SUDO=""; [ "$(id -u)" -eq 0 ] || { sudo -n true 2>/dev/null && SUDO="sudo -n"; }
    if [ "$(id -u)" -eq 0 ] || [ -n "$SUDO" ]; then
      log "installing the Verilator build toolchain (apt-get): git g++ make autoconf flex bison perl"
      DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -q git g++ make autoconf flex bison perl libfl-dev zlib1g-dev 2>&1 | grep -E "^(Setting up|E:)" || true
    fi
  fi
  log "building Verilator from source into tools/verilator-src"
  mkdir -p tools
  git clone -q --depth 1 -b stable https://github.com/verilator/verilator tools/verilator-src
  (cd tools/verilator-src && autoconf && ./configure && make -j"$(nproc)") > tools/verilator-build.log 2>&1
fi
hw/run_sim.sh                               # the NSU: nsu_top (streaming early-out)        -> hw/sim_result.txt
hw/run_sim.sh "" fixed                      # reference: nsu_top_fixed + nsu_core (31 lanes) -> hw/sim_result_fixed.txt
