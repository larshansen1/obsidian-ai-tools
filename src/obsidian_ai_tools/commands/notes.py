"""digest and overview commands."""

from pathlib import Path
from typing import Annotated

import typer

from ..config import get_settings


def register(app: typer.Typer) -> None:
    app.command()(digest)
    app.command()(overview)


def digest(
    days: Annotated[
        int,
        typer.Option("--days", "-d", help="Number of days to include in digest"),
    ] = 7,
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Save to vault inbox with this filename"),
    ] = None,
    format_type: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: terminal, markdown, json"),
    ] = "terminal",
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Generate a knowledge digest for the specified period.

    Summarizes vault activity including new notes, top tags,
    most referenced notes, and inbox status.

    Examples:
        kai digest                           # Weekly summary to terminal
        kai digest --days 1                  # Daily summary
        kai digest --output weekly-review    # Save to vault inbox
        kai digest --format json             # JSON output
    """
    from ..digest import (
        format_digest_json,
        format_digest_markdown,
        format_digest_terminal,
        generate_digest,
    )

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    typer.echo(f"📊 Generating digest for last {days} day(s)...")

    try:
        report = generate_digest(
            vault_path=vault_path,
            since_days=days,
            inbox_folder=settings.obsidian_inbox_folder,
        )
    except Exception as e:
        typer.echo(f"❌ Failed to generate digest: {e}", err=True)
        raise typer.Exit(1) from e

    if format_type == "json":
        formatted = format_digest_json(report)
    elif format_type == "markdown":
        formatted = format_digest_markdown(report)
    else:
        formatted = format_digest_terminal(report)

    if output:
        inbox_path = vault_path / settings.obsidian_inbox_folder
        filename = output if output.endswith(".md") else f"{output}.md"
        output_path = inbox_path / filename
        file_content = format_digest_markdown(report)
        output_path.write_text(file_content)
        typer.echo(f"✅ Digest saved to: {output_path}")
    else:
        typer.echo("")
        typer.echo(formatted)


def overview(
    format_type: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: terminal, markdown, json, compact"),
    ] = "terminal",
    top_n: Annotated[
        int,
        typer.Option("--top-n", help="Number of keywords per folder"),
    ] = 8,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Show a structured overview of your vault by folder.

    Reports per-folder note counts, distinctive keywords (TF-IDF), and top tags.
    Use --format compact to generate a dense summary for agent system prompts.

    Examples:
        kai overview
        kai overview --format compact
        kai overview --format json
        kai overview --top-n 5
    """
    from ..overview import (
        format_overview_compact,
        format_overview_json,
        format_overview_markdown,
        format_overview_terminal,
        generate_overview,
    )

    valid_formats = {"terminal", "markdown", "json", "compact"}
    if format_type not in valid_formats:
        typer.echo(
            f"❌ Invalid format '{format_type}'. Choose from: {', '.join(sorted(valid_formats))}",
            err=True,
        )
        raise typer.Exit(1)

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    try:
        report = generate_overview(vault_path=vault_path, top_n=top_n)
    except Exception as e:
        typer.echo(f"❌ Failed to generate overview: {e}", err=True)
        raise typer.Exit(1) from e

    formatters = {
        "json": format_overview_json,
        "markdown": format_overview_markdown,
        "compact": format_overview_compact,
    }
    formatted = formatters.get(format_type, format_overview_terminal)(report)
    typer.echo(formatted)
