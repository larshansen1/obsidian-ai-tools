"""Tests for the CRAP gate (scripts/crap_report.py).

The script lives in ``scripts/`` which pytest does not auto-import, so the
repo root is pushed onto ``sys.path`` before importing it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts import crap_report  # noqa: E402
from scripts.crap_report import (  # noqa: E402
    Function,
    _coverage_for,
    _normalize_path,
    _resolve_threshold,
    _walk,
    crap,
    main,
)


def _func(path: str, name: str, complexity: int, line_range: range) -> Function:
    return Function(path, name, complexity, frozenset(line_range))


# ------------------------- crap() formula -------------------------


def test_crap_formula_exact_values() -> None:
    # CRAP = comp**3 * (1-cov)**2 + comp; cov as a 0..1 fraction.
    assert crap(2, 0.5) == 4.0
    assert crap(3, 0.0) == 30.0
    assert crap(4, 0.0) == 68.0


def test_crap_at_full_coverage_equals_complexity() -> None:
    assert crap(2, 1.0) == 2.0
    assert crap(17, 1.0) == 17.0


# ------------------------- closure flattening -------------------------


def test_flatten_excludes_closure_ranges_exactly() -> None:
    entries = [
        {
            "type": "function",
            "name": "outer",
            "complexity": 3,
            "lineno": 1,
            "endline": 20,
            "closures": [
                {
                    "type": "function",
                    "name": "middle",
                    "complexity": 2,
                    "lineno": 5,
                    "endline": 10,
                    "closures": [],
                },
                {
                    "type": "function",
                    "name": "inner",
                    "complexity": 1,
                    "lineno": 12,
                    "endline": 15,
                    "closures": [],
                },
            ],
        }
    ]
    functions = _walk(entries, "src/x.py")
    by_name = {fn.name: fn for fn in functions}
    assert set(by_name) == {
        "outer",
        "outer.<locals>.middle",
        "outer.<locals>.inner",
    }
    assert by_name["outer"].owned == frozenset(
        [1, 2, 3, 4, 11, 16, 17, 18, 19, 20]
    )
    assert by_name["outer.<locals>.middle"].owned == frozenset(range(5, 11))
    assert by_name["outer.<locals>.inner"].owned == frozenset(range(12, 16))


def test_flatten_drops_top_level_method_duplicates() -> None:
    # Radon lists methods both inside the class's methods list and as bare
    # top-level entries; the bare duplicates must be skipped.
    entries = [
        {
            "type": "class",
            "name": "Widget",
            "lineno": 1,
            "endline": 5,
            "methods": [
                {
                    "type": "method",
                    "name": "size",
                    "complexity": 2,
                    "lineno": 3,
                    "endline": 5,
                    "closures": [],
                }
            ],
        },
        {
            "type": "method",
            "name": "size",
            "complexity": 2,
            "lineno": 3,
            "endline": 5,
            "closures": [],
        },
    ]
    functions = _walk(entries, "src/x.py")
    assert [fn.name for fn in functions] == ["Widget.size"]


def test_method_closure_kept_under_class() -> None:
    entries = [
        {
            "type": "class",
            "name": "Parser",
            "lineno": 1,
            "endline": 10,
            "methods": [
                {
                    "type": "method",
                    "name": "parse",
                    "complexity": 2,
                    "lineno": 3,
                    "endline": 10,
                    "closures": [
                        {
                            "type": "function",
                            "name": "text",
                            "complexity": 1,
                            "lineno": 5,
                            "endline": 6,
                            "closures": [],
                        }
                    ],
                }
            ],
        }
    ]
    functions = _walk(entries, "src/x.py")
    assert [fn.name for fn in functions] == ["Parser.parse", "Parser.parse.<locals>.text"]
    assert functions[0].owned == frozenset([3, 4, 7, 8, 9, 10])
    assert functions[1].owned == frozenset([5, 6])


# ------------------------- coverage merging -------------------------


def test_coverage_fully_executed() -> None:
    fn = _func("src/x.py", "f", 2, range(1, 4))
    cov_by_path = {"src/x.py": {"executed_lines": [1, 2, 3], "missing_lines": []}}
    missing: set[str] = set()
    assert _coverage_for(fn, cov_by_path, missing) == 1.0
    assert missing == set()


def test_coverage_partially_missing_exact_fraction() -> None:
    fn = _func("src/x.py", "f", 2, range(1, 5))
    cov_by_path = {
        "src/x.py": {"executed_lines": [1, 2], "missing_lines": [3, 4]}
    }
    missing: set[str] = set()
    assert _coverage_for(fn, cov_by_path, missing) == 0.5
    assert missing == set()


def test_coverage_empty_executable_range_yields_one() -> None:
    # No executable lines inside the function's range (decorators, docstring
    # only) must count as fully covered, not 0%.
    fn = _func("src/x.py", "f", 2, range(1, 4))
    cov_by_path = {"src/x.py": {"executed_lines": [50], "missing_lines": [60]}}
    missing: set[str] = set()
    assert _coverage_for(fn, cov_by_path, missing) == 1.0


def test_missing_file_yields_one_and_records_note() -> None:
    fn = _func("src/x.py", "f", 2, range(1, 4))
    missing: set[str] = set()
    assert _coverage_for(fn, {}, missing) == 1.0
    assert missing == {"src/x.py"}


def test_normalize_path_absolute_and_relative() -> None:
    assert _normalize_path("src/obsidian_ai_tools/x.py") == "src/obsidian_ai_tools/x.py"
    assert (
        _normalize_path("/home/user/repo/src/obsidian_ai_tools/x.py")
        == "src/obsidian_ai_tools/x.py"
    )
    assert _normalize_path("unrelated/path.py") == "unrelated/path.py"


# ------------------------- threshold precedence -------------------------


def test_threshold_precedence_cli_over_env(monkeypatch) -> None:
    monkeypatch.setenv("CRAP_THRESHOLD", "10")
    assert _resolve_threshold(20.0) == 20.0
    assert _resolve_threshold(None) == 10.0


def test_threshold_default_is_30(monkeypatch) -> None:
    monkeypatch.delenv("CRAP_THRESHOLD", raising=False)
    assert _resolve_threshold(None) == 30.0


# ------------------------- exit code via subprocess -------------------------


def _run_report(tmp_path: Path, *, threshold: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(Path(__file__).parents[1] / "scripts" / "crap_report.py")]
    if threshold is not None:
        cmd += ["--threshold", threshold]
    env = dict(os.environ)
    env.pop("CRAP_THRESHOLD", None)
    return subprocess.run(
        cmd, cwd=tmp_path, capture_output=True, text=True, env=env, check=False
    )


def _fixture_files(tmp_path: Path, cc: dict, coverage: dict) -> None:
    (tmp_path / "cc.json").write_text(json.dumps(cc))
    (tmp_path / "coverage.json").write_text(json.dumps({"files": coverage}))


def test_exit_zero_when_crap_below_threshold(tmp_path: Path) -> None:
    # CC 3 fully covered -> CRAP 3 < 30.
    _fixture_files(
        tmp_path,
        {"src/x.py": [{"type": "function", "name": "f", "complexity": 3,
                      "lineno": 1, "endline": 3, "closures": []}]},
        {"src/x.py": {"executed_lines": [1, 2, 3], "missing_lines": []}},
    )
    result = _run_report(tmp_path)
    assert result.returncode == 0
    assert "CRAP report (threshold 30.0" in result.stdout


def test_exit_zero_at_exact_threshold_boundary(tmp_path: Path) -> None:
    # CC 3, 0% coverage -> CRAP 30 == threshold 30: passes.
    _fixture_files(
        tmp_path,
        {"src/x.py": [{"type": "function", "name": "f", "complexity": 3,
                      "lineno": 1, "endline": 3, "closures": []}]},
        {"src/x.py": {"executed_lines": [], "missing_lines": [1, 2, 3]}},
    )
    result = _run_report(tmp_path)
    assert result.returncode == 0
    assert "FAIL:" not in result.stderr


def test_exit_one_just_above_threshold(tmp_path: Path) -> None:
    # CC 4, 0% coverage -> CRAP 68 > 30: fails.
    _fixture_files(
        tmp_path,
        {"src/x.py": [{"type": "function", "name": "f", "complexity": 4,
                      "lineno": 1, "endline": 3, "closures": []}]},
        {"src/x.py": {"executed_lines": [], "missing_lines": [1, 2, 3]}},
    )
    result = _run_report(tmp_path)
    assert result.returncode == 1
    assert result.stderr == "FAIL: src/x.py:f has CRAP 68.00 > 30.00\n"


def test_exit_one_with_cli_threshold_override(tmp_path: Path) -> None:
    # CC 3, 0% coverage -> CRAP 30; threshold 29 via flag fails.
    _fixture_files(
        tmp_path,
        {"src/x.py": [{"type": "function", "name": "f", "complexity": 3,
                      "lineno": 1, "endline": 3, "closures": []}]},
        {"src/x.py": {"executed_lines": [], "missing_lines": [1, 2, 3]}},
    )
    result = _run_report(tmp_path, threshold="29")
    assert result.returncode == 1
    assert result.stderr == "FAIL: src/x.py:f has CRAP 30.00 > 29.00\n"


def test_ranked_table_worst_first(tmp_path: Path) -> None:
    _fixture_files(
        tmp_path,
        {
            "src/a.py": [
                {"type": "function", "name": "low", "complexity": 1,
                 "lineno": 1, "endline": 1, "closures": []}
            ],
            "src/b.py": [
                {"type": "function", "name": "high", "complexity": 5,
                 "lineno": 1, "endline": 5, "closures": []}
            ],
        },
        {
            "src/a.py": {"executed_lines": [1], "missing_lines": []},
            "src/b.py": {"executed_lines": [], "missing_lines": [1, 2, 3, 4, 5]},
        },
    )
    result = _run_report(tmp_path)
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert lines[0] == "CRAP report (threshold 30.0; worst first)"
    assert "src/b.py:high" in lines[2]
    assert "src/a.py:low" in lines[3]


def test_missing_file_note_on_stderr(tmp_path: Path) -> None:
    _fixture_files(
        tmp_path,
        {"src/__main__.py": [{"type": "function", "name": "f", "complexity": 2,
                             "lineno": 1, "endline": 2, "closures": []}]},
        {},
    )
    result = _run_report(tmp_path)
    assert result.returncode == 0
    assert result.stderr == "note: no coverage data for src/__main__.py, treated as fully covered\n"


def test_main_function_testable(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.delenv("CRAP_THRESHOLD", raising=False)
    monkeypatch.chdir(tmp_path)
    _fixture_files(
        tmp_path,
        {"src/x.py": [{"type": "function", "name": "f", "complexity": 1,
                      "lineno": 1, "endline": 1, "closures": []}]},
        {"src/x.py": {"executed_lines": [1], "missing_lines": []}},
    )
    assert main([]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines()[0] == "CRAP report (threshold 30.0; worst first)"
    assert out.err == ""