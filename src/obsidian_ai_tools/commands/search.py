"""search command."""

from pathlib import Path
from typing import Annotated

import typer

from ..config import get_settings
from ..observability import track_command
from ..obsidian import build_obsidian_url


def register(app: typer.Typer) -> None:
    app.command()(search)


@track_command("search")
def search(
    keyword: Annotated[
        str | None, typer.Option("--keyword", "-k", help="Search for keyword in content")
    ] = None,
    tag: Annotated[str | None, typer.Option("--tag", "-t", help="Filter by tag")] = None,
    after: Annotated[
        str | None, typer.Option("--after", help="Show notes created after date (YYYY-MM-DD)")
    ] = None,
    before: Annotated[
        str | None, typer.Option("--before", help="Show notes created before date (YYYY-MM-DD)")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Maximum number of results")] = 10,
    explain: Annotated[
        bool,
        typer.Option("--explain", help="Show why each result matched"),
    ] = False,
    no_boost: Annotated[
        bool,
        typer.Option("--no-boost", help="Disable backlink score boosting"),
    ] = False,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Search your Obsidian vault for notes.

    Search by keyword, filter by tags, or limit by date range.
    Results are ranked by BM25 score boosted by backlink popularity.

    Notes:
        - --tag/--after/--before apply as filters
        - --explain prints reason, tags, and keywords
        - --no-boost disables backlink score boosting

    Examples:
        kai search --keyword "machine learning"
        kai search --tag ai
        kai search --keyword agents --tag llm
        kai search --after 2026-01-01
    """
    from datetime import datetime

    from ..indexer import build_index
    from ..search import SearchQuery, build_whoosh_index, search_notes
    from ..wikilinks import count_backlinks

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    after_date = None
    before_date = None

    if after:
        try:
            after_date = datetime.fromisoformat(after)
        except ValueError:
            typer.echo(f"❌ Invalid date format for --after: {after}", err=True)
            typer.echo("💡 Use format: YYYY-MM-DD", err=True)
            raise typer.Exit(1) from None

    if before:
        try:
            before_date = datetime.fromisoformat(before)
        except ValueError:
            typer.echo(f"❌ Invalid date format for --before: {before}", err=True)
            typer.echo("💡 Use format: YYYY-MM-DD", err=True)
            raise typer.Exit(1) from None

    if not any([keyword, tag, after_date, before_date]):
        typer.echo("❌ No search criteria provided", err=True)
        typer.echo("💡 Use --keyword, --tag, --after, or --before", err=True)
        raise typer.Exit(1)

    typer.echo("🔍 Searching vault...")

    vault_index = build_index(vault_path, settings.obsidian_inbox_folder)
    index_dir = vault_path / ".kai" / "whoosh_index"
    build_whoosh_index(vault_index, index_dir)
    backlinks = count_backlinks(vault_index)

    query = SearchQuery(
        keyword=keyword,
        tag=tag,
        after=after_date,
        before=before_date,
        limit=limit,
        explain=explain,
        no_boost=no_boost,
    )

    results = search_notes(query, vault_index, index_dir, backlinks=backlinks)

    if not results:
        typer.echo("   No results found")
        return

    typer.echo(f"   Found {len(results)} result(s):\n")

    for i, result in enumerate(results, 1):
        note = result.note
        obsidian_url = build_obsidian_url(vault_path, note.file_path)

        typer.echo(f"{i}. {note.title}")
        typer.echo(f"   Tags: {', '.join(note.tags) if note.tags else 'none'}")
        if note.created:
            typer.echo(f"   Created: {note.created.strftime('%Y-%m-%d')}")
        if note.author:
            typer.echo(f"   Author: {note.author}")
        typer.echo(f"   Path: {note.file_path}")
        typer.echo(f"   Open: {obsidian_url}")
        if result.highlights:
            import re

            clean_preview = re.sub(r"<[^>]+>", "", result.highlights)
            typer.echo(f"   Preview: {clean_preview[:100]}...")
        if result.explanation:
            typer.echo(f"   {result.explanation}")
        if result.outgoing_links:
            links_str = "  ".join(f"[[{link}]]" for link in result.outgoing_links)
            typer.echo(f"   Links: {links_str}")
        typer.echo()
