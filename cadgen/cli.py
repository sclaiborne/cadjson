"""Command-line entry point. Phase 0 placeholder: commands are stubs until the schema is agreed."""

import click

from cadgen import SCHEMA_VERSION, __version__


@click.group()
@click.version_option(__version__)
def main() -> None:
    """cadgen: build CAD files from a JSON feature tree."""


@main.command()
@click.argument("part", type=click.Path(exists=True, dir_okay=False))
def validate(part: str) -> None:
    """Validate PART against the schema (not implemented yet)."""
    raise click.ClickException(f"validate is not implemented yet (schema {SCHEMA_VERSION})")


@main.command()
@click.argument("part", type=click.Path(exists=True, dir_okay=False))
def build(part: str) -> None:
    """Build PART to STEP/STL/drawings (not implemented yet)."""
    raise click.ClickException(f"build is not implemented yet (schema {SCHEMA_VERSION})")
