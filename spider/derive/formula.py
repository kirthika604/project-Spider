"""A small safe expression evaluator for user formulas (D7, section 11).

Only the whitelisted functions and operators below can run - user formulas
must never execute arbitrary code, so this walks the AST instead of calling
eval() on the string.
"""

from __future__ import annotations

import ast
import math
import re

from ..standardize import dates, units


class FormulaError(Exception):
    pass


# ------------------------------------------------------------------ functions
def _numbers(values):
    out = []
    for value in values:
        if isinstance(value, (list, tuple)):
            out.extend(_numbers(value))
        elif isinstance(value, bool):
            out.append(float(value))
        elif isinstance(value, (int, float)):
            out.append(float(value))
        elif value is not None:
            number = units.parse_number(value)
            if number is not None:
                out.append(number)
    return out


def f_min(*args):
    nums = _numbers(args)
    return min(nums) if nums else None


def f_max(*args):
    nums = _numbers(args)
    return max(nums) if nums else None


def f_avg(*args):
    nums = _numbers(args)
    return round(sum(nums) / len(nums), 6) if nums else None


def f_sum(*args):
    nums = _numbers(args)
    return sum(nums) if nums else None


def f_count(*args):
    total = 0
    for value in args:
        if isinstance(value, (list, tuple, set)):
            total += len(value)
        elif value is not None and value != "":
            total += 1
    return total


def f_band(value, cutoffs, labels):
    """band(altitude, [1500, 3000], ['subtropical','temperate','alpine'])"""
    number = units.parse_number(value)
    if number is None:
        return None
    cutoffs, labels = list(cutoffs), list(labels)
    if len(labels) != len(cutoffs) + 1:
        raise FormulaError(f"band needs {len(cutoffs)+1} labels for {len(cutoffs)} cutoffs")
    for index, cutoff in enumerate(cutoffs):
        if number < float(cutoff):
            return labels[index]
    return labels[-1]


def f_if(condition, when_true, when_false=None):
    return when_true if condition else when_false


def f_convert(value, from_unit, to_unit):
    number = units.parse_number(value)
    if number is None:
        return None
    try:
        converted = units.convert(number, from_unit, to_unit)
        # keep the precision a reader expects: 9842.52 ft, not 9842.519685
        return round(converted, 2 if abs(converted) >= 1 else 6)
    except units.UnitError as exc:
        raise FormulaError(str(exc)) from exc


def f_midpoint(low, high=None):
    if high is None and isinstance(low, (list, tuple)) and len(low) == 2:
        low, high = low
    nums = _numbers([low, high])
    return round(sum(nums) / len(nums), 6) if nums else None


def f_concat(*args, separator="; "):
    parts = [str(a) for a in args if a not in (None, "")]
    return separator.join(parts) if parts else None


def _number(value):
    """Every maths function reads its argument the way the rest of Spider
    does, so "5.972e24 kg" works as well as a bare number."""
    return units.parse_number(value)


def f_sqrt(value):
    number = _number(value)
    if number is None or number < 0:
        return None
    return math.sqrt(number)


def f_log(value, base=None):
    number = _number(value)
    if number is None or number <= 0:
        return None
    if base is None:
        return math.log(number)
    base_number = _number(base)
    if not base_number or base_number <= 0 or base_number == 1:
        return None
    return math.log(number, base_number)


def _one(function):
    def wrapped(value):
        number = _number(value)
        return None if number is None else function(number)
    return wrapped


def f_pow(value, exponent):
    base, power = _number(value), _number(exponent)
    if base is None or power is None:
        return None
    try:
        return base ** power
    except (OverflowError, ValueError, ZeroDivisionError):
        return None


def f_atan2(y, x):
    a, b = _number(y), _number(x)
    return None if a is None or b is None else math.atan2(a, b)


def f_hypot(*args):
    nums = _numbers(args)
    return math.hypot(*nums) if nums else None


def f_distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points, in kilometres.

    Written out so a project that wants "how far from the centre" does not
    have to spell the haversine formula in YAML.
    """
    values = [_number(v) for v in (lat1, lon1, lat2, lon2)]
    if any(v is None for v in values):
        return None
    phi1, lambda1, phi2, lambda2 = (math.radians(v) for v in values)
    d_phi, d_lambda = phi2 - phi1, lambda2 - lambda1
    h = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return round(2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h))), 4)


def f_clamp(value, low, high):
    number, floor_value, ceiling = _number(value), _number(low), _number(high)
    if None in (number, floor_value, ceiling):
        return None
    return max(floor_value, min(ceiling, number))


# Constants, so a formula can be written the way it is written on paper.
CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

FUNCTIONS = {
    "min": f_min, "max": f_max, "avg": f_avg, "sum": f_sum, "count": f_count,
    "sqrt": f_sqrt, "log": f_log, "log10": _one(math.log10),
    "exp": _one(math.exp), "floor": _one(math.floor), "ceil": _one(math.ceil),
    "pow": f_pow, "clamp": f_clamp, "sign": _one(lambda n: (n > 0) - (n < 0)),
    "sin": _one(math.sin), "cos": _one(math.cos), "tan": _one(math.tan),
    "asin": _one(lambda n: math.asin(max(-1.0, min(1.0, n)))),
    "acos": _one(lambda n: math.acos(max(-1.0, min(1.0, n)))),
    "atan": _one(math.atan), "atan2": f_atan2, "hypot": f_hypot,
    "radians": _one(math.radians), "degrees": _one(math.degrees),
    "distance_km": f_distance_km,
    "band": f_band, "if": f_if, "if_": f_if, "convert": f_convert,
    "midpoint": f_midpoint,
    "season_of": dates.season_of, "year_of": dates.year_of, "concat": f_concat,
    "round": lambda v, n=0: (None if units.parse_number(v) is None
                             else round(units.parse_number(v), int(n))),
    "abs": lambda v: (None if units.parse_number(v) is None else abs(units.parse_number(v))),
    "len": lambda v: (len(v) if v is not None else 0),
    "lower": lambda v: str(v).lower() if v is not None else None,
    "upper": lambda v: str(v).upper() if v is not None else None,
}

ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call,
    ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple, ast.IfExp, ast.keyword,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
)

REL_CALL = re.compile(r"\b(\w+)\s+via\s+(\w+)\b")
# `altitude_m over plant` reads a whole column, the way `x via rel` reads a
# relation: both become two plain string arguments
COLUMN_CALL = re.compile(r"\b(\w+)\s+over\s+(\w+)\b")
IF_CALL = re.compile(r"\bif\s*\(")
DOTTED = re.compile(r"\b([A-Za-z_]\w*)\.(min|max|value|count)\b")


def prepare(formula: str) -> str:
    """Rewrite Spider's two sugar forms into plain calls."""
    text = REL_CALL.sub(r'"\1", "\2"', formula)        # count(plant via grows_in)
    text = COLUMN_CALL.sub(r'"\1", "\2"', text)        # mean(altitude_m over plant)
    text = DOTTED.sub(r"\1__\2", text)                 # altitude_m.min
    text = IF_CALL.sub("if_(", text)                   # `if` is a Python keyword
    return text


def referenced_names(formula: str) -> list[str]:
    try:
        tree = ast.parse(prepare(formula), mode="eval")
    except SyntaxError:
        return []
    return sorted({node.id for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and node.id not in FUNCTIONS})


def evaluate(formula: str, variables: dict, functions: dict | None = None):
    """Evaluate a user formula against this entity's values."""
    source = prepare(formula)
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"cannot read the formula: {exc.msg}") from exc

    table = dict(FUNCTIONS)
    table.update(functions or {})

    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise FormulaError(
                f"'{type(node).__name__}' is not allowed in a formula - "
                f"use only arithmetic and {', '.join(sorted(table))}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise FormulaError(
                    "method and attribute calls are not allowed in a formula - "
                    f"use only {', '.join(sorted(k for k in table if k != 'if_'))}")
            if node.func.id not in table:
                raise FormulaError(f"unknown function '{node.func.id}' - allowed: "
                                   f"{', '.join(sorted(k for k in table if k != 'if_'))}")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in variables:
                return variables[node.id]
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            raise FormulaError(f"unknown field '{node.id.replace('__', '.')}'")
        if isinstance(node, ast.List):
            return [walk(e) for e in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(walk(e) for e in node.elts)
        if isinstance(node, ast.IfExp):
            return walk(node.body) if walk(node.test) else walk(node.orelse)
        if isinstance(node, ast.UnaryOp):
            value = walk(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            number = units.parse_number(value)
            if number is None:
                return None
            return -number if isinstance(node.op, ast.USub) else number
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add) and (isinstance(left, str) or isinstance(right, str)):
                return f"{left}{right}"
            left_n, right_n = units.parse_number(left), units.parse_number(right)
            if left_n is None or right_n is None:
                return None
            try:
                return {
                    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
                    ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
                    ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
                    ast.Pow: lambda a, b: a ** b,
                }[type(node.op)](left_n, right_n)
            except ZeroDivisionError:
                return None
        if isinstance(node, ast.BoolOp):
            values = [walk(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = walk(node.left)
            for operator, comparator in zip(node.ops, node.comparators):
                right = walk(comparator)
                if isinstance(operator, (ast.In, ast.NotIn)):
                    inside = right is not None and left in right
                    result = inside if isinstance(operator, ast.In) else not inside
                else:
                    left_n, right_n = units.parse_number(left), units.parse_number(right)
                    if left_n is None or right_n is None:
                        if isinstance(operator, ast.Eq):
                            result = str(left) == str(right)
                        elif isinstance(operator, ast.NotEq):
                            result = str(left) != str(right)
                        else:
                            return None
                    else:
                        result = {
                            ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
                            ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
                            ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
                        }[type(operator)](left_n, right_n)
                if not result:
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            args = [walk(a) for a in node.args]
            kwargs = {k.arg: walk(k.value) for k in node.keywords}
            return table[node.func.id](*args, **kwargs)
        raise FormulaError(f"'{type(node).__name__}' is not allowed in a formula")

    return walk(tree)
