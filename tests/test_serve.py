"""Tests for the `kai serve` / `kai version` commands and the HTTP app factory.

Two layers:

* CLI layer (typer CliRunner) for option validation, background-server state
  files, and the foreground uvicorn path - all with subprocess os-level side
  effects mocked away.
* Direct unit calls (always through the module functions themselves) so that
  signature defaults are exercised - typer re-registers commands from the
  original signature, which would otherwise hide default-value mutants from
  CLI-only tests.
"""

import io
import subprocess
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app as cli_app
from obsidian_ai_tools.commands import serve as serve_cmd
from obsidian_ai_tools.server.app import create_app

runner = CliRunner()

EXACT_LOG_ERROR = "❌ --log must be used with --status.\n"
EXACT_ACTION_ERROR = "❌ Use only one of --background, --stop, or --status.\n"
EXACT_NOT_RUNNING = "kai server is not running in the background.\n"


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


def test_register_installs_serve_and_version_on_a_fresh_app() -> None:
    """register() must wire both commands onto whatever app it is given."""
    fresh = typer.Typer()
    serve_cmd.register(fresh)
    for command in ("serve", "version"):
        result = runner.invoke(fresh, [command, "--help"])
        assert result.exit_code == 0, f"{command!r} not registered: {result.output}"


# ---------------------------------------------------------------------------
# serve: option validation
# ---------------------------------------------------------------------------


def test_serve_log_requires_status_errors_to_stderr(tmp_path: Path) -> None:
    """--log without --status is a usage error reported on stderr only."""
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch.dict(sys.modules, {"uvicorn": None}),
    ):
        result = runner.invoke(cli_app, ["serve", "--log"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == EXACT_LOG_ERROR


def test_serve_rejects_combining_actions(tmp_path: Path) -> None:
    """--background/--stop/--status are mutually exclusive."""
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        result = runner.invoke(cli_app, ["serve", "--background", "--status"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == EXACT_ACTION_ERROR
    mock_popen.assert_not_called()


# ---------------------------------------------------------------------------
# serve: status / stop state commands
# ---------------------------------------------------------------------------


def test_serve_status_reports_not_running_exactly(tmp_path: Path) -> None:
    with patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, ["serve", "--status"])

    assert result.exit_code == 0
    assert result.stdout == EXACT_NOT_RUNNING


def test_serve_stop_when_not_running_prints_exact_message(tmp_path: Path) -> None:
    with patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, ["serve", "--stop"])

    assert result.exit_code == 0
    assert result.stdout == EXACT_NOT_RUNNING


def test_serve_status_reads_log_without_a_pid_file(tmp_path: Path) -> None:
    """--status --log reads the log even when no server is running."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    (state_dir / "server.log").write_text(
        "\n".join(f"log line {number}" for number in range(25)),
        encoding="utf-8",
    )

    with patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, ["serve", "--status", "--log"])

    assert result.exit_code == 0
    assert "log line 4" not in result.stdout
    assert "log line 5" in result.stdout
    assert "log line 24" in result.stdout


def test_serve_status_log_with_missing_log_file_prints_exact_message(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / ".kai" / "server.log"
    with patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, ["serve", "--status", "--log"])

    assert result.exit_code == 0
    assert result.stdout == f"{EXACT_NOT_RUNNING}No server log found at {log_path}.\n"


def test_serve_stop_unlinks_pid_file_even_if_it_disappears(
    tmp_path: Path,
) -> None:
    """The PID file may vanish between the liveness check and the unlink."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    pid_file = state_dir / "server.pid"
    pid_file.write_text("4321\n", encoding="utf-8")

    def vanish_and_kill(pid: int, sig: int) -> None:
        # Simulate a racing `kai serve --stop`: the pid file is gone by the
        # time _stop_background_server tries to clean it up itself.
        pid_file.unlink(missing_ok=True)

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.os.kill", side_effect=vanish_and_kill),
    ):
        result = runner.invoke(cli_app, ["serve", "--stop"])

    assert result.exit_code == 0
    assert "kai server stopped (PID 4321)" in result.stdout


def test_get_running_server_pid_tolerates_gone_pid_file(tmp_path: Path) -> None:
    """On ProcessLookupError the pid file is cleaned up best-effort only."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    pid_file = state_dir / "server.pid"
    pid_file.write_text("4321\n", encoding="utf-8")

    def vanish_and_kill(pid: int, sig: int) -> None:
        # Simulate a racing `kai serve --stop`: by the time the cleanup unlink
        # runs, the pid file is already gone.
        pid_file.unlink(missing_ok=True)
        raise ProcessLookupError()

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.os.kill", side_effect=vanish_and_kill),
    ):
        result = runner.invoke(cli_app, ["serve", "--status"])

    assert result.exit_code == 0
    assert result.stdout == EXACT_NOT_RUNNING


# ---------------------------------------------------------------------------
# serve: --background
# ---------------------------------------------------------------------------


def test_serve_background_refuses_when_already_running(tmp_path: Path) -> None:
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    (state_dir / "server.pid").write_text("4321\n", encoding="utf-8")

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.os.kill"),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        result = runner.invoke(cli_app, ["serve", "--background"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "❌ kai server is already running in the background (PID 4321).\n"
    mock_popen.assert_not_called()


def test_serve_background_builds_the_full_command_and_popen_kwargs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4321
        serve_cmd._start_background_server("0.0.0.0", 9876, reload=True)

    command = mock_popen.call_args.args[0]
    assert command == [
        sys.executable,
        "-m",
        "obsidian_ai_tools.cli",
        "serve",
        "--host",
        "0.0.0.0",
        "--port",
        "9876",
        "--reload",
    ]
    kwargs = mock_popen.call_args.kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert isinstance(kwargs["stdout"], io.TextIOBase)
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True

    out = capsys.readouterr().out
    assert "🚀 kai server started in the background on http://0.0.0.0:9876" in out
    assert "PID: 4321" in out
    assert f"Log: {tmp_path}/.kai/server.log" in out
    assert "Stop with: kai serve --stop" in out
    assert "XX" not in out
    assert (tmp_path / ".kai" / "server.pid").read_text(encoding="utf-8") == "4321\n"


def test_serve_background_tolerates_state_dir_already_existing(tmp_path: Path) -> None:
    (tmp_path / ".kai").mkdir()

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4321
        result = runner.invoke(cli_app, ["serve", "--background"])

    assert result.exit_code == 0
    mock_popen.assert_called_once()


def test_serve_background_creates_missing_state_dir_hierarchy(tmp_path: Path) -> None:
    """mkdir(parents=True) must create the whole ~/.kai hierarchy, not just
    the leaf directory."""
    home = tmp_path / "deeply" / "nested"
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=home),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4321
        result = runner.invoke(cli_app, ["serve", "--background"])

    assert result.exit_code == 0
    pid_path = home / ".kai" / "server.pid"
    assert pid_path.read_text(encoding="utf-8") == "4321\n"


def test_serve_background_with_reload_adds_the_flag(tmp_path: Path) -> None:
    """serve forwards its --reload flag into the child command line."""
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4321
        result = runner.invoke(cli_app, ["serve", "--background", "--reload"])

    assert result.exit_code == 0
    assert "--reload" in mock_popen.call_args.args[0]


def test_serve_background_writes_state_files_as_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child output log and the pid file are opened with an explicit utf-8
    encoding, never the locale default or a non-canonical name."""
    seen: list[tuple[str, dict]] = []
    real_open: Any = Path.open
    real_write_text: Any = Path.write_text

    def spy_open(self: object, *args: object, **kwargs: object) -> object:
        mode = args[0] if args else kwargs.get("mode")
        if mode == "a":
            seen.append(("open", dict(kwargs)))
        return real_open(self, *args, **kwargs)

    def spy_write_text(self: object, *args: object, **kwargs: object) -> object:
        seen.append(("write_text", dict(kwargs)))
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(Path, "write_text", spy_write_text)

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4321
        result = runner.invoke(cli_app, ["serve", "--background"])

    assert result.exit_code == 0
    recording = [entry for entry in seen if entry[0] in ("open", "write_text")]
    assert recording, "expected utf-8 file operations to be recorded"
    assert all(entry[1].get("encoding") == "utf-8" for entry in recording)


def test_serve_status_log_reads_the_log_as_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    (state_dir / "server.log").write_text("line\n", encoding="utf-8")

    seen: list[dict] = []
    real_read_text: Any = Path.read_text

    def spy_read_text(self: object, *args: object, **kwargs: object) -> str:
        if getattr(self, "name", "") == "server.log":
            seen.append(dict(kwargs))
        return cast(str, real_read_text(self, *args, **kwargs))

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    with patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, ["serve", "--status", "--log"])

    assert result.exit_code == 0
    assert seen, "expected the server log to be read"
    assert all(entry.get("encoding") == "utf-8" for entry in seen)


# ---------------------------------------------------------------------------
# serve: foreground (uvicorn) path + defaults
# ---------------------------------------------------------------------------


def test_serve_foreground_uses_defaults_and_launches_uvicorn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no options, serve must run uvicorn with documented defaults.

    Called directly (not via typer) so the signature defaults themselves are
    what runs - typer would otherwise supply them from the original signature.
    """

    fake_run = MagicMock()
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("uvicorn.run", fake_run),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        serve_cmd.serve()

    captured = capsys.readouterr()
    assert "🚀 kai server starting on http://127.0.0.1:8765" in captured.out
    assert "Chrome extension → load chrome-extension/" in captured.out
    assert "Press Ctrl+C to stop" in captured.out
    assert "XX" not in captured.out
    assert captured.err == ""

    fake_run.assert_called_once()
    app_arg = fake_run.call_args.args[0]
    assert app_arg is not None
    assert fake_run.call_args.kwargs == {"host": "127.0.0.1", "port": 8765, "reload": False}
    mock_popen.assert_not_called()


def test_serve_foreground_warns_on_non_loopback_host(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Binding beyond loopback must warn on stderr before starting uvicorn."""

    fake_run = MagicMock()
    with patch("uvicorn.run", fake_run):
        serve_cmd.serve(host="0.0.0.0", port=9000)

    captured = capsys.readouterr()
    assert "⚠️  Binding to 0.0.0.0 exposes the unauthenticated ingest API" in captured.err
    assert "beyond this machine. Use 127.0.0.1 unless you know what you're doing." in captured.err
    assert "XX" not in captured.err
    assert "🚀 kai server starting on http://0.0.0.0:9000" in captured.out
    assert fake_run.call_args.kwargs == {"host": "0.0.0.0", "port": 9000, "reload": False}


def test_serve_foreground_does_not_warn_on_loopback_hosts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    fake_run = MagicMock()
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("uvicorn.run", fake_run),
    ):
        serve_cmd.serve(host="localhost", port=9000)

    assert capsys.readouterr().err == ""


def test_serve_reports_when_uvicorn_is_missing() -> None:
    """The ImportError branch must be hit when uvicorn cannot be imported."""
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=Path("/tmp/none")),
        patch.dict(sys.modules, {"uvicorn": None}),
    ):
        result = runner.invoke(cli_app, ["serve"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        '❌ uvicorn not installed.\n💡 Run: pip install "obsidian-ai-tools[server]"\n'
    )


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_version_prints_exact_metadata() -> None:
    result = runner.invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert result.stdout == "obsidian-ai-tools v1.0.0\nKnowledge AI Tools for Obsidian\n"


# ---------------------------------------------------------------------------
# server/app.py create_app factory
# ---------------------------------------------------------------------------


def test_create_app_exposes_docs_and_metadata() -> None:
    app = create_app()
    assert app.title == "kai"
    assert app.description == "Knowledge AI Tools — local ingestion service"
    assert app.version == "1.0.0"

    openapi = app.openapi()
    assert openapi["info"]["title"] == "kai"
    assert openapi["info"]["version"] == "1.0.0"

    client = TestClient(app)
    response = client.get("/docs")
    assert response.status_code == 200


def test_create_app_cors_preflight_allows_extension_methods_and_headers() -> None:
    client = TestClient(create_app())
    response = client.options(
        "/ingest",
        headers={
            "Origin": "chrome-extension://abc123",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-custom-header",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "chrome-extension://abc123"
    assert response.headers["access-control-allow-methods"] == "GET, POST"
    assert response.headers["access-control-allow-headers"] == "x-custom-header"


def test_create_app_cors_rejects_disallowed_preflight_method() -> None:
    client = TestClient(create_app())
    response = client.options(
        "/ingest",
        headers={
            "Origin": "chrome-extension://abc123",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-methods"] == "GET, POST"
