"""Variant List Optimizer."""

import math
import heapq
from typing import cast
from dataclasses import dataclass

import ray
import numpy as np
import pandas as pd
import networkx as nx
from more_itertools import divide

import pandera as pa
import pandera.typing as pat
import pandera.dtypes as pad

from pango_aliasor.aliasor import Aliasor

TIME_UNIT = 30 * 24 * 3600  # 1 Month

__all__ = [
    "get_time_importance",
    "get_distance_badness",
    "make_variant_tree",
    "prune_tree",
    "make_nearest_included_ancestor",
    "optimize_greedy",
    "optimize_beam_search",
]


# importance = time_importance * exp(-time)
def get_time_importance(ref_time: float = 1.0, ref_importance: float = 0.1) -> float:
    return ref_importance / math.exp(-ref_time)


# badness = distance_badness * exp(distance)
def get_distance_badness(ref_distance: float = 1.0, ref_badness: float = 10.0) -> float:
    return ref_badness / math.exp(ref_distance)


class VariantDF(pa.DataFrameModel):
    date: pat.Series[pad.Timestamp] = pa.Field(nullable=False, coerce=True)
    variant: pat.Series[str] = pa.Field(nullable=False, coerce=True)
    weight: pat.Series[float] = pa.Field(nullable=False, coerce=True)


def make_variant_tree(
    data: pd.DataFrame, ref_time: pd.Timestamp, time_importance: float
) -> nx.DiGraph:
    data = cast(pd.DataFrame, VariantDF.validate(data))
    data = cast(pd.DataFrame, data[["date", "variant", "weight"]].copy())
    data["time"] = np.abs((ref_time - data.date).dt.total_seconds()) / TIME_UNIT

    seen_variants = data.variant.unique().tolist()

    namer = Aliasor()
    namer.enable_expansion()

    tree = nx.DiGraph()

    for child in seen_variants:
        while child != "SARS-CoV-2":
            parent = namer.parent(child)
            if parent == "":
                parent = "SARS-CoV-2"
            tree.add_edge(parent, child)

            child = parent
            parent = namer.parent(child)

    for variant in tree:
        if variant in seen_variants:
            df = data[data.variant == variant]

            time = df.time.to_numpy()
            weight = df.weight.to_numpy()
            importance = time_importance * np.exp(-time) * weight

            tree.nodes[variant]["weight"] = np.sum(weight)
            tree.nodes[variant]["importance"] = np.sum(importance)
        else:
            tree.nodes[variant]["weight"] = 0.0
            tree.nodes[variant]["importance"] = 0.0

    for u, v in tree.edges:
        tree.edges[u, v]["distance"] = 1.0

    assert nx.is_tree(tree)

    return tree


def prune_tree(tree: nx.DiGraph, threshold: float = 1.0) -> nx.DiGraph:
    tree = nx.DiGraph(tree)

    vertices = list(nx.topological_sort(tree))
    vertices = list(reversed(vertices))

    for u in vertices:
        if tree.nodes[u]["weight"] < threshold:
            # Remove leaf nodes with "zero" weight
            if len(tree.succ[u]) == 0:
                tree.remove_node(u)

            # Remove nodes with "zero" weight and single child
            # Connect child to parent
            elif len(tree.succ[u]) == 1 and len(tree.pred[u]) == 1:
                p = list(tree.pred[u])[0]
                c = list(tree.succ[u])[0]

                distance = tree.edges[p, u]["distance"] + tree.edges[u, c]["distance"]

                tree.remove_node(u)
                tree.add_edge(p, c)
                tree.edges[p, c]["distance"] = distance

    assert nx.is_tree(tree)

    return tree


@dataclass
class NIA:
    nia: str
    distance: float
    badness: float


def make_nearest_included_ancestor(
    selected: set[str] | frozenset[str], tree: nx.DiGraph, distance_badness: float
) -> dict[str, NIA]:
    v_nia: dict[str, NIA] = {}

    for v in nx.topological_sort(tree):
        if v in selected:
            v_nia[v] = NIA(v, 0.0, 0.0)
        else:
            u = list(tree.pred[v])[0]
            distance = v_nia[u].distance + tree.edges[u, v]["distance"]
            badness = (
                distance_badness * math.exp(distance) * tree.nodes[v]["importance"]
            )
            v_nia[v] = NIA(u, distance, badness)

    return v_nia


@ray.remote
def eval_new_vertex(
    selected: set[str],
    candidates: list[str],
    tree: nx.DiGraph,
    distance_badness: float,
) -> dict[str, float]:
    v_badness = {}
    for v in candidates:
        selected_set = selected.union(v)
        v_nia = make_nearest_included_ancestor(selected_set, tree, distance_badness)
        badness = sum(nia.badness for nia in v_nia.values())
        v_badness[v] = badness
    return v_badness


def optimize_greedy(
    size: int, tree: nx.DiGraph, distance_badness: float, par: int = 1
) -> set[str]:
    # start with root node
    selected = set([v for v in tree if len(tree.pred[v]) == 0])

    candidates = set(tree)
    for v in selected:
        candidates.remove(v)

    while len(selected) < size and len(candidates) > 0:
        groups = divide(par, candidates)
        tasks = [
            eval_new_vertex.remote(selected, list(g), tree, distance_badness)
            for g in groups
        ]
        results = ray.get(tasks)
        v_badness = {k: v for d in results for k, v in d.items()}
        v = min(v_badness, key=v_badness.get)  # type: ignore
        print("Selected", v)

        selected.add(v)
        candidates.remove(v)

    return selected


@ray.remote
def eval_candidate_sets(
    candidates: list[frozenset[str]],
    tree: nx.DiGraph,
    distance_badness: float,
) -> dict[str, float]:
    candidate_badness = {}
    for candidate in candidates:
        v_nia = make_nearest_included_ancestor(candidate, tree, distance_badness)
        badness = sum(nia.badness for nia in v_nia.values())
        candidate_badness[candidate] = badness
    return candidate_badness


def optimize_beam_search(
    size: int, k: int, tree: nx.DiGraph, distance_badness: float, par: int = 1
) -> frozenset[str]:
    assert size >= 2

    root_node = [v for v in tree if len(tree.pred[v]) == 0][0]
    all_nodes = frozenset(tree)

    cur_size = 2
    cur_candidates = set()
    for v in all_nodes:
        if v != root_node:
            cur_candidates.add(frozenset([root_node, v]))

    while True:
        print(f"{cur_size=}")

        groups = divide(par, cur_candidates)
        tasks = [
            eval_candidate_sets.remote(list(g), tree, distance_badness) for g in groups
        ]
        results = ray.get(tasks)
        candidate_badness = {k: v for d in results for k, v in d.items()}
        retained_candidates = heapq.nsmallest(k, candidate_badness, key=candidate_badness.get)  # type: ignore

        if cur_size >= size:
            return retained_candidates[0]

        cur_size += 1
        cur_candidates = set()
        for parent in retained_candidates:
            for v in all_nodes - parent:
                candidate = parent.union([v])
                cur_candidates.add(candidate)
