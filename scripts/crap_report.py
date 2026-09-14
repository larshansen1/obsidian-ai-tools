#!/usr/bin/env python3
"""CRAP (Change Risk Analysis and Prediction) score gate.

Composes radon per-function cyclomatic complexity (cc.json) with coverage
line data (coverage.json) into a CRAP score per function, ranks the
results worst-first, and exits non-zero when any function exceeds the
threshold.

Stdlib only: intended to work under plain ``python3`` in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass


def crap(complexity: float, coverage: float) -> float:
    """CRAP score for one function.

    crap4j convention: ``CRAP = complexity**3 * (1 - coverage)**2 +
    complexity`` with ``coverage`` as a 0..1 fraction. At coverage 1.0 the
    score collapses to ``complexity``.

    Line-mapping approximation: radon reports per-function line ranges
    while coverage reports per-file lines; decorators and multi-line
    signatures may skew a few functions. Acceptable for a gate.

    Exponent note: some writeups use complexity**2; complexity**3 was
    chosen per issue #110 and only rescales the threshold.
    """
    return complexity**3 * (1.0 - coverage) ** 2 + complexity


@dataclass(frozen=True)
class Function:
    """One flattened radon entry plus the lines it owns (no descendants)."""

    path: str
    name: str
    complexity: int
    owned: frozenset[int]


def _walk(entries: list[dict], path: str, prefix: str = "", top_level: bool = True) -> list[Function]:
    """Flatten radon entries into functions.

    Nested closures become their own entries with qualified names
    (``outer.<locals>.inner``); a parent's owned lines exclude every
    descendant's range so nested lines are not double-counted. Class
    wrappers are dropped, their methods kept. Radon also lists methods
    as bare top-level entries, duplicating the class's own methods list,
    so top-level ``method`` entries are skipped here.
    """
    functions: list[Function] = []
    for entry in entries:
        if top_level and entry["type"] == "method":
            continue
        if prefix:
            sep = "." if entry["type"] in ("method", "class") else ".<locals>."
            name = f"{prefix}{sep}{entry['name']}"
        else:
            name = entry["name"]
        children = entry.get("closures") or entry.get("methods") or []
        if entry["type"] == "class":
            functions.extend(_walk(children, path, name, top_level=False))
            continue
        descendants = _walk(children, path, name, top_level=False)
        owned = set(range(entry["lineno"], entry["endline"] + 1))
        for _descendant in descendants:
            owned -= _descendant.owned
        functions.append(Function(path, name, entry["complexity"], frozenset(owned)))
        functions.extend(descendants)
    return functions


def _normalize_path(path: str) -> str:
    """Reduce a coverage.json key to its repo-relative radon form.

    Keys may be src-relative (``src/obsidian_ai_tools/x.py``) or absolute
    (``/…/src/obsidian_ai_tools/x.py``); suffix-match both onto the radon
    key by keeping the tail that starts at the ``/src/`` boundary.
    """
    if path.startswith("src/"):
        return path
    marker = "/src/"
    idx = path.rfind(marker)
    return path[idx + 1 :] if idx != -1 else path


def _load_coverage(path: str) -> dict[str, dict]:
    """Load coverage.json into {normalized path: file data}."""
    with open(path) as handle:
        data = json.load(handle)["files"]
    return {_normalize_path(key): value for key, value in data.items()}


def _coverage_for(
    fn: Function, cov_by_path: dict[str, dict], missing_files: set[str]
) -> float:
    """Fraction of the function's owned executable lines that ran (0..1).

    A file absent from coverage.json yields 1.0 and is recorded in
    ``missing_files`` (printed to stderr): missing evidence must not
    manufacture risk. An empty executable range also yields 1.0.
    """
    file_data = cov_by_path.get(fn.path)
    if file_data is None:
        missing_files.add(fn.path)
        return 1.0
    executed = set(file_data.get("executed_lines", []))
    missing = set(file_data.get("missing_lines", []))
    executable = (executed | missing) & fn.owned
    if not executable:
        return 1.0
    return len(executable & executed) / len(executable)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail when any function's CRAP score (complexity x lack of coverage) exceeds the threshold."
    )
    parser.add_argument("--cc-json", default="cc.json", help="radon -j output (default: cc.json)")
    parser.add_argument(
        "--coverage-json",
        default="coverage.json",
        help="coverage json output (default: coverage.json)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        help="CRAP threshold (default: CRAP_THRESHOLD env var, else 30)",
    )
    return parser.parse_args(argv)


def _resolve_threshold(cli_value: float | None) -> float:
    if cli_value is not None:
        return cli_value
    env = os.environ.get("CRAP_THRESHOLD")
    return float(env) if env is not None else 30.0


def main(argv: list[str] | None = None) -> int:
    """Rank functions by CRAP worst-first; exit 1 if any exceeds threshold."""
    args = _parse_args(argv)
    threshold = _resolve_threshold(args.threshold)

    with open(args.cc_json) as handle:
        cc_data = json.load(handle)
    cov_by_path = _load_coverage(args.coverage_json)
    missing_files: set[str] = set()

    rows: list[tuple[float, float, Function]] = []
    for path, entries in cc_data.items():
        for fn in _walk(entries, path):
            cov = _coverage_for(fn, cov_by_path, missing_files)
            rows.append((crap(fn.complexity, cov), cov, fn))
    rows.sort(key=lambda row: (-row[0], row[2].path, row[2].name))

    for path in sorted(missing_files):
        print(f"note: no coverage data for {path}, treated as fully covered", file=sys.stderr)

    print(f"CRAP report (threshold {threshold:.1f}; worst first)")
    print(f"{'CC':>3} {'cov%':>5} {'CRAP':>8}  function")
    for score, cov, fn in rows:
        print(f"{fn.complexity:>3} {cov * 100:>4.0f}% {score:>8.2f}  {fn.path}:{fn.name}")

    violations = [(score, fn) for score, cov, fn in rows if score > threshold]
    for score, fn in violations:
        print(
            f"FAIL: {fn.path}:{fn.name} has CRAP {score:.2f} > {threshold:.2f}",
            file=sys.stderr,
        )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())