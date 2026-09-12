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
@click.option("--sheet/--no-sheet", default=None, help="override the document's drawing-sheet setting")
@click.option("--flat", is_flag=True, help="write into OUT directly instead of OUT/<name>/")
def build(parts, out_dir: Path, step, stl, png, views, sheet, flat) -> None:
    """Build PARTS: run the feature tree and write the requested outputs."""
    from cadgen.build import build_document, load_document, write_outputs

    view_list = [v.strip() for v in views.split(",") if v.strip()] if views else None
    failed = 0
    for path in parts:
        try:
            doc = load_document(path)
            result = build_document(doc)
            target = out_dir if flat else out_dir / doc.name
            files = write_outputs(result, target, step=step, stl=stl, png=png, views=view_list, sheet=sheet)
            click.echo(result.summary())
            for f in files:
                click.echo(f"  wrote {f}")
        except CadgenError as exc:
            failed += 1
            click.secho(f"{path}: error: " + exc.format(), fg="red", err=True)
    sys.exit(1 if failed else 0)


@main.command("export-fusion")
@click.argument("parts", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--out", "out_dir", type=click.Path(path_type=Path), default=Path("out"), show_default=True,
              help="output directory; <name>_fusion/ is created inside it")
def export_fusion_cmd(parts, out_dir: Path) -> None:
    """Write a Fusion 360 script that rebuilds PARTS with a native parametric timeline."""
    from cadgen.build import load_document
    from cadgen.fusion import export_fusion

    failed = 0
    for path in parts:
        try:
            doc = load_document(path)
            files = export_fusion(doc, out_dir)
            for f in files:
                click.echo(f"  wrote {f}")
            click.echo(f"{doc.name}: in Fusion, Utilities > Add-Ins > Scripts > + and pick the folder {files[0].parent}")
        except CadgenError as exc:
            failed += 1
            click.secho(f"{path}: error: " + exc.format(), fg="red", err=True)
    sys.exit(1 if failed else 0)


@main.command()
@click.argument("part", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("reference", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--samples", default=20000, show_default=True, help="surface sample points each way")
def compare(part: Path, reference: Path, samples: int) -> None:
    """Build PART and compare it with a REFERENCE mesh (STL/3MF/OBJ): volume, bbox, surface distance."""
    from cadgen.build import build_document, load_document
    from cadgen.compare import compare as run_compare

    try:
        result = build_document(load_document(part))
    except CadgenError as exc:
        _fail(exc)
    click.echo(run_compare(result.part, reference, samples=samples).summary())


@main.command("export-python")
@click.argument("parts", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--out", "out_dir", type=click.Path(path_type=Path), default=Path("out"), show_default=True)
def export_python_cmd(parts, out_dir: Path) -> None:
    """Write a standalone build123d script equivalent to building PARTS."""
    from cadgen.build import load_document
    from cadgen.pyexport import export_python

    failed = 0
    for path in parts:
        try:
            doc = load_document(path)
            click.echo(f"  wrote {export_python(doc, out_dir / doc.name)}")
        except CadgenError as exc:
            failed += 1
            click.secho(f"{path}: error: " + exc.format(), fg="red", err=True)
    sys.exit(1 if failed else 0)


@main.command()
def plugins() -> None:
    """List plugin feature types found through entry points or CADGEN_PLUGINS."""
    from cadgen.plugins import registry

    registry.load()
    if not registry.features and not registry.errors:
        click.echo("no plugins found (entry-point group 'cadgen.plugins' or CADGEN_PLUGINS=module,...)")
    for line in registry.describe():
        click.echo(f"  {line}")
    for err in registry.errors:
        click.secho(f"  problem: {err}", fg="red", err=True)


@main.command()
@click.option("-o", "--out", "out_path", type=click.Path(path_type=Path), default=None,
              help="write to this file instead of stdout")
@click.option("--with-plugins", is_flag=True, help="include plugin feature types currently loadable")
def schema(out_path: Path | None, with_plugins: bool) -> None:
    """Print (or write) the JSON Schema for the document format."""
    from cadgen.schema import json_schema

    text = json.dumps(json_schema(with_plugins=with_plugins), indent=2) + "\n"
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        click.echo(f"wrote {out_path}")
    else:
        click.echo(text, nl=False)


@main.command()
@click.argument("part", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="print a JSON report instead of text")
def info(part: Path, as_json: bool) -> None:
    """Build PART and print its faces and edges, to help write selectors."""
    from cadgen.build import build_document, load_document
    from cadgen.report import build_report
    from cadgen.selectors import _describe_edge, _describe_face

    try:
        result = build_document(load_document(part))
    except CadgenError as exc:
        _fail(exc)
    if as_json:
        rep = build_report(result, [])
        rep["face_list"] = [_describe_face(f) for f in result.part.faces()]
        rep["edge_list"] = [_describe_edge(e) for e in result.part.edges()]
        click.echo(json.dumps(rep, indent=2))
        return
    click.echo(result.summary())
    click.echo("faces:")
    for f in result.part.faces():
        click.echo("  " + _describe_face(f))
    click.echo("edges:")
    for e in result.part.edges():
        click.echo("  " + _describe_edge(e))


if __name__ == "__main__":
    main()
