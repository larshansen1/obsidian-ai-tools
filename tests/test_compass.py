"""Tests for the Vault Compass scaffold: settings, /status, `compass serve`,
and the import-linter rule that keeps kai from importing compass (ADR 0005).
"""

import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from vault_compass import cli as compass_cli
from vault_compass.app import create_app
from vault_compass.config import CompassSettings, get_compass_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()

EXACT_NON_LOOPBACK_REFUSAL = (
    "❌ Refusing to bind to 0.0.0.0: the compass server is unauthenticated and "
    "local-only by default. Re-run with --i-know-what-im-doing to bind "
    "beyond loopback.\n"
)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_db_path_defaults_under_vault_kai_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)

    settings = CompassSettings()

    assert settings.obsidian_vault_path == vault.resolve()
    assert settings.compass_db_path == vault.resolve() / ".kai" / "compass.duckdb"


def test_db_path_override_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    custom = tmp_path / "elsewhere.duckdb"
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setenv("COMPASS_DB_PATH", str(custom))

    assert CompassSettings().compass_db_path == custom


def test_missing_vault_dir_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "nope"
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(missing))

    with pytest.raises(ValidationError) as exc_info:
        CompassSettings()

    errors = exc_info.value.errors()
    # Pydantic also reports the db path: its default factory needs the vault path.
    assert [(e["loc"], e["type"]) for e in errors] == [
        (("obsidian_vault_path",), "value_error"),
        (("compass_db_path",), "default_factory_not_called"),
    ]
    assert errors[0]["msg"] == f"Value error, Vault path is not an existing directory: {missing}"


def test_file_as_vault_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    not_a_dir = tmp_path / "file.md"
    not_a_dir.write_text("x")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(not_a_dir))

    with pytest.raises(ValidationError):
        CompassSettings()


def test_get_compass_settings_reads_kai_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vault path comes from the same .env kai would find."""
    vault = _vault(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(f"OBSIDIAN_VAULT_PATH={vault}\n")
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    monkeypatch.delenv("COMPASS_DB_PATH", raising=False)
    finder = MagicMock(return_value=env_file)
    monkeypatch.setattr("obsidian_ai_tools.config.find_env_file", finder)

    settings = get_compass_settings()

    finder.assert_called_once_with()
    assert settings.obsidian_vault_path == vault.resolve()
    assert settings.compass_db_path == vault.resolve() / ".kai" / "compass.duckdb"


# ---------------------------------------------------------------------------
# HTTP app
# ---------------------------------------------------------------------------


def test_status_returns_only_running_flag() -> None:
    response = TestClient(create_app()).get("/status")

    assert response.status_code == 200
    assert response.json() == {"running": True}


# ---------------------------------------------------------------------------
# compass serve
# ---------------------------------------------------------------------------


def test_serve_defaults_launch_uvicorn_on_loopback(capsys: pytest.CaptureFixture[str]) -> None:
    """Called directly so the signature defaults themselves are exercised."""
    fake_run = MagicMock()
    with patch("uvicorn.run", fake_run):
        compass_cli.serve()

    fake_run.assert_called_once_with(
        "vault_compass.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8100,
        reload=False,
    )
    out = capsys.readouterr()
    vault = get_compass_settings().obsidian_vault_path
    assert out.out == (
        "🧭 compass server starting on http://127.0.0.1:8100\n"
        f"   Vault: {vault}\n"
        "   Press Ctrl+C to stop\n\n"
    )
    assert out.err == ""


def test_serve_cli_passes_options_through() -> None:
    fake_run = MagicMock()
    with patch("uvicorn.run", fake_run):
        result = runner.invoke(
            compass_cli.app, ["serve", "--host", "localhost", "--port", "9001", "--reload"]
        )

    assert result.exit_code == 0
    fake_run.assert_called_once_with(
        "vault_compass.app:create_app",
        factory=True,
        host="localhost",
        port=9001,
        reload=True,
    )


def test_serve_refuses_non_loopback_without_ack(capsys: pytest.CaptureFixture[str]) -> None:
    fake_run = MagicMock()
    with patch("uvicorn.run", fake_run), pytest.raises(typer.Exit) as exc_info:
        compass_cli.serve(host="0.0.0.0")  # nosec B104

    assert exc_info.value.exit_code == 1
    assert capsys.readouterr().err == EXACT_NON_LOOPBACK_REFUSAL
    fake_run.assert_not_called()


def test_serve_allows_non_loopback_with_ack() -> None:
    fake_run = MagicMock()
    with patch("uvicorn.run", fake_run):
        compass_cli.serve(host="0.0.0.0", i_know_what_im_doing=True)  # nosec B104

    fake_run.assert_called_once_with(
        "vault_compass.app:create_app",
        factory=True,
        host="0.0.0.0",  # nosec B104
        port=8100,
        reload=False,
    )


def test_serve_exits_on_bad_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "missing"))
    fake_run = MagicMock()
    with patch("uvicorn.run", fake_run), pytest.raises(typer.Exit) as exc_info:
        compass_cli.serve()

    assert exc_info.value.exit_code == 1
    assert capsys.readouterr().err.startswith("❌ Configuration error:\n")
    fake_run.assert_not_called()


def test_compass_without_args_shows_serve_subcommand() -> None:
    result = runner.invoke(compass_cli.app, [])

    # no_args_is_help prints help and exits 2 (click's usage-error code).
    assert result.exit_code == 2
    assert "serve" in result.output


# ---------------------------------------------------------------------------
# Import rule (ADR 0005)
# ---------------------------------------------------------------------------


def _lint_imports(project: Path) -> subprocess.CompletedProcess[str]:
    lint_imports = Path(sys.executable).parent / "lint-imports"
    return subprocess.run(  # nosec B603
        [str(lint_imports), "--config", str(project / "pyproject.toml"), "--no-cache"],
        cwd=project,
        env={"PYTHONPATH": str(project / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.slow
def test_import_linter_passes_on_repo() -> None:
    result = _lint_imports(REPO_ROOT)

    assert result.returncode == 0, result.stdout
    assert "Contracts: 1 kept, 0 broken." in result.stdout


@pytest.mark.slow
def test_import_linter_fails_when_kai_imports_compass(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(REPO_ROOT / "src", project / "src", ignore=shutil.ignore_patterns("*.egg-info"))
    shutil.copy(REPO_ROOT / "pyproject.toml", project / "pyproject.toml")
    (project / "src" / "obsidian_ai_tools" / "_bad_probe.py").write_text(
        "import vault_compass  # noqa: F401\n"
    )

    result = _lint_imports(project)

    assert result.returncode == 1, result.stdout
    assert "kai must not import vault_compass BROKEN" in result.stdout
    assert "obsidian_ai_tools._bad_probe -> vault_compass" in result.stdout
