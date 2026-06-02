"""rebuild-index, process-inbox, update-rules, stats, quality, follow, connect, refresh commands."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from ..config import get_settings

if TYPE_CHECKING:
    from ..folder_organizer import NoteToMove, RuleSuggestion


def register(app: typer.Typer) -> None:
    app.command()(rebuild_index)
    app.command()(process_inbox)
    app.command()(update_rules)
    app.command()(stats)
    app.command()(quality)
    app.command()(follow)
    app.command()(connect)
    app.command()(refresh)


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
    from ..indexer import build_index
    from ..search import build_whoosh_index

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    typer.echo("🔄 Rebuilding indexes...")
    typer.echo("   📋 Rebuilding vault index...")
    vault_index = build_index(vault_path, folder=None, force_rebuild=True)
    typer.echo(f"      ✓ Indexed {len(vault_index.notes)} note(s)")
    typer.echo("   🔍 Rebuilding search index...")
    index_dir = vault_path / ".kai" / "whoosh_index"
    build_whoosh_index(vault_index, index_dir)
    typer.echo("      ✓ Search index rebuilt")
    typer.echo("✅ Index rebuild complete!")


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
    from ..folder_organizer import (
        InvalidRulesError,
        PathTraversalError,
        load_folder_rules,
        move_note,
        scan_inbox_notes,
        track_move,
    )

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        typer.echo("💡 Make sure you have a .env file with required settings.", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

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

    typer.echo("📥 Scanning inbox for notes...")
    notes, failed_files = scan_inbox_notes(vault_path, settings.obsidian_inbox_folder, rules)

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

    _display_batch_summary(notes, dry_run)

    if dry_run:
        return

    if not confirm:
        typer.echo("\n⚠️  Add --confirm to execute moves")
        return

    if not yes:
        user_confirm = typer.confirm(f"\n❓ Move {len(notes)} note(s)?")
        if not user_confirm:
            typer.echo("❌ Cancelled")
            return
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

    if not dry_run:
        success_count = sum(1 for r in results if r.success)
        typer.echo(f"\n✅ Successfully moved {success_count}/{len(results)} note(s)")
    else:
        typer.echo(f"\n🔍 Dry run complete - {len(notes)} note(s) would be moved")


def update_rules(
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Update folder_rules.json (prompts unless --yes)"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt (requires --confirm)"),
    ] = False,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
    min_notes: Annotated[
        int,
        typer.Option("--min-notes", help="Minimum unprocessed inbox notes a tag must appear in"),
    ] = 2,
    max_suggestions: Annotated[
        int,
        typer.Option("--max-suggestions", help="Maximum rule suggestions to show"),
    ] = 10,
    include_singletons: Annotated[
        bool,
        typer.Option("--include-singletons", help="Include tags used by only one unprocessed note"),
    ] = False,
) -> None:
    """Suggest and optionally add folder rules for unprocessed inbox notes.

    Scans inbox notes that do not match any existing rule in folder_rules.json.
    Missing tags are suggested as new tag-to-folder mappings.

    Examples:
        kai update-rules
        kai update-rules --include-singletons
        kai update-rules --confirm
        kai update-rules --confirm --yes
    """
    from ..folder_organizer import (
        InvalidRulesError,
        PathTraversalError,
        load_folder_rules_or_empty,
        suggest_folder_rules,
        update_folder_rules,
    )

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        typer.echo("💡 Make sure you have a .env file with required settings.", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    effective_min_notes = 1 if include_singletons else min_notes
    if effective_min_notes < 1:
        typer.echo("❌ --min-notes must be at least 1", err=True)
        raise typer.Exit(1)
    if max_suggestions < 1:
        typer.echo("❌ --max-suggestions must be at least 1", err=True)
        raise typer.Exit(1)

    try:
        typer.echo("📂 Loading folder rules...")
        rules = load_folder_rules_or_empty(vault_path)
        if rules:
            typer.echo(f"   ✓ Loaded {len(rules)} rule(s)")
        else:
            typer.echo("   No folder_rules.json found; suggestions will create it")

        typer.echo("📥 Scanning inbox for unprocessed notes...")
        suggestions, failed_files = suggest_folder_rules(
            vault_path,
            settings.obsidian_inbox_folder,
            rules,
            min_notes=effective_min_notes,
            max_suggestions=max_suggestions,
        )
    except (InvalidRulesError, PathTraversalError) as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1) from e

    if failed_files:
        typer.echo(
            f"⚠️  Warning: Could not parse {len(failed_files)} file(s): "
            f"{', '.join(failed_files[:5])}"
            + (f" and {len(failed_files) - 5} more" if len(failed_files) > 5 else ""),
            err=True,
        )

    if not suggestions:
        typer.echo("✅ No rule suggestions (inbox is empty or all notes already match rules)")
        return

    _display_rule_suggestions(suggestions)

    if not confirm:
        typer.echo("\n⚠️  Add --confirm to update folder_rules.json")
        return

    user_confirm = True if yes else typer.confirm(f"\n❓ Add {len(suggestions)} rule(s)?")
    if not user_confirm:
        typer.echo("❌ Cancelled")
        return

    try:
        updated_rules = update_folder_rules(vault_path, suggestions)
    except (InvalidRulesError, PathTraversalError) as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(f"\n✅ Updated folder_rules.json ({len(updated_rules)} total rule(s))")


def _display_batch_summary(notes: list["NoteToMove"], dry_run: bool = False) -> None:
    if dry_run:
        typer.echo("🔍 DRY RUN - No files will be moved\n")
    else:
        typer.echo(f"📋 Found {len(notes)} note(s) to move:\n")
    for note in notes:
        typer.echo(f"  📄 {note.file_path.name}")
        typer.echo(f"     Tags: {', '.join(note.tags)}")
        matched_tags_str = ", ".join(note.matched_tags) if note.matched_tags else "none"
        typer.echo(
            f"     → {note.best_folder} (matched: {matched_tags_str}, score: {note.score:.1f})"
        )
        typer.echo()


def _display_rule_suggestions(suggestions: list["RuleSuggestion"]) -> None:
    typer.echo(f"📋 Found {len(suggestions)} rule suggestion(s):\n")
    for suggestion in suggestions:
        typer.echo(f'  "{suggestion.tag}": "{suggestion.folder}"')
        typer.echo(f"     Notes: {suggestion.note_count}")
        if suggestion.existing_folder_match:
            typer.echo("     Folder: existing match")
        typer.echo(f"     Examples: {', '.join(suggestion.example_notes)}")
        typer.echo()


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
    from ..observability import get_db

    try:
        get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    obs_db = get_db()

    if recent:
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

    summary = obs_db.get_cost_summary(days=days)
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
    from ..observability import get_db

    try:
        get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    summary = get_db().get_quality_summary(days=days)

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
    from ..indexer import build_index
    from ..wikilinks import resolve_wikilink

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
    from ..concept_linking import ConceptLinker, find_orphan_notes
    from ..indexer import build_index, scan_vault

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    if folder:
        folder_path = vault_path / folder
        if not folder_path.exists():
            typer.echo(f"❌ Folder not found: {folder_path}", err=True)
            raise typer.Exit(1)

        typer.echo(f"📁 Scanning folder: {folder}")
        notes = scan_vault(vault_path, folder=folder)
        if not notes:
            typer.echo("   No notes found in folder")
            return

        typer.echo(f"   Found {len(notes)} notes")

        from ..indexer import VaultIndex

        folder_index = VaultIndex(notes=notes, index_path=vault_path / ".kai" / "temp_index.json")
        linker = ConceptLinker(folder_index)
        typer.echo("   Building TF-IDF index...")
        linker.build_tfidf_index()
        suggestions = linker.find_all_connections(threshold=threshold)

        if not suggestions:
            typer.echo("\n✅ No connections found above threshold")
            return

        typer.echo(f"\n🔗 Found {len(suggestions)} potential connection(s):\n")
        for i, suggestion in enumerate(suggestions[:20], 1):
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

            if not confirm:
                typer.echo("\n⚠️  Add --confirm to insert links")
                return

            if not yes:
                proceed = typer.confirm(
                    f"Insert {len(suggestions)} link(s) into {len(by_source)} note(s)?"
                )
                if not proceed:
                    typer.echo("❌ Cancelled")
                    return

            total_inserted = 0
            for source_path, source_suggestions in by_source.items():
                links = linker.insert_wikilinks(source_path, source_suggestions, dry_run=False)
                total_inserted += len(links)

            typer.echo(f"\n✅ Inserted {total_inserted} link(s) into {len(by_source)} note(s)")

        return

    if not note and not orphans:
        typer.echo("❌ Please specify --note, --folder, or --orphans", err=True)
        raise typer.Exit(1)

    note_path = None
    if note:
        if not note.endswith(".md"):
            note = note + ".md"
        note_path = vault_path / note
        if not note_path.exists():
            typer.echo(f"❌ Note not found: {note_path}", err=True)
            raise typer.Exit(1)

    typer.echo("📋 Building vault index...")
    vault_index = build_index(vault_path, folder=None)
    typer.echo(f"   Indexed {len(vault_index.notes)} notes")

    if orphans:
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

    assert note is not None
    assert note_path is not None

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

    if auto_link:
        if dry_run:
            typer.echo("🔍 DRY RUN - Would insert these links:")
            links = linker.insert_wikilinks(note_path, suggestions, dry_run=True)
            for link in links:
                typer.echo(f"   {link}")
            return

        if not confirm:
            typer.echo("\n⚠️  Add --confirm to insert links")
            return

        if not yes:
            proceed = typer.confirm(f"Insert {len(suggestions)} wikilink(s)?")
            if not proceed:
                typer.echo("❌ Cancelled")
                return

        links = linker.insert_wikilinks(note_path, suggestions, dry_run=False)
        typer.echo(f"✅ Inserted {len(links)} wikilink(s)")


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
    from ..refresh import (
        estimate_refresh_cost,
        find_refresh_candidates,
        parse_frontmatter,
        refresh_batch,
    )

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    if show_diff:
        note_path = vault_path / show_diff
        if not note_path.exists():
            typer.echo(f"❌ Note not found: {note_path}", err=True)
            raise typer.Exit(1)

        typer.echo(f"🔍 Preview for: {show_diff}")
        typer.echo("━" * 50)
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

    estimated_cost = estimate_refresh_cost(candidates, settings.llm_model)
    typer.echo(f"\n📋 Found {len(candidates)} note(s) eligible for refresh:\n")

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

    if dry_run:
        typer.echo("\n🔍 DRY RUN - No changes made")
        typer.echo("💡 Add --confirm to execute refresh")
        return

    if not confirm:
        typer.echo("\n⚠️  Add --confirm to execute refresh")
        return

    if not yes:
        proceed = typer.confirm(f"Refresh {len(candidates)} note(s)?")
        if not proceed:
            typer.echo("❌ Cancelled")
            return

    if no_backup and not yes:
        proceed = typer.confirm(
            "⚠️  --no-backup: Files will be overwritten without backup. Continue?"
        )
        if not proceed:
            typer.echo("❌ Cancelled")
            return

    typer.echo(f"\n🔄 Refreshing {len(candidates)} note(s)...")
    summary = refresh_batch(
        candidates=candidates,
        vault_path=vault_path,
        model=settings.llm_model,
        api_key=settings.openrouter_api_key,
        create_backup_file=not no_backup,
    )

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
