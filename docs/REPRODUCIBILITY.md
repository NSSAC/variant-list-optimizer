# Reproducibility Guide: Contour-Regularized Variant List Optimization

This companion document provides complete programmatic instructions, data provenance, command-line examples, and a table-by-table mapping to replicate all results reported in the Oxford *Bioinformatics* manuscript ([`docs/oup-authoring-template.tex`](oup-authoring-template.tex)).

---

## 1. Repository Layout & Architecture

```text
variant-list-optimizer/
├── src/variant_list_optimizer/
│   ├── contour.py          # Volume-normalized temporal contours, JSD + W1 + log-velocity
│   │                       # divergence, bridge skipping, and extinct-trunk umbrella penalty
│   ├── make_graph.py       # Pango tree builder, corrected temporal decay, FIPS normalization,
│   │                       # X* recombinant donor resolution, and HDF5/JSON serialization
│   └── optimize.py         # Corrected multi-hop NIA traversal, open-source HiGHS MILP solver
│                           # (optimize-highs), and dynamic --slot-cost / --max-size sizing
├── docs/
│   ├── oup-authoring-template.tex       # Overleaf-ready Oxford Bioinformatics LaTeX manuscript
│   ├── contour_optimization_concept.png # Figure 1 conceptual workflow diagram
│   ├── REPRODUCIBILITY.md               # This companion reproducibility guide
│   └── LongitudinalVariantBenchmarkingDatasets.pdf # Benchmark dataset specification report
└── benchmarks/
    ├── reproduce_all.sh                                  # Turnkey end-to-end replication runner
    ├── stage_benchmark_data.py                           # Programmatic dataset downloader/stager
    ├── pango_rbd_resolver.py                             # Phylogenetic Pango -> Spike RBD (331-531) resolver
    ├── run_track2_benchmark.py                           # Track 2A: Rolling biweekly concordance & lead times
    ├── evaluate_sensitivity_and_prospective_concordance.py # Track 2B: 8w prospective VI/NMI & sensitivity/FDR
    ├── evaluate_dynamic_slot_cost.py                     # Track 2C: Dynamic --slot-cost tradeoff sweep
    ├── run_track1_benchmark.py                           # Track 1: DMS escape & wet-lab Racmacs cartography
    ├── data/                                             # Staged Track 1 & Track 2 reference datasets
    ├── results_track2/                                   # Generated Track 2 CSV tables
    └── results_track1/                                   # Generated Track 1 CSV tables
```

---

## 2. Environment Setup

Requires Python $\ge 3.10$ with `scipy` (which bundles the open-source **HiGHS** mixed-integer linear programming solver), `numpy`, `pandas`, `networkx`, `click`, `h5py`, and `pango_aliasor`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install numpy pandas scipy networkx click more-itertools pyarrow pandera h5py requests scikit-learn matplotlib pyyaml pango_aliasor
pip install -e .
```

---

## 3. Running the CLI (`make_graph` and `optimize`)

### 3.1 Step 1: Build the Contour-Annotated Pango Hierarchy (`make_graph.py`)

```bash
python -m variant_list_optimizer.make_graph \
    --data-file benchmarks/data/outbreak_info_variants_states_long.csv.gz \
    --alias-file benchmarks/data/alias_key.json \
    --start-date 2024-01-13 \
    --end-date 2024-04-06 \
    --fips-list "10,11,24,42,51,54" \
    --temporal-importance-decay 0.1 \
    --weighting-mode contour \
    --resolve-recombinants \
    --output-file graph_contour_2024-04-06.json
```

### 3.2 Step 2: Optimize the Variant List (`optimize.py`)

1. **Fixed-Size Contour-Regularized MILP (`K = 20`)**:
   ```bash
   python -m variant_list_optimizer.optimize \
       --graph-file graph_contour_2024-04-06.json \
       --output-file list_contour_fixed20.json \
       optimize-highs \
       --size 20 \
       --penalty-mode contour
   ```

2. **Dynamic Behavior $\times$ Support Contour MILP (`--slot-cost` with `--max-size`)**:
   ```bash
   python -m variant_list_optimizer.optimize \
       --graph-file graph_contour_2024-04-06.json \
       --output-file list_contour_dynamic.json \
       optimize-highs \
       --size 20 \
       --max-size \
       --slot-cost 0.005 \
       --penalty-mode contour
   ```

3. **Baseline Static Tier-Penalty MILP (`DISTANCE_PENALTY = 10.0`, `K = 20`)**:
   ```bash
   python -m variant_list_optimizer.optimize \
       --graph-file graph_contour_2024-04-06.json \
       --output-file list_baseline_fixed20.json \
       optimize-highs \
       --size 20 \
       --distance-penalty 10.0 \
       --penalty-mode static
   ```

---

## 4. Turnkey Replication of All Manuscript Benchmarks

To stage all public datasets and execute the complete 133-window Track 2 and Track 1 longitudinal benchmarks (`2021-06-19` to `2026-07-18`, HHS Region 3) in a single command:

```bash
bash benchmarks/reproduce_all.sh
```

Or run each stage individually:

```bash
# Stage Track 1 & Track 2 public reference datasets into benchmarks/data/
python3 benchmarks/stage_benchmark_data.py

# Track 2A: Biweekly rolling curator concordance & emergence lead-time benchmark
python3 benchmarks/run_track2_benchmark.py

# Track 2B: 8-week lag-tolerant prospective concordance & standalone empirical sensitivity/precision
python3 benchmarks/evaluate_sensitivity_and_prospective_concordance.py

# Track 2C: Dynamic slot-cost (--slot-cost & --max-size) sensitivity/precision tradeoff sweep
python3 benchmarks/evaluate_dynamic_slot_cost.py

# Track 1: Antigenic Cartography & DMS Immune-Escape Validation (Benchmarks 1A, 1B, 1C, 1D)
python3 benchmarks/run_track1_benchmark.py
```

> **Custom Data Directory**: By default, scripts read from `benchmarks/data/`. If your regional sequence archive (`outbreak_info_variants_states_long.csv.gz`) or CDC archive (`cdc_variant_proportions_jr58_6ysp.csv`) is stored elsewhere, set `export VLOPT_DATA_DIR=/path/to/data` before running `reproduce_all.sh`.

---

## 5. Data Provenance Manifest

All reference datasets staged by [`benchmarks/stage_benchmark_data.py`](../benchmarks/stage_benchmark_data.py) originate from public genomic surveillance and experimental immunology repositories:

| File Path in `benchmarks/data/` | Description | Public Source / Repository |
| :--- | :--- | :--- |
| `outbreak_info_variants_states_long.csv.gz` | Daily state-level (FIPS) Pango lineage sequence counts (2020–2026) | CoV-Spectrum / Outbreak.info (`pull_cov-spectrum_strain_data-dates.py` / `immune_motif`) |
| `alias_key.json` | Official Pango alias prefix mapping | [`cov-lineages/pango-designation`](https://github.com/cov-lineages/pango-designation/blob/master/pango_designation/alias_key.json) |
| `lineage_notes.txt` | Official Pango lineage hierarchy & defining AA/nt mutations | [`cov-lineages/pango-designation`](https://github.com/cov-lineages/pango-designation/blob/master/lineage_notes.txt) |
| `cdc_variant_proportions_jr58_6ysp.csv` | Historical US CDC COVID Data Tracker Nowcast variant proportions by HHS Region | [`data.cdc.gov` (`jr58-6ysp`)](https://data.cdc.gov/api/views/jr58-6ysp/rows.csv?accessType=DOWNLOAD) |
| `clade_display_names.yml` & `nextstrain_clade_history.txt` | Nextstrain clade display names and git commit designation dates | [`nextstrain/ncov`](https://github.com/nextstrain/ncov/blob/master/defaults/clade_display_names.yml) |
| `track1/escape.csv` | 53,282 yeast-display DMS antibody escape measurements across 823 mAbs and RBD sites 331–531 | [`jbloomlab/SARS2-RBD-escape-calc`](https://github.com/jbloomlab/SARS2-RBD-escape-calc/blob/main/results/escape.csv) |
| `track1/antibody_IC50s.csv` | 11,143 virus-specific mAb neutralization $\text{IC}_{50}$ measurements across 17 SARS-CoV-2 variants | [`jbloomlab/SARS2-RBD-escape-calc`](https://github.com/jbloomlab/SARS2-RBD-escape-calc/blob/main/results/antibody_IC50s.csv) |
| `track1/roessler2024_coords.json` & `roessler2024_gmt.csv` | Multi-era wet-lab Racmacs 2D antigenic coordinates and serum GMT titers (`Wuhan` through `JN.1`, `KP.2`, `KP.3`) | [`antonia-netzl/roat_netzl2024`](https://github.com/antonia-netzl/roat_netzl2024) |
| `track1/roessler2023_coords.json` & `roessler2023_titers.csv` | Human Omicron (`BA.1`, `BA.2`, `BA.5`, `BQ.1.1`, `XBB.1`) cartography and serum titers | [`antonia-netzl/roessler2023_cartography`](https://github.com/antonia-netzl/roessler2023_cartography) |
| `track1/wilks2023_titerdata.csv` | Human pre-Omicron and Delta (`B.1.617.2`, `AY.1`, `AY.2`, `AY.3`) neutralization titers | [`acorg/wilks2022_sars2_cartography`](https://github.com/acorg/wilks2022_sars2_cartography) |
| `track1/covrdb_variant_consensus.csv` | Consensus Spike amino acid mutations across designated variants | [`hivdb/covid-drdb-payload`](https://github.com/hivdb/covid-drdb-payload) |

---

## 6. Mapping from Manuscript Tables (`oup-authoring-template.tex`) to Scripts & Output CSVs

Every quantitative table and statistical test in [`docs/oup-authoring-template.tex`](oup-authoring-template.tex) is directly reproduced by the scripts and CSV outputs listed below:

| Manuscript Element | Generating Script | Output CSV File in `benchmarks/` | Key Columns / Metrics |
| :--- | :--- | :--- | :--- |
| **Table 1**: Track 2 Longitudinal Partition Concordance (Same-Day $\text{VI}_{\text{now}}$, 4-Week-Ahead $\text{VI}_{t+4\text{w}}$, & 8-Week Lag-Tolerant Prospective $\text{VI}_{[t,t+8\text{w}]}$ / $\text{NMI}_{[t,t+8\text{w}]}$) | [`run_track2_benchmark.py`](../benchmarks/run_track2_benchmark.py) & [`evaluate_sensitivity_and_prospective_concordance.py`](../benchmarks/evaluate_sensitivity_and_prospective_concordance.py) | [`results_track2/track2_rolling_metrics.csv`](../benchmarks/results_track2/track2_rolling_metrics.csv) & [`results_track2/track2_prospective_and_attention_metrics.csv`](../benchmarks/results_track2/track2_prospective_and_attention_metrics.csv) | `ay_share_base`, `ay_share_contour`, `vi_cdc_now_base`, `vi_cdc_now_contour`, `vi_cdc_plus4w_base`, `vi_cdc_plus4w_contour`, `vi_prospective8w_base`, `vi_prospective8w_contour`, `nmi_prospective8w_base`, `nmi_prospective8w_contour` |
| **Table 2**: Early-Warning Emergence Detection Dates & Lead Times ($\Delta t_{\text{lead}}$ across 23 Focal Variants) | [`run_track2_benchmark.py`](../benchmarks/run_track2_benchmark.py) | [`results_track2/track2_emergence_lead_times.csv`](../benchmarks/results_track2/track2_emergence_lead_times.csv) | `focal_variant`, `t_contour`, `t_baseline`, `t_cdc_hhs3`, `t_nextstrain_git`, `lead_contour_vs_base_days`, `lead_contour_vs_cdc_days`, `lead_contour_vs_nextstrain_days` |
| **Table 3**: Standalone Empirical Sensitivity (Recall on $N=83$ Significant & $N=36$ Dominant Sweeps), False-Attention Breakdown, & Dynamic Slot-Cost Sweep | [`evaluate_sensitivity_and_prospective_concordance.py`](../benchmarks/evaluate_sensitivity_and_prospective_concordance.py) & [`evaluate_dynamic_slot_cost.py`](../benchmarks/evaluate_dynamic_slot_cost.py) | [`results_track2/track2_distinct_significant_sensitivity.csv`](../benchmarks/results_track2/track2_distinct_significant_sensitivity.csv) & [`results_track2/track2_dynamic_slot_cost_sweep.csv`](../benchmarks/results_track2/track2_dynamic_slot_cost_sweep.csv) | `mean_returned_size`, `unfiltered_slot_precision`, `unfiltered_slot_fizzled_fdr`, `signal_mass_precision`, `sig_recall_ever`, `sig_recall_lt5pct`, `dom_recall_ever`, `dom_recall_lt2pct`, `dom_median_detect_share_pct` |
| **Table 4**: Track 1 Benchmarks 1A, 1C, & 1D (Redundant Split Share $d_{\text{DMS}}=0$, Distortion $W_{\text{DMS}}$ & $W_{\text{Racmacs}}$, Variance Ratio $\mathcal{I}_{\text{ag}}$, Neutralization Titer RMSE, & Mantel $r_M$) | [`run_track1_benchmark.py`](../benchmarks/run_track1_benchmark.py) | [`results_track1/track1_window_metrics.csv`](../benchmarks/results_track1/track1_window_metrics.csv) | `base_redund_share`, `cont_redund_share`, `base_W_dms`, `cont_W_dms`, `base_W_rac`, `cont_W_rac`, `base_CH_rac`, `cont_CH_rac`, `dyn_CH_rac`, `base_rmse_dms`, `cont_rmse_dms`, `base_rmse_rac`, `cont_rmse_rac`, `mantel_r_contour_dms`, `mantel_p_contour_dms` |
| **Table 5**: Track 1 Benchmark 1B Parent–Child Split vs. Fold Antigenic Escape Discrimination ($N = 31,317$ Active Edges) | [`run_track1_benchmark.py`](../benchmarks/run_track1_benchmark.py) | [`results_track1/track1_edge_discrimination.csv`](../benchmarks/results_track1/track1_edge_discrimination.csv) | `d_contour`, `d_dms`, `d_racmacs`, `n_rbd_diff`, `split_base`, `split_contour`, `split_dyn`, `only_contour`, `only_base` |

---

## 7. Compiling the Manuscript in Overleaf

To compile [`docs/oup-authoring-template.tex`](oup-authoring-template.tex) in Overleaf:
1. Upload the Oxford University Press authoring template bundle files (`oup-authoring-template.cls` and supporting style/font files) into your Overleaf project root.
2. Replace `oup-authoring-template.tex` in Overleaf with [`docs/oup-authoring-template.tex`](oup-authoring-template.tex).
3. Upload [`docs/contour_optimization_concept.png`](contour_optimization_concept.png) to the Overleaf project root (or into the `Fig/` folder; `\graphicspath{{./}{Fig/}}` is pre-configured to search both).
4. Compile using **pdfLaTeX** (or **XeLaTeX**).
