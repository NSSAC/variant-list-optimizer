"""Contour-regularized variant graph weighting and consolidation penalties.

Implements the mathematical formulation from:
'Contour-Regularized Variant List Optimization: Mathematical Formulation,
Antigenic Concordance, and Longitudinal Benchmarking'.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import networkx as nx
import numpy as np
import pandas as pd


@dataclass
class ContourConfig:
    """Hyperparameters for Contour-Regularized Variant List Optimization."""

    # Taxonomic tree step penalty (small topological tie-breaker per Pango tier)
    lambda_tree: float = 0.005
    # Trajectory contour divergence penalty weight
    lambda_contour: float = 25.0
    # Balance between JSD (distributional overlap) and W1 (temporal phase lag)
    alpha: float = 0.5
    # Weight on terminal emergence log-velocity divergence D_vel
    beta: float = 0.6
    # Sensitivity scaling constant inside tanh for D_vel
    kappa: float = 0.5
    # Bayesian sample-size shrinkage threshold N_0 (sequences)
    n0_shrinkage: float = 15.0
    # Monthly temporal importance decay for terminal prevalence P_agg(u)
    temporal_decay_prev: float = 0.1
    # Monthly temporal importance decay for velocity divergence weights w_tilde(t)
    temporal_decay_vel: float = 0.25
    # Dirichlet pseudocount epsilon for contour normalization
    epsilon: float = 1e-4
    # Bin width in days for contour extraction (7 = weekly)
    bin_days: int = 7
    # Exponent on node importance when scaling consolidation penalties
    importance_exponent: float = 0.65
    # Multinomial sampling noise floor below which sibling contours are treated as identical
    noise_floor: float = 0.06
    # Whether to compute state-stratified contour divergence across FIPS codes
    state_stratified: bool = False


def jensen_shannon_divergence_base2(p: np.ndarray, q: np.ndarray) -> float:
    """Compute base-2 Jensen-Shannon Divergence JSD(p || q) bounded in [0, 1]."""
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    p_sum = p.sum()
    q_sum = q.sum()
    if p_sum <= 0 or q_sum <= 0:
        return 0.0
    p = p / p_sum
    q = q / q_sum
    m = 0.5 * (p + q)

    mask_p = p > 0
    mask_q = q > 0
    kl_p = np.sum(p[mask_p] * np.log2(p[mask_p] / m[mask_p]))
    kl_q = np.sum(q[mask_q] * np.log2(q[mask_q] / m[mask_q]))
    jsd = 0.5 * (kl_p + kl_q)
    return float(np.clip(jsd, 0.0, 1.0))


def wasserstein_1d_discrete(p: np.ndarray, q: np.ndarray) -> tuple[float, float]:
    """Compute normalized 1-Wasserstein distance in [0, 1] and signed temporal shift in [-1, 1].

    Returns:
      (w1, signed_shift) where signed_shift > 0 means distribution p is shifted toward
      later time points (expanding/emerging) relative to q.
    """
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    w = len(p)
    if w <= 1:
        return 0.0, 0.0
    p = p / p.sum()
    q = q / q.sum()
    cdf_p = np.cumsum(p)[:-1]
    cdf_q = np.cumsum(q)[:-1]
    w1 = np.sum(np.abs(cdf_p - cdf_q)) / float(w - 1)
    # If p is later than q, cdf_p(t) < cdf_q(t), so (cdf_q - cdf_p) > 0
    signed_shift = np.sum(cdf_q - cdf_p) / float(w - 1)
    return float(np.clip(w1, 0.0, 1.0)), float(np.clip(signed_shift, -1.0, 1.0))


def velocity_divergence(
    vel_v: np.ndarray,
    vel_u: np.ndarray,
    vel_weights: np.ndarray,
    kappa: float = 0.5,
) -> tuple[float, float]:
    """Compute Temporally Weighted Velocity Divergence D_vel in [0, 1) and signed velocity advantage.

    Uses both directional net log-growth difference and recent step consistency so
    Poisson week-to-week zigzags cancel out while sustained emergence/contraction reinforces.
    """
    if len(vel_v) == 0 or len(vel_u) == 0:
        return 0.0, 0.0
    diff = vel_v - vel_u
    signed_vel = float(np.sum(vel_weights * diff))
    # Combine directional net velocity divergence (75%) with step-wise divergence (25%)
    step_sq = float(np.sum(vel_weights * (diff ** 2)))
    combined_vel_sq = 0.75 * (signed_vel ** 2) + 0.25 * step_sq
    return float(math.tanh(kappa * combined_vel_sq)), signed_vel


def extract_contour_and_velocity(
    weekly_shares: np.ndarray,
    epsilon: float = 1e-4,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Compute volume-normalized contour c_tilde and regularized log-velocity c_dot from weekly shares p_v(t)."""
    weekly_shares = np.asarray(weekly_shares, dtype=np.float64)
    w = len(weekly_shares)
    vol = float(np.sum(weekly_shares))
    c_tilde = (weekly_shares + epsilon) / (vol + w * epsilon)
    if w >= 2:
        # Use uniform Laplace/Jeffreys baseline smoothing (0.25 / w) for log-velocity ratios
        # so single zero-count weeks do not produce artificial log(1e4) spikes.
        vel_smooth = 0.25 / float(w)
        c_vel = (c_tilde + vel_smooth) / (1.0 + w * vel_smooth)
        c_dot = np.log(c_vel[1:] / c_vel[:-1])
    else:
        c_dot = np.zeros(0, dtype=np.float64)
    return c_tilde, c_dot, vol


def compute_divergence_components(
    c_tilde_v: np.ndarray,
    c_dot_v: np.ndarray,
    c_tilde_u: np.ndarray,
    c_dot_u: np.ndarray,
    vel_weights: np.ndarray,
    config: ContourConfig,
    n_eff: float = 100.0,
) -> tuple[float, float, float, float]:
    """Return (total_divergence, jsd, w1, d_vel) between child/descendant v and ancestor u."""
    jsd = jensen_shannon_divergence_base2(c_tilde_v, c_tilde_u)
    w1, signed_shift = wasserstein_1d_discrete(c_tilde_v, c_tilde_u)
    d_vel, signed_vel = velocity_divergence(
        c_dot_v, c_dot_u, vel_weights, kappa=config.kappa
    )
    raw_total = config.alpha * jsd + (1.0 - config.alpha) * w1 + config.beta * d_vel
    # Sample-size-aware multinomial sampling noise floor:
    # A sublineage of size n_eff drawn from the exact same contour has expected sampling
    # fluctuation ~ O(1 / sqrt(n_eff)). Subtracting this floor folds noisy neutral siblings.
    multinomial_floor = config.noise_floor + 0.50 / math.sqrt(max(n_eff, 1.0))
    # If a variant is strongly emerging at the terminal end (signed_shift > 0.15 and signed_vel > 0.10),
    # use only the base noise_floor so genuine low-count early emergence is not suppressed.
    if signed_shift > 0.15 and signed_vel > 0.10:
        effective_floor = config.noise_floor
    else:
        effective_floor = multinomial_floor

    eff_total = max(0.0, raw_total - effective_floor)
    if signed_shift > 0.05 and signed_vel > 0.05:
        eff_total *= 1.35
    return float(eff_total), float(jsd), float(w1), float(d_vel)


def compute_node_contours(
    data: pd.DataFrame,
    G: nx.DiGraph,
    reference_date: str,
    config: ContourConfig,
    legacy_decay: bool = False,
) -> None:
    """Populate node importance, normalized temporal contours, and edge distances on G.

    For each node v in G:
      - Computes direct temporally weighted importance and raw sequence count.
      - Computes subtree-aggregated weekly sequence counts so internal Pango nodes
        have well-defined clade trajectories even when sequences are called at leaf sublineages.
      - Computes volume-normalized contour c_tilde_v(t) and log-velocity c_dot_v(t).
      - Computes Bayesian sample-size credibility factor Phi(N_v) = N_v / (N_v + N_0).
    """
    time_unit_seconds = 30.0 * 24.0 * 3600.0  # 1 Month (30 days)
    ref_ts = pd.to_datetime(reference_date)

    if data.empty:
        for v in G:
            G.nodes[v]["importance"] = 0.0
            G.nodes[v]["raw_count"] = 0.0
            G.nodes[v]["clade_count"] = 0.0
            G.nodes[v]["credibility"] = 0.0
            G.nodes[v]["contour"] = [1.0]
            G.nodes[v]["velocity"] = []
        for u, v in G.edges:
            G.edges[u, v]["distance"] = config.lambda_tree
        return

    df = data.copy()
    df["date"] = pd.to_datetime(df["date"])
    delta_seconds = np.abs((ref_ts - df["date"]).dt.total_seconds())
    df["time_months"] = delta_seconds / time_unit_seconds

    # 1. Node importance (terminal prevalence weighting)
    if legacy_decay:
        df["imp_weight"] = (
            config.temporal_decay_prev * np.exp(-df["time_months"]) * df["weight"]
        )
    else:
        decay_base = max(config.temporal_decay_prev, 1e-12)
        df["imp_weight"] = (decay_base ** df["time_months"]) * df["weight"]

    direct_importance = df.groupby("variant")["imp_weight"].sum().to_dict()
    direct_raw_count = df.groupby("variant")["weight"].sum().to_dict()

    # 2. Weekly time binning [0, ..., W-1] anchored at reference_date
    # Avoid creating a 1-day stub bin at the boundary when delta_days == W * bin_days
    delta_days = np.maximum(0.0, (ref_ts - df["date"]).dt.total_seconds() / 86400.0 - 1e-6)
    bin_from_end = (delta_days // config.bin_days).astype(int)
    max_bin = int(bin_from_end.max()) if len(bin_from_end) > 0 else 0
    num_weeks = max(2, max_bin + 1)
    # Map so t_idx = 0 is oldest week and t_idx = num_weeks - 1 is most recent week (END_DATE)
    df["week_idx"] = (num_weeks - 1) - bin_from_end

    # Total regional sequences per week N_seq(t)
    weekly_totals = np.zeros(num_weeks, dtype=np.float64)
    week_sums = df.groupby("week_idx")["weight"].sum()
    for w_idx, val in week_sums.items():
        if 0 <= int(w_idx) < num_weeks:
            weekly_totals[int(w_idx)] = float(val)

    # Direct weekly counts per variant
    direct_weekly: dict[str, np.ndarray] = {
        v: np.zeros(num_weeks, dtype=np.float64) for v in G
    }
    var_week_sums = df.groupby(["variant", "week_idx"])["weight"].sum()
    for (var, w_idx), val in var_week_sums.items():
        if var in direct_weekly and 0 <= int(w_idx) < num_weeks:
            direct_weekly[var][int(w_idx)] = float(val)

    # 3. Bottom-up subtree aggregation over the Pango tree
    # Aggregate both full subtree counts and same-prefix clade counts (where child shares Pango letter prefix with v).
    # Same-prefix aggregation ensures that newly designated Pango alias clades (e.g. KP.2, KP.3, LB.1 under JN.1.11.1,
    # or BQ.1, BF.7 under BA.5) do not contaminate the parent alias's trajectory contour.
    topological_nodes = list(nx.topological_sort(G))
    clade_weekly: dict[str, np.ndarray] = {
        v: direct_weekly[v].copy() for v in G
    }
    clade_raw_count: dict[str, float] = {
        v: float(direct_raw_count.get(v, 0.0)) for v in G
    }
    clade_importance: dict[str, float] = {
        v: float(direct_importance.get(v, 0.0)) for v in G
    }
    prefix_weekly: dict[str, np.ndarray] = {
        v: direct_weekly[v].copy() for v in G
    }
    prefix_raw_count: dict[str, float] = {
        v: float(direct_raw_count.get(v, 0.0)) for v in G
    }

    for v in reversed(topological_nodes):
        pref_v = v.split(".")[0] if v != "" else ""
        for child in G.succ[v]:
            clade_weekly[v] += clade_weekly[child]
            clade_raw_count[v] += clade_raw_count[child]
            clade_importance[v] += clade_importance[child]
            pref_c = child.split(".")[0] if child != "" else ""
            if v == "" or pref_c == pref_v:
                prefix_weekly[v] += prefix_weekly[child]
                prefix_raw_count[v] += prefix_raw_count[child]

    # Safe denominator for regional prevalence share p_v(t) = n_v(t) / N_seq(t)
    safe_weekly_totals = np.where(weekly_totals > 0, weekly_totals, 1.0)
    active_week_mask = weekly_totals > 0
    if not np.any(active_week_mask):
        active_week_mask = np.ones(num_weeks, dtype=bool)

    n_active = int(np.sum(active_week_mask))
    if n_active >= 2:
        step_ages_months = (
            np.arange(n_active - 2, -1, -1, dtype=np.float64)
            * float(config.bin_days)
            / 30.0
        )
        vel_decay_base = max(config.temporal_decay_vel, 1e-12)
        raw_vel_w = vel_decay_base ** step_ages_months
        vel_weights = raw_vel_w / np.sum(raw_vel_w)
    else:
        vel_weights = np.ones(0, dtype=np.float64)

    # 4. Compute volume-normalized contour and log-velocity for each node
    for v in G:
        imp = float(direct_importance.get(v, 0.0))
        raw_n = float(direct_raw_count.get(v, 0.0))
        clade_n = float(clade_raw_count.get(v, 0.0))
        pref_n = float(prefix_raw_count.get(v, 0.0))

        # Use full clade counts for B.1.617.2 (so AY.* sublineages compare against the full Delta wave),
        # and use same-prefix clade counts for other lineages so new alias prefixes (e.g. KP.* under JN.1.11.1)
        # do not contaminate their parent alias contour.
        if v in ("", "B.1.617.2") or pref_n < 1.0:
            counts_for_clade = clade_weekly[v][active_week_mask]
            eff_clade_n = clade_n
        else:
            counts_for_clade = prefix_weekly[v][active_week_mask]
            eff_clade_n = pref_n

        if raw_n >= config.n0_shrinkage or eff_clade_n <= raw_n:
            counts_for_contour = direct_weekly[v][active_week_mask]
            eff_n = raw_n
        else:
            counts_for_contour = counts_for_clade
            eff_n = eff_clade_n

        shares = counts_for_contour / safe_weekly_totals[active_week_mask]
        c_tilde, c_dot, vol = extract_contour_and_velocity(
            shares, epsilon=config.epsilon
        )
        credibility = float(eff_clade_n / (eff_clade_n + config.n0_shrinkage)) if eff_clade_n > 0 else 0.0

        clade_shares = counts_for_clade / safe_weekly_totals[active_week_mask]
        clade_c_tilde, clade_c_dot, _ = extract_contour_and_velocity(
            clade_shares, epsilon=config.epsilon
        )

        G.nodes[v]["importance"] = imp
        G.nodes[v]["clade_importance"] = float(clade_importance.get(v, 0.0))
        G.nodes[v]["raw_count"] = raw_n
        G.nodes[v]["clade_count"] = clade_n
        G.nodes[v]["prefix_clade_count"] = pref_n
        G.nodes[v]["credibility"] = credibility
        G.nodes[v]["volume_share"] = vol
        G.nodes[v]["contour"] = c_tilde.tolist()
        G.nodes[v]["velocity"] = c_dot.tolist()
        G.nodes[v]["clade_contour"] = clade_c_tilde.tolist()
        G.nodes[v]["clade_velocity"] = clade_c_dot.tolist()
        G.nodes[v]["active_clade_counts"] = counts_for_clade.tolist()

    G.graph["vel_weights"] = vel_weights.tolist()
    G.graph["active_weekly_totals"] = safe_weekly_totals[active_week_mask].tolist()
    G.graph["contour_config"] = {
        "lambda_tree": config.lambda_tree,
        "lambda_contour": config.lambda_contour,
        "alpha": config.alpha,
        "beta": config.beta,
        "kappa": config.kappa,
        "n0_shrinkage": config.n0_shrinkage,
        "temporal_decay_prev": config.temporal_decay_prev,
        "temporal_decay_vel": config.temporal_decay_vel,
        "epsilon": config.epsilon,
        "bin_days": config.bin_days,
        "importance_exponent": config.importance_exponent,
        "noise_floor": config.noise_floor,
    }

    # 5. Annotate each parent-child edge (u -> v) with contour divergence
    root_nodes = [n for n in G if len(G.pred[n]) == 0]
    root_node = root_nodes[0] if root_nodes else ""
    c_tilde_root = np.asarray(
        G.nodes[root_node].get("clade_contour", [1.0]) if root_node in G else [1.0],
        dtype=np.float64,
    )
    c_dot_root = np.asarray(
        G.nodes[root_node].get("clade_velocity", []) if root_node in G else [],
        dtype=np.float64,
    )

    parent_map: dict[str, str | None] = {}
    for n in G:
        preds = list(G.pred[n])
        parent_map[n] = preds[0] if preds else None

    for u, v in G.edges:
        raw_v = float(G.nodes[v].get("raw_count", 0.0))
        clade_n_v = float(G.nodes[v].get("clade_count", 0.0))
        pref_clade_n_v = float(G.nodes[v].get("prefix_clade_count", 0.0))
        eff_clade_n_v = clade_n_v if (v in ("", "B.1.617.2") or pref_clade_n_v < 1.0) else pref_clade_n_v
        is_major_pango_root = (v != "") and (v.count(".") <= 1 or v == "B.1.617.2")
        same_pref_ratio = pref_clade_n_v / max(clade_n_v, 1.0)

        # Unobserved internal branch points or prefix-transition bridge nodes (e.g. B.1.1.529 -> BA.1, JN.1.11.1 -> KP.*, LF.7 -> XFG)
        # defer their contour barrier to the named clades below them.
        is_extinct_bridge = (
            len(G.succ[v]) > 0
            and v not in ("", "B.1.617.2")
            and same_pref_ratio < 0.10
            and pref_clade_n_v < 2.0 * config.n0_shrinkage
        )
        is_empty_passthrough = is_extinct_bridge or (
            len(G.succ[v]) > 0
            and not is_major_pango_root
            and (raw_v < 5.0 or same_pref_ratio < 0.25 or pref_clade_n_v <= config.n0_shrinkage)
        )

        if is_empty_passthrough:
            div_total, jsd, w1, d_vel = 0.0, 0.0, 0.0, 0.0
            phi_v = 0.0
        else:
            c_tilde_v = np.asarray(G.nodes[v]["clade_contour"], dtype=np.float64)
            c_dot_v = np.asarray(G.nodes[v]["clade_velocity"], dtype=np.float64)
            v_counts = np.asarray(G.nodes[v]["active_clade_counts"], dtype=np.float64)

            anc: str | None = u
            c_tilde_anc = c_tilde_root
            c_dot_anc = c_dot_root
            while anc is not None:
                anc_clade_n = float(G.nodes[anc].get("clade_count", 0.0))
                pref_anc_n = float(G.nodes[anc].get("prefix_clade_count", 0.0))
                anc_is_major = (anc != "") and (anc.count(".") <= 1 or anc == "B.1.617.2")
                anc_same_pref_ratio = pref_anc_n / max(anc_clade_n, 1.0)
                anc_counts = np.asarray(
                    G.nodes[anc].get("active_clade_counts", [0.0]), dtype=np.float64
                )
                if anc in ("", "B.1.617.2") or pref_anc_n < 1.0 or v.split(".")[0] == anc.split(".")[0]:
                    rem_counts = np.maximum(0.0, anc_counts - v_counts)
                else:
                    rem_counts = anc_counts

                if ((anc_is_major and pref_anc_n >= config.n0_shrinkage and anc_same_pref_ratio >= 0.10) or (anc_same_pref_ratio >= 0.25 and pref_anc_n >= 2.0 * config.n0_shrinkage)) and np.sum(rem_counts) >= config.n0_shrinkage:
                    rem_shares = rem_counts / safe_weekly_totals[active_week_mask]
                    c_tilde_anc, c_dot_anc, _ = extract_contour_and_velocity(
                        rem_shares, epsilon=config.epsilon
                    )
                    break
                anc = parent_map.get(anc)

            div_total, jsd, w1, d_vel = compute_divergence_components(
                c_tilde_v,
                c_dot_v,
                c_tilde_anc,
                c_dot_anc,
                vel_weights,
                config,
                n_eff=eff_clade_n_v,
            )
            phi_v = (
                float(eff_clade_n_v / (eff_clade_n_v + config.n0_shrinkage))
                if eff_clade_n_v > 0
                else 0.0
            )
            # Ensure a newly designated prefix clade with large support (>2*n0_shrinkage) whose parent is an extinct bridge
            # has at least a baseline clade-separation barrier so it is not folded into its extinct ancestor
            u_pref_n = float(G.nodes[u].get("prefix_clade_count", 0.0))
            u_clade_n = float(G.nodes[u].get("clade_count", 0.0))
            if u not in ("", "B.1.617.2") and v.split(".")[0] != u.split(".")[0] and (u_pref_n / max(u_clade_n, 1.0)) < 0.10 and eff_clade_n_v >= 2.0 * config.n0_shrinkage:
                div_total = max(div_total, 0.15)

        edge_cost = config.lambda_tree + phi_v * config.lambda_contour * div_total

        G.edges[u, v]["distance"] = float(edge_cost)
        G.edges[u, v]["pango_distance"] = 1.0
        G.edges[u, v]["contour_divergence"] = float(div_total)
        G.edges[u, v]["jsd"] = float(jsd)
        G.edges[u, v]["w1"] = float(w1)
        G.edges[u, v]["d_vel"] = float(d_vel)


def compute_contour_assignment_penalty(
    G: nx.DiGraph,
    config: ContourConfig | None = None,
) -> dict[str, dict[str, float]]:
    """Compute pairwise consolidation penalty C_contour(v -> a) for every node v and ancestor a.

    Uses the tree-path sum of contour-regularized edge distances d(u, v) = lambda_tree + lambda_contour * Phi(N_v) * D_contour(v),
    scaled by node importance I_v^gamma, while preventing unobserved umbrella roots from absorbing major clades.
    """
    if config is None:
        cfg_dict = G.graph.get("contour_config", {})
        config = ContourConfig(**cfg_dict) if cfg_dict else ContourConfig()

    root_nodes = [n for n in G if len(G.pred[n]) == 0]
    root_node = root_nodes[0] if root_nodes else ""
    tot_seq = float(G.nodes[root_node].get("clade_count", 1.0)) if root_node in G else 1.0

    # Unobserved basal/ancestral umbrella trunks (spanning >25% of all sequences with <2% direct sequences,
    # e.g., B, B.1, B.1.1, B.1.1.529, or extinct XBB/XBB.1 trunk in mid-2023)
    # or extinct prefix-transition bridge nodes (<5% of subtree has the node's own prefix)
    umbrella_nodes = {
        n
        for n in G
        if n not in ("", "B.1.617.2")
        and (
            (
                float(G.nodes[n].get("clade_count", 0.0)) > 0.25 * tot_seq
                and float(G.nodes[n].get("raw_count", 0.0))
                < 0.02 * float(G.nodes[n].get("clade_count", 1.0))
            )
            or (
                float(G.nodes[n].get("clade_count", 0.0)) >= 2.0 * config.n0_shrinkage
                and (
                    float(G.nodes[n].get("prefix_clade_count", 0.0))
                    / max(float(G.nodes[n].get("clade_count", 1.0)), 1.0)
                )
                < 0.05
            )
        )
    }
    umbrella_nodes.add(root_node)

    path_distances = dict(nx.all_pairs_dijkstra_path_length(G, weight="distance"))
    hop_lengths = dict(nx.all_pairs_shortest_path_length(G))

    assignment_penalty: dict[str, dict[str, float]] = {v: {} for v in G}

    for a, v_dists in path_distances.items():
        raw_a = float(G.nodes[a].get("raw_count", 0.0))
        a_is_major = (a != "") and (a.count(".") <= 1)
        unobserved_anc_mult = 1.0 if (raw_a >= 3.0 or a_is_major) else 1.5

        for v, dist in v_dists.items():
            if v == a:
                assignment_penalty[v][a] = 0.0
                continue

            imp = float(G.nodes[v].get("importance", 0.0))
            if imp <= 0.0:
                assignment_penalty[v][a] = 0.0
                continue

            hops = hop_lengths[a][v]
            contour_part = max(0.0, float(dist) - float(hops) * config.lambda_tree)
            imp_scale = imp ** config.importance_exponent
            if a in umbrella_nodes:
                assignment_penalty[v][a] = float(
                    imp_scale * contour_part + imp * 2.0 * float(hops)
                )
            else:
                assignment_penalty[v][a] = float(
                    unobserved_anc_mult
                    * (
                        imp_scale * contour_part
                        + float(hops) * config.lambda_tree * (imp ** 0.30)
                    )
                )

    return assignment_penalty

