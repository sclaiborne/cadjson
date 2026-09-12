"""Command-line entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cadgen import SCHEMA_VERSION, __version__
from cadgen.errors import CadgenError


def _fail(exc: CadgenError) -> None:
    click.secho("error: " + exc.format(), fg="red", err=True)
    sys.exit(1)


@click.group()
@click.version_option(__version__)
def main() -> None:
    """cadgen: build CAD files from a JSON feature tree."""


@main.command()
@click.argument("parts", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
def validate(parts: tuple[Path, ...]) -> None:
    """Check PARTS against the schema and resolve their params (no geometry)."""
    from cadgen.build import load_document
    from cadgen.context import Context

    ok = True
    for path in parts:
        try:
            doc = load_document(path)
            ctx = Context(doc.params, doc.units)
            click.echo(f"{path}: ok ({len(doc.features)} features, {len(ctx.params)} params)")
        except CadgenError as exc:
            ok = False
            click.secho(f"{path}: " + exc.format(), fg="red", err=True)
    sys.exit(0 if ok else 1)


@main.command()
@click.argument("parts", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--out", "out_dir", type=click.Path(path_type=Path), default=Path("out"), show_default=True,
              help="output directory; a subfolder per part is created")
@click.option("--step/--no-step", default=None, help="override the document's STEP setting")
@click.option("--stl/--no-stl", default=None, help="override the document's STL setting")
@click.option("--png/--no-png", default=None, help="override the document's PNG preview setting")
@click.option("--views", default=None, help="comma-separated views, e.g. front,top,right,iso (overrides the document)")
@click.option("--flat", is_flag=True, help="write into OUT directly instead of OUT/<name>/")
def build(parts, out_dir: Path, step, stl, png, views, flat) -> None:
    """Build PARTS: run the feature tree and write the requested outputs."""
    from cadgen.build import build_document, load_document, write_outputs

    view_list = [v.strip() for v in views.split(",") if v.strip()] if views else None
    failed = 0
    for path in parts:
        try:
            doc = load_document(path)
            result = build_document(doc)
            target = out_dir if flat else out_dir / doc.name
            files = write_outputs(result, target, step=step, stl=stl, png=png, views=view_list)
            click.echo(result.summary())
            for f in files:
                click.echo(f"  wrote {f}")
        except CadgenError as exc:
            failed += 1
            click.secho(f"{path}: error: " + exc.format(), fg="red", err=True)
    sys.exit(1 if failed else 0)


@main.command()
def schema() -> None:
    """Print the JSON Schema for the document format."""
    from cadgen.schema import json_schema

    click.echo(json.dumps(json_schema(), indent=2))


@main.command()
@click.argument("part", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def info(part: Path) -> None:
    """Build PART and print its faces and edges, to help write selectors."""
    from cadgen.build import build_document, load_document
    from cadgen.selectors import _describe_edge, _describe_face

    try:
        result = build_document(load_document(part))
    except CadgenError as exc:
        _fail(exc)
    click.echo(result.summary())
    click.echo("faces:")
    for f in result.part.faces():
        click.echo("  " + _describe_face(f))
    click.echo("edges:")
    for e in result.part.edges():
        click.echo("  " + _describe_edge(e))


if __name__ == "__main__":
    main()
