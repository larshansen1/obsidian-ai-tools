"""rebuild-index, process-inbox, usage commands."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from ..config import get_settings
from ..observability import get_db, track_command

if TYPE_CHECKING:
    from ..folder_organizer import NoteToMove


def register(app: typer.Typer) -> None:
    app.command()(rebuild_index)
    app.command()(process_inbox)
    app.command()(usage)


@track_command("rebuild-index")
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


@track_command("process-inbox")
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


@track_command("usage")
def usage(
    days: Annotated[
        int,
        typer.Option("--days", "-d", help="Look-back window in days"),
    ] = 30,
    show_all: Annotated[
        bool,
        typer.Option("--all", help="Include commands with zero invocations"),
    ] = False,
) -> None:
    """Show command usage statistics.

    Displays how often each command has been invoked, its success rate,
    and when it was last used. Commands that have never been run are
    listed as candidates for removal when --all is passed.

    Examples:
        kai usage
        kai usage --days 7
        kai usage --all
    """
    try:
        get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    rows = get_db().get_invocation_summary(days=days)

    typer.echo(f"Command usage — last {days} day(s)")
    typer.echo("━" * 60)

    if not rows:
        typer.echo("No command invocations recorded yet.")
        if show_all:
            typer.echo("(All commands shown below have never been run)")
        else:
            typer.echo("Run some commands, then check back — or use --all to see all commands.")
        typer.echo()

    known_commands = [
        "ingest",
        "preview",
        "rebuild-index",
        "process-inbox",
        "usage",
        "search",
        "serve:ingest",
    ]

    seen = {r["command"] for r in rows}

    header = f"{'command':<24} {'calls':>6}  {'success':>8}  {'last used':>12}"
    typer.echo(header)
    typer.echo("-" * 60)

    for row in rows:
        success_str = f"{row['success_pct']:.0f}%"
        typer.echo(
            f"{row['command']:<24} {row['calls']:>6}  {success_str:>8}  {row['last_used']:>12}"
        )

    if show_all:
        for cmd in known_commands:
            if cmd not in seen:
                typer.echo(f"{cmd:<24} {'0':>6}  {'—':>8}  {'never':>12}  <- never used")

    provider_rows = get_db().get_provider_summary(days=days)
    if provider_rows:
        typer.echo()
        typer.echo(f"Provider attempts — last {days} day(s)")
        typer.echo("━" * 60)
        p_header = f"{'provider':<10} {'strategy':<10} {'attempts':>8}  {'success':>8}"
        typer.echo(p_header)
        typer.echo("-" * 60)

        # Calculate fallback rates per provider
        by_provider: dict[str, dict[str, int]] = {}
        for row in provider_rows:
            prov = row["provider"]
            by_provider.setdefault(prov, {})
            by_provider[prov][row["strategy"]] = row["attempts"]

        for row in provider_rows:
            prov = row["provider"]
            strat = row["strategy"]
            success_str = f"{row['success_pct']:.0f}%"
            line = f"{prov:<10} {strat:<10} {row['attempts']:>8}  {success_str:>8}"
            if strat == "fallback":
                total = sum(by_provider[prov].values())
                fallback_rate = row["attempts"] / total * 100 if total else 0
                line += f"  ({fallback_rate:.0f}% fallback rate)"
            typer.echo(line)
