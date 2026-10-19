"""Programmatic Data Staging & Verification for Track 1 & Track 2 Longitudinal Benchmarks.

Stages all public reference datasets required to replicate the Track 1 (Antigenic Cartography
& DMS Immune-Escape) and Track 2 (Curator Concordance, Lead-Time, & Sensitivity/Precision)
benchmarks into `benchmarks/data/` and `benchmarks/data/track1/`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARKS_DIR.parent
DATA_DIR = Path(os.environ.get("VLOPT_DATA_DIR", str(BENCHMARKS_DIR / "data")))
TRACK1_DIR = DATA_DIR / "track1"

DATA_DIR.mkdir(parents=True, exist_ok=True)
TRACK1_DIR.mkdir(parents=True, exist_ok=True)

# Canonical public URLs for all Track 1 & Track 2 reference datasets
TRACK2_PUBLIC_URLS: dict[str, str] = {
    "alias_key.json": "https://raw.githubusercontent.com/cov-lineages/pango-designation/master/pango_designation/alias_key.json",
    "lineage_notes.txt": "https://raw.githubusercontent.com/cov-lineages/pango-designation/master/lineage_notes.txt",
    "clade_display_names.yml": "https://raw.githubusercontent.com/nextstrain/ncov/master/defaults/clade_display_names.yml",
    "clades.tsv": "https://raw.githubusercontent.com/nextstrain/ncov/master/defaults/clades.tsv",
    "cdc_variant_proportions_jr58_6ysp.csv": "https://data.cdc.gov/api/views/jr58-6ysp/rows.csv?accessType=DOWNLOAD",
}

TRACK1_PUBLIC_URLS: dict[str, str] = {
    # 1. Bloom Lab SARS2-RBD-escape-calc (Greaney et al. 2022, Cao et al. 2023, Dadonaite et al. 2024)
    "config.yaml": "https://raw.githubusercontent.com/jbloomlab/SARS2-RBD-escape-calc/main/config.yaml",
    "escape.csv": "https://raw.githubusercontent.com/jbloomlab/SARS2-RBD-escape-calc/main/results/escape.csv",
    "antibody_IC50s.csv": "https://raw.githubusercontent.com/jbloomlab/SARS2-RBD-escape-calc/main/results/antibody_IC50s.csv",
    "antibody_reweighting.csv": "https://raw.githubusercontent.com/jbloomlab/SARS2-RBD-escape-calc/main/data/antibody_reweighting.csv",
    "antibody_sources.csv": "https://raw.githubusercontent.com/jbloomlab/SARS2-RBD-escape-calc/main/results/antibody_sources.csv",
    # 2. Roessler & Netzl et al. 2024/2025 (Multi-era wet-lab cartography through JN.1/KP.2/KP.3)
    "roessler2024_gmt.csv": "https://raw.githubusercontent.com/antonia-netzl/roat_netzl2024/main/data/titer_data/240905_GMT_table.csv",
    "roessler2024_fc.csv": "https://raw.githubusercontent.com/antonia-netzl/roat_netzl2024/main/data/titer_data/240905_FC_table.csv",
    "roessler2024_map.ace": "https://raw.githubusercontent.com/antonia-netzl/roat_netzl2024/main/som/01_antigenic_maps/maps/map_no_outliers_1000_2d.ace",
    # 3. Roessler et al. 2023 (Nature Communications human Omicron cartography)
    "roessler2023_titers.csv": "https://raw.githubusercontent.com/antonia-netzl/roessler2023_cartography/main/data/titer_data/titers_I_II_III.csv",
    "roessler2023_map.ace": "https://raw.githubusercontent.com/antonia-netzl/roessler2023_cartography/main/maps/map_I_II_III_new.ace",
    # 4. Wilks et al. 2023 (Science human cartography & Delta AY.* neutralization data)
    "wilks2023_titerdata.csv": "https://raw.githubusercontent.com/acorg/wilks2022_sars2_cartography/main/data/titer_data/titerdata_human.csv",
    "wilks2023_effect_sizes.csv": "https://raw.githubusercontent.com/acorg/wilks2022_sars2_cartography/main/som/variant_GMT_fold_changes/effect_sizes.csv",
    # 5. Stanford CoV-RDB consensus Spike mutations
    "covrdb_variant_consensus.csv": "https://raw.githubusercontent.com/hivdb/covid-drdb-payload/master/tables/variant_consensus_mutations.csv",
    "covrdb_isolate_mutations.csv": "https://raw.githubusercontent.com/hivdb/covid-drdb-payload/master/tables/isolate_mutations.csv",
}


def parse_ace_map(ace_text: str) -> dict[str, tuple[float, float]]:
    """Extract 2D antigen coordinates from a Racmacs .ace JSON file."""
    m_a = re.search(r'"a"\s*:\s*(\[.*?\])\s*,\s*"[A-Za-z]"', ace_text, re.S)
    ags_raw = json.loads(m_a.group(1))
    ags = [a["N"] for a in ags_raw]
    pos = ace_text.find('"P"')
    m_l = re.search(r'"l"\s*:\s*(\[\s*\[.*?\]\s*\])', ace_text[pos:], re.S)
    coords_list = json.loads(m_l.group(1))
    ag_coords = {}
    for i, name in enumerate(ags):
        row = coords_list[i]
        if row and len(row) >= 2:
            ag_coords[name] = (float(row[0]), float(row[1]))
    return ag_coords


def download_if_missing(dest: Path, url: str) -> bool:
    """Download `url` to `dest` if `dest` does not already exist and is non-empty."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [OK] {dest.name} ({dest.stat().st_size:,} bytes) already present.")
        return True
    print(f"  [DOWNLOAD] Fetching {dest.name} from {url} ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
            shutil.copyfileobj(resp, out)
        print(f"  [SAVED] {dest.name} ({dest.stat().st_size:,} bytes)")
        return True
    except Exception as exc:
        print(f"  [WARN] Could not download {dest.name}: {exc}")
        return False


def stage_all_datasets() -> None:
    print(f"Staging benchmark datasets into {DATA_DIR} ...")

    # 1. Stage local regional sequence table if available in sibling repository
    seq_dest = DATA_DIR / "outbreak_info_variants_states_long.csv.gz"
    sibling_seq = REPO_ROOT.parent / "immune_motif" / "data" / "outbreak_info_variants_states_long.csv.gz"
    if not seq_dest.exists() and sibling_seq.exists():
        shutil.copy2(sibling_seq, seq_dest)
        print(f"  [COPIED] {seq_dest.name} from {sibling_seq}")
    elif seq_dest.exists():
        print(f"  [OK] {seq_dest.name} ({seq_dest.stat().st_size:,} bytes) present.")
    else:
        print(
            f"  [NOTE] Place regional sequence counts at {seq_dest} "
            "(or set VLOPT_DATA_DIR to directory containing outbreak_info_variants_states_long.csv.gz)."
        )

    # 2. Extract Nextstrain git history if sibling repo ../ncov exists and history file is missing
    ns_hist = DATA_DIR / "nextstrain_clade_history.txt"
    sibling_ncov = REPO_ROOT.parent / "ncov"
    if not ns_hist.exists() and (sibling_ncov / ".git").exists():
        cmd = [
            "git",
            "-C",
            str(sibling_ncov),
            "log",
            "-p",
            "--format=COMMIT_DATE:%cs %H",
            "--",
            "defaults/clade_display_names.yml",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout:
            ns_hist.write_text(res.stdout, encoding="utf-8")
            print(f"  [EXTRACTED] {ns_hist.name} from {sibling_ncov}")

    # 3. Download Track 2 public reference files
    print("\n--- Track 2 Reference Files ---")
    for fname, url in TRACK2_PUBLIC_URLS.items():
        download_if_missing(DATA_DIR / fname, url)

    # 4. Download Track 1 public reference files
    print("\n--- Track 1 Reference Files ---")
    for fname, url in TRACK1_PUBLIC_URLS.items():
        # Skip large .ace download if parsed coords JSON already exists
        if fname == "roessler2024_map.ace" and (TRACK1_DIR / "roessler2024_coords.json").exists():
            print("  [OK] roessler2024_coords.json already extracted.")
            continue
        if fname == "roessler2023_map.ace" and (TRACK1_DIR / "roessler2023_coords.json").exists():
            print("  [OK] roessler2023_coords.json already extracted.")
            continue
        download_if_missing(TRACK1_DIR / fname, url)

    # 5. Parse Racmacs .ace maps into 2D coordinate JSONs if needed
    for prefix in ("roessler2024", "roessler2023"):
        coords_json = TRACK1_DIR / f"{prefix}_coords.json"
        ace_file = TRACK1_DIR / f"{prefix}_map.ace"
        if not coords_json.exists() and ace_file.exists():
            coords = parse_ace_map(ace_file.read_text(encoding="utf-8"))
            coords_json.write_text(json.dumps(coords, indent=2), encoding="utf-8")
            print(f"  [PARSED] {coords_json.name} ({len(coords)} antigens)")


if __name__ == "__main__":
    stage_all_datasets()
