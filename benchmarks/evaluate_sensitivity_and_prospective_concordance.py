"""Evaluate Lag-Adjusted Prospective Concordance and Empirical Sensitivity / False-Attention.

Computes:
  1. Lag-Adjusted (Prospective) Curator Concordance & Conditional Entropy Decomposition:
     - H(CDC_t | Alg_t): Missed curator distinction error (under-splitting)
     - VI_prospective(Alg_t, CDC_[t, t+8w]): Concordance without penalizing early detections
       of variants that CDC adopts within the next 8 weeks.
  2. Standalone Empirical Ground-Truth Sensitivity & False-Attention Breakdown:
     - Classifies all lineages into:
         (a) Distinct Significant Variants (peak share >= 5% and dynamic divergence/displacement from parent)
         (b) Distinct Dominant Variants (peak share >= 20% and dynamic divergence/displacement from parent)
         (c) Neutral Parent-Hitchhikers (parallel contour to parent clade, JSD < 0.08)
         (d) Fizzled Minor Lineages (never reach >= 2% regional share and never curated)
     - Measures Early-Warning Sensitivity (recall before reaching 1%, 2%, 5% share, median share at first
       detection, and lead time before peak prevalence) and False-Attention rates (slot share & sequence
       prevalence share spent on Neutral Parent-Hitchhikers vs. Fizzled Minor Lineages).
"""

from __future__ import annotations

import math
from pathlib import Path
import numpy as np
import pandas as pd

from pango_aliasor.aliasor import Aliasor
from variant_list_optimizer.contour import jensen_shannon_divergence_base2
from variant_list_optimizer.make_graph import _expand_fips_tokens, _resolve_parent
from run_track2_benchmark import (
    DATA_DIR,
    RESULTS_DIR,
    HHS3_FIPS,
    filter_collection_date_typos,
    load_cdc_hhs3_lists,
    get_cdc_active_list_at_date,
    map_variants_to_curator_list,
    variation_of_information,
    parse_nextstrain_clade_dates,
    IMMUNE_CLUSTERING_OCT22_23_LIST,
)


def conditional_entropies_and_vi(
    var_shares: dict[str, float],
    map_alg: dict[str, str],
    map_cur: dict[str, str],
) -> tuple[float, float, float, float]:
    """Return (VI, NMI, H(Cur | Alg) [missed distinction], H(Alg | Cur) [extra split])."""
    if not var_shares:
        return 0.0, 1.0, 0.0, 0.0

    joint: dict[tuple[str, str], float] = {}
    marg_a: dict[str, float] = {}
    marg_c: dict[str, float] = {}

    for v, w in var_shares.items():
        if w <= 0:
            continue
        ca = map_alg.get(v, "")
        cc = map_cur.get(v, "")
        joint[(ca, cc)] = joint.get((ca, cc), 0.0) + w
        marg_a[ca] = marg_a.get(ca, 0.0) + w
        marg_c[cc] = marg_c.get(cc, 0.0) + w

    tot = sum(marg_a.values())
    if tot <= 0:
        return 0.0, 1.0, 0.0, 0.0

    h_a = -sum((p / tot) * math.log2(p / tot) for p in marg_a.values() if p > 0)
    h_c = -sum((q / tot) * math.log2(q / tot) for q in marg_c.values() if q > 0)

    mi = 0.0
    for (ca, cc), pq in joint.items():
        if pq > 0:
            pa = marg_a[ca] / tot
            pc = marg_c[cc] / tot
            p_ac = pq / tot
            mi += p_ac * math.log2(p_ac / (pa * pc))

    h_c_given_a = max(0.0, h_c - mi)  # Under-splitting: curator distinctions missed by alg
    h_a_given_c = max(0.0, h_a - mi)  # Over-splitting relative to curator
    vi = h_c_given_a + h_a_given_c
    nmi = (2.0 * mi / (h_a + h_c)) if (h_a + h_c) > 1e-12 else 1.0
    return float(vi), float(nmi), float(h_c_given_a), float(h_a_given_c)


def get_prospective_cdc_reference_list(
    df_hhs3_cdc: pd.DataFrame,
    target_date: str,
    alg_selected_list: list[str],
    horizon_weeks: int = 8,
    min_share_now: float = 0.005,
    min_share_future: float = 0.005,
) -> list[str]:
    """Build lag-tolerant reference list: CDC active list at target_date PLUS any variant in
    alg_selected_list that CDC adopts within [target_date, target_date + horizon_weeks].
    """
    cdc_now = set(get_cdc_active_list_at_date(df_hhs3_cdc, target_date, min_share=min_share_now))
    target_ts = pd.to_datetime(target_date)
    horizon_ts = target_ts + pd.Timedelta(weeks=horizon_weeks)

    # All variants reported by CDC at >= min_share_future in the next horizon_weeks
    sub_fut = df_hhs3_cdc[
        (df_hhs3_cdc["week_ending"] >= target_ts)
        & (df_hhs3_cdc["week_ending"] <= horizon_ts)
        & (df_hhs3_cdc["share"] >= min_share_future)
    ]
    cdc_future_adopted = set(sub_fut["variant"].unique().tolist()) - {"Other", "Total", ""}

    # Any variant selected early by the algorithm that CDC adopts within the horizon is a prospective hit
    prospective_hits = set(alg_selected_list) & cdc_future_adopted
    return sorted(cdc_now | prospective_hits)


def main():
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
    df_hhs3 = filter_collection_date_typos(
        raw_df[raw_df["fips"].isin(_expand_fips_tokens(HHS3_FIPS))].copy()
    )

    df_cdc_hhs3 = load_cdc_hhs3_lists()
    nextstrain_dates = parse_nextstrain_clade_dates()
    df_rolling = pd.read_csv(RESULTS_DIR / "track2_rolling_metrics.csv")

    # 1. Build biweekly clade prevalence matrix across all 133 evaluation dates
    eval_dates = df_rolling["end_date"].tolist()
    all_variants = sorted(df_hhs3["variant"].unique().tolist())

    # Map every variant to its ancestors in the Pango tree (with recombinant resolution)
    ancestors_of: dict[str, list[str]] = {}
    parent_of: dict[str, str] = {}
    for v in all_variants:
        curr = v
        visited = {curr}
        anc_list = [curr]
        while curr != "":
            p = _resolve_parent(aliasor, curr, resolve_recombinants=True)
            if curr == v:
                parent_of[v] = p
            if p in visited:
                break
            visited.add(p)
            anc_list.append(p)
            if p not in parent_of and p != "":
                parent_of[p] = _resolve_parent(aliasor, p, resolve_recombinants=True)
            curr = p
        ancestors_of[v] = anc_list

    # All nodes in the global Pango tree
    all_nodes = sorted({a for anc_list in ancestors_of.values() for a in anc_list if a != ""})

    # Compute biweekly direct and full-clade shares at each evaluation date (2-week trailing window)
    node_clade_shares = {n: np.zeros(len(eval_dates), dtype=np.float64) for n in all_nodes}
    node_prefix_shares = {n: np.zeros(len(eval_dates), dtype=np.float64) for n in all_nodes}
    node_clade_counts = {n: 0.0 for n in all_nodes}
    date_var_shares: dict[str, dict[str, float]] = {}

    for t_idx, d_str in enumerate(eval_dates):
        end_ts = pd.to_datetime(d_str)
        start_2w = end_ts - pd.Timedelta(weeks=2)
        sub = df_hhs3[(df_hhs3["date"] >= start_2w) & (df_hhs3["date"] < end_ts)]
        vw = sub.groupby("variant")["weight"].sum().to_dict()
        tot_w = sum(vw.values())
        vshares = {k: val / tot_w for k, val in vw.items()} if tot_w > 0 else {}
        date_var_shares[d_str] = vshares

        for v, count_val in vw.items():
            pref_v = v.split(".")[0]
            for a in ancestors_of.get(v, [v]):
                if a == "":
                    continue
                node_clade_counts[a] += count_val
                if tot_w > 0:
                    node_clade_shares[a][t_idx] += count_val / tot_w
                    if a == "B.1.617.2" or a.split(".")[0] == pref_v:
                        node_prefix_shares[a][t_idx] += count_val / tot_w

    # 2. Classify every node empirically using its same-prefix clade wave (with AY.* folded into B.1.617.2):
    #    Why same-prefix wave? Because in Pango nomenclature, full subtree shares cause BA.2 to inherit
    #    XBB/JN.1/KP.3/XFG through 2026, BE.1.1.1 to inherit BQ.1, and JN.1.11.1 to inherit KP.3/LP.8.1.
    all_cdc_curated = set(
        df_cdc_hhs3[df_cdc_hhs3["share"] >= 0.01]["variant"].unique().tolist()
    ) - {"Other", "Total", ""}
    all_curated_union = all_cdc_curated | set(nextstrain_dates.keys()) | set(IMMUNE_CLUSTERING_OCT22_23_LIST)

    node_category: dict[str, str] = {}
    node_meta: dict[str, dict] = {}

    for n in all_nodes:
        # Use same-prefix clade share so bridge nodes don't steal successor prefix waves
        traj_v = node_prefix_shares[n]
        peak_s = float(np.max(traj_v))
        peak_idx = int(np.argmax(traj_v))
        # Total sequence count within same-prefix clade
        tot_c = float(np.sum(traj_v) * 500.0)

        # Find nearest major ancestor clade u (either same-prefix ancestor or parent prefix wave)
        curr = parent_of.get(n, "")
        major_anc = ""
        while curr != "":
            anc_traj = node_prefix_shares.get(curr, np.zeros(1))
            # Check if ancestor was active during or right before v's wave
            v_active = traj_v >= max(0.005, 0.10 * peak_s)
            if np.any(v_active):
                anc_during_v = float(np.max(anc_traj[v_active]))
            else:
                anc_during_v = float(np.max(anc_traj))
            if float(np.max(anc_traj)) >= max(0.08, 1.25 * peak_s) and anc_during_v >= 0.02:
                major_anc = curr
                break
            curr = parent_of.get(curr, "")

        if major_anc != "":
            traj_u = node_prefix_shares[major_anc]
            if major_anc.split(".")[0] == n.split(".")[0] or (major_anc == "B.1.617.2" and n.startswith("AY.")):
                rem_u = np.maximum(0.0, traj_u - traj_v)
            else:
                rem_u = traj_u.copy()

            # Evaluate JSD ONLY over v's active window (where v >= 10% of its peak or >= 0.5%)
            v_window = (traj_v >= max(0.005, 0.10 * peak_s)) | (rem_u >= max(0.02, 0.15 * float(np.max(rem_u))))
            if np.sum(v_window) >= 2 and np.sum(rem_u[v_window]) > 0 and np.sum(traj_v[v_window]) > 0:
                jsd_vs_parent = jensen_shannon_divergence_base2(
                    traj_v[v_window], rem_u[v_window]
                )
            else:
                jsd_vs_parent = 0.0

            # Compute relative takeover within parent: share(v, t) / (share(v, t) + share(rem_u, t))
            denom = traj_v + rem_u
            valid_denom = (denom >= 0.03) & (traj_v > 0)
            if np.any(valid_denom):
                ratios = np.where(valid_denom, traj_v / np.maximum(denom, 1e-6), 0.0)
                v_idx_list = np.where(valid_denom)[0]
                init_ratio = float(np.mean(ratios[v_idx_list[:2]]))
                # Max ratio at or near v's peak
                peak_window = valid_denom & (np.arange(len(traj_v)) >= v_idx_list[0])
                max_ratio = float(np.max(ratios[peak_window]))
                delta_takeover = max(0.0, max_ratio - init_ratio)
            else:
                max_ratio = 0.0
                delta_takeover = 0.0

            # Also check peak timing separation (days between parent complement peak and v's peak)
            rem_peak_idx = int(np.argmax(rem_u)) if np.max(rem_u) > 0 else peak_idx
            peak_lag_days = (pd.to_datetime(eval_dates[peak_idx]) - pd.to_datetime(eval_dates[rem_peak_idx])).days
        else:
            jsd_vs_parent = 1.0
            max_ratio = 1.0
            delta_takeover = 1.0
            peak_lag_days = 999

        # Empirical classification:
        # A sublineage with a major co-circulating parent u is DISTINCT from its parent label iff:
        #   - It achieves substantial competitive takeover of its parent (delta_takeover >= 0.20), OR
        #   - It represents a temporally displaced successor wave peaking >= 28 days after u \ v with JSD >= 0.20.
        # Otherwise, if it rises and falls in lockstep with u (delta_takeover < 0.20 and peak_lag_days < 28,
        # or is an AY.* sublineage of Delta), it is a Neutral Parent-Hitchhiker!
        is_distinct_from_parent = (
            major_anc == ""
            or (
                not n.startswith("AY.")
                and (
                    delta_takeover >= 0.20
                    or (peak_lag_days >= 28 and jsd_vs_parent >= 0.20 and max_ratio >= 0.25)
                )
            )
        )

        is_neutral_hitchhiker = (
            major_anc != ""
            and not is_distinct_from_parent
            and (peak_s >= 0.015 or (n.startswith("AY.") and peak_s >= 0.005))
        )

        if is_neutral_hitchhiker:
            cat = "Neutral_Parent_Hitchhiker"
        elif peak_s >= 0.05 and is_distinct_from_parent:
            cat = "Distinct_Significant"
        elif (peak_s >= 0.015 and is_distinct_from_parent) or (n in all_curated_union and is_distinct_from_parent):
            cat = "Minor_Distinct_or_Curated"
        else:
            cat = "Fizzled_Noise"

        node_category[n] = cat
        node_meta[n] = {
            "variant": n,
            "category": cat,
            "peak_share": peak_s,
            "peak_date": eval_dates[peak_idx],
            "total_seq": tot_c,
            "major_parent": major_anc,
            "jsd_vs_parent": jsd_vs_parent,
            "delta_takeover": delta_takeover,
            "peak_lag_days": peak_lag_days,
            "in_curated": n in all_curated_union,
        }

    # Deduplicate single-chain taxonomic wrappers among Distinct_Significant nodes:
    # If node u and its child v both have the same prefix, peak within 14 days of each other,
    # and v accounts for >= 80% of u's peak share (e.g., BA.2.12 -> BA.2.12.1, EG.5 -> EG.5.1,
    # XFG.1 -> XFG.1.1, PY.1 -> PY.1.1 -> PY.1.1.1), keep the curated label (or the child if neither/both curated).
    sig_candidates = {n for n, c in node_category.items() if c == "Distinct_Significant"}
    redundant_wrappers = set()
    for v in list(sig_candidates):
        p = parent_of.get(v, "")
        if p in sig_candidates and p.split(".")[0] == v.split(".")[0]:
            ratio_v_to_p = node_meta[v]["peak_share"] / max(node_meta[p]["peak_share"], 1e-6)
            days_diff = abs(
                (pd.to_datetime(node_meta[v]["peak_date"]) - pd.to_datetime(node_meta[p]["peak_date"])).days
            )
            if ratio_v_to_p >= 0.80 and days_diff <= 14:
                # Prefer the specific child (e.g. BA.2.12.1 over BA.2.12, EG.5.1/EG.5) unless parent is curated and child isn't
                if (p in all_curated_union) and (v not in all_curated_union):
                    redundant_wrappers.add(v)
                else:
                    redundant_wrappers.add(p)

    df_meta = pd.DataFrame(list(node_meta.values()))
    sig_df = df_meta[
        (df_meta["category"] == "Distinct_Significant") & (~df_meta["variant"].isin(redundant_wrappers))
    ].sort_values("peak_share", ascending=False)
    print(f"Identified {len(sig_df)} deduplicated Distinct Significant Clades (peak >= 5%, distinct from parent):")
    print(
        sig_df[
            ["variant", "peak_share", "peak_date", "major_parent", "jsd_vs_parent", "delta_takeover", "peak_lag_days"]
        ].to_string(index=False)
    )

    # 3. Evaluate Sensitivity (Recall) & Pre-Peak Early-Warning on Distinct Significant Variants
    # A variant v is considered "attended to" at date t if v (or its immediate 1-step wrapper/child in the same wave) is in selected_list
    def is_attended(target: str, sel_list: list[str]) -> bool:
        if target in sel_list:
            return True
        t_uncomp = aliasor.uncompress(target)
        for s in sel_list:
            if not s:
                continue
            s_uncomp = aliasor.uncompress(s)
            if s_uncomp.startswith(t_uncomp + ".") and s.split(".")[0] == target.split(".")[0]:
                if len(s_uncomp.split(".")) - len(t_uncomp.split(".")) == 1:
                    return True
            if t_uncomp.startswith(s_uncomp + ".") and s.split(".")[0] == target.split(".")[0]:
                if len(t_uncomp.split(".")) - len(s_uncomp.split(".")) == 1:
                    if node_meta.get(target, {}).get("peak_share", 0) >= 0.80 * node_meta.get(s, {}).get("peak_share", 1):
                        return True
        return False

    sens_records = []
    for _, row in sig_df.iterrows():
        v = row["variant"]
        if v in ("B", "B.1", "B.1.1", "B.1.1.529"):
            continue
        traj_v = node_prefix_shares[v]
        peak_idx = int(np.argmax(traj_v))
        peak_date = eval_dates[peak_idx]
        peak_s = float(traj_v[peak_idx])

        # Find first detection date and prevalence share at first detection for Baseline and Contour
        # Only count detections within the variant's actual wave (within 365 days before peak_idx and after v first has >0 sequences)
        first_nonzero_idx = int(np.argmax(traj_v > 0))
        first_base_idx = None
        first_cont_idx = None
        for t_idx, r_row in df_rolling.iterrows():
            if int(t_idx) < first_nonzero_idx:
                continue
            b_list = r_row["base_list"].split(",") if pd.notna(r_row["base_list"]) else []
            c_list = r_row["contour_list"].split(",") if pd.notna(r_row["contour_list"]) else []
            if first_base_idx is None and is_attended(v, b_list):
                first_base_idx = int(t_idx)
            if first_cont_idx is None and is_attended(v, c_list):
                first_cont_idx = int(t_idx)

        share_at_base = float(traj_v[first_base_idx]) if first_base_idx is not None else np.nan
        share_at_cont = float(traj_v[first_cont_idx]) if first_cont_idx is not None else np.nan
        lead_before_peak_base = (
            (pd.to_datetime(peak_date) - pd.to_datetime(eval_dates[first_base_idx])).days
            if first_base_idx is not None
            else np.nan
        )
        lead_before_peak_cont = (
            (pd.to_datetime(peak_date) - pd.to_datetime(eval_dates[first_cont_idx])).days
            if first_cont_idx is not None
            else np.nan
        )

        sens_records.append(
            {
                "variant": v,
                "peak_share": peak_s,
                "peak_date": peak_date,
                "major_parent": row["major_parent"],
                "jsd_vs_parent": row["jsd_vs_parent"],
                "delta_takeover": row["delta_takeover"],
                "detected_base": first_base_idx is not None,
                "detected_contour": first_cont_idx is not None,
                "detected_before_peak_base": (first_base_idx is not None and first_base_idx < peak_idx),
                "detected_before_peak_contour": (first_cont_idx is not None and first_cont_idx < peak_idx),
                "detected_below_2pct_base": (first_base_idx is not None and share_at_base < 0.02),
                "detected_below_2pct_contour": (first_cont_idx is not None and share_at_cont < 0.02),
                "detected_below_5pct_base": (first_base_idx is not None and share_at_base < 0.05),
                "detected_below_5pct_contour": (first_cont_idx is not None and share_at_cont < 0.05),
                "share_at_first_detect_base": share_at_base,
                "share_at_first_detect_contour": share_at_cont,
                "days_before_peak_base": lead_before_peak_base,
                "days_before_peak_contour": lead_before_peak_cont,
            }
        )

    df_sens = pd.DataFrame(sens_records)
    df_sens.to_csv(RESULTS_DIR / "track2_distinct_significant_sensitivity.csv", index=False)

    # Filter to variants that emerged during our rolling window (peak_date >= "2021-07-15")
    df_sens_emerged = df_sens[df_sens["peak_date"] >= "2021-07-15"].copy()
    df_dom_emerged = df_sens_emerged[df_sens_emerged["peak_share"] >= 0.15].copy()

    print("\n=== EMPIRICAL SENSITIVITY ON DISTINCT SIGNIFICANT VARIANTS (Peak >= 5%, Emerged >= Jul 2021) ===")
    print(f"Total Distinct Significant Variants evaluated: {len(df_sens_emerged)}")
    print(
        f"  Overall Recall (Ever Attended):          Baseline = {df_sens_emerged['detected_base'].mean()*100:.1f}% | Contour = {df_sens_emerged['detected_contour'].mean()*100:.1f}%"
    )
    print(
        f"  Pre-Peak Recall (Attended Before Peak):  Baseline = {df_sens_emerged['detected_before_peak_base'].mean()*100:.1f}% | Contour = {df_sens_emerged['detected_before_peak_contour'].mean()*100:.1f}%"
    )
    print(
        f"  Early Recall (Attended at <5% Share):    Baseline = {df_sens_emerged['detected_below_5pct_base'].mean()*100:.1f}% | Contour = {df_sens_emerged['detected_below_5pct_contour'].mean()*100:.1f}%"
    )
    print(
        f"  Very Early Recall (Attended at <2%):     Baseline = {df_sens_emerged['detected_below_2pct_base'].mean()*100:.1f}% | Contour = {df_sens_emerged['detected_below_2pct_contour'].mean()*100:.1f}%"
    )
    print(
        f"  Median Share at First Attention:         Baseline = {df_sens_emerged['share_at_first_detect_base'].median()*100:.2f}% | Contour = {df_sens_emerged['share_at_first_detect_contour'].median()*100:.2f}%"
    )
    print(
        f"  Mean Lead Time Before Peak (Days):       Baseline = {df_sens_emerged['days_before_peak_base'].mean():.1f} d | Contour = {df_sens_emerged['days_before_peak_contour'].mean():.1f} d"
    )

    print("\n=== EMPIRICAL SENSITIVITY ON DISTINCT DOMINANT VARIANTS (Peak >= 15%, Emerged >= Jul 2021) ===")
    print(f"Total Distinct Dominant Variants evaluated: {len(df_dom_emerged)}")
    print(
        f"  Overall Recall (Ever Attended):          Baseline = {df_dom_emerged['detected_base'].mean()*100:.1f}% | Contour = {df_dom_emerged['detected_contour'].mean()*100:.1f}%"
    )
    print(
        f"  Early Recall (Attended at <5% Share):    Baseline = {df_dom_emerged['detected_below_5pct_base'].mean()*100:.1f}% | Contour = {df_dom_emerged['detected_below_5pct_contour'].mean()*100:.1f}%"
    )
    print(
        f"  Very Early Recall (Attended at <2%):     Baseline = {df_dom_emerged['detected_below_2pct_base'].mean()*100:.1f}% | Contour = {df_dom_emerged['detected_below_2pct_contour'].mean()*100:.1f}%"
    )
    print(
        f"  Median Share at First Attention:         Baseline = {df_dom_emerged['share_at_first_detect_base'].median()*100:.2f}% | Contour = {df_dom_emerged['share_at_first_detect_contour'].median()*100:.2f}%"
    )
    print(
        f"  Mean Lead Time Before Peak (Days):       Baseline = {df_dom_emerged['days_before_peak_base'].mean():.1f} d | Contour = {df_dom_emerged['days_before_peak_contour'].mean():.1f} d"
    )

    # 4. Evaluate Lag-Adjusted Prospective Concordance & False-Attention Breakdown across all 133 dates
    prospective_records = []
    for t_idx, r_row in df_rolling.iterrows():
        d_str = r_row["end_date"]
        regime = r_row["regime"]
        b_list = [x for x in (r_row["base_list"].split(",") if pd.notna(r_row["base_list"]) else []) if x]
        c_list = [x for x in (r_row["contour_list"].split(",") if pd.notna(r_row["contour_list"]) else []) if x]
        vshares = date_var_shares[d_str]
        obs_vars = list(vshares.keys())

        map_base = map_variants_to_curator_list(obs_vars, b_list, aliasor)
        map_cont = map_variants_to_curator_list(obs_vars, c_list, aliasor)

        cdc_now = get_cdc_active_list_at_date(df_cdc_hhs3, d_str, min_share=0.005)
        vi_b_now, nmi_b_now, h_cdc_given_b, h_b_given_cdc = np.nan, np.nan, np.nan, np.nan
        vi_c_now, nmi_c_now, h_cdc_given_c, h_c_given_cdc = np.nan, np.nan, np.nan, np.nan
        vi_b_prosp, nmi_b_prosp = np.nan, np.nan
        vi_c_prosp, nmi_c_prosp = np.nan, np.nan

        if len(cdc_now) >= 2 and len(obs_vars) > 0:
            map_cdc_now = map_variants_to_curator_list(obs_vars, cdc_now, aliasor)
            vi_b_now, nmi_b_now, h_cdc_given_b, h_b_given_cdc = conditional_entropies_and_vi(
                vshares, map_base, map_cdc_now
            )
            vi_c_now, nmi_c_now, h_cdc_given_c, h_c_given_cdc = conditional_entropies_and_vi(
                vshares, map_cont, map_cdc_now
            )

            # Prospective (Lag-Tolerant) Reference Lists over [t, t + 8 weeks]
            prosp_ref_b = get_prospective_cdc_reference_list(df_cdc_hhs3, d_str, b_list, horizon_weeks=8)
            prosp_ref_c = get_prospective_cdc_reference_list(df_cdc_hhs3, d_str, c_list, horizon_weeks=8)
            map_prosp_b = map_variants_to_curator_list(obs_vars, prosp_ref_b, aliasor)
            map_prosp_c = map_variants_to_curator_list(obs_vars, prosp_ref_c, aliasor)
            vi_b_prosp, nmi_b_prosp = variation_of_information(vshares, map_base, map_prosp_b)
            vi_c_prosp, nmi_c_prosp = variation_of_information(vshares, map_cont, map_prosp_c)

        # Classify attention allocation (by prevalence mass and by active slots >= 1% assigned share)
        def attention_breakdown(sel_list: list[str], v_map: dict[str, str]):
            assigned_mass: dict[str, float] = {}
            mass_sig = 0.0
            mass_hitch = 0.0
            mass_fizz = 0.0
            for v_obs, w_share in vshares.items():
                assigned_label = v_map.get(v_obs, "")
                assigned_mass[assigned_label] = assigned_mass.get(assigned_label, 0.0) + w_share
                cat = node_category.get(assigned_label, "Distinct_Significant")
                if cat == "Neutral_Parent_Hitchhiker":
                    mass_hitch += w_share
                elif cat == "Fizzled_Noise":
                    mass_fizz += w_share
                else:
                    mass_sig += w_share

            active_non_basal = [
                s for s in sel_list
                if s not in ("", "A", "B", "B.1", "B.1.1", "B.1.1.529")
                and assigned_mass.get(s, 0.0) >= 0.01
            ]
            n_active = max(len(active_non_basal), 1)
            slot_sig = sum(
                1 for s in active_non_basal
                if node_category.get(s) in ("Distinct_Significant", "Minor_Distinct_or_Curated")
            ) / n_active
            slot_hitch = sum(
                1 for s in active_non_basal
                if node_category.get(s) == "Neutral_Parent_Hitchhiker"
            ) / n_active
            slot_fizz = sum(
                1 for s in active_non_basal
                if node_category.get(s) == "Fizzled_Noise"
            ) / n_active
            return slot_sig, slot_hitch, slot_fizz, mass_sig, mass_hitch, mass_fizz, len(active_non_basal)

        b_ssig, b_shitch, b_sfizz, b_msig, b_mhitch, b_mfizz, b_nact = attention_breakdown(b_list, map_base)
        c_ssig, c_shitch, c_sfizz, c_msig, c_mhitch, c_mfizz, c_nact = attention_breakdown(c_list, map_cont)

        prospective_records.append(
            {
                "regime": regime,
                "end_date": d_str,
                "vi_base_now": vi_b_now,
                "vi_contour_now": vi_c_now,
                "vi_base_prospective8w": vi_b_prosp,
                "vi_contour_prospective8w": vi_c_prosp,
                "nmi_base_prospective8w": nmi_b_prosp,
                "nmi_contour_prospective8w": nmi_c_prosp,
                "missed_cdc_entropy_base": h_cdc_given_b,
                "missed_cdc_entropy_contour": h_cdc_given_c,
                "mass_signal_base": b_msig,
                "mass_signal_contour": c_msig,
                "mass_hitchhiker_base": b_mhitch,
                "mass_hitchhiker_contour": c_mhitch,
                "mass_fizzled_base": b_mfizz,
                "mass_fizzled_contour": c_mfizz,
                "active_slots_base": b_nact,
                "active_slots_contour": c_nact,
                "slot_signal_base": b_ssig,
                "slot_signal_contour": c_ssig,
                "slot_hitchhiker_base": b_shitch,
                "slot_hitchhiker_contour": c_shitch,
                "slot_fizzled_base": b_sfizz,
                "slot_fizzled_contour": c_sfizz,
            }
        )

    df_prosp = pd.DataFrame(prospective_records)
    df_prosp.to_csv(RESULTS_DIR / "track2_prospective_and_attention_metrics.csv", index=False)

    print("\n=== PROSPECTIVE (LAG-TOLERANT) CONCORDANCE BY REGIME ===")
    prosp_summary = (
        df_prosp.groupby("regime", sort=False)[
            [
                "vi_base_now",
                "vi_contour_now",
                "vi_base_prospective8w",
                "vi_contour_prospective8w",
                "nmi_base_prospective8w",
                "nmi_contour_prospective8w",
                "missed_cdc_entropy_base",
                "missed_cdc_entropy_contour",
            ]
        ]
        .mean()
        .reset_index()
    )
    print(prosp_summary.to_string(index=False))

    print("\n=== ATTENTION ALLOCATION & FALSE-ATTENTION BREAKDOWN BY REGIME ===")
    att_summary = (
        df_prosp.groupby("regime", sort=False)[
            [
                "mass_signal_base",
                "mass_signal_contour",
                "mass_hitchhiker_base",
                "mass_hitchhiker_contour",
                "mass_fizzled_base",
                "mass_fizzled_contour",
                "active_slots_base",
                "active_slots_contour",
                "slot_signal_base",
                "slot_signal_contour",
                "slot_hitchhiker_base",
                "slot_hitchhiker_contour",
            ]
        ]
        .mean()
        .reset_index()
    )
    print(att_summary.to_string(index=False))


if __name__ == "__main__":
    main()
