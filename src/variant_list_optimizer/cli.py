"""Command line interface."""

import click

from .make_graph import make_graph
from .optimize import optimize


@click.group()
def cli():
    """Variant list optimizer."""


cli.add_command(make_graph)
cli.add_command(optimize)
