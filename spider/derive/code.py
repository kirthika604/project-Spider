"""`method: code` - a small Python file, for analysts (D7, section 11).

Formulas are walked as a syntax tree and can never run code. This is the one
deliberate exception: a file the user wrote, in their own project, that they
pointed `code:` at. It is loaded from the project folder only, it is called
once per record with that record's values, and anything it raises is reported
against that record instead of stopping the build.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


class CodeError(Exception):
    pass


_loaded: dict[str, object] = {}


def _resolve(root: Path, relative: str) -> Path:
    """Only files inside the project may be loaded."""
    root = root.resolve()
    path = Path(relative)
    full = (path if path.is_absolute() else root / path).resolve()
    if not full.exists():
        raise CodeError(f"code file not found: {relative}")
    if root not in full.parents and full.parent != root:
        raise CodeError(
            f"{relative} is outside the project folder - keep derivation code "
            f"beside the project so the build stays reproducible")
    if full.suffix != ".py":
        raise CodeError(f"{relative} is not a Python file")
    return full


def load(root: Path, relative: str, function_name: str = "compute"):
    """Import the file once and return the function to call per record."""
    full = _resolve(root, relative)
    key = f"{full}:{function_name}"
    if key in _loaded:
        return _loaded[key]

    spec = importlib.util.spec_from_file_location(f"spider_derivation_{full.stem}", full)
    if spec is None or spec.loader is None:
        raise CodeError(f"cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise CodeError(f"{relative} failed to load: {type(exc).__name__}: {exc}") from exc

    function = getattr(module, function_name, None)
    if function is None:
        raise CodeError(
            f"{relative} has no function called '{function_name}' - define "
            f"`def {function_name}(values):` returning the value, or set `function:`")
    if not callable(function):
        raise CodeError(f"'{function_name}' in {relative} is not a function")
    _loaded[key] = function
    return function


def run(root: Path, relative: str, function_name: str, values: dict):
    """Call the user's function with this record's values."""
    function = load(root, relative, function_name)
    try:
        return function(dict(values))
    except Exception as exc:
        raise CodeError(f"{relative}:{function_name} raised "
                        f"{type(exc).__name__}: {exc}") from exc


def forget() -> None:
    """Drop the import cache, so an edited file is picked up on a rebuild."""
    _loaded.clear()
