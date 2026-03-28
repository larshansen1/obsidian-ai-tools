"""Command-line interface for obsidian-ai-tools."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from .config import get_settings

if TYPE_CHECKING:
    from .folder_organizer import NoteToMove
    from .tag_hygiene import TagHygienePlan
from .llm import generate_note
from .logging import setup_logging
from .models import ArticleMetadata, VideoMetadata
from .obsidian import write_note
from .youtube import (
    InvalidYouTubeURLError,
    TranscriptUnavailableError,
)

app = typer.Typer(
    name="kai",
    help="Knowledge AI Tools - AI-powered tools for Obsidian knowledge management",
    add_completion=False,
)


@app.command()
def ingest(
    url: Annotated[str, typer.Argument(help="URL or file path to ingest")],
    vault: Annotated[
        str | None,
        typer.Option(
            "--vault",
            "-v",
            help="Override vault path (default: from .env OBSIDIAN_VAULT_PATH)",
        ),
    ] = None,
    prompt_version: Annotated[
        str | None,
        typer.Option(
            "--prompt-version",
            "-p",
            help="Prompt version (default: auto-detected based on source)",
        ),
    ] = None,
    max_pages: Annotated[
        int | None,
        typer.Option(
            "--max-pages",
            help="Maximum pages to extract from PDF (default: 50)",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Enable verbose logging",
        ),
    ] = False,
    transcript_providers: Annotated[
        str | None,
        typer.Option(
            "--transcript-providers",
            help="Override provider order (comma-separated: direct,supadata,decodo)",
        ),
    ] = None,
) -> None:
    """Ingest content into your Obsidian vault.

    Supports:
    - YouTube videos (fetches transcript)
    - Web articles (fetches text content)
    - PDF documents (extracts text, local or remote)
    - Local Markdown files (reads content)

    Fetches content, generates a structured note using LLM,
    and saves it to your vault's inbox folder.

    Examples:
        kai ingest https://www.youtube.com/watch?v=dQw4w9WgXcQ
        kai ingest https://example.com/blog/article
        kai ingest https://example.com/research-paper.pdf
        kai ingest ./documents/paper.pdf --max-pages 30
        kai ingest ./notes/draft.md
    """
    # Setup logging
    setup_logging(verbose)

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        typer.echo("💡 Make sure you have a .env file with required settings.", err=True)
        raise typer.Exit(1) from e

    vault_path = Path(vault) if vault else settings.obsidian_vault_path

    # Provider selection
    from .providers.factory import ProviderFactory

    try:
        provider = ProviderFactory.get_provider(url)
    except ValueError:
        typer.echo("❌ Unknown source type. Please provide a valid URL or file path.", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"🌐 Ingesting {provider.name} content...")
    typer.echo(f"   Source: {url}")

    # Determine default prompt version
    if not prompt_version:
        if provider.name == "youtube":
            prompt_version = "youtube_v2"
        elif provider.name == "file":
            prompt_version = "markdown_v1"
        elif provider.name == "pdf":
            prompt_version = "pdf_v1"
        else:
            prompt_version = "article_v1"

    # Step 1: Fetch content metadata
    metadata: VideoMetadata | ArticleMetadata

    try:
        typer.echo(f"📥 Fetching content using {provider.name} provider...")

        # Show provider order for YouTube if specified
        if provider.name == "youtube" and transcript_providers:
            providers_list = transcript_providers.replace(",", ", ")
            typer.echo(f"   🔍 Trying transcript providers: {providers_list}")

        # Pass parameters to provider if applicable
        kwargs: dict[str, int | str] = {}
        if provider.name == "pdf" and max_pages is not None:
            kwargs["max_pages"] = max_pages
        if provider.name == "youtube" and transcript_providers is not None:
            kwargs["provider_order"] = transcript_providers

        metadata = provider.ingest(url, **kwargs)

        # Log success based on metadata type
        if isinstance(metadata, VideoMetadata):
            # Display provider used for YouTube
            if provider.name == "youtube" and metadata.provider_used:
                typer.echo(
                    f"   ✓ Transcript via {metadata.provider_used} "
                    f"({len(metadata.transcript)} chars)"
                )
            else:
                typer.echo(f"   ✓ Transcript fetched ({len(metadata.transcript)} chars)")
        elif isinstance(metadata, ArticleMetadata):
            typer.echo(f"   ✓ Content fetched: '{metadata.title}' ({len(metadata.content)} chars)")

            # Check for PDF truncation (provider may have added note in content)
            if provider.name == "pdf" and "Only the first" in metadata.content:
                typer.echo("   ⚠️  PDF truncated due to page limit", err=False)

    except InvalidYouTubeURLError as e:
        typer.echo(f"❌ Invalid URL: {e}", err=True)
        raise typer.Exit(1) from e
    except TranscriptUnavailableError as e:
        typer.echo(f"❌ Transcript unavailable: {e}", err=True)
        typer.echo(
            "💡 This video may not have English captions or may be private.",
            err=True,
        )
        raise typer.Exit(1) from e
    except FileNotFoundError as e:
        typer.echo(f"❌ File not found: {e}", err=True)
        raise typer.Exit(1) from e
    except Exception as e:
        typer.echo(f"❌ Failed to fetch content: {e}", err=True)
        raise typer.Exit(1) from e

    # Step 2: Generate note via LLM
    try:
        typer.echo(f"🤖 Generating note with {settings.llm_model} ({prompt_version})...")
        note = generate_note(
            metadata=metadata,
            model=settings.llm_model,
            api_key=settings.openrouter_api_key,
            vault_path=vault_path,  # Pass vault for tag discovery
            max_content_length=settings.max_transcript_length,
            prompt_version=prompt_version,
        )
        typer.echo(f"   ✓ Note generated: '{note.title}'")
        typer.echo(f"   ✓ Tags: {', '.join(note.tags)}")

    except Exception as e:
        typer.echo(f"❌ Failed to generate note: {e}", err=True)
        typer.echo("💡 Check your OpenRouter API key and model configuration.", err=True)
        raise typer.Exit(1) from e

    # Step 3: Write note to vault
    try:
        typer.echo("💾 Writing note to vault...")
        file_path = write_note(
            note=note,
            vault_path=vault_path,
            inbox_folder=settings.obsidian_inbox_folder,
        )
        import logging

        logging.getLogger("obsidian_ai_tools.cli").info(
            "Note persisted to vault",
            extra={
                "file_path": str(file_path),
                "title": note.title,
                "url": url,
            },
        )
        typer.echo(f"   ✓ Note saved to: {file_path}")

    except Exception as e:
        typer.echo(f"❌ Failed to write note: {e}", err=True)
        raise typer.Exit(1) from e

    # Success!
    typer.echo("✅ Ingestion complete!")


@app.command()
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

    from .indexer import build_index
    from .search import SearchQuery, build_whoosh_index, search_notes
    from .wikilinks import count_backlinks

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    # Parse dates if provided
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

    # Check if any search criteria provided
    if not any([keyword, tag, after_date, before_date]):
        typer.echo("❌ No search criteria provided", err=True)
        typer.echo("💡 Use --keyword, --tag, --after, or --before", err=True)
        raise typer.Exit(1)

    typer.echo("🔍 Searching vault...")

    # Build vault index
    vault_index = build_index(vault_path, settings.obsidian_inbox_folder)

    # Build Whoosh index and backlink counts
    index_dir = vault_path / ".kai" / "whoosh_index"
    build_whoosh_index(vault_index, index_dir)
    backlinks = count_backlinks(vault_index)

    # Search
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

    # Display results
    if not results:
        typer.echo("   No results found")
        return

    typer.echo(f"   Found {len(results)} result(s):\n")

    for i, result in enumerate(results, 1):
        note = result.note

        # Calculate relative path from vault root for Obsidian URL
        rel_path = note.file_path.relative_to(vault_path)
        vault_name = vault_path.name
        obsidian_url = f"obsidian://open?vault={vault_name}&file={rel_path}"

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


@app.command()
def list_tags(
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
    by_folder: Annotated[
        bool,
        typer.Option("--by-folder", "-f", help="Group tags by folder"),
    ] = False,
) -> None:
    """List all tags in your vault with counts.

    Examples:
        kai list-tags
        kai list-tags --by-folder
    """
    from .indexer import build_index
    from .search import list_all_tags, list_tags_by_folder

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    # Build vault index
    # Always scan entire vault for tag listing
    vault_index = build_index(vault_path, folder=None)

    if by_folder:
        # List tags grouped by folder
        typer.echo("📋 Listing tags by folder...")

        folder_tags = list_tags_by_folder(vault_index, vault_path)

        if not folder_tags:
            typer.echo("   No tags found")
            return

        typer.echo(f"   Found tags in {len(folder_tags)} folder(s):\n")

        for folder, tag_counts in folder_tags.items():
            typer.echo(f"📁 {folder}/")
            for tag, count in tag_counts.items():
                typer.echo(f"   {tag}: {count} note(s)")
            typer.echo("")  # Empty line between folders
    else:
        # List all tags globally
        typer.echo("📋 Listing tags...")

        tag_counts = list_all_tags(vault_index)

        if not tag_counts:
            typer.echo("   No tags found")
            return

        typer.echo(f"   Found {len(tag_counts)} unique tag(s):\n")

        for tag, count in tag_counts.items():
            typer.echo(f"   {tag}: {count} note(s)")


@app.command()
def rebuild_index(
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Rebuild the tag and content indexes for your vault.

    Forces a complete rebuild of both the vault metadata index and
    the Whoosh search index. Useful when:
    - The index becomes corrupted
    - Tags are changed manually in files
    - Notes are added/modified outside of kai
    - Troubleshooting search issues

    Example:
        kai rebuild-index
    """
    from .indexer import build_index
    from .search import build_whoosh_index

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    typer.echo("🔄 Rebuilding indexes...")

    # Step 1: Rebuild vault index (metadata cache)
    typer.echo("   📋 Rebuilding vault index...")
    vault_index = build_index(
        vault_path,
        folder=None,  # Scan entire vault, not just inbox
        force_rebuild=True,
    )
    typer.echo(f"      ✓ Indexed {len(vault_index.notes)} note(s)")

    # Step 2: Rebuild Whoosh search index
    typer.echo("   🔍 Rebuilding search index...")
    index_dir = vault_path / ".kai" / "whoosh_index"
    build_whoosh_index(vault_index, index_dir)
    typer.echo("      ✓ Search index rebuilt")

    # Success!
    typer.echo("✅ Index rebuild complete!")


@app.command()
def process_inbox(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview changes without executing"),
    ] = False,
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Execute moves (prompts for confirmation unless --yes)"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt (requires --confirm)"),
    ] = False,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Process inbox notes and move them to folders based on tag rules.

    Reads folder_rules.json from vault root to map tags to folders.
    When a note has multiple matching tags, uses scoring to pick best folder.

    Examples:
        kai process-inbox --dry-run              # Preview changes
        kai process-inbox --confirm              # Execute with confirmation
        kai process-inbox --confirm --yes        # Execute without confirmation
    """
    from .folder_organizer import (
        InvalidRulesError,
        PathTraversalError,
        load_folder_rules,
        move_note,
        scan_inbox_notes,
        track_move,
    )

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        typer.echo("💡 Make sure you have a .env file with required settings.", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    # Load folder rules
    try:
        typer.echo("📂 Loading folder rules...")
        rules = load_folder_rules(vault_path)
        typer.echo(f"   ✓ Loaded {len(rules)} rule(s)")
    except (InvalidRulesError, PathTraversalError) as e:
        typer.echo(f"❌ {e}", err=True)
        typer.echo(
            "💡 Create folder_rules.json in your vault root. Example:\n"
            "   {\n"
            '     "ai": "AI & Machine Learning",\n'
            '     "python": "Development/Python"\n'
            "   }",
            err=True,
        )
        raise typer.Exit(1) from e

    # Scan inbox for notes to move
    typer.echo("📥 Scanning inbox for notes...")
    notes, failed_files = scan_inbox_notes(vault_path, settings.obsidian_inbox_folder, rules)

    # Report any parsing failures
    if failed_files:
        typer.echo(
            f"⚠️  Warning: Could not parse {len(failed_files)} file(s): "
            f"{', '.join(failed_files[:5])}"
            + (f" and {len(failed_files) - 5} more" if len(failed_files) > 5 else ""),
            err=True,
        )

    if not notes:
        typer.echo("✅ No notes to move (inbox is empty or no notes match rules)")
        return

    # Display summary
    _display_batch_summary(notes, dry_run)

    # Dry run mode - stop here
    if dry_run:
        return

    # Phase 3: Must confirm to execute
    if not confirm:
        typer.echo("\n⚠️  Add --confirm to execute moves")
        return

    # Prompt for confirmation (skip if --yes)
    if not yes:
        user_confirm = typer.confirm(f"\n❓ Move {len(notes)} note(s)?")
        if not user_confirm:
            typer.echo("❌ Cancelled")
            return

        # Execute moves
        typer.echo("\n💾 Moving notes...")

    results = []
    for note in notes:
        result = move_note(note, vault_path, dry_run)
        results.append(result)

        if not dry_run:
            if result.success:
                track_move(result, vault_path)
                typer.echo(f"   ✓ Moved {result.file} → {result.to_folder}")
            else:
                typer.echo(f"   ✗ Failed to move {result.file}: {result.error}", err=True)

    # Summary
    if not dry_run:
        success_count = sum(1 for r in results if r.success)
        typer.echo(f"\n✅ Successfully moved {success_count}/{len(results)} note(s)")
    else:
        typer.echo(f"\n🔍 Dry run complete - {len(notes)} note(s) would be moved")


def _display_batch_summary(notes: list["NoteToMove"], dry_run: bool = False) -> None:
    """Display summary of planned moves.

    Args:
        notes: List of notes to move
        dry_run: Whether this is a dry run
    """
    if dry_run:
        typer.echo("🔍 DRY RUN - No files will be moved\n")
    else:
        typer.echo(f"📋 Found {len(notes)} note(s) to move:\n")

    for note in notes:
        typer.echo(f"  📄 {note.file_path.name}")
        typer.echo(f"     Tags: {', '.join(note.tags)}")
        # Display all matched tags
        matched_tags_str = ", ".join(note.matched_tags) if note.matched_tags else "none"
        typer.echo(
            f"     → {note.best_folder} (matched: {matched_tags_str}, score: {note.score:.1f})"
        )
        typer.echo()


@app.command()
def stats(
    days: Annotated[
        int,
        typer.Option("--days", "-d", help="Number of days to include in summary"),
    ] = 30,
    recent: Annotated[
        bool,
        typer.Option("--recent", "-r", help="Show recent individual requests"),
    ] = False,
) -> None:
    """Show LLM API cost statistics.

    Displays cost summary from the observability database including:
    - Total costs for the period
    - Costs breakdown by model
    - Costs breakdown by source type
    -Costs breakdown by operation
    - Recent costs (last 7 days)

    Use --recent to see individual requests with details.

    Examples:
        kai stats
        kai stats --days 7
        kai stats --recent
    """
    from .config import get_settings
    from .observability import ObservabilityDB

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    db_path = settings.obsidian_vault_path / ".kai" / "observability.duckdb"
    obs_db = ObservabilityDB(db_path)

    if recent:
        # Show recent individual requests
        typer.echo("📝 Recent Requests")
        typer.echo("━" * 80)

        records = obs_db.get_recent_costs(limit=20)
        if not records:
            typer.echo("No cost records found")
            return

        for record in records:
            typer.echo(
                f"{record['timestamp'][:19]} | {record['source_type'] or 'N/A':8} | "
                f"${record['total_cost_usd']:.5f} | "
                f"{record['input_tokens']:5}→{record['output_tokens']:5} tokens"
            )
            if record["source_url"]:
                typer.echo(f"  → {record['source_url']}")
        return

    # Get cost summary
    summary = obs_db.get_cost_summary(days=days)

    # Display results
    typer.echo(f"💰 Cost Summary (Last {days} days)")
    typer.echo("━" * 40)
    typer.echo(f"Total: ${summary['total_cost']:.4f}")
    typer.echo()

    if summary["by_source_type"]:
        typer.echo("By Source Type:")
        total = summary["total_cost"]
        for source_type, cost, count in summary["by_source_type"]:
            percentage = (cost / total * 100) if total > 0 else 0
            typer.echo(f"  {source_type}: ${cost:.4f} ({percentage:.1f}%) - {count} requests")
        typer.echo()

    if summary["by_model"]:
        typer.echo("By Model:")
        total = summary["total_cost"]
        for model, cost in summary["by_model"]:
            percentage = (cost / total * 100) if total > 0 else 0
            typer.echo(f"  {model}: ${cost:.4f} ({percentage:.1f}%)")
        typer.echo()

    if summary["by_operation"]:
        typer.echo("By Operation:")
        total = summary["total_cost"]
        for operation, cost in summary["by_operation"]:
            percentage = (cost / total * 100) if total > 0 else 0
            typer.echo(f"  {operation}: ${cost:.4f} ({percentage:.1f}%)")
        typer.echo()

    typer.echo(f"Recent (Last 7 days): ${summary['recent_cost_7days']:.4f}")


@app.command()
def quality(
    days: Annotated[
        int,
        typer.Option("--days", "-d", help="Number of days to include in summary"),
    ] = 30,
) -> None:
    """Show ingestion quality metrics.

    Displays quality metrics from the observability database including:
    - Success rates by source type
    - Average processing times
    - Common errors

    Examples:
        kai quality
        kai quality --days 7
        kai quality --days 90
    """
    from .config import get_settings
    from .observability import ObservabilityDB

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    db_path = settings.obsidian_vault_path / ".kai" / "observability.duckdb"
    obs_db = ObservabilityDB(db_path)

    # Get quality summary
    summary = obs_db.get_quality_summary(days=days)

    # Display results
    typer.echo(f"📊 Quality Metrics (Last {days} days)")
    typer.echo("━" * 40)
    typer.echo(f"Total Ingestions: {summary['total_ingestions']}")
    typer.echo(
        f"Success Rate: {summary['success_rate']:.1f}% "
        f"({summary['successes']}/{summary['total_ingestions']})"
    )
    typer.echo()

    if summary["by_source"]:
        typer.echo("By Source Type:")
        for source in summary["by_source"]:
            rate = (source["successes"] / source["total"] * 100) if source["total"] > 0 else 0
            typer.echo(
                f"  {source['source_type']}: {rate:.1f}% "
                f"({source['successes']}/{source['total']}) "
                f"- avg {source['avg_duration']:.1f}s"
            )
        typer.echo()

    if summary["common_errors"]:
        typer.echo("Common Errors:")
        for error, count in summary["common_errors"]:
            typer.echo(f"  {count}. {error}")


@app.command()
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
    from .digest import (
        format_digest_json,
        format_digest_markdown,
        format_digest_terminal,
        generate_digest,
    )

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    typer.echo(f"📊 Generating digest for last {days} day(s)...")

    # Generate digest
    try:
        report = generate_digest(
            vault_path=vault_path,
            since_days=days,
            inbox_folder=settings.obsidian_inbox_folder,
        )
    except Exception as e:
        typer.echo(f"❌ Failed to generate digest: {e}", err=True)
        raise typer.Exit(1) from e

    # Format output
    if format_type == "json":
        formatted = format_digest_json(report)
    elif format_type == "markdown":
        formatted = format_digest_markdown(report)
    else:
        formatted = format_digest_terminal(report)

    # Output handling
    if output:
        # Save to vault inbox
        inbox_path = vault_path / settings.obsidian_inbox_folder

        # Use provided filename, add .md extension if not present
        if output.endswith(".md"):
            filename = output
        else:
            filename = f"{output}.md"

        output_path = inbox_path / filename

        # Use markdown format for file output
        file_content = format_digest_markdown(report)
        output_path.write_text(file_content)

        typer.echo(f"✅ Digest saved to: {output_path}")
    else:
        # Print to terminal
        typer.echo("")
        typer.echo(formatted)


@app.command()
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
    from .overview import (
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


@app.command()
def follow(
    note_title: Annotated[str, typer.Argument(help="Note title or wikilink target to resolve")],
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Resolve a wikilink target and print the full note content.

    Matches by title first (case-insensitive), then by filename stem.
    Prints frontmatter + body to stdout, suitable for piping to an agent.

    Examples:
        kai follow "Attention Mechanisms"
        kai follow "attention-mechanisms"
    """
    from .indexer import build_index
    from .wikilinks import resolve_wikilink

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    try:
        vault_index = build_index(vault_path, folder=None)
    except Exception as e:
        typer.echo(f"❌ Failed to build vault index: {e}", err=True)
        raise typer.Exit(1) from e

    note = resolve_wikilink(note_title, vault_index)
    if note is None:
        typer.echo(f"❌ No note found matching '{note_title}'", err=True)
        raise typer.Exit(1)

    try:
        content = note.file_path.read_text(encoding="utf-8")
    except OSError as e:
        typer.echo(f"❌ Could not read note file: {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(content, nl=False)


@app.command()
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
    import sys
    import time

    from .config import get_settings
    from .observability import ObservabilityDB
    from .preview import (
        PreviewError,
        PreviewInfo,
        ReadingListEntry,
        UnsupportedURLError,
        format_preview_json,
        format_preview_terminal,
        generate_preview,
        save_to_reading_list,
    )

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    # Determine URLs to process
    urls: list[str] = []

    if batch:
        # Read URLs from stdin
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

    # Process each URL
    previews: list[PreviewInfo] = []
    db_path = settings.obsidian_vault_path / ".kai" / "observability.duckdb"

    for target_url in urls:
        start_time = time.time()

        try:
            typer.echo(f"\n🔍 Previewing: {target_url[:80]}...")
            preview_info = generate_preview(target_url)
            previews.append(preview_info)
            duration = time.time() - start_time

            # Record success metric
            try:
                obs_db = ObservabilityDB(db_path)
                obs_db.record_metric(
                    source_type=preview_info.source_type,
                    outcome="success",
                    duration_seconds=duration,
                    provider_used="preview",
                )
            except Exception:
                pass  # Never fail preview due to observability

            # Format output
            if format_type == "json":
                typer.echo(format_preview_json(preview_info))
            else:
                typer.echo(format_preview_terminal(preview_info))

            # Interactive mode
            if interactive and not batch:
                typer.echo("\n  Actions:")
                typer.echo("    [i] Ingest now")
                typer.echo("    [s] Save to reading list")
                typer.echo("    [x] Skip")

                choice = typer.prompt("  Choice", default="x")

                if choice.lower() == "i":
                    # Ingest immediately
                    typer.echo("\n🌐 Starting ingestion...")
                    # Call ingest command programmatically
                    from typer.testing import CliRunner

                    runner = CliRunner()
                    result = runner.invoke(app, ["ingest", target_url, "--vault", str(vault_path)])
                    typer.echo(result.output)
                elif choice.lower() == "s":
                    # Save to reading list
                    entry = ReadingListEntry(url=target_url, preview=preview_info)
                    save_to_reading_list(entry, vault_path)
                    typer.echo("  ✓ Saved to reading list")
                else:
                    typer.echo("  ✓ Skipped")

        except UnsupportedURLError as e:
            typer.echo(f"⚠️  Unsupported URL type: {e}", err=True)
            duration = time.time() - start_time
            try:
                obs_db = ObservabilityDB(db_path)
                obs_db.record_metric(
                    source_type="unknown",
                    outcome="failure",
                    duration_seconds=duration,
                    error_type="UnsupportedURLError",
                    provider_used="preview",
                )
            except Exception:
                pass
        except PreviewError as e:
            typer.echo(f"⚠️  Preview failed: {e}", err=True)
            duration = time.time() - start_time
            try:
                obs_db = ObservabilityDB(db_path)
                obs_db.record_metric(
                    source_type="unknown",
                    outcome="failure",
                    duration_seconds=duration,
                    error_type="PreviewError",
                    provider_used="preview",
                )
            except Exception:
                pass

    # Summary for batch mode
    if batch and len(urls) > 1:
        typer.echo(f"\n✅ Previewed {len(previews)}/{len(urls)} URL(s)")
        if previews:
            total_cost = sum(p.estimated_cost_usd for p in previews)
            typer.echo(f"   Total estimated cost: ${total_cost:.4f}")


# =============================================================================
# Reading List Command Group
# =============================================================================

reading_list_app = typer.Typer(
    name="reading-list",
    help="Manage your reading list of saved URLs",
)
app.add_typer(reading_list_app, name="reading-list")


@reading_list_app.command("list")
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
    from .preview import load_reading_list

    # Load settings
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

    # Filter by status if specified
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


@reading_list_app.command("ingest")
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
    from .preview import load_reading_list, update_reading_list_status

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    entries = load_reading_list(vault_path)

    # Get pending items
    pending = [e for e in entries if e.status == "pending"]

    if not pending:
        typer.echo("✅ No pending items in reading list")
        return

    # Determine which items to ingest
    to_ingest = pending if all_pending else pending[:1]

    typer.echo(f"📥 Ingesting {len(to_ingest)} item(s)...\n")

    for entry in to_ingest:
        typer.echo(f"🔄 Ingesting: {entry.preview.title[:50]}...")
        typer.echo(f"   URL: {entry.url}")

        # Call ingest via CLI runner
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["ingest", entry.url, "--vault", str(vault_path)])

        if result.exit_code == 0:
            update_reading_list_status(entry.url, "ingested", vault_path)
            typer.echo("   ✅ Ingested successfully\n")
        else:
            typer.echo("   ❌ Failed to ingest\n")
            typer.echo(result.output)

    # Summary
    remaining = len(pending) - len(to_ingest)
    typer.echo(f"✅ Ingested {len(to_ingest)} item(s). {remaining} pending remaining.")


@reading_list_app.command("clear")
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
    from .preview import load_reading_list

    # Load settings
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

    # Filter entries to keep
    if status == "all":
        to_remove = entries
        to_keep: list = []
    else:
        to_remove = [e for e in entries if e.status == status]
        to_keep = [e for e in entries if e.status != status]

    if not to_remove:
        typer.echo(f"📋 No items with status '{status}' to clear")
        return

    # Phase 3: Must confirm to execute
    if not confirm:
        typer.echo("\n⚠️  Add --confirm to clear items")
        return

    # Prompt for confirmation (skip if --yes)
    if not yes:
        if not typer.confirm(f"Remove {len(to_remove)} item(s) with status '{status}'?"):
            typer.echo("❌ Cancelled")
            return

    # Write back
    list_path = vault_path / ".kai" / "reading_list.jsonl"
    with open(list_path, "w", encoding="utf-8") as f:
        for entry in to_keep:
            f.write(entry.model_dump_json() + "\n")

    typer.echo(f"✅ Cleared {len(to_remove)} item(s). {len(to_keep)} remaining.")


# =============================================================================
# Concept Linking Command
# =============================================================================


@app.command()
def connect(
    note: Annotated[
        str | None,
        typer.Option("--note", "-n", help="Path to note (relative to vault)"),
    ] = None,
    folder: Annotated[
        str | None,
        typer.Option("--folder", "-f", help="Scan folder for all connections"),
    ] = None,
    orphans: Annotated[
        bool,
        typer.Option("--orphans", help="Find orphan notes with no links"),
    ] = False,
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", help="Minimum similarity score (0-1)"),
    ] = 0.3,
    top_n: Annotated[
        int,
        typer.Option("--top", help="Maximum suggestions per note"),
    ] = 5,
    auto_link: Annotated[
        bool,
        typer.Option("--auto-link", help="Auto-insert wikilinks"),
    ] = False,
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Confirm before modifying files"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompts (requires --confirm)"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview changes without modifying"),
    ] = False,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Find related notes and suggest connections.

    Uses TF-IDF similarity to discover notes with similar content.
    Can scan a folder for all connections, a single note, or detect orphans.

    Examples:
        kai connect --folder "AI/LLMs"
        kai connect --note "AI/Attention.md"
        kai connect --orphans
        kai connect --folder "AI" --auto-link --confirm
    """
    from .concept_linking import ConceptLinker, find_orphan_notes
    from .indexer import build_index, scan_vault

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    # Folder scanning mode
    if folder:
        folder_path = vault_path / folder
        if not folder_path.exists():
            typer.echo(f"❌ Folder not found: {folder_path}", err=True)
            raise typer.Exit(1)

        typer.echo(f"📁 Scanning folder: {folder}")

        # Scan only the specified folder
        notes = scan_vault(vault_path, folder=folder)
        if not notes:
            typer.echo("   No notes found in folder")
            return

        typer.echo(f"   Found {len(notes)} notes")

        # Create a temporary vault index for this folder
        from .indexer import VaultIndex

        folder_index = VaultIndex(notes=notes, index_path=vault_path / ".kai" / "temp_index.json")

        linker = ConceptLinker(folder_index)
        typer.echo("   Building TF-IDF index...")
        linker.build_tfidf_index()

        suggestions = linker.find_all_connections(threshold=threshold)

        if not suggestions:
            typer.echo("\n✅ No connections found above threshold")
            return

        typer.echo(f"\n🔗 Found {len(suggestions)} potential connection(s):\n")

        for i, suggestion in enumerate(suggestions[:20], 1):  # Limit display
            source_rel = suggestion.source_note.relative_to(vault_path)
            typer.echo(
                f"  {i}. {source_rel.stem} → {suggestion.target_title} "
                f"({suggestion.similarity_score:.2f})"
            )
            if suggestion.keywords_shared:
                keywords = ", ".join(suggestion.keywords_shared[:3])
                typer.echo(f"     Keywords: {keywords}")

        if len(suggestions) > 20:
            typer.echo(f"\n   ... and {len(suggestions) - 20} more")

        if auto_link:
            # Group suggestions by source note
            from collections import defaultdict

            by_source: dict[Path, list] = defaultdict(list)
            for suggestion in suggestions:
                by_source[suggestion.source_note].append(suggestion)

            if dry_run:
                typer.echo(f"\n🔍 DRY RUN - Would insert links into {len(by_source)} notes:")
                for source_path, source_suggestions in by_source.items():
                    source_rel = source_path.relative_to(vault_path)
                    links = [f"[[{s.target_title}]]" for s in source_suggestions]
                    typer.echo(f"   {source_rel.stem}: {', '.join(links)}")
                return

            # Phase 3: Must confirm to execute
            if not confirm:
                typer.echo("\n⚠️  Add --confirm to insert links")
                return

            # Prompt for confirmation (skip if --yes)
            if not yes:
                proceed = typer.confirm(
                    f"Insert {len(suggestions)} link(s) into {len(by_source)} note(s)?"
                )
                if not proceed:
                    typer.echo("❌ Cancelled")
                    return

            # Insert links into each source note
            total_inserted = 0
            for source_path, source_suggestions in by_source.items():
                links = linker.insert_wikilinks(source_path, source_suggestions, dry_run=False)
                total_inserted += len(links)

            typer.echo(f"\n✅ Inserted {total_inserted} link(s) into {len(by_source)} note(s)")

        return

    # Build vault index (entire vault) for single note / orphan modes
    typer.echo("📋 Building vault index...")
    vault_index = build_index(vault_path, folder=None)
    typer.echo(f"   Indexed {len(vault_index.notes)} notes")

    if orphans:
        # Find orphan notes
        typer.echo("\n🔍 Finding orphan notes...")
        orphan_notes = find_orphan_notes(vault_index)

        if not orphan_notes:
            typer.echo("✅ No orphan notes found - all notes are connected!")
            return

        typer.echo(f"\nFound {len(orphan_notes)} orphan note(s):\n")
        for i, orphan in enumerate(orphan_notes, 1):
            rel_path = orphan.file_path.relative_to(vault_path)
            typer.echo(f"  {i}. {orphan.title}")
            typer.echo(f"     Path: {rel_path}")
        return

    if not note:
        typer.echo("❌ Please specify --note, --folder, or --orphans", err=True)
        raise typer.Exit(1)

    # Resolve note path
    if not note.endswith(".md"):
        note = note + ".md"

    note_path = vault_path / note
    if not note_path.exists():
        typer.echo(f"❌ Note not found: {note_path}", err=True)
        raise typer.Exit(1)

    # Find connections
    typer.echo(f"\n🔗 Finding connections for: {note}")

    linker = ConceptLinker(vault_index)
    typer.echo("   Building TF-IDF index...")
    linker.build_tfidf_index()

    suggestions = linker.find_similar(note_path, top_n=top_n, threshold=threshold)

    if not suggestions:
        typer.echo("\n   No similar notes found above threshold")
        return

    typer.echo(f"\nFound {len(suggestions)} potential connection(s):\n")

    for i, suggestion in enumerate(suggestions, 1):
        rel_path = suggestion.target_note.relative_to(vault_path)
        typer.echo(f"  {i}. {suggestion.target_title} ({suggestion.similarity_score:.2f})")
        typer.echo(f"     Path: {rel_path}")
        if suggestion.keywords_shared:
            keywords = ", ".join(suggestion.keywords_shared)
            typer.echo(f"     Keywords: {keywords}")
        typer.echo()

    # Auto-link if requested
    if auto_link:
        if dry_run:
            typer.echo("🔍 DRY RUN - Would insert these links:")
            links = linker.insert_wikilinks(note_path, suggestions, dry_run=True)
            for link in links:
                typer.echo(f"   {link}")
            return

        # Phase 3: Must confirm to execute
        if not confirm:
            typer.echo("\n⚠️  Add --confirm to insert links")
            return

        # Prompt for confirmation (skip if --yes)
        if not yes:
            proceed = typer.confirm(f"Insert {len(suggestions)} wikilink(s)?")
            if not proceed:
                typer.echo("❌ Cancelled")
                return

        links = linker.insert_wikilinks(note_path, suggestions, dry_run=False)
        typer.echo(f"✅ Inserted {len(links)} wikilink(s)")


# =============================================================================
# Smart Re-Processing Command
# =============================================================================


@app.command()
def refresh(
    prompt_version: Annotated[
        str,
        typer.Option("--prompt-version", "-p", help="Target prompt version (e.g., youtube_v2)"),
    ],
    tag: Annotated[
        str | None,
        typer.Option("--tag", "-t", help="Filter by tag"),
    ] = None,
    current_version: Annotated[
        str | None,
        typer.Option("--current", "-c", help="Only refresh notes with this prompt version"),
    ] = None,
    since: Annotated[
        int | None,
        typer.Option("--since", "-s", help="Only notes older than N days"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List candidates without refreshing"),
    ] = False,
    show_diff: Annotated[
        str | None,
        typer.Option("--show-diff", help="Show preview for a specific note path"),
    ] = None,
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Execute refresh (creates backups)"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompts (requires --confirm)"),
    ] = False,
    no_backup: Annotated[
        bool,
        typer.Option("--no-backup", help="Skip backup creation (dangerous)"),
    ] = False,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Re-process notes with a new prompt version.

    Update old notes generated with outdated prompts to use improved prompt templates.
    Creates backups before modifying files.

    Examples:
        kai refresh -p youtube_v2 --dry-run                    # List candidates
        kai refresh -p youtube_v2 --tag ai --dry-run           # Filter by tag
        kai refresh -p youtube_v2 --show-diff "AI/Attention.md"  # Preview changes
        kai refresh -p youtube_v2 --tag ai --confirm           # Execute refresh
    """
    from .refresh import (
        estimate_refresh_cost,
        find_refresh_candidates,
        parse_frontmatter,
        refresh_batch,
    )

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    # Show-diff mode: preview a single note
    if show_diff:
        note_path = vault_path / show_diff
        if not note_path.exists():
            typer.echo(f"❌ Note not found: {note_path}", err=True)
            raise typer.Exit(1)

        typer.echo(f"🔍 Preview for: {show_diff}")
        typer.echo("━" * 50)

        # Parse current frontmatter
        metadata = parse_frontmatter(note_path)
        current_ver = metadata.get("prompt_version", "unknown")
        source_url = metadata.get("source_url")
        source_type = metadata.get("source_type")

        typer.echo(f"   Current version: {current_ver}")
        typer.echo(f"   Target version:  {prompt_version}")
        typer.echo(f"   Source URL:      {source_url}")
        typer.echo(f"   Source type:     {source_type}")
        typer.echo()

        if not source_url:
            typer.echo("❌ Note has no source_url - cannot refresh", err=True)
            raise typer.Exit(1)

        typer.echo("💡 To refresh this note, run:")
        typer.echo(f'   kai refresh -p {prompt_version} --show-diff "{show_diff}" --confirm')
        return

    # Find candidates
    typer.echo(f"🔍 Searching for notes to refresh to {prompt_version}...")

    candidates = find_refresh_candidates(
        vault_path=vault_path,
        target_version=prompt_version,
        tag=tag,
        current_version=current_version,
        since_days=since,
    )

    if not candidates:
        typer.echo("✅ No notes found matching criteria")
        return

    # Cost estimation
    estimated_cost = estimate_refresh_cost(candidates, settings.llm_model)

    typer.echo(f"\n📋 Found {len(candidates)} note(s) eligible for refresh:\n")

    # Display candidates
    for i, candidate in enumerate(candidates[:20], 1):
        rel_path = candidate.file_path.relative_to(vault_path)
        typer.echo(
            f"  {i}. {candidate.title[:50]}"
            f" ({candidate.current_prompt_version} → {candidate.target_prompt_version})"
        )
        typer.echo(f"     Path: {rel_path}")

    if len(candidates) > 20:
        typer.echo(f"\n   ... and {len(candidates) - 20} more")

    typer.echo(f"\n💰 Estimated cost: ${estimated_cost:.4f}")

    # Dry-run mode: stop here
    if dry_run:
        typer.echo("\n🔍 DRY RUN - No changes made")
        typer.echo("💡 Add --confirm to execute refresh")
        return

    # Must explicitly confirm
    if not confirm:
        typer.echo("\n⚠️  Add --confirm to execute refresh")
        return

    # Warning for no-backup (skip if --yes)
    if no_backup and not yes:
        proceed = typer.confirm(
            "⚠️  --no-backup: Files will be overwritten without backup. Continue?"
        )
        if not proceed:
            typer.echo("❌ Cancelled")
            return

    # Execute refresh
    typer.echo(f"\n🔄 Refreshing {len(candidates)} note(s)...")

    summary = refresh_batch(
        candidates=candidates,
        vault_path=vault_path,
        model=settings.llm_model,
        api_key=settings.openrouter_api_key,
        create_backup_file=not no_backup,
    )

    # Display results
    typer.echo("\n✅ Refresh complete!")
    typer.echo(f"   Refreshed: {summary.refreshed}/{summary.total_candidates}")
    typer.echo(f"   Skipped:   {summary.skipped}")
    typer.echo(f"   Cost:      ${summary.total_cost_usd:.4f}")

    if summary.errors:
        typer.echo(f"\n⚠️  Errors ({len(summary.errors)}):")
        for error in summary.errors[:5]:
            typer.echo(f"   - {error}")
        if len(summary.errors) > 5:
            typer.echo(f"   ... and {len(summary.errors) - 5} more")


# =============================================================================
# Tag Hygiene Command
# =============================================================================


@app.command("tags")
def tags_command(
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Execute fixes (prompts for confirmation unless --yes)"),
    ] = False,
    plan: Annotated[
        bool,
        typer.Option("--plan", help="Output JSON plan for review"),
    ] = False,
    apply_file: Annotated[
        Path | None,
        typer.Option("--apply", help="Apply fixes from plan file"),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Auto-accept all suggestions (requires --confirm)"),
    ] = False,
    check: Annotated[
        str | None,
        typer.Option("--check", "-c", help="Run specific check: similar, cooccurrence, orphans"),
    ] = None,
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", help="Similarity threshold for tag matching (0-1)"),
    ] = 0.8,
    min_overlap: Annotated[
        int,
        typer.Option("--min-overlap", help="Minimum co-occurrence count to report"),
    ] = 3,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Analyze and fix tag hygiene issues.

    Detects near-duplicate tags, high co-occurrence patterns,
    and orphan tags. Can automatically apply consolidation fixes.

    Examples:
        kai tags                        # Show issues (read-only)
        kai tags --confirm              # Interactive fixes
        kai tags --confirm --yes        # Auto-fix all
        kai tags --plan > plan.json     # Generate plan
        kai tags --apply plan.json      # Apply plan
        kai tags --check similar        # Run only similar tag check
    """
    from .indexer import build_index
    from .tag_hygiene import (
        TagHygienePlan,
        apply_plan,
        generate_plan,
    )

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    # Mode: Apply from plan file
    if apply_file:
        typer.echo(f"📋 Loading plan from {apply_file}...")
        try:
            plan_obj = TagHygienePlan.from_file(apply_file)
        except Exception as e:
            typer.echo(f"❌ Failed to load plan: {e}", err=True)
            raise typer.Exit(1) from e

        # Count all applicable actions
        consolidations_to_apply = [c for c in plan_obj.consolidations if c.apply]
        orphans_to_remove = [o for o in plan_obj.orphan_tags if o.remove]
        cooc_merges = [c for c in plan_obj.high_cooccurrence if c.merge_into]

        total_actions = len(consolidations_to_apply) + len(orphans_to_remove) + len(cooc_merges)

        typer.echo(f"   Found {len(consolidations_to_apply)} consolidation(s)")
        typer.echo(f"   Found {len(orphans_to_remove)} orphan tag(s) to remove")
        if cooc_merges:
            typer.echo(f"   Found {len(cooc_merges)} co-occurrence merge(s)")

        if total_actions == 0:
            typer.echo("✅ No actions marked for apply")
            return

        if not yes:
            confirm = typer.confirm(f"Apply {total_actions} action(s)?")
            if not confirm:
                typer.echo("❌ Cancelled")
                return

        typer.echo("🔧 Applying changes...")
        modified, skipped = apply_plan(plan_obj, create_backups=True)
        typer.echo(f"✅ Done: {modified} notes modified, {skipped} skipped")
        return

    # Build vault index (scan entire vault)
    if not plan:
        typer.echo("🔍 Analyzing vault tags...")
    vault_index = build_index(vault_path, folder=None)

    # Generate plan
    plan_obj = generate_plan(
        vault_index,
        similarity_threshold=threshold,
        min_cooccurrence=min_overlap,
    )

    # Filter by check type if specified
    if check:
        if check == "similar":
            plan_obj.high_cooccurrence = []
            plan_obj.orphan_tags = []
        elif check == "cooccurrence":
            plan_obj.similar_tags = []
            plan_obj.consolidations = []
            plan_obj.orphan_tags = []
        elif check == "orphans":
            plan_obj.similar_tags = []
            plan_obj.consolidations = []
            plan_obj.high_cooccurrence = []
        else:
            typer.echo(f"❌ Unknown check type: {check}", err=True)
            typer.echo("💡 Valid options: similar, cooccurrence, orphans", err=True)
            raise typer.Exit(1)

    # Mode: Output JSON plan
    if plan:
        typer.echo(plan_obj.to_json())
        return

    # Display analysis results
    _display_tag_hygiene_report(plan_obj)

    # Check if there's anything to fix
    if not plan_obj.consolidations:
        typer.echo("\n✅ No consolidations needed")
        return

    # Phase 3: Must confirm to execute
    if not confirm:
        typer.echo("\n⚠️  Add --confirm to apply fixes")
        return

    # Mode: Interactive fix
    _interactive_fix(plan_obj, yes)


def _display_tag_hygiene_report(plan: "TagHygienePlan") -> None:  # noqa: F821
    """Display tag hygiene analysis report."""

    typer.echo("\n→ Tag Hygiene Report\n")

    # Similar tags
    if plan.similar_tags:
        typer.echo("🔤 Similar Tags (consider consolidating):\n")
        for group in plan.similar_tags:
            canonical_msg = f"  {group.canonical} ({group.total_notes} notes total)"
            typer.echo(f"{canonical_msg} ← suggested canonical")
            for variant in group.variants:
                score = group.similarity_scores.get(variant, 0)
                typer.echo(f"    └── {variant} (similarity: {score:.2f})")
            typer.echo()
    else:
        typer.echo("🔤 Similar Tags: None found\n")

    # High co-occurrence
    if plan.high_cooccurrence:
        typer.echo("🔗 High Co-occurrence (often used together):\n")
        for cooc in plan.high_cooccurrence[:5]:  # Limit to top 5
            pct = cooc.jaccard_similarity * 100
            typer.echo(
                f"  {cooc.tag_a} + {cooc.tag_b}: "
                f"{cooc.co_occurrence_count} notes ({pct:.0f}% overlap)"
            )
        if len(plan.high_cooccurrence) > 5:
            typer.echo(f"  ... and {len(plan.high_cooccurrence) - 5} more")
        typer.echo()

    # Orphan tags
    if plan.orphan_tags:
        preview = [o.tag for o in plan.orphan_tags[:10]]
        typer.echo(f"👻 Orphan Tags (used once): {len(plan.orphan_tags)} total")
        typer.echo(f"   {', '.join(preview)}")
        if len(plan.orphan_tags) > 10:
            typer.echo(f"   ... and {len(plan.orphan_tags) - 10} more")
        typer.echo()

    # Summary
    typer.echo("─" * 50)
    typer.echo(
        f"📊 Summary: {len(plan.consolidations)} consolidation(s), "
        f"{len(plan.high_cooccurrence)} high-overlap pair(s), "
        f"{len(plan.orphan_tags)} orphan tag(s)"
    )


def _interactive_fix(plan: "TagHygienePlan", auto_yes: bool = False) -> None:  # noqa: F821
    """Interactive consolidation flow."""
    from .tag_hygiene import apply_plan

    typer.echo("\n🔧 Interactive Fix Mode\n")

    applied_count = 0
    skipped_count = 0

    for i, consolidation in enumerate(plan.consolidations, 1):
        typer.echo(f"{i}. Merge: {', '.join(consolidation.from_tags)} → {consolidation.to_tag}")
        typer.echo(f"   Affects: {consolidation.note_count} note(s)")

        if auto_yes:
            response = "y"
        else:
            response = typer.prompt(
                "   Apply? [Y/n/s(kip all)/q(uit)]",
                default="y",
                show_default=False,
            ).lower()

        if response == "q":
            typer.echo("   Quitting...")
            break
        elif response == "s":
            typer.echo("   Skipping remaining...")
            for remaining in plan.consolidations[i - 1 :]:
                remaining.apply = False
            break
        elif response in ("n", "no"):
            consolidation.apply = False
            skipped_count += 1
            typer.echo("   ⊘ Skipped")
        else:
            # Apply this consolidation
            consolidation.apply = True
            applied_count += 1
            typer.echo("   ✓ Marked for apply")

        typer.echo()

    # Now apply all marked consolidations
    if applied_count > 0:
        typer.echo(f"📝 Applying {applied_count} consolidation(s)...")
        modified, failed = apply_plan(plan, create_backups=True)
        typer.echo(f"✅ Done: {modified} notes modified")
    else:
        typer.echo("✅ No consolidations applied")


@app.command()
def version() -> None:
    """Show version information."""
    typer.echo("obsidian-ai-tools v1.0.0")
    typer.echo("Knowledge AI Tools for Obsidian")


if __name__ == "__main__":
    app()
