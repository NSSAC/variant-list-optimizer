#!/usr/bin/env bash
# ==============================================================================
# Master Reproducibility Pipeline for Contour-Regularized Variant List Optimizer
# Reproduces all Track 1 (Antigenic Cartography & DMS Escape) and Track 2
# (Curator Concordance, Lead-Time, Sensitivity/Precision, & Dynamic Sizing)
# results reported in `docs/oup-authoring-template.tex`.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "======================================================================"
echo "1. Staging Track 1 & Track 2 Public Reference Datasets"
echo "======================================================================"
"${PYTHON_BIN}" "${SCRIPT_DIR}/stage_benchmark_data.py"

echo ""
echo "======================================================================"
echo "2. Track 2A: Rolling Biweekly Curator Concordance & Lead Times (133 Windows)"
echo "======================================================================"
"${PYTHON_BIN}" "${SCRIPT_DIR}/run_track2_benchmark.py"

echo ""
echo "======================================================================"
echo "3. Track 2B: 8-Week Lag-Tolerant Concordance & Standalone Sensitivity/Precision"
echo "======================================================================"
"${PYTHON_BIN}" "${SCRIPT_DIR}/evaluate_sensitivity_and_prospective_concordance.py"

echo ""
echo "======================================================================"
echo "4. Track 2C: Dynamic Behavior x Support Slot-Cost Sweep (--slot-cost & --max-size)"
echo "======================================================================"
"${PYTHON_BIN}" "${SCRIPT_DIR}/evaluate_dynamic_slot_cost.py"

echo ""
echo "======================================================================"
echo "5. Track 1: Antigenic Cartography & DMS Immune-Escape Validation (Benchmarks 1A-1D)"
echo "======================================================================"
"${PYTHON_BIN}" "${SCRIPT_DIR}/run_track1_benchmark.py"

echo ""
echo "======================================================================"
echo "All benchmarks completed! Output CSV tables are located in:"
echo "  - ${SCRIPT_DIR}/results_track2/"
echo "  - ${SCRIPT_DIR}/results_track1/"
echo "======================================================================"
