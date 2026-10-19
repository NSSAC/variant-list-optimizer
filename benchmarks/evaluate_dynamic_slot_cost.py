"""Evaluate the Dynamic Behavior x Support Slot-Cost Mode (--slot-cost with --max-size).

Sweeps dimensionless slot_cost tau_slot across [0.0, 0.001, 0.002, 0.0035, 0.005, 0.008]
across all 133 biweekly evaluation windows (2021-06-19 to 2026-07-18) and compares against
Baseline VLOpt (size == 20).

Measures:
  1. Actual returned list size |L_t| by regime (without any post-hoc prevalence filtering)
  2. Unfiltered Slot Precision & False Discovery Rate (across ALL returned non-basal variants)
  3. Prevalence-Weighted Signal Mass vs. Hitchhiker Mass
  4. Empirical Sensitivity (Overall Recall, <5% Share Recall, <2% Share Recall, Median Share at
     First Detection, and Lead Time on the 83 Distinct Significant & 36 Distinct Dominant clades)
  5. Lag-Tolerant Prospective Concordance (VI_prospective8w and NMI_prospective8w)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pango_aliasor.aliasor import Aliasor

from variant_list_optimizer.contour import ContourConfig, compute_node_contours
from variant_list_optimizer.make_graph import _expand_fips_tokens, _resolve_parent
from variant_list_optimizer.optimize import do_optimize_highs
from run_track2_benchmark import (
    DATA_DIR,
    RESULTS_DIR,
    HHS3_FIPS,
    build_graph_in_memory,
    filter_collection_date_typos,
    load_cdc_hhs3_lists,
    get_cdc_active_list_at_date,
    map_variants_to_curator_list,
    variation_of_information,
    parse_nextstrain_clade_dates,
    IMMUNE_CLUSTERING_OCT22_23_LIST,
)
from evaluate_sensitivity_and_prospective_concordance import (
    get_prospective_cdc_reference_list,
    jensen_shannon_divergence_base2,
)


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
    eval_dates = df_rolling["end_date"].tolist()

    # Build ancestor map and empirical node classifications (same as evaluate_sensitivity_and_prospective_concordance.py)
    all_variants = sorted(df_hhs3["variant"].unique().tolist())
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

    all_nodes = sorted({a for anc_list in ancestors_of.values() for a in anc_list if a != ""})
    node_prefix_shares = {n: np.zeros(len(eval_dates), dtype=np.float64) for n in all_nodes}
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
                if tot_w > 0 and (a == "B.1.617.2" or a.split(".")[0] == pref_v):
                    node_prefix_shares[a][t_idx] += count_val / tot_w

    all_cdc_curated = set(
        df_cdc_hhs3[df_cdc_hhs3["share"] >= 0.01]["variant"].unique().tolist()
    ) - {"Other", "Total", ""}
    all_curated_union = all_cdc_curated | set(nextstrain_dates.keys()) | set(IMMUNE_CLUSTERING_OCT22_23_LIST)

    node_category: dict[str, str] = {}
    node_meta: dict[str, dict] = {}
    for n in all_nodes:
        traj_v = node_prefix_shares[n]
        peak_s = float(np.max(traj_v))
        peak_idx = int(np.argmax(traj_v))
        tot_c = float(np.sum(traj_v) * 500.0)

        curr = parent_of.get(n, "")
        major_anc = ""
        while curr != "":
            anc_traj = node_prefix_shares.get(curr, np.zeros(1))
            v_active = traj_v >= max(0.005, 0.10 * peak_s)
            anc_during_v = float(np.max(anc_traj[v_active])) if np.any(v_active) else float(np.max(anc_traj))
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

            v_window = (traj_v >= max(0.005, 0.10 * peak_s)) | (rem_u >= max(0.02, 0.15 * float(np.max(rem_u))))
            if np.sum(v_window) >= 2 and np.sum(rem_u[v_window]) > 0 and np.sum(traj_v[v_window]) > 0:
                jsd_vs_parent = jensen_shannon_divergence_base2(traj_v[v_window], rem_u[v_window])
            else:
                jsd_vs_parent = 0.0

            denom = traj_v + rem_u
            valid_denom = (denom >= 0.03) & (traj_v > 0)
            if np.any(valid_denom):
                ratios = np.where(valid_denom, traj_v / np.maximum(denom, 1e-6), 0.0)
                v_idx_list = np.where(valid_denom)[0]
                init_ratio = float(np.mean(ratios[v_idx_list[:2]]))
                peak_window = valid_denom & (np.arange(len(traj_v)) >= v_idx_list[0])
                max_ratio = float(np.max(ratios[peak_window]))
                delta_takeover = max(0.0, max_ratio - init_ratio)
            else:
                max_ratio = 0.0
                delta_takeover = 0.0

            rem_peak_idx = int(np.argmax(rem_u)) if np.max(rem_u) > 0 else peak_idx
            peak_lag_days = (pd.to_datetime(eval_dates[peak_idx]) - pd.to_datetime(eval_dates[rem_peak_idx])).days
        else:
            jsd_vs_parent = 1.0
            max_ratio = 1.0
            delta_takeover = 1.0
            peak_lag_days = 999

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
            "major_parent": major_anc,
            "jsd_vs_parent": jsd_vs_parent,
            "delta_takeover": delta_takeover,
        }

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
                if (p in all_curated_union) and (v not in all_curated_union):
                    redundant_wrappers.add(v)
                else:
                    redundant_wrappers.add(p)

    df_meta = pd.DataFrame(list(node_meta.values()))
    sig_df = df_meta[
        (df_meta["category"] == "Distinct_Significant")
        & (~df_meta["variant"].isin(redundant_wrappers))
        & (~df_meta["variant"].isin(["B", "B.1", "B.1.1", "B.1.1.529"]))
        & (df_meta["peak_date"] >= "2021-07-15")
    ].sort_values("peak_share", ascending=False)

    # Sweep slot_cost values
    slot_costs = [0.0, 0.001, 0.002, 0.0035, 0.005, 0.008]
    contour_cfg = ContourConfig(temporal_decay_prev=0.25)

    # Store selected lists per (mode_name, t_idx)
    mode_lists: dict[str, list[list[str]]] = {"Baseline (==20)": []}
    for sc in slot_costs:
        label = f"Contour (tau={sc:.4f})" if sc > 0 else "Contour (==20, tau=0)"
        mode_lists[label] = []

    for t_idx, r_row in df_rolling.iterrows():
        d_str = r_row["end_date"]
        b_list = [x for x in (r_row["base_list"].split(",") if pd.notna(r_row["base_list"]) else []) if x]
        mode_lists["Baseline (==20)"].append(b_list)

        end_ts = pd.to_datetime(d_str)
        start_12w = (end_ts - pd.Timedelta(weeks=12)).strftime("%Y-%m-%d")
        G_contour, df_cont = build_graph_in_memory(
            df_hhs3, start_12w, d_str, aliasor, resolve_recombinants=True
        )
        G_contour.graph["weighting_mode"] = "contour"
        if G_contour.number_of_nodes() > 1:
            compute_node_contours(df_cont, G_contour, reference_date=d_str, config=contour_cfg)

        for sc in slot_costs:
            label = f"Contour (tau={sc:.4f})" if sc > 0 else "Contour (==20, tau=0)"
            if sc == 0.0:
                c_list = [x for x in (r_row["contour_list"].split(",") if pd.notna(r_row["contour_list"]) else []) if x]
                mode_lists[label].append(c_list)
            else:
                if G_contour.number_of_nodes() <= 1:
                    sel = set(G_contour.nodes())
                else:
                    sel = do_optimize_highs(
                        20,
                        G_contour,
                        distance_penalty=1.0,
                        penalty_mode="contour",
                        timelimit=30.0,
                        slot_cost=sc,
                        max_size=True,
                    )
                mode_lists[label].append(sorted(s for s in sel if s != ""))

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

    summary_rows = []
    regime_size_rows = []

    for mode_name, lists_by_t in mode_lists.items():
        # 1. Evaluate Sensitivity on Sig (N=83) and Dom (N=36)
        rec_sig, rec_sig_early5, rec_sig_early2 = [], [], []
        rec_dom, rec_dom_early5, rec_dom_early2, dom_shares = [], [], [], []

        for _, s_row in sig_df.iterrows():
            v = s_row["variant"]
            traj_v = node_prefix_shares[v]
            first_nonzero_idx = int(np.argmax(traj_v > 0))
            first_idx = None
            for t_idx in range(first_nonzero_idx, len(eval_dates)):
                if is_attended(v, lists_by_t[t_idx]):
                    first_idx = t_idx
                    break
            det = first_idx is not None
            sh = float(traj_v[first_idx]) if det else np.nan
            rec_sig.append(det)
            rec_sig_early5.append(det and sh < 0.05)
            rec_sig_early2.append(det and sh < 0.02)
            if s_row["peak_share"] >= 0.15:
                rec_dom.append(det)
                rec_dom_early5.append(det and sh < 0.05)
                rec_dom_early2.append(det and sh < 0.02)
                if det:
                    dom_shares.append(sh)

        # 2. Evaluate Unfiltered Slot Precision, Signal Mass, List Size, and Prospective VI
        raw_sizes, non_basal_sizes = [], []
        unfiltered_slot_prec, unfiltered_slot_hitch, unfiltered_slot_fizz = [], [], []
        mass_sig_list, mass_hitch_list = [], []
        vi_prosp_list, nmi_prosp_list = [], []

        for t_idx, r_row in df_rolling.iterrows():
            d_str = r_row["end_date"]
            regime = r_row["regime"]
            sel_list = lists_by_t[t_idx]
            vshares = date_var_shares[d_str]
            obs_vars = list(vshares.keys())
            v_map = map_variants_to_curator_list(obs_vars, sel_list, aliasor)

            non_basal = [s for s in sel_list if s not in ("", "A", "B", "B.1", "B.1.1", "B.1.1.529")]
            n_nb = max(len(non_basal), 1)
            raw_sizes.append(len(sel_list))
            non_basal_sizes.append(len(non_basal))

            s_sig = sum(1 for s in non_basal if node_category.get(s) in ("Distinct_Significant", "Minor_Distinct_or_Curated")) / n_nb
            s_hitch = sum(1 for s in non_basal if node_category.get(s) == "Neutral_Parent_Hitchhiker") / n_nb
            s_fizz = sum(1 for s in non_basal if node_category.get(s) == "Fizzled_Noise") / n_nb
            unfiltered_slot_prec.append(s_sig)
            unfiltered_slot_hitch.append(s_hitch)
            unfiltered_slot_fizz.append(s_fizz)

            m_sig = 0.0
            m_hitch = 0.0
            for v_obs, w_share in vshares.items():
                assigned = v_map.get(v_obs, "")
                cat = node_category.get(assigned, "Distinct_Significant")
                if cat == "Neutral_Parent_Hitchhiker":
                    m_hitch += w_share
                elif cat != "Fizzled_Noise":
                    m_sig += w_share
            mass_sig_list.append(m_sig)
            mass_hitch_list.append(m_hitch)

            cdc_now = get_cdc_active_list_at_date(df_cdc_hhs3, d_str, min_share=0.005)
            if len(cdc_now) >= 2 and len(obs_vars) > 0:
                prosp_ref = get_prospective_cdc_reference_list(df_cdc_hhs3, d_str, sel_list, horizon_weeks=8)
                map_prosp = map_variants_to_curator_list(obs_vars, prosp_ref, aliasor)
                vi_p, nmi_p = variation_of_information(vshares, v_map, map_prosp)
                vi_prosp_list.append(vi_p)
                nmi_prosp_list.append(nmi_p)

            regime_size_rows.append(
                {
                    "mode": mode_name,
                    "regime": regime,
                    "list_size": len(sel_list),
                    "unfiltered_slot_precision": s_sig,
                    "unfiltered_slot_fdr": s_hitch + s_fizz,
                }
            )

        prec_val = float(np.mean(unfiltered_slot_prec))
        rec5_val = float(np.mean(rec_sig_early5))
        f1_slot = 2 * prec_val * rec5_val / max(prec_val + rec5_val, 1e-9)

        summary_rows.append(
            {
                "mode": mode_name,
                "mean_list_size": float(np.mean(raw_sizes)),
                "unfiltered_slot_precision": prec_val,
                "unfiltered_slot_hitchhiker": float(np.mean(unfiltered_slot_hitch)),
                "unfiltered_slot_fizzled": float(np.mean(unfiltered_slot_fizz)),
                "mass_signal_precision": float(np.mean(mass_sig_list)),
                "sig_overall_recall": float(np.mean(rec_sig)),
                "sig_early5_recall": rec5_val,
                "dom_overall_recall": float(np.mean(rec_dom)),
                "dom_early5_recall": float(np.mean(rec_dom_early5)),
                "dom_early2_recall": float(np.mean(rec_dom_early2)),
                "dom_median_detect_share": float(np.median(dom_shares)),
                "f1_unfiltered_slot_vs_early5": f1_slot,
                "vi_prospective8w": float(np.mean(vi_prosp_list)),
                "nmi_prospective8w": float(np.mean(nmi_prosp_list)),
            }
        )

    df_sum = pd.DataFrame(summary_rows)
    df_sum.to_csv(RESULTS_DIR / "track2_dynamic_slot_cost_sweep.csv", index=False)
    print("\n=== DYNAMIC BEHAVIOR x SUPPORT SLOT-COST TRADEOFF SWEEP (ALL 133 WINDOWS) ===")
    print(df_sum.to_string(index=False))

    df_reg = pd.DataFrame(regime_size_rows)
    reg_piv = (
        df_reg.groupby(["mode", "regime"], sort=False)[
            ["list_size", "unfiltered_slot_precision", "unfiltered_slot_fdr"]
        ]
        .mean()
        .reset_index()
    )
    print("\n=== DYNAMIC LIST SIZE & UNFILTERED SLOT PRECISION BY REGIME ===")
    print(reg_piv.to_string(index=False))


if __name__ == "__main__":
    main()
