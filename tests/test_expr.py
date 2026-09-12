import pytest

from cadjson.errors import CadjsonError
from cadjson.expr import evaluate, resolve_params


def test_numbers_and_arithmetic():
    assert evaluate(5, {}) == 5
    assert evaluate("2 + 3 * 4", {}) == 14
    assert evaluate("(2 + 3) * 4", {}) == 20
    assert evaluate("-4 / 2", {}) == -2
    assert evaluate("1.5e1", {}) == 15


def test_params_and_functions():
    p = {"L": 100.0, "D": 80.0, "x": 12.0}
    assert evaluate("L - D - x", p) == 8
    assert evaluate("max(L, D) / 2", p) == 50
    assert evaluate("sqrt(16) + abs(-1)", p) == 5
    assert evaluate("min(L, D, x)", p) == 12


def test_resolve_params_any_order_and_cycles():
    assert resolve_params({"g": "L - x", "L": 10, "x": "2 * 3"}) == {"g": 4, "L": 10, "x": 6}
    with pytest.raises(CadjsonError, match="cycle"):
        resolve_params({"a": "b + 1", "b": "a + 1"})


@pytest.mark.parametrize("bad", ["L +", "2 ** 3", "foo(1)", "import os", "1 / 0", "a b"])
def test_bad_expressions(bad):
    with pytest.raises(CadjsonError):
        evaluate(bad, {"L": 1.0, "a": 1.0, "b": 2.0})


def test_unknown_param_lists_known_ones():
    with pytest.raises(CadjsonError, match="unknown parameter 'q'.*L"):
        evaluate("q + 1", {"L": 1.0})
