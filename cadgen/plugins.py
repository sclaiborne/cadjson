"""Plugin registry: extra feature types provided by other packages.

A plugin is a module with a `register(registry)` function, discovered through the
`cadgen.plugins` entry-point group or the CADGEN_PLUGINS environment variable (a comma-separated
list of importable module names, handy while developing a plugin without packaging it).

    from cadgen.plugins import registry
    from cadgen.schema import FeatureBase, Op

    class Gear(FeatureBase):
        type: Literal["gear"]
        teeth: int
        module: Dim
        thickness: Dim
        op: Op = "add"

    def register(reg):
        @reg.feature(Gear)
        def build_gear(feat, api):
            sketch = ...              # a build123d Sketch in plane-local coords
            api.extrude(sketch, api.plane("XY"), distance=api.length(feat.thickness), op=feat.op)

The build function receives the validated feature and a FeatureAPI (see cadgen.build) with
`length`, `num`, `vec2`, `vec3`, `plane`, `sketch`, `faces`, `edges`, `extrude`, `combine`, `builder`.
Optional emitters for the Fusion and Python exporters can be registered the same way; without
them those exporters report the feature as unsupported.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Union, get_args

from pydantic import Field, create_model

from cadgen.errors import CadgenError

ENTRY_POINT_GROUP = "cadgen.plugins"
ENV_VAR = "CADGEN_PLUGINS"


@dataclass
class FeaturePlugin:
    model: type
    type_name: str
    build: Callable
    fusion: Callable | None = None
    python: Callable | None = None
    source: str = ""


@dataclass
class Registry:
    features: dict[str, FeaturePlugin] = field(default_factory=dict)
    loaded: bool = False
    errors: list[str] = field(default_factory=list)
    _doc_model: type | None = None

    # --- registration -------------------------------------------------------------------------

    def feature(self, model: type, *, fusion: Callable | None = None, python: Callable | None = None):
        """Decorator: register `model` (a FeatureBase subclass with a Literal `type`) built by the function."""
        type_name = _type_name(model)

        def deco(fn: Callable):
            from cadgen.schema import BUILTIN_FEATURE_TYPES

            if type_name in BUILTIN_FEATURE_TYPES:
                raise CadgenError(f"plugin feature type {type_name!r} clashes with a built-in feature")
            if type_name in self.features and self.features[type_name].build is not fn:
                raise CadgenError(f"plugin feature type {type_name!r} is registered twice")
            self.features[type_name] = FeaturePlugin(model, type_name, fn, fusion, python, source=fn.__module__)
            self._doc_model = None
            return fn

        return deco

    def fusion_emitter(self, type_name: str):
        def deco(fn):
            self.features[type_name].fusion = fn
            return fn

        return deco

    def python_emitter(self, type_name: str):
        def deco(fn):
            self.features[type_name].python = fn
            return fn

        return deco

    # --- discovery ----------------------------------------------------------------------------

    def load(self, force: bool = False) -> None:
        if self.loaded and not force:
            return
        self.loaded = True
        modules: list[str] = []
        try:
            from importlib.metadata import entry_points

            for ep in entry_points(group=ENTRY_POINT_GROUP):
                modules.append(ep.value.split(":")[0])
        except Exception as exc:  # pragma: no cover
            self.errors.append(f"entry points: {exc}")
        env = os.environ.get(ENV_VAR, "")
        modules += [m.strip() for m in env.split(",") if m.strip()]
        for name in modules:
            try:
                mod = importlib.import_module(name)
                reg = getattr(mod, "register", None)
                if reg is None:
                    self.errors.append(f"{name}: no register(registry) function")
                    continue
                reg(self)
            except CadgenError as exc:
                self.errors.append(f"{name}: {exc.message}")
            except Exception as exc:
                self.errors.append(f"{name}: {type(exc).__name__}: {exc}")

    def reset(self) -> None:
        self.features.clear()
        self.errors.clear()
        self.loaded = False
        self._doc_model = None

    # --- model --------------------------------------------------------------------------------

    def document_model(self):
        """The Document class with built-in plus plugin feature types."""
        self.load()
        if self._doc_model is None:
            from cadgen.schema import BUILTIN_FEATURES, Document

            extra = [p.model for p in self.features.values()]
            if not extra:
                self._doc_model = Document
            else:
                union = Annotated[Union[tuple(BUILTIN_FEATURES) + tuple(extra)], Field(discriminator="type")]
                self._doc_model = create_model("Document", __base__=Document, features=(list[union], []))
        return self._doc_model

    def plugin_for(self, feat) -> FeaturePlugin | None:
        return self.features.get(getattr(feat, "type", None))

    def describe(self) -> list[str]:
        self.load()
        return [f"{p.type_name} ({p.source})" for p in self.features.values()]


def _type_name(model: type) -> str:
    try:
        ann = model.model_fields["type"].annotation
        args = get_args(ann)
        if len(args) == 1 and isinstance(args[0], str):
            return args[0]
    except (AttributeError, KeyError):
        pass
    raise CadgenError(f"{model.__name__} must declare `type: Literal[\"<name>\"]`")


registry = Registry()
