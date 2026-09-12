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


# --- AST (used by exporters that need the expression, not its value) ----------------------------


class _AstParser(_Parser):
    """Same grammar, but returns a tree: ('num', v) ('name', s) ('neg', x) ('bin', op, l, r) ('call', f, args)."""

    def __init__(self, text: str):
        super().__init__(text, {})

    def expr(self):
        node = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return ("neg", self.unary())
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.atom()

    def atom(self):
        kind, text = self.take()
        if kind == "num":
            return ("num", float(text))
        if kind == "name":
            if self.peek() == ("op", "("):
                if text not in _FUNCS:
                    raise ValueError(f"unknown function {text!r}")
                self.take()
                args = [self.expr()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.expr())
                self.expect(")")
                return ("call", text, args)
            return ("name", text)
        if kind == "op" and text == "(":
            node = self.expr()
            self.expect(")")
            return node
        raise ValueError(f"unexpected {text!r}")


def parse_ast(text: str):
    try:
        return _AstParser(text).parse()
    except ValueError as exc:
        raise CadgenError(f"bad expression {text!r}: {exc}") from None


def names_in(dim: Dim) -> set[str]:
    """Parameter names referenced by a dim."""
    if not isinstance(dim, str):
        return set()
    found: set[str] = set()

    def walk(node):
        if node[0] == "name":
            found.add(node[1])
        elif node[0] == "neg":
            walk(node[1])
        elif node[0] == "bin":
            walk(node[2])
            walk(node[3])
        elif node[0] == "call":
            for a in node[2]:
                walk(a)

    walk(parse_ast(dim))
    return found


class UnitsError(ValueError):
    """The expression cannot be typed as a plain length or a plain number."""


def to_fusion(dim: Dim, kinds: Mapping[str, str], expect: str) -> str:
    """Render a dim as a Fusion 360 expression. `kinds` maps param name -> 'len' | 'none';
    `expect` is 'len' (a length in mm) or 'none' (a unitless count or angle in degrees).
    Raises UnitsError when the units do not work out, so callers can fall back to a number."""
    if isinstance(dim, (int, float)):
        return f"{dim:g} mm" if expect == "len" else f"{dim:g}"

    def fmt(v: float) -> str:
        return f"{v:g}"

    def typed(node):
        k = node[0]
        if k == "num":
            return fmt(node[1]), "num"
        if k == "name":
            return node[1], kinds.get(node[1], "len")
        if k == "neg":
            t, kd = typed(node[1])
            return f"-({t})", kd
        if k == "call":
            parts = [typed(a) for a in node[2]]
            if node[1] == "sqrt":
                if any(kd == "len" for _, kd in parts):
                    raise UnitsError("sqrt of a length")
                return f"sqrt({parts[0][0]})", "none" if parts[0][1] == "none" else "num"
            kds = {kd for _, kd in parts if kd != "num"}
            if len(kds) > 1:
                raise UnitsError("mixed units in function arguments")
            target = kds.pop() if kds else "num"
            texts = [t + " mm" if kd == "num" and target == "len" else t for t, kd in parts]
            return f"{node[1]}({', '.join(texts)})", target
        _, op, l, r = node
        (lt, lk), (rt, rk) = typed(l), typed(r)
        if op in "+-":
            if lk == "len" and rk == "num":
                rt, rk = rt + " mm", "len"
            elif rk == "len" and lk == "num":
                lt, lk = lt + " mm", "len"
            if lk == "num" and rk == "num":
                kd = "num"
            elif {lk, rk} <= {"none", "num"}:
                kd = "none"
            elif lk == rk == "len":
                kd = "len"
            else:
                raise UnitsError("adding a length to a number")
            return f"{lt} {op} {rt}", kd
        if op == "*":
            if lk == "len" and rk == "len":
                raise UnitsError("length times length")
            kd = "len" if "len" in (lk, rk) else ("none" if "none" in (lk, rk) else "num")
            return f"{lt} * {rt}", kd
        # division
        if lk == "len" and rk == "len":
            kd = "none"
        elif rk == "len":
            raise UnitsError("number divided by a length")
        else:
            kd = "len" if lk == "len" else ("none" if "none" in (lk, rk) else "num")
        return f"{lt} / ({rt})" if r[0] == "bin" else f"{lt} / {rt}", kd

    text, kind = typed(parse_ast(dim))
    if expect == "len":
        if kind == "num":
            return f"({text}) mm"
        if kind != "len":
            raise UnitsError("expected a length")
    elif kind == "len":
        raise UnitsError("expected a unitless value")
    return text


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
