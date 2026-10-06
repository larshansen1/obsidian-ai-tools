"""Command-line interface for Vault Compass (`compass`)."""

from typing import Annotated

import typer
from pydantic import ValidationError

from .config import get_compass_settings
from .notes import refresh_notes
from .topics import TopicsError, load_topics, seed_topics_file

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

app = typer.Typer(
    name="compass",
    help="Vault Compass - visual analysis of your Obsidian vault",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Vault Compass - visual analysis of your Obsidian vault."""
    # An explicit callback keeps `serve` a subcommand; with a single command
    # typer would otherwise collapse it into the root command.


@app.command()
def scan() -> None:
    """Rebuild the notes, tag, link and duplicate tables in compass.duckdb."""
    try:
        settings = get_compass_settings()
    except ValidationError as e:
        typer.echo(f"❌ Configuration error:\n{e}", err=True)
        raise typer.Exit(1) from None

    if seed_topics_file(settings.compass_topics_path):
        typer.echo(f"Created {settings.compass_topics_path} with the starter topics")
    try:
        topics = load_topics(settings.compass_topics_path)
    except TopicsError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1) from None

    report = refresh_notes(settings.obsidian_vault_path, settings.compass_db_path, topics)
    typer.echo(f"Notes: {report.note_count}")
    typer.echo(f"Unparsed dates: {len(report.unparsed_dates)}")
    for path in report.unparsed_dates:
        typer.echo(f"  {path}")
    typer.echo(f"Undated notes: {len(report.undated)}")
    for path in report.undated:
        typer.echo(f"  {path}")
    typer.echo(f"Unresolved links: {len(report.unresolved_links)}")
    typer.echo(f"Unmapped tags: {len(report.unmapped_tags)}")
    typer.echo(f"Likely duplicates: {len(report.duplicates)}")
    for dup in report.duplicates:
        typer.echo(f"  {dup.path_a} <-> {dup.path_b} ({dup.reason})")
    if report.unreadable:
        typer.echo(f"Unreadable files: {len(report.unreadable)}")
        for path in report.unreadable:
            typer.echo(f"  {path}")


@app.command()
def serve(
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port to listen on"),
    ] = 8100,
    host: Annotated[
        str,
        typer.Option("--host", help="Host to bind to (use 127.0.0.1 for local-only)"),
    ] = "127.0.0.1",
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Auto-reload on code changes (development only)"),
    ] = False,
    i_know_what_im_doing: Annotated[
        bool,
        typer.Option(
            "--i-know-what-im-doing",
            help="Acknowledge exposing the unauthenticated server beyond loopback",
        ),
    ] = False,
) -> None:
    """Run the Vault Compass API on localhost.

    Same security posture as `kai serve` (ADR 0002): no authentication,
    loopback-only unless --i-know-what-im-doing is passed, and /status
    returns only the running flag.

    The default port 8100 matches the web/ dev proxy.

    Examples:
        compass serve
        compass serve --port 9000
    """
    if host not in LOOPBACK_HOSTS and not i_know_what_im_doing:
        typer.echo(
            f"❌ Refusing to bind to {host}: the compass server is unauthenticated and "
            "local-only by default. Re-run with --i-know-what-im-doing to bind "
            "beyond loopback.",
            err=True,
        )
        raise typer.Exit(1)

    # Fail before starting uvicorn so a bad vault path is one clear line,
    # not a traceback per worker.
    try:
        settings = get_compass_settings()
    except ValidationError as e:
        typer.echo(f"❌ Configuration error:\n{e}", err=True)
        raise typer.Exit(1) from None

    import uvicorn

    typer.echo(f"🧭 compass server starting on http://{host}:{port}")
    typer.echo(f"   Vault: {settings.obsidian_vault_path}")
    typer.echo("   Press Ctrl+C to stop\n")

    # Import string + factory (not an app object) so --reload works.
    uvicorn.run(
        "vault_compass.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
