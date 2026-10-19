"""Create variant graph from observation data."""

import json
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pandera as pa
import pandera.typing as pat
import h5py as h5

import click

from pango_aliasor.aliasor import Aliasor

from .contour import ContourConfig, compute_node_contours


def _expand_fips_tokens(fips_list: str) -> set[str]:
    """Expand comma-separated FIPS codes to match both padded ('09') and unpadded ('9') strings."""
    tokens: set[str] = set()
    for raw in fips_list.strip().split(","):
        tok = raw.strip()
        if not tok:
            continue
        tokens.add(tok)
        if tok.isdigit():
            tokens.add(tok.zfill(2))
            tokens.add(str(int(tok)))
    return tokens


def _resolve_parent(aliasor: Aliasor, child: str, resolve_recombinants: bool) -> str:
    """Resolve the parent of a Pango lineage, optionally mapping root recombinants to spike donors."""
    parent = aliasor.parent(child)
    if (
        parent == ""
        and resolve_recombinants
        and hasattr(aliasor, "recomb2parents")
        and child in aliasor.recomb2parents
    ):
        parents = aliasor.recomb2parents.get(child, [])
        if isinstance(parents, list) and len(parents) > 0:
            # 3' / Spike donor parent is typically the last parent in the recombinant list
            candidate = str(parents[-1]).rstrip("*").strip()
            if candidate and candidate != child:
                return candidate
    return parent


class InputSchema(pa.DataFrameModel):
    date: pat.Series[pd.Timestamp] = pa.Field(coerce=True, nullable=False)
    fips: pat.Series[str] = pa.Field(coerce=True, nullable=False)
    lineage: pat.Series[str] = pa.Field(coerce=True, nullable=False)
    lineage_count: pat.Series[float] = pa.Field(coerce=True, nullable=False)


@click.group()
def make_graph():
    """Create variant graph from observation data."""


@make_graph.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to observation data file (CSV).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to validated data file (Parquet).",
)
def validate_input(input_file: Path, output_file: Path):
    """Validate input CSV file and save it in Parquet format."""
    data: pd.DataFrame
    data = pd.read_csv(
        input_file, usecols=["date", "fips", "lineage", "lineage_count"], dtype=str
    )
    data = data.dropna()
    data = InputSchema.validate(data)  # type: ignore

    data = data.rename(columns={"lineage": "variant", "lineage_count": "weight"})
    data.to_parquet(output_file, index=False, compression="zstd")


@make_graph.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to validated data file (Parquet).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to unweighted variant graph file (JSON).",
)
@click.option(
    "-f",
    "--fips-list",
    type=str,
    help="Comma separated list of fips codes.",
)
@click.option(
    "-s",
    "--start-date",
    type=str,
    help="Data start date (inclusive).",
)
@click.option(
    "-e",
    "--end-date",
    type=str,
    help="Data end date (exclusive).",
)
@click.option(
    "--alias-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional local path to pango_designation alias_key.json.",
)
@click.option(
    "--resolve-recombinants/--no-resolve-recombinants",
    default=False,
    show_default=True,
    help="Attach root-level recombinants (XBB, XEC, XFG, etc.) to their Spike-donor parent.",
)
def make_variant_graph(
    input_file: Path,
    output_file: Path,
    fips_list: str | None,
    start_date: str | None,
    end_date: str | None,
    alias_file: Path | None = None,
    resolve_recombinants: bool = False,
):
    """Create variant graph from validated input data.

    The input data is first filtered
    with the FIPS list and the start and end dates.
    If any of those is not provided,
    then the corresponding filtering is not done.

    The variants in the filtered data
    and their ancestors in the Pango lineage tree
    are used to create the variant graph.
    """
    data = pd.read_parquet(input_file)
    print("# rows in input data:", len(data))

    G = nx.DiGraph()

    if fips_list is not None:
        G.graph["fips_list"] = fips_list
        split_fips_set = _expand_fips_tokens(fips_list)
        data = data[data["fips"].isin(split_fips_set)]
        print("# rows after fips filtering:", len(data))

    if start_date is not None:
        G.graph["start_date"] = start_date
        start_date_ts = pd.to_datetime(start_date)
        data = data[data["date"] >= start_date_ts]
        print("# rows after start date filtering:", len(data))

    if end_date is not None:
        G.graph["end_date"] = end_date
        end_date_ts = pd.to_datetime(end_date)
        data = data[data["date"] < end_date_ts]
        print("# rows after end date filtering:", len(data))

    seen_variants = data["variant"].unique().tolist()
    print("# observed variants:", len(seen_variants))

    aliasor = Aliasor(str(alias_file) if alias_file is not None else None)

    for child in seen_variants:
        visited: set[str] = {child}
        while child != "":
            parent = _resolve_parent(aliasor, child, resolve_recombinants)
            if parent in visited and parent != "":
                parent = ""
            visited.add(parent)
            if G.in_degree(child) == 0 if child in G else True:
                G.add_edge(parent, child)
            child = parent

    print("# nodes in variant graph:", G.number_of_nodes())
    print("# edges in variant graph:", G.number_of_edges())

    graph_json = nx.node_link_data(G, edges="edges")  # type: ignore
    graph_json = json.dumps(graph_json)
    output_file.write_text(graph_json)


@make_graph.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to validated data file (Parquet).",
)
@click.option(
    "-g",
    "--graph",
    "graph_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to unweighted graph file (JSON).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to weighted graph file (JSON).",
)
@click.option(
    "-rd",
    "--reference-date",
    type=str,
    required=True,
    help="Reference date.",
)
@click.option(
    "-tid",
    "--temporal-importance-decay",
    type=float,
    default=0.1,
    show_default=True,
    help="Importance of an observation one month from the reference date.",
)
@click.option(
    "--legacy-decay/--no-legacy-decay",
    default=False,
    show_default=True,
    help="Use legacy decay formula (decay * exp(-time)) instead of (decay ** time).",
)
@click.option(
    "-m",
    "--weighting-mode",
    type=click.Choice(["static", "contour"]),
    default="static",
    show_default=True,
    help="Edge weighting mode: 'static' (fixed 1.0 per tier) or 'contour' (trajectory contour divergence).",
)
@click.option(
    "--lambda-tree",
    type=float,
    default=1.0,
    show_default=True,
    help="[Contour mode] Taxonomic step cost per Pango tier.",
)
@click.option(
    "--lambda-contour",
    type=float,
    default=25.0,
    show_default=True,
    help="[Contour mode] Contour divergence penalty weight.",
)
@click.option(
    "--alpha",
    type=float,
    default=0.5,
    show_default=True,
    help="[Contour mode] Balance between JSD and 1-Wasserstein distance.",
)
@click.option(
    "--beta",
    type=float,
    default=0.5,
    show_default=True,
    help="[Contour mode] Weight on terminal velocity divergence D_vel.",
)
@click.option(
    "--n0-shrinkage",
    type=float,
    default=15.0,
    show_default=True,
    help="[Contour mode] Bayesian sample-size credibility threshold N_0.",
)
@click.option(
    "--velocity-decay",
    type=float,
    default=0.75,
    show_default=True,
    help="[Contour mode] Monthly temporal decay for velocity divergence weights.",
)
def make_weighted_variant_graph(
    input_file: Path,
    graph_file: Path,
    output_file: Path,
    reference_date: str,
    temporal_importance_decay: float,
    legacy_decay: bool = False,
    weighting_mode: str = "static",
    lambda_tree: float = 1.0,
    lambda_contour: float = 25.0,
    alpha: float = 0.5,
    beta: float = 0.5,
    n0_shrinkage: float = 15.0,
    velocity_decay: float = 0.75,
):
    """Add node importance and edge distance to the variant graph.

    In 'static' mode:
      Nodes / variants are assigned "importance" based on the number and time
      of observation, and edges are assigned a fixed distance = 1.0.

    In 'contour' mode:
      Nodes are annotated with volume-normalized temporal contours and log-velocity
      vectors, and edges are weighted by contour divergence and sample-size credibility.
    """
    data = pd.read_parquet(input_file)
    G = json.loads(graph_file.read_text())
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    if "fips_list" in G.graph:
        split_fips_set = _expand_fips_tokens(G.graph["fips_list"])
        data = data[data["fips"].isin(split_fips_set)]

    if "start_date" in G.graph:
        start_date_ts = pd.to_datetime(G.graph["start_date"])
        data = data[data["date"] >= start_date_ts]

    if "end_date" in G.graph:
        end_date_ts = pd.to_datetime(G.graph["end_date"])
        data = data[data["date"] < end_date_ts]

    G.graph["reference_date"] = reference_date
    G.graph["temporal_importance_decay"] = temporal_importance_decay
    G.graph["weighting_mode"] = weighting_mode

    if weighting_mode == "contour":
        config = ContourConfig(
            lambda_tree=lambda_tree,
            lambda_contour=lambda_contour,
            alpha=alpha,
            beta=beta,
            n0_shrinkage=n0_shrinkage,
            temporal_decay_prev=temporal_importance_decay,
            temporal_decay_vel=velocity_decay,
        )
        compute_node_contours(
            data, G, reference_date=reference_date, config=config, legacy_decay=legacy_decay
        )
    else:
        time_unit = 30 * 24 * 3600  # 1 Month
        ref_time = pd.to_datetime(reference_date)
        time = ref_time - data["date"]  # type: ignore
        data["time"] = np.abs(time.dt.total_seconds()) / time_unit

        seen_variants = set(data["variant"].unique().tolist())

        for v in G:
            if v in seen_variants:
                df = data[data["variant"] == v]

                time_arr = df["time"].to_numpy()
                weight = df["weight"].to_numpy()
                if legacy_decay:
                    importance = temporal_importance_decay * np.exp(-time_arr) * weight
                else:
                    decay_base = max(temporal_importance_decay, 1e-12)
                    importance = (decay_base ** time_arr) * weight

                G.nodes[v]["importance"] = float(np.sum(importance))
            else:
                G.nodes[v]["importance"] = 0.0

        for u, v in G.edges:
            G.edges[u, v]["distance"] = 1.0

    graph_json = nx.node_link_data(G, edges="edges")  # type: ignore
    graph_json = json.dumps(graph_json)
    output_file.write_text(graph_json)


@make_graph.command()
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
    help="Path to weighted graph file (HDF5).",
)
@click.option(
    "--log-node-weight/--raw-node-weight",
    default=False,
    show_default=True,
    help="Store log(importance) in node_weight so C++ std::exp(node_weight) recovers linear importance.",
)
def make_weighted_variant_graph_h5(
    input_file: Path,
    output_file: Path,
    log_node_weight: bool = False,
):
    """Create HDF5 version of the variant graph."""

    with open(input_file, "rt") as fobj:
        G = json.load(fobj)
    G = nx.node_link_graph(G, edges="edges")  # type: ignore

    nodes = sorted(G.nodes())
    node_idx = {node: i for i, node in enumerate(nodes)}

    n = len(nodes)

    parent = np.empty(n, dtype=np.int64)
    parent.fill(-1)
    node_weight = np.zeros(n, dtype=np.float64)
    edge_weight = np.zeros(n, dtype=np.float64)

    for u in nodes:
        imp = float(G.nodes[u]["importance"])
        if log_node_weight:
            node_weight[node_idx[u]] = np.log(imp) if imp > 1e-12 else -30.0
        else:
            node_weight[node_idx[u]] = imp

    for u, v in G.edges():
        parent[node_idx[v]] = node_idx[u]
        edge_weight[node_idx[v]] = G.edges[u, v]["distance"]

    root = np.where(parent == -1)

    with h5.File(output_file, "w") as fobj:
        group = fobj.create_group("0")
        group.attrs.create("n", n, dtype=np.int64)
        group.attrs.create("root", root, dtype=np.int64)
        group.create_dataset("parent", data=parent)
        group.create_dataset("node_weight", data=node_weight)
        group.create_dataset("edge_weight", data=edge_weight)

