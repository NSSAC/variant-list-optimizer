"""Track 2 Longitudinal Benchmark: Curator Concordance & Early-Warning Lead Time.

Evaluates Baseline VLOpt (static tier penalty) vs. Contour-Regularized VLOpt
across Regimes I-IV on HHS Region 3 (FIPS 10,11,24,42,51,54) using:
  1. US CDC Variant Proportions archive (jr58-6ysp, Region 3)
  2. Nextstrain git-timestamped clade designations (clade_display_names.yml)
  3. Curated immune_clustering.py escape lineage list
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
import networkx as nx
import numpy as np
import pandas as pd

BENCHMARKS_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARKS_DIR.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pango_aliasor.aliasor import Aliasor
from variant_list_optimizer.contour import ContourConfig, compute_node_contours
from variant_list_optimizer.make_graph import _expand_fips_tokens, _resolve_parent
from variant_list_optimizer.optimize import (
    compute_assignment_penalty,
    do_optimize_highs,
    make_nia,
)

DATA_DIR = Path(os.environ.get("VLOPT_DATA_DIR", str(BENCHMARKS_DIR / "data")))
RESULTS_DIR = Path(
    os.environ.get("VLOPT_RESULTS_TRACK2_DIR", str(BENCHMARKS_DIR / "results_track2"))
)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

HHS3_FIPS = "10,11,24,42,51,54"
LIST_SIZE = 20
WINDOW_WEEKS = 12


def build_graph_in_memory(
    df_region: pd.DataFrame,
    start_date: str,
    end_date: str,
    aliasor: Aliasor,
    resolve_recombinants: bool = True,
) -> tuple[nx.DiGraph, pd.DataFrame]:
    """Filter regional data to [start_date, end_date) and construct the Pango tree G."""
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    df_win = df_region[
        (df_region["date"] >= start_ts) & (df_region["date"] < end_ts)
    ].copy()

    G = nx.DiGraph()
    G.graph["start_date"] = start_date
    G.graph["end_date"] = end_date

    seen_variants = df_win["variant"].unique().tolist()
    for child in seen_variants:
        visited: set[str] = {child}
        curr = child
        while curr != "":
            parent = _resolve_parent(aliasor, curr, resolve_recombinants)
            if parent in visited and parent != "":
                parent = ""
            visited.add(parent)
            if G.in_degree(curr) == 0 if curr in G else True:
                G.add_edge(parent, curr)
            curr = parent

    return G, df_win


def run_baseline_and_contour(
    df_region: pd.DataFrame,
    end_date: str,
    aliasor: Aliasor,
    list_size: int = LIST_SIZE,
    window_weeks: int = WINDOW_WEEKS,
    baseline_window_weeks: int = 12,
) -> dict:
    """Run both Baseline (static DISTANCE_PENALTY=10.0) and Contour-Regularized VLOpt at end_date."""
    end_ts = pd.to_datetime(end_date)
    start_contour = (end_ts - pd.Timedelta(weeks=window_weeks)).strftime("%Y-%m-%d")
    start_base = (end_ts - pd.Timedelta(weeks=baseline_window_weeks)).strftime("%Y-%m-%d")

    # 1. Baseline VLOpt (static tree distance = 1.0, DISTANCE_PENALTY = 10.0, decay = 0.1)
    G_base, df_base = build_graph_in_memory(
        df_region, start_base, end_date, aliasor, resolve_recombinants=False
    )
    if G_base.number_of_nodes() <= list_size:
        sel_base = set(G_base.nodes())
        nia_base = make_nia(sel_base, G_base, 10.0)
    else:
        time_unit = 30.0 * 24.0 * 3600.0
        df_b = df_base.copy()
        df_b["time"] = np.abs((end_ts - df_b["date"]).dt.total_seconds()) / time_unit
        df_b["imp"] = (0.1 ** df_b["time"]) * df_b["weight"]
        imp_map = df_b.groupby("variant")["imp"].sum().to_dict()
        for v in G_base:
            G_base.nodes[v]["importance"] = float(imp_map.get(v, 0.0))
        for u, v in G_base.edges:
            G_base.edges[u, v]["distance"] = 1.0
        G_base.graph["weighting_mode"] = "static"

        sel_base = do_optimize_highs(
            list_size, G_base, distance_penalty=10.0, penalty_mode="static"
        )
        pen_mat_base = compute_assignment_penalty(
            G_base, distance_penalty=10.0, penalty_mode="static"
        )
        nia_base = make_nia(sel_base, G_base, 10.0, pen_mat_base)

    # 2. Contour-Regularized VLOpt
    G_cont, df_cont = build_graph_in_memory(
        df_region, start_contour, end_date, aliasor, resolve_recombinants=True
    )
    if G_cont.number_of_nodes() <= list_size:
        sel_cont = set(G_cont.nodes())
        nia_cont = make_nia(sel_cont, G_cont, 1.0)
    else:
        G_cont.graph["weighting_mode"] = "contour"
        cfg = ContourConfig(temporal_decay_prev=0.25)
        compute_node_contours(df_cont, G_cont, reference_date=end_date, config=cfg)
        sel_cont = do_optimize_highs(
            list_size, G_cont, distance_penalty=1.0, penalty_mode="contour"
        )
        pen_mat_cont = compute_assignment_penalty(
            G_cont, distance_penalty=1.0, penalty_mode="contour"
        )
        nia_cont = make_nia(sel_cont, G_cont, 1.0, pen_mat_cont)

    # Compute recent 2-week prevalence shares of each observed variant at end_date
    recent_start = end_ts - pd.Timedelta(weeks=2)
    df_rec = df_cont[df_cont["date"] >= recent_start]
    if df_rec.empty:
        df_rec = df_cont
    var_weights = df_rec.groupby("variant")["weight"].sum().to_dict()
    total_w = sum(var_weights.values())
    var_shares = (
        {k: v / total_w for k, v in var_weights.items()} if total_w > 0 else {}
    )

    return {
        "end_date": end_date,
        "base_list": sorted([v for v in sel_base if v != ""]),
        "contour_list": sorted([v for v in sel_cont if v != ""]),
        "base_map": {k: v.nia for k, v in nia_base.items()},
        "contour_map": {k: v.nia for k, v in nia_cont.items()},
        "var_shares": var_shares,
        "G_cont": G_cont,
    }


def filter_collection_date_typos(df: pd.DataFrame, max_gap_days: float = 60.0) -> pd.DataFrame:
    """Remove isolated pre-emergence collection-date year typos using backward chaining from peak.

    Matches the chaining outlier removal methodology in immune_motif/src/seq_prep.py.
    """
    daily = (
        df.groupby(["variant", "date"])["weight"]
        .sum()
        .reset_index()
        .sort_values(["variant", "date"])
    )
    min_valid_date: dict[str, pd.Timestamp] = {}
    for var, grp in daily.groupby("variant"):
        if len(grp) <= 1:
            min_valid_date[var] = grp["date"].min()
            continue
        dates = grp["date"].to_numpy()
        weights = grp["weight"].to_numpy()
        peak_idx = int(weights.argmax())
        total_w = float(weights.sum())
        cut_idx = 0
        for i in range(peak_idx, 0, -1):
            gap_days = (dates[i] - dates[i - 1]) / pd.Timedelta(days=1)
            if gap_days > max_gap_days and float(weights[:i].sum()) < max(5.0, 0.02 * total_w):
                cut_idx = i
                break
        min_valid_date[var] = pd.Timestamp(dates[cut_idx])

    valid_min_series = df["variant"].map(min_valid_date)
    return df[df["date"] >= valid_min_series].copy()


def map_variants_to_curator_list(
    observed_variants: list[str],
    curator_list: list[str],
    aliasor: Aliasor,
) -> dict[str, str]:
    """Map each observed variant to its nearest ancestor in curator_list along the Pango tree."""
    curator_set = set(curator_list) | {""}
    mapping: dict[str, str] = {}
    for v in observed_variants:
        curr = v
        visited = {curr}
        while curr not in curator_set and curr != "":
            parent = _resolve_parent(aliasor, curr, resolve_recombinants=True)
            if parent in visited:
                curr = ""
                break
            visited.add(parent)
            curr = parent
        mapping[v] = curr
    return mapping


def variation_of_information(
    var_shares: dict[str, float],
    map_p: dict[str, str],
    map_q: dict[str, str],
) -> tuple[float, float]:
    """Compute prevalence-weighted Variation of Information VI(P, Q) and Normalized Mutual Information NMI(P, Q)."""
    if not var_shares:
        return 0.0, 1.0

    joint: dict[tuple[str, str], float] = {}
    marg_p: dict[str, float] = {}
    marg_q: dict[str, float] = {}

    for v, w in var_shares.items():
        if w <= 0:
            continue
        cp = map_p.get(v, "")
        cq = map_q.get(v, "")
        joint[(cp, cq)] = joint.get((cp, cq), 0.0) + w
        marg_p[cp] = marg_p.get(cp, 0.0) + w
        marg_q[cq] = marg_q.get(cq, 0.0) + w

    tot = sum(marg_p.values())
    if tot <= 0:
        return 0.0, 1.0

    h_p = -sum((p / tot) * math.log2(p / tot) for p in marg_p.values() if p > 0)
    h_q = -sum((q / tot) * math.log2(q / tot) for q in marg_q.values() if q > 0)

    mi = 0.0
    for (cp, cq), pq in joint.items():
        if pq > 0:
            p_prob = marg_p[cp] / tot
            q_prob = marg_q[cq] / tot
            pq_prob = pq / tot
            mi += pq_prob * math.log2(pq_prob / (p_prob * q_prob))

    vi = max(0.0, h_p + h_q - 2.0 * mi)
    nmi = (2.0 * mi / (h_p + h_q)) if (h_p + h_q) > 1e-12 else 1.0
    return float(vi), float(nmi)


def load_cdc_hhs3_lists() -> pd.DataFrame:
    """Load CDC jr58-6ysp variant proportions filtered to HHS Region 3."""
    cdc_path = DATA_DIR / "cdc_variant_proportions_jr58_6ysp.csv"
    df_cdc = pd.read_csv(cdc_path, low_memory=False)
    df_cdc["usa_or_hhsregion"] = df_cdc["usa_or_hhsregion"].astype(str).str.strip()
    df_hhs3 = df_cdc[df_cdc["usa_or_hhsregion"] == "3"].copy()
    df_hhs3["week_ending"] = pd.to_datetime(df_hhs3["week_ending"])
    df_hhs3["published_date"] = pd.to_datetime(df_hhs3["published_date"])
    df_hhs3["share"] = pd.to_numeric(df_hhs3["share"], errors="coerce").fillna(0.0)
    df_hhs3["variant"] = df_hhs3["variant"].astype(str).str.strip()
    return df_hhs3


def get_cdc_active_list_at_date(
    df_hhs3: pd.DataFrame, target_date: str, min_share: float = 0.01
) -> list[str]:
    """Reconstruct the active CDC Region 3 curated variant list at target_date without future leakage."""
    target_ts = pd.to_datetime(target_date)
    sub = df_hhs3[
        (df_hhs3["week_ending"] <= target_ts)
        & (df_hhs3["week_ending"] >= target_ts - pd.Timedelta(days=28))
    ]
    if sub.empty:
        return []
    latest_week = sub["week_ending"].max()
    sub_latest = sub[sub["week_ending"] == latest_week]
    earliest_pub = sub_latest["published_date"].min()
    sub_latest = sub_latest[sub_latest["published_date"] == earliest_pub]
    active = sub_latest[sub_latest["share"] >= min_share]["variant"].unique().tolist()
    return [v for v in active if v not in ("Other", "Total", "")]


def parse_nextstrain_clade_dates() -> dict[str, str]:
    """Parse git commit history of clade_display_names.yml to get official Nextstrain designation dates."""
    hist_path = DATA_DIR / "nextstrain_clade_history.txt"
    text = hist_path.read_text()
    clade_dates: dict[str, str] = {}
    current_date = None
    for line in text.splitlines():
        if line.startswith("COMMIT_DATE:"):
            parts = line.split()
            current_date = parts[0].replace("COMMIT_DATE:", "")
        elif line.startswith("+") and not line.startswith("+++") and current_date:
            m = re.search(r"\(([A-Za-z0-9._]+)\)", line)
            if m:
                lineage = m.group(1)
                clade_dates[lineage] = current_date
    return clade_dates


def is_lineage_or_immediate_clade_selected(
    target: str, selected_list: list[str], aliasor: Aliasor
) -> bool:
    """Check whether target (or an immediate 1-hop sublineage of target, e.g. KP.3.1 for KP.3) is selected."""
    if target in selected_list:
        return True
    target_uncomp = aliasor.uncompress(target)
    for s in selected_list:
        if s == "":
            continue
        s_uncomp = aliasor.uncompress(s)
        if s_uncomp.startswith(target_uncomp + "."):
            diff_tiers = len(s_uncomp.split(".")) - len(target_uncomp.split("."))
            if diff_tiers == 1:
                return True
    return False


# Curated escape lineage list from immune_motif/src/immune_clustering.py (Oct 2022 - Oct 2023)
IMMUNE_CLUSTERING_OCT22_23_LIST = [
    "B.1.1.529",
    "BA.1.1",
    "BA.2",
    "BA.2.12.1",
    "BA.2.75",
    "BA.4",
    "BA.4.6",
    "BA.5",
    "BA.5.2.6",
    "BF.7",
    "BF.11",
    "CH.1.1",
    "BQ.1",
    "BQ.1.1",
    "XBB",
    "XBB.1.5",
    "XBB.1.5.1",
    "FD.2",
    "XBB.1.9.1",
    "XBB.1.9.2",
    "EG.5",
    "HK.3",
    "XBB.1.16",
    "XBB.1.16.1",
    "XBB.1.16.6",
    "XBB.2.3",
]


def main():
    print("Loading surveillance data and initializing Aliasor...")
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

    hhs3_tokens = _expand_fips_tokens(HHS3_FIPS)
    df_hhs3_obs_raw = raw_df[raw_df["fips"].isin(hhs3_tokens)].copy()
    df_hhs3_obs = filter_collection_date_typos(df_hhs3_obs_raw, max_gap_days=60.0)
    print(
        f"HHS Region 3 observations: {len(df_hhs3_obs_raw)} raw -> {len(df_hhs3_obs)} after chaining outlier filter"
    )

    df_cdc_hhs3 = load_cdc_hhs3_lists()
    nextstrain_dates = parse_nextstrain_clade_dates()

    # Continuous biweekly evaluation schedule across the 4 Canonical Regimes
    eval_dates: list[tuple[str, str]] = []
    # Regime I: Delta AY.* sweep & BA.1 emergence (2021-06-19 to 2021-12-18)
    for d in pd.date_range("2021-06-19", "2021-12-18", freq="2W-SAT"):
        eval_dates.append(("Regime I (2021 Delta/AY.* -> BA.1)", d.strftime("%Y-%m-%d")))
    # Regime II: Omicron sweeps & Autumn 2022 Variant Soup (2022-01-01 to 2022-12-31)
    for d in pd.date_range("2022-01-01", "2022-12-31", freq="2W-SAT"):
        eval_dates.append(("Regime II (2022 Omicron & BQ.1/XBB Soup)", d.strftime("%Y-%m-%d")))
    # Regime III: XBB.1.5 -> EG.5 -> BA.2.86/JN.1 -> KP.2/KP.3/XEC (2023-01-14 to 2024-11-30)
    for d in pd.date_range("2023-01-14", "2024-11-30", freq="2W-SAT"):
        eval_dates.append(("Regime III (2023-2024 XBB -> JN.1 -> KP.3)", d.strftime("%Y-%m-%d")))
    # Regime IV: Low-Sequencing LP.8.1, NB.1.8.1, XFG, BA.3.2 (2024-12-14 to 2026-07-18)
    for d in pd.date_range("2024-12-14", "2026-07-18", freq="2W-SAT"):
        eval_dates.append(("Regime IV (2025-2026 LP.8.1/XFG/BA.3.2)", d.strftime("%Y-%m-%d")))

    records = []

    for regime, end_date in eval_dates:
        res = run_baseline_and_contour(df_hhs3_obs, end_date, aliasor)

        base_list = res["base_list"]
        cont_list = res["contour_list"]
        var_shares = res["var_shares"]
        obs_vars = list(var_shares.keys())

        # Count AY.* lineages in list and prevalence share fragmented into AY.* buckets
        ay_base = sum(1 for v in base_list if v.startswith("AY."))
        ay_cont = sum(1 for v in cont_list if v.startswith("AY."))
        ay_share_base = sum(
            w for v, w in var_shares.items() if res["base_map"].get(v, "").startswith("AY.")
        )
        ay_share_cont = sum(
            w for v, w in var_shares.items() if res["contour_map"].get(v, "").startswith("AY.")
        )

        # Compare against CDC jr58-6ysp active list at t_end and t_end + 4 weeks
        cdc_now = get_cdc_active_list_at_date(df_cdc_hhs3, end_date, min_share=0.005)
        future_date_4w = (pd.to_datetime(end_date) + pd.Timedelta(weeks=4)).strftime("%Y-%m-%d")
        cdc_plus_4w = get_cdc_active_list_at_date(df_cdc_hhs3, future_date_4w, min_share=0.01)

        vi_base_now, nmi_base_now = np.nan, np.nan
        vi_cont_now, nmi_cont_now = np.nan, np.nan
        vi_base_4w, nmi_base_4w = np.nan, np.nan
        vi_cont_4w, nmi_cont_4w = np.nan, np.nan
        vi_base_ic, vi_cont_ic = np.nan, np.nan

        if len(cdc_now) >= 2 and len(obs_vars) > 0:
            cdc_map_now = map_variants_to_curator_list(obs_vars, cdc_now, aliasor)
            vi_base_now, nmi_base_now = variation_of_information(
                var_shares, res["base_map"], cdc_map_now
            )
            vi_cont_now, nmi_cont_now = variation_of_information(
                var_shares, res["contour_map"], cdc_map_now
            )

        if len(cdc_plus_4w) >= 2 and len(obs_vars) > 0:
            cdc_map_4w = map_variants_to_curator_list(obs_vars, cdc_plus_4w, aliasor)
            vi_base_4w, nmi_base_4w = variation_of_information(
                var_shares, res["base_map"], cdc_map_4w
            )
            vi_cont_4w, nmi_cont_4w = variation_of_information(
                var_shares, res["contour_map"], cdc_map_4w
            )

        if "2022-10-01" <= end_date <= "2023-10-31" and len(obs_vars) > 0:
            ic_map = map_variants_to_curator_list(
                obs_vars, IMMUNE_CLUSTERING_OCT22_23_LIST, aliasor
            )
            vi_base_ic, _ = variation_of_information(var_shares, res["base_map"], ic_map)
            vi_cont_ic, _ = variation_of_information(var_shares, res["contour_map"], ic_map)

        records.append(
            {
                "regime": regime,
                "end_date": end_date,
                "ay_count_base": ay_base,
                "ay_count_contour": ay_cont,
                "ay_share_base": ay_share_base,
                "ay_share_contour": ay_share_cont,
                "cdc_active_count": len(cdc_now),
                "vi_base_now": vi_base_now,
                "vi_contour_now": vi_cont_now,
                "nmi_base_now": nmi_base_now,
                "nmi_contour_now": nmi_cont_now,
                "vi_base_future4w": vi_base_4w,
                "vi_contour_future4w": vi_cont_4w,
                "nmi_base_future4w": nmi_base_4w,
                "nmi_contour_future4w": nmi_cont_4w,
                "vi_base_immune_cluster": vi_base_ic,
                "vi_contour_immune_cluster": vi_cont_ic,
                "base_list": ",".join(base_list),
                "contour_list": ",".join(cont_list),
            }
        )
        print(
            f"[{end_date}] AY_share(base={ay_share_base:.2f}, cont={ay_share_cont:.2f}) | "
            f"VI_now(base={vi_base_now:.3f}, cont={vi_cont_now:.3f}) | "
            f"VI_4w(base={vi_base_4w:.3f}, cont={vi_cont_4w:.3f})"
        )

    df_results = pd.DataFrame(records)
    df_results.to_csv(RESULTS_DIR / "track2_rolling_metrics.csv", index=False)

    # Compute Emergence Detection Lead-Time Advantage (Delta t_lead) for focal variants
    focal_variants = [
        "B.1.617.2",
        "BA.1",
        "BA.2",
        "BA.2.12.1",
        "BA.4",
        "BA.5",
        "BA.4.6",
        "BF.7",
        "BQ.1",
        "BQ.1.1",
        "XBB",
        "XBB.1.5",
        "XBB.1.16",
        "CH.1.1",
        "EG.5.1",
        "HV.1",
        "HK.3",
        "BA.2.86",
        "JN.1",
        "KP.2",
        "KP.3",
        "KP.3.1.1",
        "LB.1",
        "XEC",
        "LP.8.1",
        "LF.7",
        "NB.1.8.1",
        "XFG",
        "BA.3.2",
    ]

    lead_records = []
    for fv in focal_variants:
        t_base = None
        t_cont = None
        for _, row in df_results.iterrows():
            d_str = row["end_date"]
            b_list = row["base_list"].split(",") if row["base_list"] else []
            c_list = row["contour_list"].split(",") if row["contour_list"] else []
            if t_base is None and is_lineage_or_immediate_clade_selected(fv, b_list, aliasor):
                t_base = d_str
            if t_cont is None and is_lineage_or_immediate_clade_selected(fv, c_list, aliasor):
                t_cont = d_str

        # Find earliest CDC Region 3 reporting date (share >= 1%)
        cdc_fv = df_cdc_hhs3[
            (df_cdc_hhs3["variant"] == fv) & (df_cdc_hhs3["share"] >= 0.01)
        ]
        t_cdc = (
            cdc_fv["published_date"].min().strftime("%Y-%m-%d")
            if not cdc_fv.empty
            else None
        )
        t_ns = nextstrain_dates.get(fv, None)

        lead_vs_base_days = (
            (pd.to_datetime(t_base) - pd.to_datetime(t_cont)).days
            if (t_base and t_cont)
            else None
        )
        lead_vs_cdc_days = (
            (pd.to_datetime(t_cdc) - pd.to_datetime(t_cont)).days
            if (t_cdc and t_cont)
            else None
        )
        lead_vs_ns_days = (
            (pd.to_datetime(t_ns) - pd.to_datetime(t_cont)).days
            if (t_ns and t_cont)
            else None
        )

        lead_records.append(
            {
                "variant": fv,
                "first_detected_contour": t_cont,
                "first_detected_baseline": t_base,
                "cdc_published_date": t_cdc,
                "nextstrain_commit_date": t_ns,
                "lead_vs_baseline_days": lead_vs_base_days,
                "lead_vs_cdc_days": lead_vs_cdc_days,
                "lead_vs_nextstrain_days": lead_vs_ns_days,
            }
        )

    df_lead = pd.DataFrame(lead_records)
    df_lead.to_csv(RESULTS_DIR / "track2_emergence_lead_times.csv", index=False)

    print("\n=== SUMMARY BY REGIME ===")
    summary = (
        df_results.groupby("regime")[
            [
                "ay_count_base",
                "ay_count_contour",
                "ay_share_base",
                "ay_share_contour",
                "vi_base_now",
                "vi_contour_now",
                "nmi_base_now",
                "nmi_contour_now",
                "vi_base_future4w",
                "vi_contour_future4w",
                "vi_base_immune_cluster",
                "vi_contour_immune_cluster",
            ]
        ]
        .mean()
        .reset_index()
    )
    print(summary.to_string(index=False))

    print("\n=== EMERGENCE LEAD-TIME ADVANTAGE ===")
    print(df_lead.to_string(index=False))


if __name__ == "__main__":
    main()
