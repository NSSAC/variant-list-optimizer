"""Compute the optimal variant list."""

import math
import json
import heapq
from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path

import networkx as nx
import numpy as np
from more_itertools import first
import click
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import csc_matrix

from .contour import compute_contour_assignment_penalty

TIME_UNIT = 30 * 24 * 3600  # 1 Month
CHUNKSIZE = 50


@click.group()
def optimize():
    """Compute the optimal variant list."""


@dataclass
class NIA:
    """Nearest included ancestor."""

    nia: str
    distance: float
    penalty: float


def make_nia(
    selected: set[str] | frozenset[str],
    G: nx.DiGraph,
    distance_penalty: float,
    assignment_penalty: dict[str, dict[str, float]] | None = None,
) -> dict[str, NIA]:
    """Map each node in the graph to its nearest included ancestor."""
    v_nia: dict[str, NIA] = {}

    use_contour = (
        assignment_penalty is None and G.graph.get("weighting_mode") == "contour"
    )
    if use_contour:
        assignment_penalty = compute_contour_assignment_penalty(G)

    for v in nx.topological_sort(G):
        if v in selected:
            v_nia[v] = NIA(v, 0.0, 0.0)
        else:
            u = first(G.pred[v])
            ancestor_nia = v_nia[u].nia
            distance = v_nia[u].distance + G.edges[u, v]["distance"]
            if assignment_penalty is not None:
                penalty = assignment_penalty[v].get(ancestor_nia, 0.0)
            else:
                penalty = (
                    distance_penalty * math.exp(distance) * G.nodes[v]["importance"]
                )
            v_nia[v] = NIA(ancestor_nia, distance, penalty)

    return v_nia


def compute_penalty(v_nia: dict[str, NIA]) -> float:
    """Compute total assignment penalty."""
    return sum(nia.penalty for nia in v_nia.values())


def do_evaluate_candidate(
    candidate: str,
    selected: set[str],
    G: nx.DiGraph,
    distance_penalty: float,
    assignment_penalty: dict[str, dict[str, float]] | None = None,
) -> tuple[str, float]:
    selected_set = selected | {candidate}
    v_nia = make_nia(selected_set, G, distance_penalty, assignment_penalty)
    penalty = sum(nia.penalty for nia in v_nia.values())
    return (candidate, penalty)


def do_optimize_greedy(
    size: int,
    G: nx.DiGraph,
    distance_penalty: float,
    penalty_mode: str = "auto",
    use_ray: bool = False,
) -> set[str]:
    assert size <= G.number_of_nodes()

    assignment_penalty = compute_assignment_penalty(
        G, distance_penalty, penalty_mode=penalty_mode
    )

    root_node = first([v for v in G if len(G.pred[v]) == 0])
    selected = {root_node}
    candidates = set(G) - selected

    cur_size = len(selected)
    penalty = compute_penalty(make_nia(selected, G, distance_penalty, assignment_penalty))
    print(f"size = {cur_size}, penalty = {penalty}")

    while cur_size < size:
        if use_ray:
            from .ray_utils import ray_map

            args_list = [(c,) for c in candidates]
            extra_args = (selected, G, distance_penalty, assignment_penalty)
            v_penalty = dict(
                ray_map(
                    do_evaluate_candidate, args_list, extra_args, chunksize=CHUNKSIZE
                )
            )
        else:
            v_penalty = dict(
                do_evaluate_candidate(
                    c, selected, G, distance_penalty, assignment_penalty
                )
                for c in candidates
            )

        v = min(v_penalty, key=v_penalty.get)  # type: ignore

        selected.add(v)
        candidates.remove(v)

        cur_size = len(selected)
        penalty = min(v_penalty.values())
        print(f"size = {cur_size}, penalty = {penalty}")

    return selected


@optimize.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to weighted graph file (JSON).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Output file (JSON).",
)
@click.option(
    "-s",
    "--size",
    type=int,
    required=True,
    help="Size of the variant list.",
)
@click.option(
    "-p",
    "--distance-penalty",
    type=float,
    default=10.0,
    show_default=True,
    help="Penalty for re-assigning observation to a variant 1.0 distance away.",
)
@click.option(
    "--penalty-mode",
    type=click.Choice(["auto", "static", "contour"]),
    default="auto",
    show_default=True,
    help="Penalty calculation mode.",
)
def optimize_greedy(
    input_file: Path,
    output_file: Path,
    size: int,
    distance_penalty: float,
    penalty_mode: str = "auto",
):
    """Use greedy optimization to create the variant list."""
    G = json.loads(input_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    assignment_penalty = compute_assignment_penalty(
        G, distance_penalty, penalty_mode=penalty_mode
    )
    selected = do_optimize_greedy(
        size, G, distance_penalty, penalty_mode=penalty_mode, use_ray=False
    )

    v_nia = make_nia(selected, G, distance_penalty, assignment_penalty)

    assignment = []
    for k, v in v_nia.items():
        assignment.append(
            dict(
                orig_label=k,
                new_label=v.nia,
                distance=v.distance,
                penalty=v.penalty,
            )
        )

    penalty = compute_penalty(v_nia)

    output = dict(
        variant_list=list(selected), assignment=assignment, penalty=penalty
    )
    output_file.write_text(json.dumps(output))


def do_evaluate_candidate_selected(
    candidate: str,
    selected: frozenset[str],
    G: nx.DiGraph,
    distance_penalty: float,
    assignment_penalty: dict[str, dict[str, float]] | None = None,
) -> tuple[frozenset[str], float]:
    selected_set = selected | {candidate}
    v_nia = make_nia(selected_set, G, distance_penalty, assignment_penalty)
    penalty = sum(nia.penalty for nia in v_nia.values())
    return (selected_set, penalty)


def do_optimize_beam_search(
    size: int,
    beam_width: int,
    G: nx.DiGraph,
    distance_penalty: float,
    penalty_mode: str = "auto",
    use_ray: bool = False,
) -> frozenset[str]:
    assert size <= G.number_of_nodes()

    assignment_penalty = compute_assignment_penalty(
        G, distance_penalty, penalty_mode=penalty_mode
    )

    root_node = first([v for v in G if len(G.pred[v]) == 0])
    selected_sets = [frozenset([root_node])]
    all_nodes = set(G)

    cur_size = len(selected_sets[0])
    penalty = compute_penalty(
        make_nia(selected_sets[0], G, distance_penalty, assignment_penalty)
    )
    print(f"size = {cur_size}, penalty = {penalty}")

    while cur_size < size:
        args_list = []
        for selected in selected_sets:
            for candidate in all_nodes - selected:
                args_list.append((candidate, selected))

        if use_ray:
            from .ray_utils import ray_map

            extra_args = (G, distance_penalty, assignment_penalty)
            candidate_penalty = dict(
                ray_map(
                    do_evaluate_candidate_selected,
                    args_list,
                    extra_args,
                    chunksize=CHUNKSIZE,
                )
            )
        else:
            candidate_penalty = dict(
                do_evaluate_candidate_selected(
                    cand, sel, G, distance_penalty, assignment_penalty
                )
                for cand, sel in args_list
            )

        selected_sets = heapq.nsmallest(
            beam_width, candidate_penalty, key=candidate_penalty.get  # type: ignore
        )

        cur_size = len(selected_sets[0])
        penalty = min(candidate_penalty.values())
        print(f"size = {cur_size}, penalty = {penalty}")

    selected_set_penalty = {
        k: compute_penalty(make_nia(k, G, distance_penalty, assignment_penalty))
        for k in selected_sets
    }
    selected_set = min(selected_set_penalty, key=selected_set_penalty.get)  # type: ignore
    return selected_set


@optimize.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to weighted graph file (JSON).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Output file (JSON).",
)
@click.option(
    "-s",
    "--size",
    type=int,
    required=True,
    help="Size of the variant list.",
)
@click.option(
    "-p",
    "--distance-penalty",
    type=float,
    default=10.0,
    show_default=True,
    help="Penalty for re-assigning observation to a variant 1.0 distance away.",
)
@click.option(
    "-bw",
    "--beam-width",
    type=int,
    required=True,
    help="Beam search width.",
)
def optimize_beam_search(
    input_file: Path,
    output_file: Path,
    size: int,
    distance_penalty: float,
    beam_width: int,
):
    """Use beam search to create the variant list."""
    G = json.loads(input_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    assignment_penalty = compute_assignment_penalty(G, distance_penalty)
    selected = do_optimize_beam_search(size, beam_width, G, distance_penalty)

    v_nia = make_nia(selected, G, distance_penalty, assignment_penalty)

    assignment = []
    for k, v in v_nia.items():
        assignment.append(
            dict(
                orig_label=k,
                new_label=v.nia,
                distance=v.distance,
                penalty=v.penalty,
            )
        )

    penalty = compute_penalty(v_nia)

    output = dict(
        variant_list=list(selected), assignment=assignment, penalty=penalty
    )
    output_file.write_text(json.dumps(output))


def compute_assignment_penalty(
    G: nx.DiGraph,
    distance_penalty: float = 10.0,
    penalty_mode: str = "auto",
) -> dict[str, dict[str, float]]:
    """Compute the assignment penalty matrix assignment_penalty[v][a]."""
    effective_mode = penalty_mode
    if effective_mode == "auto":
        effective_mode = (
            "contour"
            if G.graph.get("weighting_mode") == "contour"
            else "static"
        )

    if effective_mode == "contour":
        return compute_contour_assignment_penalty(G)

    assignment_penalty: dict[str, dict[str, float]] = defaultdict(dict)

    iterable = nx.all_pairs_bellman_ford_path_length(G, weight="distance")
    for a, v_distance in iterable:
        for v, distance in v_distance.items():
            if v == a:
                penalty = 0.0
            else:
                penalty = (
                    distance_penalty
                    * math.exp(distance)
                    * G.nodes[v]["importance"]
                )
            assignment_penalty[v][a] = penalty

    assignment_penalty.default_factory = None

    return assignment_penalty


def do_optimize_highs(
    size: int,
    G: nx.DiGraph,
    distance_penalty: float = 10.0,
    penalty_mode: str = "auto",
    timelimit: float = 300.0,
    slot_cost: float = 0.0,
    max_size: bool = False,
) -> set[str]:
    """Solve the optimal variant list ILP using SciPy's built-in HiGHS MILP solver.

    When `slot_cost > 0` (or `max_size=True`), the cardinality constraint becomes an upper
    bound (`sum_a x_a <= size`) and opening each non-root slot `a` incurs a scale-invariant
    regularization cost `slot_cost * W_total * distance_penalty`, so a slot is only opened
    when its marginal Behavior x Support reduction in consolidation penalty exceeds the threshold.
    """
    nodes = list(G.nodes())
    n = len(nodes)
    use_upper_bound = max_size or (slot_cost > 0.0)
    if size >= n and not use_upper_bound:
        return set(nodes)

    node_to_idx = {u: i for i, u in enumerate(nodes)}
    assignment_penalty = compute_assignment_penalty(
        G, distance_penalty, penalty_mode=penalty_mode
    )

    # Compute scale factor across the graph for dimensionless Behavior x Support slot opening cost
    total_importance = sum(
        float(G.nodes[u].get("importance", G.nodes[u].get("weight", 0.0)))
        for u in nodes
    )
    if total_importance <= 0.0:
        total_importance = 1.0

    use_contour = penalty_mode == "contour" or (
        penalty_mode == "auto" and G.graph.get("weighting_mode") == "contour"
    )
    if use_contour:
        cfg_dict = G.graph.get("contour_config", {})
        gamma = float(cfg_dict.get("importance_exponent", 0.65))
        lam_c = float(cfg_dict.get("lambda_contour", 25.0))
        objective_scale = (total_importance ** gamma) * lam_c * float(distance_penalty)
    else:
        objective_scale = total_importance * float(distance_penalty)

    root_node = first([v for v in G if len(G.pred[v]) == 0])
    root_idx = node_to_idx[root_node]

    # Variables:
    #   0 .. n-1: binary selection indicators x_a in {0, 1}
    #   n .. n+m-1: assignment variables y_{v, a} in [0, 1]
    pair_list: list[tuple[str, str, float]] = []
    v_to_pair_indices: dict[str, list[int]] = defaultdict(list)

    for v in nodes:
        for a, pen in assignment_penalty[v].items():
            p_idx = len(pair_list)
            pair_list.append((v, a, float(pen)))
            v_to_pair_indices[v].append(p_idx)

    m_pairs = len(pair_list)
    total_vars = n + m_pairs

    c = np.zeros(total_vars, dtype=np.float64)
    if slot_cost > 0.0:
        scaled_slot_cost = float(slot_cost) * objective_scale
        for i in range(n):
            if i != root_idx:
                c[i] = scaled_slot_cost

    for p_idx, (_, _, pen) in enumerate(pair_list):
        c[n + p_idx] = pen

    # Integrality: x_a are binary (1), y_{v,a} can be continuous (0) or binary (1)
    integrality = np.zeros(total_vars, dtype=np.int8)
    integrality[:n] = 1

    # Constraints:
    # 1) sum_a x_a == size (or <= size when use_upper_bound is True) (row 0)
    # 2) x_root == 1 (row 1)
    # 3) For each v in nodes: sum_{a in Anc(v)} y_{v,a} == 1 (rows 2 .. 2+n-1)
    # 4) For each pair (v, a): y_{v,a} - x_a <= 0 (rows 2+n .. 2+n+m_pairs-1)
    num_rows = 2 + n + m_pairs
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    lb = np.zeros(num_rows, dtype=np.float64)
    ub = np.zeros(num_rows, dtype=np.float64)

    # Row 0: sum_a x_a == size (or 1 <= sum_a x_a <= size)
    for i in range(n):
        rows.append(0)
        cols.append(i)
        vals.append(1.0)
    lb[0] = 1.0 if use_upper_bound else float(min(size, n))
    ub[0] = float(min(size, n))

    # Row 1: x_root == 1
    rows.append(1)
    cols.append(root_idx)
    vals.append(1.0)
    lb[1] = 1.0
    ub[1] = 1.0

    # Rows 2 .. 2+n-1: sum_a y_{v,a} == 1
    for v_i, v in enumerate(nodes):
        row_idx = 2 + v_i
        for p_idx in v_to_pair_indices[v]:
            rows.append(row_idx)
            cols.append(n + p_idx)
            vals.append(1.0)
        lb[row_idx] = 1.0
        ub[row_idx] = 1.0

    # Rows 2+n .. 2+n+m_pairs-1: y_{v,a} - x_a <= 0
    for p_idx, (_, a, _) in enumerate(pair_list):
        row_idx = 2 + n + p_idx
        a_idx = node_to_idx[a]
        rows.append(row_idx)
        cols.append(n + p_idx)
        vals.append(1.0)
        rows.append(row_idx)
        cols.append(a_idx)
        vals.append(-1.0)
        lb[row_idx] = -np.inf
        ub[row_idx] = 0.0

    A = csc_matrix((vals, (rows, cols)), shape=(num_rows, total_vars))
    constraints = LinearConstraint(A, lb, ub)
    bounds = Bounds(0.0, 1.0)

    res = milp(
        c=c,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": timelimit},
    )

    if not res.success or res.x is None:
        raise RuntimeError(f"HiGHS MILP optimization failed: {res.message}")

    selected: set[str] = set()
    x_sol = res.x[:n]
    if use_upper_bound:
        active_indices = [i for i in range(n) if x_sol[i] >= 0.5]
        if len(active_indices) > size:
            active_indices = list(np.argsort(-x_sol)[:size])
        for i in active_indices:
            selected.add(nodes[int(i)])
    else:
        chosen_indices = np.argsort(-x_sol)[:size]
        for i in chosen_indices:
            selected.add(nodes[int(i)])
    selected.add(root_node)

    return selected


@optimize.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to weighted graph file (JSON).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Output file (JSON).",
)
@click.option(
    "-s",
    "--size",
    type=int,
    required=True,
    help="Size (or maximum size when --max-size / --slot-cost is used) of the variant list.",
)
@click.option(
    "-p",
    "--distance-penalty",
    type=float,
    default=10.0,
    show_default=True,
    help="Penalty for re-assigning observation to a variant 1.0 distance away.",
)
@click.option(
    "--penalty-mode",
    type=click.Choice(["auto", "static", "contour"]),
    default="auto",
    show_default=True,
    help="Penalty calculation mode.",
)
@click.option(
    "--slot-cost",
    type=float,
    default=0.0,
    show_default=True,
    help="Dimensionless Behavior x Support threshold (per-slot opening cost) for dynamic list sizing.",
)
@click.option(
    "--max-size/--exact-size",
    default=False,
    show_default=True,
    help="Treat --size as an upper bound (<= size) rather than an exact equality.",
)
@click.option(
    "-t",
    "--timelimit",
    type=float,
    default=300.0,
    show_default=True,
    help="Time limit for HiGHS solver in seconds.",
)
def optimize_highs(
    input_file: Path,
    output_file: Path,
    size: int,
    distance_penalty: float,
    penalty_mode: str = "auto",
    slot_cost: float = 0.0,
    max_size: bool = False,
    timelimit: float = 300.0,
):
    """Use SciPy HiGHS MILP optimizer to create the variant list."""
    G = json.loads(input_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    assignment_penalty = compute_assignment_penalty(
        G, distance_penalty, penalty_mode=penalty_mode
    )
    selected = do_optimize_highs(
        size,
        G,
        distance_penalty=distance_penalty,
        penalty_mode=penalty_mode,
        timelimit=timelimit,
        slot_cost=slot_cost,
        max_size=max_size,
    )

    v_nia = make_nia(selected, G, distance_penalty, assignment_penalty)

    assignment = []
    for k, v in v_nia.items():
        assignment.append(
            dict(
                orig_label=k,
                new_label=v.nia,
                distance=v.distance,
                penalty=v.penalty,
            )
        )

    penalty = compute_penalty(v_nia)
    print(f"penalty: {penalty}")

    output = dict(variant_list=list(selected), assignment=assignment, penalty=penalty)
    output_file.write_text(json.dumps(output))


def do_optimize_gurobi(
    size: int,
    G: nx.DiGraph,
    distance_penalty: float,
    penalty_mode: str = "auto",
) -> set[str]:
    import gurobipy as gp
    from gurobipy import GRB

    assignment_penalty = compute_assignment_penalty(
        G, distance_penalty, penalty_mode=penalty_mode
    )

    m = gp.Model("vlrs-ilp")

    xs = list(G)
    xs = m.addVars(xs, vtype=GRB.BINARY)

    ys = [f"{v}:{a}" for v in G for a in assignment_penalty[v]]
    ys = m.addVars(ys, vtype=GRB.BINARY)

    m.addConstr(xs.sum() == size)

    root_node = first([v for v in G if len(G.pred[v]) == 0])
    m.addConstr(xs[root_node] == 1)

    for v in G:
        vars = []
        for a in assignment_penalty[v]:
            vars.append(ys[f"{v}:{a}"])
        m.addConstr(gp.quicksum(vars) == 1)

    for v in G:
        for a in assignment_penalty[v]:
            m.addConstr(ys[f"{v}:{a}"] <= xs[a])

    consts, vars = [], []
    for v in G:
        for a in assignment_penalty[v]:
            consts.append(assignment_penalty[v][a])
            vars.append(ys[f"{v}:{a}"])

    m.setObjective(gp.LinExpr(consts, vars), GRB.MINIMIZE)

    m.optimize()

    print(f"Objective value: {m.ObjVal}")

    selected = set()
    for k, v in xs.items():
        if v.X:
            selected.add(k)

    return selected


@optimize.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to weighted graph file (JSON).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Output file (JSON).",
)
@click.option(
    "-s",
    "--size",
    type=int,
    required=True,
    help="Size of the variant list.",
)
@click.option(
    "-p",
    "--distance-penalty",
    type=float,
    default=10.0,
    show_default=True,
    help="Penalty for re-assigning observation to a variant 1.0 distance away.",
)
@click.option(
    "--penalty-mode",
    type=click.Choice(["auto", "static", "contour"]),
    default="auto",
    show_default=True,
    help="Penalty calculation mode.",
)
def optimize_gurobi(
    input_file: Path,
    output_file: Path,
    size: int,
    distance_penalty: float,
    penalty_mode: str = "auto",
):
    """Use Gurobi optimizer to create the variant list."""
    G = json.loads(input_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    assignment_penalty = compute_assignment_penalty(
        G, distance_penalty, penalty_mode=penalty_mode
    )
    selected = do_optimize_gurobi(
        size, G, distance_penalty, penalty_mode=penalty_mode
    )

    v_nia = make_nia(selected, G, distance_penalty, assignment_penalty)

    assignment = []
    for k, v in v_nia.items():
        assignment.append(
            dict(
                orig_label=k,
                new_label=v.nia,
                distance=v.distance,
                penalty=v.penalty,
            )
        )

    penalty = compute_penalty(v_nia)
    print(f"penalty: {penalty}")

    output = dict(variant_list=list(selected), assignment=assignment, penalty=penalty)
    output_file.write_text(json.dumps(output))


@optimize.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to weighted graph file (JSON).",
)
@click.option(
    "-l",
    "--index-list",
    "index_list_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to optimized index list file (JSON).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Output file (JSON).",
)
@click.option(
    "-p",
    "--distance-penalty",
    type=float,
    default=10.0,
    show_default=True,
    help="Penalty for re-assigning observation to a variant 1.0 distance away.",
)
def optimize_external(
    input_file: Path,
    index_list_file: Path,
    output_file: Path,
    distance_penalty: float = 10.0,
):
    """Use external optimizer index list to create the variant list."""
    G = json.loads(input_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    selected_index = json.loads(index_list_file.read_text())["selected"]
    nodes = sorted(G.nodes())
    selected = set()
    for index in selected_index:
        selected.add(nodes[index])

    v_nia = make_nia(selected, G, distance_penalty)

    assignment = []
    for k, v in v_nia.items():
        assignment.append(
            dict(
                orig_label=k,
                new_label=v.nia,
                distance=v.distance,
                penalty=v.penalty,
            )
        )

    penalty = compute_penalty(v_nia)
    print(f"penalty: {penalty}")

    output = dict(variant_list=list(selected), assignment=assignment, penalty=penalty)
    output_file.write_text(json.dumps(output))

