"""preview and reading-list commands."""

import logging
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from ..config import get_settings
from ..observability import track_command

# Stored by register() so interactive mode can re-invoke the main app.
_app: typer.Typer | None = None


def register(app: typer.Typer) -> None:
    global _app
    _app = app

    reading_list_app = typer.Typer(
        name="reading-list",
        help="Manage your reading list of saved URLs",
    )
    reading_list_app.command("list")(reading_list_list)
    reading_list_app.command("ingest")(reading_list_ingest)
    reading_list_app.command("clear")(reading_list_clear)

    app.add_typer(reading_list_app, name="reading-list")
    app.command()(preview)


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
    from ..observability import get_db
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

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    urls: list[str] = []

    if batch:
        if sys.stdin.isatty():
            typer.echo("❌ No input provided for batch mode", err=True)
            typer.echo("💡 Pipe URLs to stdin: pbpaste | kai preview --batch", err=True)
            raise typer.Exit(1)
        for line in sys.stdin:
            line = line.strip()
            if line and line.startswith(("http://", "https://")):
                urls.append(line)
        if not urls:
            typer.echo("❌ No valid URLs found in input", err=True)
            raise typer.Exit(1)
        typer.echo(f"📋 Processing {len(urls)} URL(s)...")
    elif url:
        urls = [url]
    else:
        typer.echo("❌ No URL provided", err=True)
        typer.echo("💡 Usage: kai preview <URL> or pbpaste | kai preview --batch", err=True)
        raise typer.Exit(1)

    previews: list[PreviewInfo] = []

    for target_url in urls:
        start_time = time.time()
        try:
            typer.echo(f"\n🔍 Previewing: {target_url[:80]}...")
            preview_info = generate_preview(target_url)
            previews.append(preview_info)
            duration = time.time() - start_time

            try:
                get_db().record_metric(
                    source_type=preview_info.source_type,
                    outcome="success",
                    duration_seconds=duration,
                    provider_used="preview",
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "Failed to record successful preview metric", exc_info=True
                )

            if format_type == "json":
                typer.echo(format_preview_json(preview_info))
            else:
                typer.echo(format_preview_terminal(preview_info))

            if interactive and not batch:
                typer.echo("\n  Actions:")
                typer.echo("    [i] Ingest now")
                typer.echo("    [s] Save to reading list")
                typer.echo("    [x] Skip")
                choice = typer.prompt("  Choice", default="x")
                if choice.lower() == "i":
                    typer.echo("\n🌐 Starting ingestion...")
                    from typer.testing import CliRunner

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

        except UnsupportedURLError as e:
            typer.echo(f"⚠️  Unsupported URL type: {e}", err=True)
            duration = time.time() - start_time
            try:
                get_db().record_metric(
                    source_type="unknown",
                    outcome="failure",
                    duration_seconds=duration,
                    error_type="UnsupportedURLError",
                    provider_used="preview",
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "Failed to record unsupported preview metric", exc_info=True
                )
        except PreviewError as e:
            typer.echo(f"⚠️  Preview failed: {e}", err=True)
            duration = time.time() - start_time
            try:
                get_db().record_metric(
                    source_type="unknown",
                    outcome="failure",
                    duration_seconds=duration,
                    error_type="PreviewError",
                    provider_used="preview",
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "Failed to record failed preview metric", exc_info=True
                )

    if batch and len(urls) > 1:
        typer.echo(f"\n✅ Previewed {len(previews)}/{len(urls)} URL(s)")
        if previews:
            total_cost = sum(p.estimated_cost_usd for p in previews)
            typer.echo(f"   Total estimated cost: ${total_cost:.4f}")


@track_command("reading-list list")
def reading_list_list(
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", "-s", help="Filter by status: pending, ingested, skipped"),
    ] = None,
) -> None:
    """List items in your reading list.

    Shows saved URLs with their preview information and status.

    Examples:
        kai reading-list list
        kai reading-list list --status pending
    """
    from ..preview import load_reading_list

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    entries = load_reading_list(vault_path)

    if not entries:
        typer.echo("📋 Reading list is empty")
        return

    if status:
        entries = [e for e in entries if e.status == status]
        if not entries:
            typer.echo(f"📋 No items with status '{status}'")
            return

    typer.echo(f"📋 Reading List ({len(entries)} item(s)):\n")
    for i, entry in enumerate(entries, 1):
        status_emoji = {"pending": "⏳", "ingested": "✅", "skipped": "⏭️"}.get(entry.status, "❓")
        typer.echo(f"{i}. {status_emoji} {entry.preview.title[:60]}")
        typer.echo(f"   URL: {entry.url[:70]}...")
        typer.echo(f"   Cost: ${entry.preview.estimated_cost_usd:.4f} | Status: {entry.status}")
        typer.echo()


@track_command("reading-list ingest")
def reading_list_ingest(
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
    all_pending: Annotated[
        bool,
        typer.Option("--all", "-a", help="Ingest all pending items"),
    ] = False,
) -> None:
    """Ingest the next pending item from your reading list.

    Ingests the oldest pending URL and marks it as ingested.

    Examples:
        kai reading-list ingest
        kai reading-list ingest --all
    """
    from ..preview import load_reading_list, update_reading_list_status

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    entries = load_reading_list(vault_path)
    pending = [e for e in entries if e.status == "pending"]

    if not pending:
        typer.echo("✅ No pending items in reading list")
        return

    to_ingest = pending if all_pending else pending[:1]
    typer.echo(f"📥 Ingesting {len(to_ingest)} item(s)...\n")

    for entry in to_ingest:
        typer.echo(f"🔄 Ingesting: {entry.preview.title[:50]}...")
        typer.echo(f"   URL: {entry.url}")

        from typer.testing import CliRunner

        assert _app is not None
        runner = CliRunner()
        result = runner.invoke(_app, ["ingest", entry.url, "--vault", str(vault_path)])

        if result.exit_code == 0:
            update_reading_list_status(entry.url, "ingested", vault_path)
            typer.echo("   ✅ Ingested successfully\n")
        else:
            typer.echo("   ❌ Failed to ingest\n")
            typer.echo(result.output)

    remaining = len(pending) - len(to_ingest)
    typer.echo(f"✅ Ingested {len(to_ingest)} item(s). {remaining} pending remaining.")


@track_command("reading-list clear")
def reading_list_clear(
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
    status: Annotated[
        str,
        typer.Option("--status", "-s", help="Status to clear: ingested, skipped, all"),
    ] = "ingested",
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Execute clear (prompts for confirmation unless --yes)"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt (requires --confirm)"),
    ] = False,
) -> None:
    """Clear completed items from reading list.

    Removes items with the specified status. Defaults to removing
    ingested items only.

    Examples:
        kai reading-list clear --confirm                    # Clear ingested with prompt
        kai reading-list clear --confirm --yes              # Clear ingested without prompt
        kai reading-list clear --status skipped --confirm   # Clear skipped
        kai reading-list clear --status all --confirm       # Clear everything
    """
    from ..preview import load_reading_list

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    entries = load_reading_list(vault_path)

    if not entries:
        typer.echo("📋 Reading list is already empty")
        return

    if status == "all":
        to_remove = entries
        to_keep: list = []
    else:
        to_remove = [e for e in entries if e.status == status]
        to_keep = [e for e in entries if e.status != status]

    if not to_remove:
        typer.echo(f"📋 No items with status '{status}' to clear")
        return

    if not confirm:
        typer.echo("\n⚠️  Add --confirm to clear items")
        return

    if not yes:
        if not typer.confirm(f"Remove {len(to_remove)} item(s) with status '{status}'?"):
            typer.echo("❌ Cancelled")
            return

    list_path = vault_path / ".kai" / "reading_list.jsonl"
    with open(list_path, "w", encoding="utf-8") as f:
        for entry in to_keep:
            f.write(entry.model_dump_json() + "\n")

    typer.echo(f"✅ Cleared {len(to_remove)} item(s). {len(to_keep)} remaining.")
