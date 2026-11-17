"""High-level in-memory Python API for Variant List Optimizer.

Designed for direct integration into downstream surveillance pipelines such as
`VariantTrackingPublic` without intermediate Parquet/JSON/HDF5 files or C++ binaries.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd
from pango_aliasor.aliasor import Aliasor

from .contour import ContourConfig, compute_node_contours
from .make_graph import _expand_fips_tokens, _resolve_parent
from .optimize import compute_assignment_penalty, do_optimize_highs, make_nia


@dataclass
class VariantSelectionResult:
    """Result of running automated variant list selection."""

    variants: list[str]
    assignment: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variants": self.variants,
            "assignment": self.assignment,
            "metadata": self.metadata,
        }


def filter_collection_date_typos(
    df: pd.DataFrame,
    gap_days: int = 60,
    tail_frac: float = 0.005,
) -> pd.DataFrame:
    """Remove isolated pre-emergence collection-date typos via backward chaining."""
    if df.empty:
        return df

    weekly = (
        df.assign(week=df["date"].dt.to_period("W").dt.start_time)
        .groupby(["variant", "week"], as_index=False)["weight"]
        .sum()
    )
    weekly = weekly[weekly["weight"] > 0].sort_values(["variant", "week"])

    min_valid_dates: dict[str, pd.Timestamp] = {}
    for var, grp in weekly.groupby("variant", sort=False):
        if len(grp) <= 1:
            continue
        weeks = grp["week"].tolist()
        weights = grp["weight"].to_numpy()
        total_w = float(weights.sum())
        if total_w <= 0:
            continue
        cum_before = np.cumsum(weights) - weights
        cutoff_date = None
        for i in range(len(weeks) - 1, 0, -1):
            gap = (weeks[i] - weeks[i - 1]).days
            if gap > gap_days and (cum_before[i] / total_w) < tail_frac:
                cutoff_date = weeks[i]
                break
        if cutoff_date is not None:
            min_valid_dates[str(var)] = cutoff_date

    if not min_valid_dates:
        return df

    cutoffs = df["variant"].map(min_valid_dates)
    mask = cutoffs.isna() | (df["date"] >= cutoffs)
    return df[mask].copy()


def _normalize_observation_df(data: pd.DataFrame | str | Path) -> pd.DataFrame:
    """Load and normalize long-format surveillance counts into [date, fips, variant, weight]."""
    if isinstance(data, (str, Path)):
        df = pd.read_csv(
            data,
            dtype={"fips": str},
            low_memory=False,
        )
    else:
        df = data.copy()

    col_map: dict[str, str] = {}
    if "lineage" in df.columns and "variant" not in df.columns:
        col_map["lineage"] = "variant"
    if "lineage_count" in df.columns and "weight" not in df.columns:
        col_map["lineage_count"] = "weight"
    if col_map:
        df = df.rename(columns=col_map)

    required = {"date", "fips", "variant", "weight"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input data missing required columns: {sorted(missing)}")

    df = df[["date", "fips", "variant", "weight"]].dropna(subset=["date", "variant"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["fips"] = df["fips"].astype(str).str.strip()
    df["variant"] = df["variant"].astype(str).str.strip()
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce").fillna(0.0)
    df = df[(df["weight"] > 0) & (~df["variant"].isin(["", "Other", "other", "Unassigned"]))]
    return df


def select_variant_list(
    data: pd.DataFrame | str | Path,
    *,
    end_date: str | pd.Timestamp | None = None,
    window_weeks: int = 12,
    fips_list: str | Sequence[str] | None = None,
    fallback_fips: str | None = "US",
    min_window_sequences: float = 150.0,
    max_variants: int = 12,
    dynamic_sizing: bool = True,
    slot_cost: float = 0.004,
    weighting_mode: str = "contour",
    temporal_importance_decay: float = 0.1,
    distance_penalty: float = 10.0,
    resolve_recombinants: bool = True,
    filter_date_typos: bool = True,
    aliasor: Aliasor | None = None,
    alias_file: str | Path | None = None,
    contour_config: ContourConfig | None = None,
    timelimit: float = 120.0,
) -> VariantSelectionResult:
    """Select an optimal watchlist of up to `max_variants` Pango lineages in memory.

    Parameters
    ----------
    data:
        Long-format DataFrame or CSV path with columns `date`, `fips`, `lineage` (or `variant`),
        and `lineage_count` (or `weight`).
    end_date:
        Inclusive end date (`YYYY-MM-DD`) of observations to consider. Defaults to max date in `data`.
    window_weeks:
        Trailing lookback window in weeks used for contour estimation (default: 12).
    fips_list:
        Comma-separated string or sequence of target FIPS codes (e.g., `"51"`, `["51", "24"]`, `"US"`).
        If `None`, uses `"US"` if available, else pools all rows.
    fallback_fips:
        Fallback FIPS code (default `"US"`) used if the requested `fips_list` has fewer than
        `min_window_sequences` total sequences in the lookback window.
    max_variants:
        Maximum number of non-root Pango variants to return (default: 12).
    dynamic_sizing:
        If True (default), uses `--max-size` with `slot_cost` so low-diversity waves return fewer
        than `max_variants` variants rather than parking slots on minor noise lineages.
    slot_cost:
        Dimensionless Behavior x Support threshold when `dynamic_sizing=True` (default: 0.004).
    weighting_mode:
        `"contour"` (default) or `"static"`.
    """
    df = _normalize_observation_df(data)
    if df.empty:
        return VariantSelectionResult(variants=[], metadata={"reason": "empty_input_data"})

    max_obs_ts = df["date"].max()
    if end_date is not None:
        requested_end_ts = pd.to_datetime(end_date)
        end_ts = min(requested_end_ts, max_obs_ts)
    else:
        end_ts = max_obs_ts

    # Make end_date inclusive of observations on end_date itself
    exclusive_end_ts = end_ts + pd.Timedelta(days=1)
    start_ts = exclusive_end_ts - pd.Timedelta(weeks=window_weeks)
    start_str = start_ts.strftime("%Y-%m-%d")
    end_str = exclusive_end_ts.strftime("%Y-%m-%d")

    has_us_row = "US" in set(df["fips"].unique())
    if fips_list is None:
        requested_fips_str = "US" if has_us_row else "ALL"
    elif isinstance(fips_list, str):
        requested_fips_str = fips_list.strip()
    else:
        requested_fips_str = ",".join(str(x).strip() for x in fips_list if str(x).strip())

    eff_fallback = fallback_fips
    if eff_fallback == "US" and not has_us_row:
        eff_fallback = "ALL"

    typo_lookback_start = start_ts - pd.Timedelta(days=180)

    def _subset_by_fips(fips_spec: str) -> pd.DataFrame:
        sub_time = df[(df["date"] >= typo_lookback_start) & (df["date"] < exclusive_end_ts)]
        if fips_spec.upper() == "ALL" or not fips_spec:
            sub = sub_time[sub_time["fips"] != "US"].copy() if has_us_row else sub_time.copy()
        else:
            tokens = _expand_fips_tokens(fips_spec)
            sub = sub_time[sub_time["fips"].isin(tokens)].copy()
        if filter_date_typos and not sub.empty:
            sub = filter_collection_date_typos(sub)
        return sub[(sub["date"] >= start_ts) & (sub["date"] < exclusive_end_ts)].copy()

    df_win = _subset_by_fips(requested_fips_str)
    win_seqs = float(df_win["weight"].sum())
    used_fallback = False
    effective_fips_str = requested_fips_str

    if (
        win_seqs < min_window_sequences
        and eff_fallback
        and requested_fips_str.upper() != eff_fallback.upper()
        and (eff_fallback.upper() == "ALL" or eff_fallback in set(df["fips"].unique()))
    ):
        df_fallback = _subset_by_fips(eff_fallback)
        if float(df_fallback["weight"].sum()) > win_seqs:
            df_win = df_fallback
            win_seqs = float(df_win["weight"].sum())
            effective_fips_str = eff_fallback
            used_fallback = True

    if df_win.empty or win_seqs <= 0:
        return VariantSelectionResult(
            variants=[],
            metadata={
                "start_date": start_str,
                "end_date": end_ts.strftime("%Y-%m-%d"),
                "requested_fips": requested_fips_str,
                "effective_fips": effective_fips_str,
                "window_sequences": 0.0,
                "reason": "no_sequences_in_window",
            },
        )

    if aliasor is None:
        aliasor = Aliasor(str(alias_file) if alias_file is not None else None)

    # Build rooted Pango hierarchy G over observed variants in the window
    G = nx.DiGraph()
    G.graph["start_date"] = start_str
    G.graph["end_date"] = end_str
    G.graph["weighting_mode"] = weighting_mode

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

    ilp_target_size = max(2, int(max_variants) + 1)  # +1 for mandatory root ""

    if weighting_mode == "contour":
        cfg = contour_config or ContourConfig(temporal_decay_prev=0.25)
        compute_node_contours(
            df_win,
            G,
            reference_date=end_str,
            config=cfg,
            legacy_decay=False,
        )
        eff_slot_cost = float(slot_cost) if dynamic_sizing else 0.0
        selected_raw = do_optimize_highs(
            size=ilp_target_size,
            G=G,
            distance_penalty=1.0,
            penalty_mode="contour",
            timelimit=timelimit,
            slot_cost=eff_slot_cost,
            max_size=dynamic_sizing,
        )
        pen_mat = compute_assignment_penalty(G, 1.0, penalty_mode="contour")
        nia_map = make_nia(selected_raw, G, 1.0, pen_mat)
    else:
        time_unit = 30.0 * 24.0 * 3600.0
        df_b = df_win.copy()
        df_b["time"] = np.abs((exclusive_end_ts - df_b["date"]).dt.total_seconds()) / time_unit
        df_b["imp"] = (temporal_importance_decay ** df_b["time"]) * df_b["weight"]
        imp_map = df_b.groupby("variant")["imp"].sum().to_dict()
        for n in G.nodes():
            G.nodes[n]["importance"] = float(imp_map.get(n, 0.0))
        for u, v in G.edges():
            G.edges[u, v]["distance"] = 1.0
        eff_slot_cost = float(slot_cost) if dynamic_sizing else 0.0
        selected_raw = do_optimize_highs(
            size=ilp_target_size,
            G=G,
            distance_penalty=distance_penalty,
            penalty_mode="static",
            timelimit=timelimit,
            slot_cost=eff_slot_cost,
            max_size=dynamic_sizing,
        )
        pen_mat = compute_assignment_penalty(G, distance_penalty, penalty_mode="static")
        nia_map = make_nia(selected_raw, G, distance_penalty, pen_mat)

    # Filter out basal root "" (and obsolete zero-direct basal trunks like "B", "B.1")
    recent_cutoff = exclusive_end_ts - pd.Timedelta(weeks=4)
    recent_counts = (
        df_win[df_win["date"] >= recent_cutoff]
        .groupby("variant")["weight"]
        .sum()
        .to_dict()
    )
    total_recent = max(1.0, float(sum(recent_counts.values())))
    direct_win_counts = df_win.groupby("variant")["weight"].sum().to_dict()

    basal_umbrellas = {"", "A", "B", "B.1", "B.1.1"}
    filtered_variants: list[str] = []
    for v in selected_raw:
        if v == "":
            continue
        if v in basal_umbrellas and (direct_win_counts.get(v, 0.0) / win_seqs) < 0.01:
            continue
        filtered_variants.append(v)

    # Compute folded recent share per selected variant for clean ordering
    assignment: dict[str, str] = {}
    folded_recent_counts: dict[str, float] = {v: 0.0 for v in filtered_variants}
    valid_set = set(filtered_variants)
    for node, info in nia_map.items():
        if node == "":
            continue
        target = info.nia if info.nia in valid_set else "other"
        assignment[node] = target
        if target in folded_recent_counts:
            folded_recent_counts[target] += float(recent_counts.get(node, 0.0))

    # Order variants reproducibly by folded recent share (descending), breaking ties alphabetically
    ordered_variants = sorted(
        filtered_variants,
        key=lambda v: (-folded_recent_counts.get(v, 0.0), v),
    )

    folded_shares = {
        v: round(folded_recent_counts.get(v, 0.0) / total_recent, 4)
        for v in ordered_variants
    }

    metadata: dict[str, Any] = {
        "start_date": start_str,
        "end_date": end_ts.strftime("%Y-%m-%d"),
        "window_weeks": window_weeks,
        "requested_fips": requested_fips_str,
        "effective_fips": effective_fips_str,
        "used_fallback_fips": used_fallback,
        "window_sequences": round(win_seqs, 1),
        "observed_lineages_in_window": len(seen_variants),
        "max_variants": max_variants,
        "returned_variants_count": len(ordered_variants),
        "dynamic_sizing": dynamic_sizing,
        "slot_cost": float(slot_cost) if dynamic_sizing else 0.0,
        "weighting_mode": weighting_mode,
        "folded_recent_4w_shares": folded_shares,
    }

    return VariantSelectionResult(
        variants=ordered_variants,
        assignment=assignment,
        metadata=metadata,
    )
