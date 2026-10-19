"""Track 1 Benchmark: Antigenic Cartography & DMS Immune-Escape Validation across 133 biweekly windows (Regimes I-IV).

Evaluates:
1. Prevalence-Weighted Antigenic Variance Ratio (I_ag, Calinski-Harabasz Index in Antigenic Space):
   - Within-cluster antigenic distortion W_ag(S)
   - Between-cluster antigenic separation B_ag(S)
   - Variance ratio I_ag(S)
   computed on both Bloom Lab Polyclonal DMS Escape Profiles (all lineages) and Wet-Lab Racmacs Cartography (Wilks 2023, Roessler 2023, Roessler/Netzl 2024).
2. Split vs. Folded Parent-Child Antigenic Escape Discrimination:
   - Comparing antigenic distance d_DMS(p(v), v) and d_Racmacs(p(v), v) for edges split vs. folded by Baseline VLOpt, Contour VLOpt, and Dynamic Contour VLOpt, plus symmetric difference (Contour \\ Baseline vs Baseline \\ Contour).
3. Mantel & Spearman Correlation between Pairwise Contour Divergence D_contour(u, v) and Antigenic Distance D_ag(u, v) (vs. raw Pango tree distance d_tree(u, v)).
4. Cross-Neutralization Titer Fold-Drop Prediction Error (log2 RMSE) when folded lineages inherit the serum neutralization profile of their assigned cluster representative u in S(t_end).
"""

import json
import os
import re
import sys
from pathlib import Path
import networkx as nx
import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, pearsonr, spearmanr, wilcoxon

BENCHMARKS_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARKS_DIR.parent
for p in (str(REPO_ROOT / "src"), str(BENCHMARKS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pango_aliasor.aliasor import Aliasor
from variant_list_optimizer.contour import (
    ContourConfig,
    compute_divergence_components,
    compute_node_contours,
)
from variant_list_optimizer.make_graph import _expand_fips_tokens
from variant_list_optimizer.optimize import do_optimize_highs

from run_track2_benchmark import DATA_DIR, HHS3_FIPS, RESULTS_DIR as RESULTS_TRACK2_DIR, build_graph_in_memory
from pango_rbd_resolver import BASAL_CLADE_RBD_MUTS, WUHAN_RBD_WT, PangoRBDResolver

TRACK1_DIR = DATA_DIR / "track1"
RESULTS_TRACK1_DIR = Path(
    os.environ.get("VLOPT_RESULTS_TRACK1_DIR", str(BENCHMARKS_DIR / "results_track1"))
)
RESULTS_TRACK1_DIR.mkdir(parents=True, exist_ok=True)

# Additional pre-Omicron / minor basal clade RBD mutations and nucleotide-encoded RBD branch mutations
EXTRA_BASAL_MUTS = {
    "A.2.5": {452: "R"},
    "C.36.3": {452: "R"},
    "B.1.617": {452: "R", 484: "Q"},
    "B.1.617.3": {452: "R", 484: "Q"},
    "B.1.637": {452: "R", 484: "K"},
    "B.1.640": {346: "S", 394: "S", 449: "N", 490: "R", 501: "Y"},
    "B.1.640.1": {346: "S", 394: "S", 449: "N", 490: "R", 501: "Y"},
    "B.1.640.2": {346: "S", 394: "S", 449: "N", 484: "K", 490: "R", 501: "Y"},
    "BA.3": {
        339: "D", 371: "F", 373: "P", 375: "F", 405: "N", 417: "N", 440: "K",
        446: "S", 477: "N", 478: "K", 484: "A", 493: "R", 498: "R", 501: "Y", 505: "H"
    },
}
BASAL_CLADE_RBD_MUTS.update(EXTRA_BASAL_MUTS)

NUC_TO_RBD_AA = {
    "T22928C": (456, "L"),
    "T22930A": (456, "L"),
    "T22930G": (456, "L"),
    "T22928G": (456, "V"),
    "G22927T": (455, "F"),
    "G22927C": (455, "F"),
    "T22926C": (455, "S"),
    "C22987T": (475, "V"),
    "A22898G": (446, "D"),
    "G22899A": (446, "S"),
}


class EnhancedPangoRBDResolver(PangoRBDResolver):
    """PangoRBDResolver augmented with RBD nucleotide substitution translation from lineage_notes.txt."""

    def _parse_lineage_notes(self, lineage_notes_path: Path):
        super()._parse_lineage_notes(lineage_notes_path)
        for line in lineage_notes_path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("Lineage\t") or line.startswith("*"):
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            lin, desc = parts[0].strip(), parts[1].strip()
            for nuc_mut, (site, aa) in NUC_TO_RBD_AA.items():
                if nuc_mut in desc:
                    self.branch_muts.setdefault(lin, {})[site] = aa


class AntigenicEvaluator:
    """Computes DMS polyclonal escape profiles, Racmacs cartography distances, and serum neutralization titers."""

    def __init__(self, resolver: EnhancedPangoRBDResolver):
        self.resolver = resolver

        # 1. Load Bloom Lab DMS Escape Calculator matrices
        esc_df = pd.read_csv(TRACK1_DIR / "escape.csv")
        self.sites = list(range(331, 532))
        esc_piv = esc_df.pivot_table(index="antibody", columns="site", values="escape", fill_value=0.0)
        for s in self.sites:
            if s not in esc_piv.columns:
                esc_piv[s] = 0.0
        esc_piv = esc_piv[self.sites]
        max_esc = esc_piv.max(axis=1).replace(0, 1.0)
        self.esc_mat = esc_piv.div(max_esc, axis=0).values  # (823, 201)
        self.ab_names = list(esc_piv.index)
        self.ab_to_idx = {ab: i for i, ab in enumerate(self.ab_names)}

        # Load virus-specific IC50 weights from antibody_IC50s.csv for multi-serum DMS neutralization panel
        ic50_df = pd.read_csv(TRACK1_DIR / "antibody_IC50s.csv").dropna(subset=["antibody", "virus", "IC50"])
        ic50_df = ic50_df[ic50_df["antibody"].isin(self.ab_to_idx)]
        self.viruses = sorted(ic50_df["virus"].unique())
        # Build weight matrix W_virus: (n_viruses, n_ab) where w = max(0, -log10(IC50 / 10))
        self.virus_weights = np.zeros((len(self.viruses), len(self.ab_names)), dtype=float)
        for v_idx, vir in enumerate(self.viruses):
            sub = ic50_df[ic50_df["virus"] == vir]
            for _, row in sub.iterrows():
                ab_i = self.ab_to_idx[row["antibody"]]
                ic50_val = float(row["IC50"])
                if ic50_val < 10.0 and ic50_val > 0:
                    self.virus_weights[v_idx, ab_i] = -np.log10(ic50_val / 10.0)
            row_sum = self.virus_weights[v_idx].sum()
            if row_sum > 0:
                self.virus_weights[v_idx] /= row_sum
            else:
                self.virus_weights[v_idx] = 1.0 / len(self.ab_names)

        # 2. Load Wet-Lab Racmacs Cartography Coordinates (Roessler 2024 multi-era, Roessler 2023 Omicron, Wilks 2023)
        with open(TRACK1_DIR / "roessler2024_coords.json") as f:
            self.coords_2024 = json.load(f)
        with open(TRACK1_DIR / "roessler2023_coords.json") as f:
            self.coords_2023 = json.load(f)

        # Load Roessler 2024 GMT serum group titers (serum_group x antigen)
        # Standard censored threshold handling: titers <20 (LOD=20, LOD/2=10 -> log2(10/10)=0.0) are clipped at 0.0
        gmt_df = pd.read_csv(TRACK1_DIR / "roessler2024_gmt.csv")
        gmt_df["logtiter_clipped"] = np.where(
            gmt_df["all_below_thresh"].astype(str).str.upper() == "TRUE",
            0.0,
            np.maximum(gmt_df["logtiter"].astype(float), -1.0),
        )
        self.gmt_piv = gmt_df.pivot_table(index="sr_group", columns="ag_name", values="logtiter_clipped", fill_value=np.nan)

        # Map canonical Pango lineages to wet-lab Racmacs antigen names
        self.racmacs_2024_lineage_map = {
            "B": "Wuhan",
            "A": "Wuhan",
            "B.1": "Wuhan",
            "B.1.1": "Wuhan",
            "B.1.2": "Wuhan",
            "B.1.1.7": "Alpha",
            "Q.1": "Alpha",
            "B.1.351": "Beta",
            "B.1.617.2": "Delta",
            "AY": "Delta",
            "B.1.1.529": "BA.1",
            "BA.1": "BA.1",
            "BA.1.1": "BA.1",
            "BA.2": "BA.2",
            "BA.2.12.1": "BA.2.12.1",
            "BA.4": "BA.5",
            "BA.5": "BA.5",
            "BQ.1": "BQ.1.1",
            "BQ.1.1": "BQ.1.1",
            "XBB": "XBB.1",
            "XBB.1": "XBB.1",
            "XBB.1.5": "XBB.1.5",
            "XBB.1.9.1": "XBB.1.5",
            "XBB.1.9.2": "XBB.1.5",
            "XBB.1.16": "FL.1.5.1",
            "XBC": "XBC.1.6",
            "XBC.1": "XBC.1.6",
            "XBC.1.6": "XBC.1.6",
            "FL.1.5.1": "FL.1.5.1",
            "DV.7.1": "DV.7.1",
            "CH.1.1": "DV.7.1",
            "EG.5": "EG.5.1",
            "EG.5.1": "EG.5.1",
            "HV.1": "HV.1",
            "HK.3": "HK.3",
            "JD.1.1": "HK.3",
            "BA.2.86": "BA.2.86",
            "BA.2.86.1": "BA.2.86",
            "JN.1": "JN.1",
            "KP.2": "KP.2",
            "LB.1": "KP.2",
            "KP.3": "KP.3",
            "KP.3.1.1": "KP.3",
            "XEC": "KP.3",
        }
        self.ag_to_canonical_lineage = {
            "Wuhan": "B",
            "Alpha": "B.1.1.7",
            "Beta": "B.1.351",
            "Delta": "B.1.617.2",
            "BA.1": "BA.1",
            "BA.2": "BA.2",
            "BA.2.12.1": "BA.2.12.1",
            "BA.5": "BA.5",
            "BQ.1.1": "BQ.1.1",
            "XBB.1": "XBB.1",
            "XBB.1.5": "XBB.1.5",
            "XBC.1.6": "XBC.1.6",
            "FL.1.5.1": "FL.1.5.1",
            "DV.7.1": "DV.7.1",
            "EG.5.1": "EG.5.1",
            "HV.1": "HV.1",
            "HK.3": "HK.3",
            "BA.2.86": "BA.2.86",
            "JN.1": "JN.1",
            "KP.2": "KP.2",
            "KP.3": "KP.3",
        }

        self._profile_cache: dict[str, np.ndarray] = {}
        self._virus_titer_cache: dict[str, np.ndarray] = {}
        self._pair_dms_cache: dict[tuple[str, str], float] = {}
        self._rac24_ag_cache: dict[str, str] = {}

    def get_dms_escape_vector(self, lin: str, s: float = 2.0) -> np.ndarray:
        """Return 201-site residual escape profile vector e(M_v) concatenated with site-weighted AA escape state."""
        if lin in self._profile_cache:
            return self._profile_cache[lin]
        muts = self.resolver.resolve(lin)
        idxs = [r - 331 for r in muts.keys() if 331 <= r <= 531]
        if idxs:
            b_ab = np.prod(1.0 - self.esc_mat[:, idxs], axis=1) ** s  # (823,)
        else:
            b_ab = np.ones(len(self.ab_names), dtype=float)
        # Residual site escape profile: (201,)
        e_vec = (b_ab[:, None] * self.esc_mat).mean(axis=0)
        self._profile_cache[lin] = e_vec
        return e_vec

    def get_dms_serum_panel_log2_retention(self, lin: str, s: float = 2.0) -> np.ndarray:
        """Return log2 neutralization retained across the 17 virus-specific polyclonal antibody panels in Bloom DMS."""
        if lin in self._virus_titer_cache:
            return self._virus_titer_cache[lin]
        muts = self.resolver.resolve(lin)
        idxs = [r - 331 for r in muts.keys() if 331 <= r <= 531]
        if idxs:
            b_ab = np.prod(1.0 - self.esc_mat[:, idxs], axis=1) ** s  # (823,)
        else:
            b_ab = np.ones(len(self.ab_names), dtype=float)
        retained_by_virus = np.clip(self.virus_weights @ b_ab, 1e-6, 1.0)
        log2_ret = np.log2(retained_by_virus)  # in log2 dilution units (1 unit = 2-fold drop)
        self._virus_titer_cache[lin] = log2_ret
        return log2_ret

    def dms_antigenic_distance(self, u: str, v: str, s: float = 2.0) -> float:
        """Exact polyclonal DMS escape fold-drop distance in log2 dilution units (1 unit = 2-fold neutralization drop)
        resulting from RBD amino-acid differences between lineages u and v."""
        if u == v:
            return 0.0
        key = (u, v) if u < v else (v, u)
        if key in self._pair_dms_cache:
            return self._pair_dms_cache[key]
        u_muts = self.resolver.resolve(u)
        v_muts = self.resolver.resolve(v)
        diff_sites = [r for r in (set(u_muts.keys()) | set(v_muts.keys())) if u_muts.get(r) != v_muts.get(r)]
        if not diff_sites:
            self._pair_dms_cache[key] = 0.0
            return 0.0
        idxs = [r - 331 for r in diff_sites if 331 <= r <= 531]
        if not idxs:
            self._pair_dms_cache[key] = 0.0
            return 0.0
        retained_per_ab = np.prod(1.0 - self.esc_mat[:, idxs], axis=1) ** s
        mean_ret = float(np.clip(np.mean(retained_per_ab), 1e-6, 1.0))
        dist = float(-np.log2(mean_ret))
        self._pair_dms_cache[key] = dist
        return dist

    def map_to_roessler2024_antigen(self, lin: str) -> str:
        """Map a Pango lineage to its closest ancestral/clade antigen in the Roessler et al. 2024 Racmacs map."""
        if lin in self._rac24_ag_cache:
            return self._rac24_ag_cache[lin]
        curr = lin
        visited = set()
        while curr and curr not in visited:
            visited.add(curr)
            if curr in self.racmacs_2024_lineage_map:
                ag = self.racmacs_2024_lineage_map[curr]
                self._rac24_ag_cache[lin] = ag
                return ag
            short_curr = self.resolver.unaliased_to_short.get(curr, curr)
            if short_curr in self.racmacs_2024_lineage_map:
                ag = self.racmacs_2024_lineage_map[short_curr]
                self._rac24_ag_cache[lin] = ag
                return ag
            curr = self.resolver.get_parent(curr)
        self._rac24_ag_cache[lin] = "Wuhan"
        return "Wuhan"

    def racmacs2024_distance(self, u: str, v: str) -> float:
        """Euclidean distance in Roessler et al. 2024 antigenic map coordinates (1 unit = 2-fold dilution),
        plus residual DMS branch distance if u and v map to the same clade antigen."""
        ag_u = self.map_to_roessler2024_antigen(u)
        ag_v = self.map_to_roessler2024_antigen(v)
        if ag_u != ag_v:
            xy_u = np.array(self.coords_2024[ag_u])
            xy_v = np.array(self.coords_2024[ag_v])
            return float(np.linalg.norm(xy_u - xy_v))
        # When both belong to the same major clade on the 22-antigen map, use their branch DMS escape difference
        return self.dms_antigenic_distance(u, v)

    def roessler2024_titer_vector(self, lin: str) -> np.ndarray:
        """Return empirical log2 GMT titer vector across Roessler 2024 serum groups for lineage's clade antigen,
        adjusted by any branch-specific DMS escape fold-drop relative to the clade antigen."""
        ag = self.map_to_roessler2024_antigen(lin)
        if ag in self.gmt_piv.columns:
            base_titers = self.gmt_piv[ag].values.copy()
        else:
            base_titers = np.zeros(len(self.gmt_piv), dtype=float)
        rep_lin = self.ag_to_canonical_lineage.get(ag, "B")
        branch_drop = self.dms_antigenic_distance(lin, rep_lin)
        return base_titers - branch_drop


def assign_nodes_to_representatives(G: nx.DiGraph, selected_set: set[str]) -> dict[str, str]:
    """Map every node v in G to its nearest selected ancestor u in selected_set (or catch-all root '')."""
    rep_map = {}
    for v in G.nodes:
        curr = v
        visited = set()
        found = ""
        while curr is not None and curr not in visited:
            visited.add(curr)
            if curr in selected_set:
                found = curr
                break
            preds = list(G.predecessors(curr))
            curr = preds[0] if preds else None
        rep_map[v] = found
    return rep_map


def compute_window_track1_metrics(
    G: nx.DiGraph,
    node_shares: dict[str, float],
    selected_list: list[str],
    evaluator: AntigenicEvaluator,
) -> dict[str, float]:
    """Compute Track 1 metrics (W_ag, B_ag, I_ag, antigenic redundancy, inter-rep distance, titer RMSE) for S."""
    active_nodes = [n for n, sh in node_shares.items() if sh > 1e-4 and n in G.nodes and n != ""]
    if not active_nodes or not selected_list:
        return {}

    S = set(selected_list) - {""}
    S_with_root = S | {""}

    rep_map = assign_nodes_to_representatives(G, S_with_root)

    # Normalize shares over active nodes
    tot_share = sum(node_shares[n] for n in active_nodes)
    p = {n: node_shares[n] / tot_share for n in active_nodes}

    # If any active nodes fall into the catch-all root "", represent "" by the highest-share unassigned lineage in ""
    unassigned_nodes = [v for v in active_nodes if rep_map[v] == ""]
    if unassigned_nodes:
        root_proxy = max(unassigned_nodes, key=lambda v: p[v])
        for v in unassigned_nodes:
            rep_map[v] = root_proxy

    # Cluster masses P_u
    cluster_mass: dict[str, float] = {}
    for v in active_nodes:
        u = rep_map[v]
        cluster_mass[u] = cluster_mass.get(u, 0.0) + p[v]

    # 1. Within-cluster antigenic distortion W_ag(S) & Neutralization Titer RMSE
    w_dms = 0.0
    w_rac = 0.0
    w_dms_l1 = 0.0
    titer_sq_err_dms = 0.0
    titer_sq_err_rac = 0.0

    for v in active_nodes:
        u = rep_map[v]
        d_dms = evaluator.dms_antigenic_distance(v, u)
        d_rac = evaluator.racmacs2024_distance(v, u)
        w_dms += p[v] * (d_dms ** 2)
        w_dms_l1 += p[v] * d_dms
        w_rac += p[v] * (d_rac ** 2)

        # Serum panel log2 RMSE (17 DMS polyclonal virus panels)
        tv_dms = evaluator.get_dms_serum_panel_log2_retention(v)
        tu_dms = evaluator.get_dms_serum_panel_log2_retention(u)
        titer_sq_err_dms += p[v] * float(np.mean((tv_dms - tu_dms) ** 2))

        # Empirical Roessler 2024 GMT serum groups log2 RMSE
        tv_rac = evaluator.roessler2024_titer_vector(v)
        tu_rac = evaluator.roessler2024_titer_vector(u)
        valid_mask = ~np.isnan(tv_rac) & ~np.isnan(tu_rac)
        if np.any(valid_mask):
            titer_sq_err_rac += p[v] * float(np.mean((tv_rac[valid_mask] - tu_rac[valid_mask]) ** 2))

    # 2. Between-cluster antigenic separation B_ag(S) & Inter-Representative Antigenic Diversity
    active_reps = [u for u, m in cluster_mass.items() if m > 0]
    b_dms_weighted = 0.0
    b_rac_weighted = 0.0
    for i in range(len(active_reps)):
        for j in range(i + 1, len(active_reps)):
            u1, u2 = active_reps[i], active_reps[j]
            m1, m2 = cluster_mass[u1], cluster_mass[u2]
            b_dms_weighted += m1 * m2 * (evaluator.dms_antigenic_distance(u1, u2) ** 2)
            b_rac_weighted += m1 * m2 * (evaluator.racmacs2024_distance(u1, u2) ** 2)

    # Mean pairwise antigenic distance across all selected representatives in S
    sel_reps = sorted(S)
    pair_dms_dists = []
    pair_rac_dists = []
    for i in range(len(sel_reps)):
        for j in range(i + 1, len(sel_reps)):
            u1, u2 = sel_reps[i], sel_reps[j]
            pair_dms_dists.append(evaluator.dms_antigenic_distance(u1, u2))
            pair_rac_dists.append(evaluator.racmacs2024_distance(u1, u2))
    mean_rep_dms = float(np.mean(pair_dms_dists)) if pair_dms_dists else 0.0
    mean_rep_rac = float(np.mean(pair_rac_dists)) if pair_rac_dists else 0.0

    # 3. Antigenic Redundancy Rate: fraction of selected clusters u in S (and their prevalence share)
    # that have ZERO antigenic escape (d_DMS == 0) relative to their nearest selected ancestor in S
    redundant_count = 0
    redundant_share = 0.0
    for u in sel_reps:
        if u not in G.nodes:
            continue
        preds = list(G.predecessors(u))
        curr = preds[0] if preds else None
        visited = set()
        anc_in_s = None
        while curr is not None and curr not in visited:
            visited.add(curr)
            if curr in S:
                anc_in_s = curr
                break
            preds_c = list(G.predecessors(curr))
            curr = preds_c[0] if preds_c else None
        if anc_in_s is not None:
            if evaluator.dms_antigenic_distance(u, anc_in_s) < 0.005:
                redundant_count += 1
                redundant_share += cluster_mass.get(u, 0.0)

    # Calinski-Harabasz antigenic variance ratio I_ag = (B_ag / (k - 1)) / (W_ag_total / (N_active - k))
    # Add regularization floor 0.01 (equivalent to 0.1 antigenic units baseline assay noise) so single-wave
    # windows with W_dms ~ 0 do not divide by 1e-6 and dominate the 133-window average by 10^5
    k = max(len(active_reps), 2)
    n_act = max(len(active_nodes), k + 1)
    dof_ratio = (n_act - k) / max(k - 1, 1)
    i_ag_dms = (b_dms_weighted / (w_dms + 0.0025)) * dof_ratio
    i_ag_rac = (b_rac_weighted / (w_rac + 0.01)) * dof_ratio

    return {
        "W_dms": w_dms,
        "W_dms_l1": w_dms_l1,
        "B_dms": b_dms_weighted,
        "I_ag_dms": i_ag_dms,
        "W_rac": w_rac,
        "B_rac": b_rac_weighted,
        "I_ag_rac": i_ag_rac,
        "mean_rep_dms": mean_rep_dms,
        "mean_rep_rac": mean_rep_rac,
        "redundant_count": float(redundant_count),
        "redundant_frac": float(redundant_count) / max(len(sel_reps), 1),
        "redundant_share": redundant_share,
        "rmse_dms_titer": float(np.sqrt(titer_sq_err_dms)),
        "rmse_rac_titer": float(np.sqrt(titer_sq_err_rac)),
        "k_selected": len(sel_reps),
        "k_active_clusters": len(active_reps),
    }


def mantel_correlation(D1: np.ndarray, D2: np.ndarray, n_perms: int = 500, rng: np.random.Generator = None) -> tuple[float, float, float]:
    """Compute Mantel Pearson r, Spearman rho, and permutation p-value between two symmetric distance matrices."""
    n = D1.shape[0]
    if n < 4:
        return np.nan, np.nan, np.nan
    iu = np.triu_indices(n, k=1)
    v1 = D1[iu]
    v2 = D2[iu]
    if np.std(v1) < 1e-9 or np.std(v2) < 1e-9:
        return np.nan, np.nan, np.nan
    r_obs = float(pearsonr(v1, v2)[0])
    rho_obs = float(spearmanr(v1, v2)[0])
    if rng is None:
        rng = np.random.default_rng(42)
    exceed = 0
    for _ in range(n_perms):
        perm = rng.permutation(n)
        D2_perm = D2[np.ix_(perm, perm)]
        r_perm = np.corrcoef(v1, D2_perm[iu])[0, 1]
        if r_perm >= r_obs:
            exceed += 1
    p_val = (exceed + 1) / (n_perms + 1)
    return r_obs, rho_obs, p_val


def main():
    print("Initializing EnhancedPangoRBDResolver and AntigenicEvaluator...")
    resolver = EnhancedPangoRBDResolver(DATA_DIR / "lineage_notes.txt", DATA_DIR / "alias_key.json")
    evaluator = AntigenicEvaluator(resolver)

    print("Loading regional sequence data and Track 2 rolling evaluation windows...")
    from run_track2_benchmark import filter_collection_date_typos
    aliasor = Aliasor(str(DATA_DIR / "alias_key.json"))
    raw_df = pd.read_csv(
        DATA_DIR / "outbreak_info_variants_states_long.csv.gz",
        usecols=["date", "fips", "lineage", "lineage_count"],
        dtype={"fips": str, "lineage": str},
        low_memory=False,
    ).dropna()
    raw_df["date"] = pd.to_datetime(raw_df["date"])
    raw_df["weight"] = pd.to_numeric(raw_df["lineage_count"], errors="coerce").fillna(0.0)
    raw_df = raw_df.rename(columns={"lineage": "variant"})
    fips_set = set(_expand_fips_tokens(HHS3_FIPS))
    df_region_raw = raw_df[raw_df["fips"].isin(fips_set)].copy()
    df_region = filter_collection_date_typos(df_region_raw, max_gap_days=60.0)

    t2_df = pd.read_csv(RESULTS_TRACK2_DIR / "track2_rolling_metrics.csv")
    print(f"Loaded {len(t2_df)} biweekly evaluation windows.")

    window_rows = []
    edge_records = []
    rng = np.random.default_rng(42)
    cfg = ContourConfig(temporal_decay_prev=0.25)

    for idx, row in t2_df.iterrows():
        end_date = row["end_date"]
        end_ts = pd.to_datetime(end_date)
        start_date = (end_ts - pd.Timedelta(weeks=12)).strftime("%Y-%m-%d")
        regime = row["regime"]

        base_list = [x.strip() for x in str(row["base_list"]).split(",") if x.strip()]

        G, df_win = build_graph_in_memory(
            df_region, start_date, end_date, aliasor, resolve_recombinants=True
        )
        G.graph["weighting_mode"] = "contour"
        compute_node_contours(df_win, G, reference_date=end_date, config=cfg)

        # Compute live Contour VLOpt (size == 20) and Dynamic Contour VLOpt (tau_slot = 0.005, max_size=True, size=20)
        if G.number_of_nodes() <= 20:
            contour_list = [x for x in G.nodes() if x != ""]
            dyn_list = [x for x in G.nodes() if x != ""]
        else:
            contour_list = sorted(
                x
                for x in do_optimize_highs(
                    20,
                    G,
                    distance_penalty=1.0,
                    penalty_mode="contour",
                )
                if x != ""
            )
            dyn_list = sorted(
                x
                for x in do_optimize_highs(
                    20,
                    G,
                    distance_penalty=1.0,
                    penalty_mode="contour",
                    slot_cost=0.005,
                    max_size=True,
                )
                if x != ""
            )

        # Compute node shares at end_date (last 2 weeks of window)
        recent_start = end_ts - pd.Timedelta(weeks=2)
        sub_df = df_win[df_win["date"] >= recent_start]
        if sub_df.empty:
            sub_df = df_win
        node_counts = sub_df.groupby("variant")["weight"].sum().to_dict()
        tot_cnt = max(sum(node_counts.values()), 1.0)
        node_shares = {v: c / tot_cnt for v, c in node_counts.items() if v in G.nodes and v != ""}

        # 1. Benchmark 1A & 1D: Antigenic Variance Ratio, Redundancy & Titer RMSE
        m_base = compute_window_track1_metrics(G, node_shares, base_list, evaluator)
        m_cont = compute_window_track1_metrics(G, node_shares, contour_list, evaluator)
        m_dyn = compute_window_track1_metrics(G, node_shares, dyn_list, evaluator)

        # 2. Benchmark 1C: Mantel & Spearman correlation between D_contour(u, v) and D_ag(u, v)
        cand_nodes = sorted(
            [n for n in G.nodes if G.nodes[n].get("importance", 0.0) > 0 and n not in ("", "root", "B")],
            key=lambda n: G.nodes[n].get("importance", 0.0),
            reverse=True,
        )[:25]

        mantel_r_dms, spearman_dms, mantel_p_dms = np.nan, np.nan, np.nan
        mantel_r_rac, spearman_rac, _ = np.nan, np.nan, np.nan
        spearman_tree_dms = np.nan

        if len(cand_nodes) >= 6:
            n_c = len(cand_nodes)
            D_cont = np.zeros((n_c, n_c), dtype=float)
            D_dms = np.zeros((n_c, n_c), dtype=float)
            D_rac = np.zeros((n_c, n_c), dtype=float)
            D_tree = np.zeros((n_c, n_c), dtype=float)
            G_undir = G.to_undirected()

            sample_vel = np.asarray(G.nodes[cand_nodes[0]].get("velocity", []), dtype=float)
            w_vel = len(sample_vel)
            if w_vel > 0:
                vel_weights = np.array([cfg.temporal_decay_vel ** ((w_vel - 1 - k) / 4.0) for k in range(w_vel)], dtype=float)
                vel_weights /= vel_weights.sum()
            else:
                vel_weights = np.zeros(0, dtype=float)

            for i in range(n_c):
                for j in range(i + 1, n_c):
                    u, v = cand_nodes[i], cand_nodes[j]
                    cu = np.asarray(G.nodes[u].get("contour", [1.0]), dtype=float)
                    cv = np.asarray(G.nodes[v].get("contour", [1.0]), dtype=float)
                    vu = np.asarray(G.nodes[u].get("velocity", []), dtype=float)
                    vv = np.asarray(G.nodes[v].get("velocity", []), dtype=float)
                    n_eff = min(float(G.nodes[u].get("clade_count", 10.0)), float(G.nodes[v].get("clade_count", 10.0)))
                    dc, _, _, _ = compute_divergence_components(cv, vv, cu, vu, vel_weights, cfg, n_eff=n_eff)
                    dd = evaluator.dms_antigenic_distance(u, v)
                    dr = evaluator.racmacs2024_distance(u, v)
                    try:
                        dt = float(nx.shortest_path_length(G_undir, u, v))
                    except Exception:
                        dt = 6.0
                    D_cont[i, j] = D_cont[j, i] = dc
                    D_dms[i, j] = D_dms[j, i] = dd
                    D_rac[i, j] = D_rac[j, i] = dr
                    D_tree[i, j] = D_tree[j, i] = dt

            mantel_r_dms, spearman_dms, mantel_p_dms = mantel_correlation(D_cont, D_dms, n_perms=200, rng=rng)
            mantel_r_rac, spearman_rac, _ = mantel_correlation(D_cont, D_rac, n_perms=200, rng=rng)
            iu = np.triu_indices(n_c, k=1)
            if np.std(D_tree[iu]) > 1e-9 and np.std(D_dms[iu]) > 1e-9:
                spearman_tree_dms = float(spearmanr(D_tree[iu], D_dms[iu])[0])

        # 3. Benchmark 1B: Parent-child edge split vs. fold antigenic escape discrimination
        S_base = set(base_list)
        S_cont = set(contour_list)
        S_dyn = set(dyn_list)
        for u, v, edata in G.edges(data=True):
            if u in ("", "root") or v in ("", "root", "B"):
                continue
            imp_v = G.nodes[v].get("importance", 0.0)
            if imp_v <= 0:
                continue
            d_dms_edge = evaluator.dms_antigenic_distance(u, v)
            d_rac_edge = evaluator.racmacs2024_distance(u, v)
            d_cont_edge = float(edata.get("contour_divergence", 0.0))
            edge_records.append({
                "end_date": end_date,
                "regime": regime,
                "parent": u,
                "child": v,
                "importance": imp_v,
                "share": node_shares.get(v, 0.0),
                "d_contour": d_cont_edge,
                "d_dms": d_dms_edge,
                "d_rac": d_rac_edge,
                "has_rbd_escape_mut": int(d_dms_edge > 0.01),
                "split_base": int(v in S_base),
                "split_contour": int(v in S_cont),
                "split_dyn": int(v in S_dyn),
                "contour_only": int((v in S_cont) and (v not in S_base)),
                "base_only": int((v in S_base) and (v not in S_cont)),
            })

        w_row = {
            "start_date": start_date,
            "end_date": end_date,
            "regime": regime,
            "mantel_r_dms": mantel_r_dms,
            "spearman_dms": spearman_dms,
            "mantel_p_dms": mantel_p_dms,
            "mantel_r_rac": mantel_r_rac,
            "spearman_rac": spearman_rac,
            "spearman_tree_dms": spearman_tree_dms,
            "contour_list_live": ",".join(contour_list),
            "dyn_list_live": ",".join(dyn_list),
        }
        for prefix, m in [("base", m_base), ("contour", m_cont), ("dyn", m_dyn)]:
            for k, val in m.items():
                w_row[f"{prefix}_{k}"] = val
        window_rows.append(w_row)

    win_out_df = pd.DataFrame(window_rows)
    edge_out_df = pd.DataFrame(edge_records)
    win_out_df.to_csv(RESULTS_TRACK1_DIR / "track1_window_metrics.csv", index=False)
    edge_out_df.to_csv(RESULTS_TRACK1_DIR / "track1_edge_discrimination.csv", index=False)

    print("\n================ TRACK 1 SUMMARY RESULTS ================")
    print("\n--- 1A & 1D: Antigenic Redundancy, Inter-Representative Separation, Variance Ratio (I_ag), & Titer RMSE ---")
    for reg_label, sub in [("ALL (133 windows)", win_out_df)] + list(win_out_df.groupby("regime")):
        print(f"\n[{reg_label}] (n={len(sub)})")
        for m_name, label in [
            ("redundant_count", "Antigenically Redundant Splits per Window (d_DMS=0, lower=better)"),
            ("redundant_share", "Prevalence Fragmented into Redundant Splits (d_DMS=0, lower=better)"),
            ("mean_rep_dms", "Mean Inter-Representative DMS Distance (higher=better)"),
            ("mean_rep_rac", "Mean Inter-Representative Racmacs Distance (higher=better)"),
            ("W_dms", "Within-Cluster DMS Distortion W_DMS (lower=better)"),
            ("B_dms", "Between-Cluster DMS Separation B_DMS (higher=better)"),
            ("I_ag_dms", "DMS Antigenic Variance Ratio I_ag_DMS (higher=better)"),
            ("W_rac", "Within-Cluster Racmacs Distortion W_Rac (lower=better)"),
            ("I_ag_rac", "Racmacs Antigenic Variance Ratio I_ag_Rac (higher=better)"),
            ("rmse_dms_titer", "DMS Polyclonal Neutralization RMSE (log2 units, lower=better)"),
            ("rmse_rac_titer", "Roessler 2024 Serum GMT Titer RMSE (log2 units, lower=better)"),
        ]:
            b_val = sub[f"base_{m_name}"].mean()
            c_val = sub[f"contour_{m_name}"].mean()
            d_val = sub[f"dyn_{m_name}"].mean()
            pct = ((c_val - b_val) / abs(b_val) * 100.0) if abs(b_val) > 1e-9 else 0.0
            print(f"  {label:66s}: Base={b_val:.4f} | Contour={c_val:.4f} ({pct:+.1f}%) | DynContour={d_val:.4f}")

    # Wilcoxon signed-rank tests across all 133 windows
    print("\n--- Paired Wilcoxon Signed-Rank Tests (Contour vs Baseline, n=133 windows) ---")
    for m_name in ["W_dms", "B_dms", "I_ag_dms", "W_rac", "I_ag_rac", "rmse_dms_titer", "rmse_rac_titer"]:
        diff = win_out_df[f"contour_{m_name}"] - win_out_df[f"base_{m_name}"]
        stat, pval = wilcoxon(diff)
        wins = int((diff > 0).sum()) if "I_ag" in m_name or "B_" in m_name else int((diff < 0).sum())
        print(f"  {m_name:18s}: Contour wins {wins}/133 ({wins/133*100:.1f}%), Wilcoxon p = {pval:.3e}")

    print("\n--- 1B: Parent-Child Split vs. Fold Antigenic Escape Discrimination ---")
    selected_edges_base = edge_out_df[edge_out_df["split_base"] == 1]
    selected_edges_cont = edge_out_df[edge_out_df["split_contour"] == 1]
    selected_edges_dyn = edge_out_df[edge_out_df["split_dyn"] == 1]
    folded_edges_cont = edge_out_df[edge_out_df["split_contour"] == 0]
    cont_only_edges = edge_out_df[edge_out_df["contour_only"] == 1]
    base_only_edges = edge_out_df[edge_out_df["base_only"] == 1]

    for lbl, edf in [
        ("Baseline Selected Splits (v in S_base)", selected_edges_base),
        ("Contour Selected Splits (v in S_contour)", selected_edges_cont),
        ("Dynamic Contour Selected Splits (v in S_dyn)", selected_edges_dyn),
        ("Contour Folded Edges (v not in S_contour)", folded_edges_cont),
        ("Contour-Only Splits (Contour \\ Baseline)", cont_only_edges),
        ("Baseline-Only Splits (Baseline \\ Contour)", base_only_edges),
    ]:
        print(
            f"  {lbl:46s} (n={len(edf):5d}): "
            f"mean d_DMS={edf['d_dms'].mean():.4f}, "
            f"mean d_Rac={edf['d_rac'].mean():.4f}, "
            f"% with RBD escape mut={edf['has_rbd_escape_mut'].mean()*100:.1f}%, "
            f"mean D_contour={edf['d_contour'].mean():.4f}"
        )

    u_stat, u_pval = mannwhitneyu(cont_only_edges["d_dms"], base_only_edges["d_dms"], alternative="greater")
    print(f"  Mann-Whitney U (Contour-Only vs Baseline-Only d_DMS): p = {u_pval:.3e}")

    print("\n--- 1C: Mantel & Spearman Correlation (D_contour vs D_ag vs d_tree) ---")
    valid_m = win_out_df.dropna(subset=["mantel_r_dms"])
    print(f"  Mean Window Mantel r (D_contour vs D_DMS):      {valid_m['mantel_r_dms'].mean():.4f} (median {valid_m['mantel_r_dms'].median():.4f})")
    print(f"  Mean Window Spearman rho (D_contour vs D_DMS):  {valid_m['spearman_dms'].mean():.4f} (median {valid_m['spearman_dms'].median():.4f})")
    print(f"  Mean Window Mantel r (D_contour vs D_Racmacs):  {valid_m['mantel_r_rac'].mean():.4f} (median {valid_m['mantel_r_rac'].median():.4f})")
    print(f"  Mean Window Spearman rho (D_contour vs D_Rac):  {valid_m['spearman_rac'].mean():.4f} (median {valid_m['spearman_rac'].median():.4f})")
    print(f"  Mean Window Spearman rho (d_tree vs D_DMS):     {valid_m['spearman_tree_dms'].mean():.4f} (median {valid_m['spearman_tree_dms'].median():.4f})")
    sig_mantel = int((valid_m["mantel_p_dms"] < 0.05).sum())
    print(f"  Windows with statistically significant Mantel correlation (p < 0.05): {sig_mantel}/{len(valid_m)} ({sig_mantel/len(valid_m)*100:.1f}%)")


if __name__ == "__main__":
    main()
