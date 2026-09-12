# Contributing

## Setup

```
py -3.12 -m venv .venv            # python3.12 -m venv .venv on Linux/macOS
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

The suite builds every part in `examples/` and checks known volumes, runs the Fusion export
against a fake API, and rebuilds every example through the Python export. It takes about two
minutes; `-k <name>` narrows it.

## Adding a feature type

1. Model in `cadjson/schema.py` (a `FeatureBase` subclass with `type: Literal["..."]`), added to
   `BUILTIN_FEATURES` and `BUILTIN_FEATURE_TYPES`.
2. Build branch in `cadjson/build.py` (`_run_feature`), usually a method on `Builder`.
3. Emitters in `cadjson/fusion.py` and `cadjson/pyexport.py`, or an explicit "not exported"
   error and a skip in the corresponding tests.
4. An example part in `examples/` that uses it, a test with a known volume or property, and a
   section in `docs/schema-v0.md`. Regenerate the schema: `cadjson schema -o schema/cadjson-0.1.schema.json`.
5. If the Claude skill should mention it, edit `.claude/skills/cadjson/SKILL.md` and copy it to
   `cadjson/skill/SKILL.md` (a test checks they match).

Prefer a plugin (`docs/extending.md`) for anything domain-specific.

## Conventions

- Errors are `CadjsonError` with the feature id and a hint a person could act on.
- Selectors never use raw indices.
- Every dimension a user would write on a sketch must be expressible as a named param.
- Commit messages say why, not what.
