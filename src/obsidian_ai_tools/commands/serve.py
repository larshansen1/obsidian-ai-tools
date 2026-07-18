"""serve and version commands."""

import os
import signal
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Annotated

import typer


def register(app: typer.Typer) -> None:
    app.command()(serve)
    app.command()(version)


def _server_state_paths() -> tuple[Path, Path]:
    state_dir = Path.home() / ".kai"
    return state_dir / "server.pid", state_dir / "server.log"


def _get_running_server_pid() -> int | None:
    pid_path, _ = _server_state_paths()
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        return None
    except PermissionError:
        pass
    return pid


def _start_background_server(host: str, port: int, reload: bool) -> None:
    pid_path, log_path = _server_state_paths()
    running_pid = _get_running_server_pid()
    if running_pid is not None:
        typer.echo(
            f"❌ kai server is already running in the background (PID {running_pid}).",
            err=True,
        )
        raise typer.Exit(1)

    pid_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "obsidian_ai_tools.cli",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        command.append("--reload")

    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    typer.echo(f"🚀 kai server started in the background on http://{host}:{port}")
    typer.echo(f"   PID: {process.pid}")
    typer.echo(f"   Log: {log_path}")
    typer.echo("   Stop with: kai serve --stop")


def _stop_background_server() -> None:
    pid_path, _ = _server_state_paths()
    running_pid = _get_running_server_pid()
    if running_pid is None:
        typer.echo("kai server is not running in the background.")
        return
    os.kill(running_pid, signal.SIGTERM)
    pid_path.unlink(missing_ok=True)
    typer.echo(f"🛑 kai server stopped (PID {running_pid}).")


def _show_background_server_log() -> None:
    _, log_path = _server_state_paths()
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        typer.echo(f"No server log found at {log_path}.")
        return
    typer.echo(f"\nRecent log output ({log_path}):")
    for line in lines[-20:]:
        typer.echo(line)


def serve(
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port to listen on"),
    ] = 8765,
    host: Annotated[
        str,
        typer.Option("--host", help="Host to bind to (use 127.0.0.1 for local-only)"),
    ] = "127.0.0.1",
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Auto-reload on code changes (development only)"),
    ] = False,
    background: Annotated[
        bool,
        typer.Option("--background", "-b", help="Run detached and return to the shell"),
    ] = False,
    stop: Annotated[
        bool,
        typer.Option("--stop", help="Stop the detached server"),
    ] = False,
    status: Annotated[
        bool,
        typer.Option("--status", help="Show whether the detached server is running"),
    ] = False,
    log: Annotated[
        bool,
        typer.Option("--log", help="With --status, show the last 20 server log lines"),
    ] = False,
) -> None:
    """Run kai as a local HTTP service for the Chrome extension.

    Exposes two endpoints:
      GET  /status  — health check, returns vault/model config
      POST /ingest  — full ingest pipeline (fetch → LLM → vault write)

    Install server dependencies first:
        pip install "obsidian-ai-tools[server]"

    Then load the Chrome extension from the chrome-extension/ directory.

    Examples:
        kai serve
        kai serve --background
        kai serve --status
        kai serve --status --log
        kai serve --stop
        kai serve --port 9000
    """
    if log and not status:
        typer.echo("❌ --log must be used with --status.", err=True)
        raise typer.Exit(1)

    selected_actions = sum((background, stop, status))
    if selected_actions > 1:
        typer.echo("❌ Use only one of --background, --stop, or --status.", err=True)
        raise typer.Exit(1)

    if stop:
        _stop_background_server()
        return

    if status:
        running_pid = _get_running_server_pid()
        if running_pid is None:
            typer.echo("kai server is not running in the background.")
        else:
            typer.echo(f"kai server is running in the background (PID {running_pid}).")
        if log:
            _show_background_server_log()
        return

    if background:
        _start_background_server(host, port, reload)
        return

    try:
        import uvicorn
    except ImportError:
        typer.echo("❌ uvicorn not installed.", err=True)
        typer.echo('💡 Run: pip install "obsidian-ai-tools[server]"', err=True)
        raise typer.Exit(1) from None

    from ..server.app import create_app as _create_app

    if host not in ("127.0.0.1", "localhost"):
        typer.echo(
            f"⚠️  Binding to {host} exposes the unauthenticated ingest API "
            "beyond this machine. Use 127.0.0.1 unless you know what you're doing.",
            err=True,
        )

    typer.echo(f"🚀 kai server starting on http://{host}:{port}")
    typer.echo("   Chrome extension → load chrome-extension/ as an unpacked extension")
    typer.echo("   Press Ctrl+C to stop\n")

    uvicorn.run(_create_app(), host=host, port=port, reload=reload)


def version() -> None:
    """Show version information."""
    typer.echo("obsidian-ai-tools v1.0.0")
    typer.echo("Knowledge AI Tools for Obsidian")
