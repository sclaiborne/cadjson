"""Tiny, safe arithmetic for dimension fields.

Grammar: numbers, parameter names, + - * /, parentheses, unary minus, and the functions
min, max, abs, sqrt. Nothing else on purpose (see docs/schema-v0.md section 2).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

from cadgen.errors import CadgenError

Dim = int | float | str

_TOKEN = re.compile(r"\s*(?:(\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?|([A-Za-z_]\w*)|(.))")
_FUNCS = {
    "min": min,
    "max": max,
    "abs": abs,
    "sqrt": math.sqrt,
}


class _Parser:
    def __init__(self, text: str, params: Mapping[str, float]):
        self.text = text
        self.params = params
        self.tokens: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise ValueError(f"cannot read {text[pos:]!r}")
            pos = m.end()
            num, name, other = m.groups()
            if num is not None:
                self.tokens.append(("num", m.group(0).strip()))
            elif name is not None:
                self.tokens.append(("name", name))
            elif other.strip():
                self.tokens.append(("op", other))
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self.i += 1
        return tok

    def expect(self, op: str) -> None:
        tok = self.take()
        if tok != ("op", op):
            raise ValueError(f"expected {op!r}, found {tok[1]!r}")

    def parse(self) -> float:
        value = self.expr()
        if self.peek() is not None:
            raise ValueError(f"unexpected {self.peek()[1]!r}")
        return value

    def expr(self) -> float:
        value = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self) -> float:
        value = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            rhs = self.unary()
            if op == "/":
                if rhs == 0:
                    raise ValueError("division by zero")
                value = value / rhs
            else:
                value = value * rhs
        return value

    def unary(self) -> float:
        if self.peek() == ("op", "-"):
            self.take()
            return -self.unary()
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.atom()

    def atom(self) -> float:
        kind, text = self.take()
        if kind == "num":
            return float(text)
        if kind == "name":
            if self.peek() == ("op", "("):
                if text not in _FUNCS:
                    raise ValueError(f"unknown function {text!r} (allowed: {', '.join(_FUNCS)})")
                self.take()
                args = [self.expr()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.expr())
                self.expect(")")
                if text in ("abs", "sqrt") and len(args) != 1:
                    raise ValueError(f"{text} takes one argument")
                return float(_FUNCS[text](*args))
            if text not in self.params:
                known = ", ".join(sorted(self.params)) or "(none)"
                raise ValueError(f"unknown parameter {text!r}; params are: {known}")
            return float(self.params[text])
        if kind == "op" and text == "(":
            value = self.expr()
            self.expect(")")
            return value
        raise ValueError(f"unexpected {text!r}")


def evaluate(dim: Dim, params: Mapping[str, float]) -> float:
    """Evaluate a dim (number or expression string) against resolved params."""
    if isinstance(dim, bool):
        raise CadgenError(f"expected a number or expression, got {dim!r}")
    if isinstance(dim, (int, float)):
        return float(dim)
    if not isinstance(dim, str):
        raise CadgenError(f"expected a number or expression, got {dim!r}")
    try:
        return _Parser(dim, params).parse()
    except ValueError as exc:
        raise CadgenError(f"bad expression {dim!r}: {exc}") from None


def resolve_params(raw: Mapping[str, Dim]) -> dict[str, float]:
    """Resolve a params block. Entries may reference each other in any order; cycles are errors."""
    resolved: dict[str, float] = {}
    visiting: list[str] = []

    class _Lazy(Mapping):
        def __getitem__(self, name: str) -> float:
            if name in resolved:
                return resolved[name]
            if name not in raw:
                raise KeyError(name)
            if name in visiting:
                cycle = " -> ".join(visiting + [name])
                raise CadgenError(f"params reference each other in a cycle: {cycle}")
            visiting.append(name)
            try:
                resolved[name] = evaluate(raw[name], self)
            finally:
                visiting.pop()
            return resolved[name]

        def __iter__(self):
            return iter(raw)

        def __len__(self):
            return len(raw)

        def __contains__(self, name: object) -> bool:
            return name in raw

    lazy = _Lazy()
    for name in raw:
        try:
            lazy[name]
        except CadgenError as exc:
            raise CadgenError(f"param {name!r}: {exc.message}") from None
    return resolved
