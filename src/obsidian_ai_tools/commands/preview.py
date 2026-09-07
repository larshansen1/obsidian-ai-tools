"""preview commands."""

import logging
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from typer.testing import CliRunner

from ..config import get_settings
from ..observability import get_db, track_command
from ..preview import (
    PreviewError,
    PreviewInfo,
    ReadingListEntry,
    UnsupportedURLError,
    format_preview_json,
    format_preview_terminal,
    generate_preview,
    save_to_reading_list,
)

# Stored by register() so interactive mode can re-invoke the main app.
_app: typer.Typer | None = None

# Warning message per (outcome, error_type) metric arm.
_METRIC_WARNINGS: dict[tuple[str, str | None], str] = {
    ("success", None): "Failed to record successful preview metric",
    ("failure", "UnsupportedURLError"): "Failed to record unsupported preview metric",
    ("failure", "PreviewError"): "Failed to record failed preview metric",
}


def register(app: typer.Typer) -> None:
    global _app
    _app = app

    app.command()(preview)


def _resolve_urls(url: str | None, batch: bool) -> list[str]:
    """Determine the URLs to preview, exiting with a Typer error on bad input."""
    if batch:
        if sys.stdin.isatty():
            typer.echo("❌ No input provided for batch mode", err=True)
            typer.echo("💡 Pipe URLs to stdin: pbpaste | kai preview --batch", err=True)
            raise typer.Exit(1)
        urls: list[str] = []
        for line in sys.stdin:
            line = line.strip()
            if line and line.startswith(("http://", "https://")):
                urls.append(line)
        if not urls:
            typer.echo("❌ No valid URLs found in input", err=True)
            raise typer.Exit(1)
        typer.echo(f"📋 Processing {len(urls)} URL(s)...")
        return urls
    if url:
        return [url]
    typer.echo("❌ No URL provided", err=True)
    typer.echo("💡 Usage: kai preview <URL> or pbpaste | kai preview --batch", err=True)
    raise typer.Exit(1)


def _record_preview_metric(
    source_type: str,
    outcome: str,
    duration_seconds: float,
    error_type: str | None = None,
) -> None:
    """Record a preview metric, swallowing observability failures like before."""
    try:
        get_db().record_metric(
            source_type=source_type,
            outcome=outcome,
            duration_seconds=duration_seconds,
            error_type=error_type,
            provider_used="preview",
        )
    except Exception:
        logging.getLogger(__name__).warning(_METRIC_WARNINGS[(outcome, error_type)], exc_info=True)


def _run_interactive_action(preview_info: PreviewInfo, target_url: str, vault_path: Path) -> None:
    """Show the interactive action menu and act on the user's choice."""
    typer.echo("\n  Actions:")
    typer.echo("    [i] Ingest now")
    typer.echo("    [s] Save to reading list")
    typer.echo("    [x] Skip")
    choice = typer.prompt("  Choice", default="x")
    if choice.lower() == "i":
        typer.echo("\n🌐 Starting ingestion...")
        # Re-enter through CliRunner so a failed sub-ingest (typer.Exit(1) on
        # config error / IngestionError) is isolated instead of aborting the
        # outer preview loop.
        assert _app is not None
        runner = CliRunner()
        result = runner.invoke(_app, ["ingest", target_url, "--vault", str(vault_path)])
        typer.echo(result.output)
    elif choice.lower() == "s":
        entry = ReadingListEntry(url=target_url, preview=preview_info)
        save_to_reading_list(entry, vault_path)
        typer.echo("  ✓ Saved to reading list")
    else:
        typer.echo("  ✓ Skipped")


@track_command("preview")
def preview(
    url: Annotated[
        str | None,
        typer.Argument(help="URL to preview"),
    ] = None,
    batch: Annotated[
        bool,
        typer.Option("--batch", "-b", help="Read URLs from stdin (one per line)"),
    ] = False,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", "-i", help="Interactive mode with actions"),
    ] = False,
    format_type: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: terminal, json"),
    ] = "terminal",
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Preview a URL before ingesting.

    Shows metadata, estimated LLM cost, and key topics without full ingestion.
    Use this to decide whether a URL is worth ingesting.

    Examples:
        kai preview https://youtube.com/watch?v=...
        kai preview https://example.com/article
        kai preview https://example.com/paper.pdf
        pbpaste | kai preview --batch
        kai preview URL --interactive
    """
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    urls = _resolve_urls(url, batch)
    previews: list[PreviewInfo] = []

    for target_url in urls:
        start_time = time.time()
        try:
            typer.echo(f"\n🔍 Previewing: {target_url[:80]}...")
            preview_info = generate_preview(target_url)
            previews.append(preview_info)
            duration = time.time() - start_time
            _record_preview_metric(preview_info.source_type, "success", duration)

            if format_type == "json":
                typer.echo(format_preview_json(preview_info))
            else:
                typer.echo(format_preview_terminal(preview_info))

            if interactive and not batch:
                _run_interactive_action(preview_info, target_url, vault_path)

        except UnsupportedURLError as e:
            typer.echo(f"⚠️  Unsupported URL type: {e}", err=True)
            _record_preview_metric(
                "unknown",
                "failure",
                time.time() - start_time,
                error_type="UnsupportedURLError",
            )
        except PreviewError as e:
            typer.echo(f"⚠️  Preview failed: {e}", err=True)
            _record_preview_metric(
                "unknown",
                "failure",
                time.time() - start_time,
                error_type="PreviewError",
            )

    if batch and len(urls) > 1:
        typer.echo(f"\n✅ Previewed {len(previews)}/{len(urls)} URL(s)")
        if previews:
            total_cost = sum(p.estimated_cost_usd for p in previews)
            typer.echo(f"   Total estimated cost: ${total_cost:.4f}")
