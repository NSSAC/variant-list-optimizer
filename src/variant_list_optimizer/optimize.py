"""Compute the optimal variant list."""

import math
import json
import heapq
from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path

import ray
import networkx as nx
from more_itertools import first
import click

import gurobipy as gp
from gurobipy import GRB

from .ray_utils import ray_map

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
    selected: set[str] | frozenset[str], G: nx.DiGraph, distance_penalty: float
) -> dict[str, NIA]:
    """Map each node in the graph to its nearest included ancestor."""
    v_nia: dict[str, NIA] = {}

    for v in nx.topological_sort(G):
        if v in selected:
            v_nia[v] = NIA(v, 0.0, 0.0)
        else:
            u = first(G.pred[v])
            distance = v_nia[u].distance + G.edges[u, v]["distance"]
            penalty = distance_penalty * math.exp(distance) * G.nodes[v]["importance"]
            v_nia[v] = NIA(u, distance, penalty)

    return v_nia


def compute_penalty(v_nia: dict[str, NIA]) -> float:
    """Compute total assignment penalty."""
    return sum(nia.penalty for nia in v_nia.values())


def do_evaluate_candidate(
    candidate: str,
    selected: set[str],
    G: nx.DiGraph,
    distance_penalty: float,
) -> tuple[str, float]:
    selected_set = selected | {candidate}
    v_nia = make_nia(selected_set, G, distance_penalty)
    penalty = sum(nia.penalty for nia in v_nia.values())
    return (candidate, penalty)


def do_optimize_greedy(size: int, G: nx.DiGraph, distance_penalty: float) -> set[str]:
    assert size <= G.number_of_nodes()

    root_node = first([v for v in G if len(G.pred[v]) == 0])
    selected = {root_node}
    candidates = set(G) - selected

    cur_size = len(selected)
    penalty = compute_penalty(make_nia(selected, G, distance_penalty))
    print(f"size = {cur_size}, penalty = {penalty}")

    while cur_size < size:
        args_list = [(c,) for c in candidates]
        extra_args = (selected, G, distance_penalty)

        v_penalty = ray_map(
            do_evaluate_candidate, args_list, extra_args, chunksize=CHUNKSIZE
        )
        v_penalty = dict(v_penalty)
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
def optimize_greedy(
    input_file: Path, output_file: Path, size: int, distance_penalty: float
):
    """Use greedy optimization to create the variant list."""
    G = json.loads(input_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    ray.init(include_dashboard=False)
    try:
        selected = do_optimize_greedy(size, G, distance_penalty)

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

        output = dict(
            variant_list=list(selected), assignment=assignment, penalty=penalty
        )
        output_file.write_text(json.dumps(output))
    finally:
        ray.shutdown()


def do_evaluate_candidate_selected(
    candidate: str,
    selected: frozenset[str],
    G: nx.DiGraph,
    distance_penalty: float,
) -> tuple[frozenset[str], float]:
    selected_set = selected | {candidate}
    v_nia = make_nia(selected_set, G, distance_penalty)
    penalty = sum(nia.penalty for nia in v_nia.values())
    return (selected_set, penalty)


def do_optimize_beam_search(
    size: int, beam_width: int, G: nx.DiGraph, distance_penalty: float
) -> frozenset[str]:
    assert size <= G.number_of_nodes()

    root_node = first([v for v in G if len(G.pred[v]) == 0])
    selected_sets = [frozenset([root_node])]
    all_nodes = set(G)

    cur_size = len(selected_sets[0])
    penalty = compute_penalty(make_nia(selected_sets[0], G, distance_penalty))
    print(f"size = {cur_size}, penalty = {penalty}")

    while cur_size < size:
        args_list = []
        for selected in selected_sets:
            for candidate in all_nodes - selected:
                args_list.append((candidate, selected))

        extra_args = (G, distance_penalty)

        candidate_penalty = ray_map(
            do_evaluate_candidate_selected, args_list, extra_args, chunksize=CHUNKSIZE
        )
        candidate_penalty = dict(candidate_penalty)
        selected_sets = heapq.nsmallest(beam_width, candidate_penalty, key=candidate_penalty.get)  # type: ignore

        cur_size = len(selected_sets[0])
        penalty = min(candidate_penalty.values())
        print(f"size = {cur_size}, penalty = {penalty}")

    selected_set_penalty = {
        k: compute_penalty(make_nia(k, G, distance_penalty)) for k in selected_sets
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

    ray.init(include_dashboard=False)
    try:
        selected = do_optimize_beam_search(size, beam_width, G, distance_penalty)

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

        output = dict(
            variant_list=list(selected), assignment=assignment, penalty=penalty
        )
        output_file.write_text(json.dumps(output))
    finally:
        ray.shutdown()


def compute_assignment_penalty(
    G: nx.DiGraph, distance_penalty: float
) -> dict[str, dict[str, float]]:
    assignment_penalty = defaultdict(dict)

    iterable = nx.all_pairs_bellman_ford_path_length(G, weight="weight")
    for a, v_distance in iterable:
        for v, distance in v_distance.items():
            penalty = distance_penalty * math.exp(distance) * G.nodes[v]["importance"]
            assignment_penalty[v][a] = penalty

    assignment_penalty.default_factory = None

    return assignment_penalty


def do_optimize_gurobi(size: int, G: nx.DiGraph, distance_penalty: float) -> set[str]:
    assignment_penalty = compute_assignment_penalty(G, distance_penalty)

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
def optimize_gurobi(
    input_file: Path, output_file: Path, size: int, distance_penalty: float
):
    """Use Gurobi optimizer to create the variant list."""
    G = json.loads(input_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    selected = do_optimize_gurobi(size, G, distance_penalty)

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
def optimize_external(input_file: Path, index_list_file: Path, output_file: Path):
    """Use external optimizer index list to create the variant list."""
    G = json.loads(input_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    selected_index = json.loads(index_list_file.read_text())["selected"]
    nodes = sorted(G.nodes())
    selected = set()
    for index in selected_index:
        selected.add(nodes[index])

    v_nia = make_nia(selected, G, 1.0)

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
