"""Create a weighted variant tree from observation count data."""

import json
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pandera as pa
import pandera.typing as pat

from pango_aliasor.aliasor import Aliasor

import click


class InputSchema(pa.DataFrameModel):
    date: pat.Series[pd.Timestamp] = pa.Field(coerce=True, nullable=False)
    fips: pat.Series[str] = pa.Field(coerce=True, nullable=False)
    lineage: pat.Series[str] = pa.Field(coerce=True, nullable=False)
    lineage_count: pat.Series[float] = pa.Field(coerce=True, nullable=False)


@click.group()
def cli():
    """Create a weighted variant tree from observation count data."""


@cli.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to lineage count file (CSV).",
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


@cli.command()
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
    help="Path to unweighted variant graph data file (JSON node-link).",
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
def make_variant_tree(
    input_file: Path,
    output_file: Path,
    fips_list: str | None,
    start_date: str | None,
    end_date: str | None,
):
    """Create variant tree from validated input data.

    The input data is first filtered
    with the fips list and the start and end dates.
    If any of those is not provided,
    then the corresponding filtering is not done.

    The varinats in the filtered data
    and their ancestors in the Pango lineage tree
    are used to create the variant tree.
    """
    data = pd.read_parquet(input_file)
    print("# rows in input data:", len(data))

    G = nx.DiGraph()

    if fips_list is not None:
        G.graph["fips_list"] = fips_list
        split_fips_list = fips_list.strip().split(",")
        data = data[data["fips"].isin(split_fips_list)]
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

    aliasor = Aliasor()

    for child in seen_variants:
        while child != "":
            parent = aliasor.parent(child)
            G.add_edge(parent, child)

            child = parent

    print("# nodes in variant tree:", G.number_of_nodes())
    print("# edges in variant tree:", G.number_of_edges())

    graph_json = nx.node_link_data(G, edges="edges")
    graph_json = json.dumps(graph_json)
    output_file.write_text(graph_json)


@cli.command()
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to validated input file (Parquet).",
)
@click.option(
    "-g",
    "--graph",
    "graph_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to unweighted graph data file (JSON node-link).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to weighted graph data file (JSON node-link).",
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
def make_weighted_variant_tree(
    input_file: Path,
    graph_file: Path,
    output_file: Path,
    reference_date: str,
    temporal_importance_decay: float,
):
    """Add node importance and edge distance to the variant tree.

    Nodes / variants are assigned "importance"
    based on the number and time of observation.
    Importance of an observation decay's exponentially with time
    and is proportional to the weight of the observation.

    Edges are assigned a fixed distance = 1.0
    """

    data = pd.read_parquet(input_file)
    G = json.loads(graph_file.read_text())
    G = nx.node_link_graph(G, edges="edges")

    if "fips_list" in G.graph:
        split_fips_list = G.graph["fips_list"].strip().split(",")
        data = data[data["fips"].isin(split_fips_list)]

    if "start_date" in G.graph:
        start_date_ts = pd.to_datetime(G.graph["start_date"])
        data = data[data["date"] >= start_date_ts]

    if "end_date" in G.graph:
        end_date_ts = pd.to_datetime(G.graph["end_date"])
        data = data[data["date"] < end_date_ts]

    G.graph["reference_date"] = reference_date
    G.graph["temporal_importance_decay"] = temporal_importance_decay

    time_unit = 30 * 24 * 3600  # 1 Month
    ref_time = pd.to_datetime(reference_date)
    time = ref_time - data["date"]  # type: ignore
    data["time"] = np.abs(time.dt.total_seconds()) / time_unit

    seen_variants = data["variant"].unique().tolist()
    seen_variants = set(seen_variants)

    for v in G:
        if v in seen_variants:
            df = data[data["variant"] == v]

            time = df["time"].to_numpy()
            weight = df["weight"].to_numpy()
            importance = temporal_importance_decay * np.exp(-time) * weight

            G.nodes[v]["importance"] = np.sum(importance)
        else:
            G.nodes[v]["importance"] = 0.0

    for u, v in G.edges:
        G.edges[u, v]["distance"] = 1.0

    graph_json = nx.node_link_data(G, edges="edges")
    graph_json = json.dumps(graph_json)
    output_file.write_text(graph_json)


if __name__ == "__main__":
    cli()
