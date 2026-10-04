# =============================================================================
#  deepcopy benchmark - environment, build, correctness and measurement
# =============================================================================
#  Normally driven by ../script_deepcopy.sh, which runs these steps in order:
#
#    make deps         apt packages (root)          make venv     Python venv
#    make flamegraph_tool  FlameGraph scripts       make ext      C extension
#    make test         correctness suite            make pyperformance
#    make compare      timing A/B                   make fixedstat counters
#    make flamegraph opt_flamegraph pyspy opt_pyspy                profiles
#    make report       collect everything into compare_report/
#
#  make help lists every target. Baseline and optimized are ALWAYS launched the
#  same way ($(PYTHON) <script>.py), so every comparison below is a genuine A/B.
# =============================================================================

# ---- configuration (override on the command line: make bench PYTHON=python3) -
BENCH        ?= deepcopy

# The project measures on CPython's debug build (python3-dbg), inside a venv
# made from it. Before `make venv` has run, fall back to the system python3-dbg.
PY_BASE      ?= python3-dbg
VENV         ?= venv
# Absolute paths: some recipes cd into fastdeepcopy/ first.
PYTHON       ?= $(if $(wildcard $(VENV)/bin/python),$(CURDIR)/$(VENV)/bin/python,$(PY_BASE))
PYSPY        ?= $(if $(wildcard $(VENV)/bin/py-spy),$(CURDIR)/$(VENV)/bin/py-spy,py-spy)

# Pinned so a fresh clone measures with the same tools as the report.
# (bm_deepcopy/run_benchmark.py is byte-identical in pyperformance 1.10-1.14,
# which run_tests.sh checks against baseline_deepcopy.py.)
PIP_PKGS     ?= pyperf==2.10.0 pyperformance==1.14.0 py-spy==0.4.2
APT_PKGS     ?= build-essential git curl perl time \
                python3 python3-dev python3-dbg python3-venv python3-pip \
                linux-tools-common iverilog

# "fp" yields no usable stacks on an -Og debug interpreter; dwarf does.
CALLGRAPH    ?= dwarf
PERF_FREQ    ?= 99
PERF_REPEAT  ?= 3

# ---- layout -----------------------------------------------------------------
RESULTS_DIR  := perf_results
OUT_DIR      := $(RESULTS_DIR)/$(BENCH)
REPORT_DIR   := compare_report/$(BENCH)
FLAMEGRAPH   := ./FlameGraph

BASELINE     := baseline_$(BENCH).py
OPTIMIZED    := optimized_$(BENCH).py

# ---- deepcopy-specific components -------------------------------------------
EXT_DIR      := fastdeepcopy
EXT_SRC      := $(EXT_DIR)/fastdeepcopymodule.c
EXT_SETUP    := $(EXT_DIR)/setup.py
EXT_STAMP    := $(EXT_DIR)/.build-stamp
FIXED_DRIVER := fixed_work.py
VERIFY_PY    := verify_deepcopy.py
TEST_SH      := ./run_tests.sh

EVENTS := cpu-clock,task-clock,cycles,instructions,branch-instructions,branch-misses,bus-cycles,cache-references,cache-misses,ref-cycles,page-faults,minor-faults,major-faults,context-switches,LLC-loads,LLC-stores

# ---- generated artifacts ----------------------------------------------------
BASE_JSON  := $(OUT_DIR)/$(BENCH)_baseline.json
OPT_JSON   := $(OUT_DIR)/$(BENCH)_optimized.json
BASE_DATA  := $(OUT_DIR)/perf_baseline.data
OPT_DATA   := $(OUT_DIR)/perf_opt.data
BASE_SVG   := $(OUT_DIR)/flamegraph_baseline.svg
OPT_SVG    := $(OUT_DIR)/flamegraph_opt.svg
PYPERF_JSON:= $(OUT_DIR)/pyperformance_baseline.json
BASE_PYSPY := $(OUT_DIR)/pyspy_flamegraph_baseline.svg
OPT_PYSPY  := $(OUT_DIR)/pyspy_flamegraph_opt.svg
FIX_BASE   := $(OUT_DIR)/perf_fixed_baseline.txt
FIX_OPT    := $(OUT_DIR)/perf_fixed_opt.txt

.DEFAULT_GOAL := help
.PHONY: help deps venv flamegraph_tool setup tools ext verify test pyperformance \
        measure opt_measure flamegraph opt_flamegraph pyspy opt_pyspy fixedstat \
        compare report bench stat_wholerun hw_sim clean distclean

# =============================================================================
#  Help
# =============================================================================
help:
	@echo "Usage: make <target> [BENCH=$(BENCH)] [PYTHON=$(PYTHON)]"
	@echo ""
	@echo "Environment (a clean Ubuntu 22.04 VM; see ../script_deepcopy.sh)"
	@echo "  deps             apt update/upgrade + build tools, python3-dbg, perf (root)"
	@echo "  venv             $(VENV)/ from $(PY_BASE) with $(PIP_PKGS)"
	@echo "  flamegraph_tool  clone Brendan Gregg's FlameGraph scripts into $(FLAMEGRAPH)"
	@echo "  setup            deps + venv + flamegraph_tool"
	@echo ""
	@echo "Build & correctness"
	@echo "  ext              Build the fastdeepcopy C extension in place"
	@echo "  verify           Assert the optimized copy still matches copy.deepcopy"
	@echo "  test             Full automated test suite ($(TEST_SH))"
	@echo "  tools            Check that every required tool is installed"
	@echo ""
	@echo "Timing  (pyperf - this is what proves the speedup)"
	@echo "  pyperformance    Reference: the stock benchmark through pyperformance itself"
	@echo "                   -> $(PYPERF_JSON)"
	@echo "  measure          Baseline  -> $(BASE_JSON)"
	@echo "  opt_measure      Optimized -> $(OPT_JSON)"
	@echo "  compare          pyperf compare_to table (builds the JSONs if missing)"
	@echo ""
	@echo "Hardware counters"
	@echo "  fixedstat        perf stat over a FIXED number of copies, both sides."
	@echo "                   This is the ONLY valid counter comparison - see below."
	@echo ""
	@echo "Profiles"
	@echo "  flamegraph       perf record --call-graph $(CALLGRAPH), baseline"
	@echo "  opt_flamegraph   same for the optimized script"
	@echo "  pyspy            Python-level flame graph (baseline)"
	@echo "  opt_pyspy        Python-level flame graph (optimized)"
	@echo ""
	@echo "Pipelines"
	@echo "  bench            verify + all measurements + report"
	@echo "  report           Collect results into $(REPORT_DIR)/"
	@echo ""
	@echo "Hardware proposal (hw/)"
	@echo "  hw_sim           Icarus Verilog testbench of the memo-cache RTL"
	@echo ""
	@echo "Diagnostics"
	@echo "  stat_wholerun    perf stat over the whole pyperf run. NOT a valid"
	@echo "                   comparison: pyperf calibrates the loop count to a"
	@echo "                   time budget, so faster code simply performs more"
	@echo "                   copies and appears to use MORE instructions. Kept"
	@echo "                   only to reproduce DEEPCOPY_REPORT.md section 5.2."
	@echo ""
	@echo "Cleanup"
	@echo "  clean            Remove results for BENCH=$(BENCH) only"
	@echo "  distclean        Remove all results and the compiled extension"
	@echo "                   (the venv and FlameGraph are kept; rm -rf them by hand)"

tools:
	@ok=1; \
	for t in $(PYTHON) perf gcc $(PYSPY); do \
	  command -v $$t >/dev/null 2>&1 && echo "  ok      $$t" || { echo "  MISSING $$t"; ok=0; }; \
	done; \
	if [ -x $(FLAMEGRAPH)/flamegraph.pl ]; then echo "  ok      FlameGraph"; else echo "  MISSING $(FLAMEGRAPH)"; ok=0; fi; \
	if $(PYTHON) -c 'import pyperf' 2>/dev/null; then echo "  ok      pyperf"; else echo "  MISSING pyperf for $(PYTHON)"; ok=0; fi; \
	exit $$(( 1 - $$ok ))

# =============================================================================
#  Environment
# =============================================================================
# Non-interactive: no debconf questions, no needrestart dialog, keep existing
# config files on upgrade.
APT := DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -y \
       -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold

deps:
	@[ "$$(id -u)" = 0 ] || { echo "deps installs system packages: run as root (sudo make deps)"; exit 1; }
	$(APT) update
	$(APT) upgrade
	$(APT) install $(APT_PKGS)
	@# perf must match the running kernel; fall back to the generic meta-package
	$(APT) install linux-tools-$$(uname -r) || $(APT) install linux-tools-generic
	@perf --version

$(VENV)/.stamp:
	$(PY_BASE) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install $(PIP_PKGS)
	@touch $@

venv: $(VENV)/.stamp

$(FLAMEGRAPH)/flamegraph.pl:
	git clone --depth 1 https://github.com/brendangregg/FlameGraph.git $(FLAMEGRAPH)

flamegraph_tool: $(FLAMEGRAPH)/flamegraph.pl

setup: deps venv flamegraph_tool

# =============================================================================
#  Build & correctness
# =============================================================================
$(OUT_DIR) $(REPORT_DIR):
	mkdir -p $@

$(EXT_STAMP): $(EXT_SRC) $(EXT_SETUP)
	cd $(EXT_DIR) && $(PYTHON) setup.py build_ext --inplace
	@touch $@

ext: $(EXT_STAMP)

verify: $(EXT_STAMP)
	$(PYTHON) $(VERIFY_PY)

test:
	$(TEST_SH)

# =============================================================================
#  Timing - pyperf
# =============================================================================
$(BASE_JSON): $(BASELINE) | $(OUT_DIR)
	@rm -f $@
	$(PYTHON) $(BASELINE) -o $@

$(OPT_JSON): $(OPTIMIZED) $(EXT_STAMP) | $(OUT_DIR)
	@rm -f $@
	$(PYTHON) $(OPTIMIZED) -o $@

measure:     $(BASE_JSON)
opt_measure: $(OPT_JSON)

compare: $(BASE_JSON) $(OPT_JSON)
	@$(PYTHON) -m pyperf compare_to $(BASE_JSON) $(OPT_JSON) --table

# The unmodified benchmark through pyperformance's own harness. pyperformance
# builds its private benchmark venv from the running interpreter (the same debug
# build) under $(VENV)/cpython3.*-*/. This is the reference that
# baseline_deepcopy.py must reproduce.
$(PYPERF_JSON): | $(OUT_DIR)
	@rm -f $@
	$(PYTHON) -m pyperformance run --benchmarks $(BENCH) -o $@

pyperformance: $(PYPERF_JSON)

# =============================================================================
#  Hardware counters - fixed work, so both sides do the SAME amount of work
# =============================================================================
$(FIX_BASE): $(FIXED_DRIVER) | $(OUT_DIR)
	perf stat -r $(PERF_REPEAT) -e $(EVENTS) -o $@ -- $(PYTHON) $(FIXED_DRIVER) --impl baseline

$(FIX_OPT): $(FIXED_DRIVER) $(EXT_STAMP) | $(OUT_DIR)
	perf stat -r $(PERF_REPEAT) -e $(EVENTS) -o $@ -- $(PYTHON) $(FIXED_DRIVER) --impl optimized

fixedstat: $(FIX_BASE) $(FIX_OPT)
	@echo "Fixed-work counters written to $(FIX_BASE) and $(FIX_OPT)"

# =============================================================================
#  Profiles
# =============================================================================
$(BASE_DATA): $(BASELINE) | $(OUT_DIR)
	perf record -e cpu-clock -F $(PERF_FREQ) --call-graph $(CALLGRAPH) -o $@ $(PYTHON) $(BASELINE)

$(OPT_DATA): $(OPTIMIZED) $(EXT_STAMP) | $(OUT_DIR)
	perf record -e cpu-clock -F $(PERF_FREQ) --call-graph $(CALLGRAPH) -o $@ $(PYTHON) $(OPTIMIZED)

$(BASE_SVG): $(BASE_DATA)
	perf report -i $< --stdio > $(OUT_DIR)/perf_report_baseline.txt
	perf report -i $< --no-children --stdio -g none --percent-limit 0.7 \
	    | grep -E '^ +[0-9]' > $(OUT_DIR)/perf_self_baseline.txt
	perf script -i $< | $(FLAMEGRAPH)/stackcollapse-perf.pl | $(FLAMEGRAPH)/flamegraph.pl > $@

$(OPT_SVG): $(OPT_DATA)
	perf report -i $< --stdio > $(OUT_DIR)/perf_report_opt.txt
	perf report -i $< --no-children --stdio -g none --percent-limit 0.7 \
	    | grep -E '^ +[0-9]' > $(OUT_DIR)/perf_self_opt.txt
	perf script -i $< | $(FLAMEGRAPH)/stackcollapse-perf.pl | $(FLAMEGRAPH)/flamegraph.pl > $@

flamegraph:     $(BASE_SVG)
opt_flamegraph: $(OPT_SVG)

$(BASE_PYSPY): $(BASELINE) | $(OUT_DIR)
	@p=$$(command -v $(PYSPY) 2>/dev/null || find venv -name py-spy -type f 2>/dev/null | head -1); \
	test -n "$$p" || { echo "py-spy not found - run: make tools"; exit 1; }; \
	echo "$$p record -s --rate 100 --native -o $@ -- $(PYTHON) $(BASELINE)"; \
	"$$p" record -s --rate 100 --native -o $@ -- $(PYTHON) $(BASELINE)

$(OPT_PYSPY): $(OPTIMIZED) $(EXT_STAMP) | $(OUT_DIR)
	@p=$$(command -v $(PYSPY) 2>/dev/null || find venv -name py-spy -type f 2>/dev/null | head -1); \
	test -n "$$p" || { echo "py-spy not found - run: make tools"; exit 1; }; \
	echo "$$p record -s --rate 100 --native -o $@ -- $(PYTHON) $(OPTIMIZED)"; \
	"$$p" record -s --rate 100 --native -o $@ -- $(PYTHON) $(OPTIMIZED)

pyspy:     $(BASE_PYSPY)
opt_pyspy: $(OPT_PYSPY)

# =============================================================================
#  Diagnostic only - NOT a valid before/after comparison (see `make help`)
# =============================================================================
stat_wholerun: $(BASELINE) $(OPTIMIZED) $(EXT_STAMP) | $(OUT_DIR)
	@echo "WARNING: pyperf calibrates loops to a time budget, so these two runs"
	@echo "         do DIFFERENT amounts of work. Do not compare them directly."
	perf stat -r $(PERF_REPEAT) -e $(EVENTS) -o $(OUT_DIR)/perf_stat_baseline.txt -- $(PYTHON) $(BASELINE)
	perf stat -r $(PERF_REPEAT) -e $(EVENTS) -o $(OUT_DIR)/perf_stat_opt.txt -- $(PYTHON) $(OPTIMIZED)

# =============================================================================
#  Report assembly
# =============================================================================
report: compare fixedstat $(BASE_SVG) $(OPT_SVG) $(BASE_PYSPY) $(OPT_PYSPY) | $(REPORT_DIR)
	@$(PYTHON) -m pyperf compare_to $(BASE_JSON) $(OPT_JSON) --table > $(REPORT_DIR)/time_comparison.txt
	@if [ -f $(PYPERF_JSON) ]; then \
	   $(PYTHON) -m pyperf compare_to $(PYPERF_JSON) $(BASE_JSON) $(OPT_JSON) --table \
	     > $(REPORT_DIR)/time_comparison_pyperformance.txt; fi
	@{ echo "==========================================================="; \
	   echo " FIXED-WORK HARDWARE COUNTERS - identical work on both sides"; \
	   echo " (the only valid counter comparison; see DEEPCOPY_REPORT.md)"; \
	   echo "==========================================================="; \
	   echo; echo "--- BASELINE ---";  cat $(FIX_BASE); \
	   echo; echo "--- OPTIMIZED ---"; cat $(FIX_OPT); } > $(REPORT_DIR)/hw_stat_comparison.txt
	@cp $(OUT_DIR)/*.svg $(REPORT_DIR)/
	@echo "Report assembled in $(REPORT_DIR)/"

bench: verify report
	@echo ""
	@echo "Pipeline complete."
	@echo ""
	@cat $(REPORT_DIR)/time_comparison.txt

# =============================================================================
#  Hardware proposal - RTL testbench (no FPGA/ASIC flow; see report section 5)
# =============================================================================
hw_sim:
	iverilog -g2012 -o hw/tb_memo_cache.vvp hw/tb_memo_cache.v hw/memo_cache.v
	vvp -n hw/tb_memo_cache.vvp | tee hw/tb_memo_cache.log
	@grep -q ' 0 errors' hw/tb_memo_cache.log

# =============================================================================
#  Cleanup
# =============================================================================
clean:
	rm -rf $(OUT_DIR) $(REPORT_DIR)
	@echo "Removed results for BENCH=$(BENCH)."

distclean:
	rm -rf $(RESULTS_DIR) compare_report $(EXT_DIR)/build $(EXT_STAMP) $(EXT_DIR)/*.so
	@echo "Removed all results and the compiled extension."
