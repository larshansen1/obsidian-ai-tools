"""flashcards command — AI-generated flashcard extraction for Obsidian Spaced Repetition."""

from pathlib import Path
from typing import Annotated

import typer

from ..config import get_settings
from ..observability import track_command


def register(app: typer.Typer) -> None:
    app.command()(flashcards)


@track_command("flashcards")
def flashcards(
    note_path: Annotated[
        str | None,
        typer.Argument(help="Path to a single note (relative to vault root)"),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option("--tag", "-t", help="Filter batch by tag"),
    ] = None,
    since: Annotated[
        int | None,
        typer.Option("--since", "-s", help="Batch: notes ingested in the last N days"),
    ] = None,
    folder: Annotated[
        str | None,
        typer.Option("--folder", "-f", help="Batch: restrict scan to this vault folder"),
    ] = None,
    count: Annotated[
        int,
        typer.Option("--count", "-n", help="Cards to generate per note (default: 5)"),
    ] = 5,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing flashcard files (resets SR history)"),
    ] = False,
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Execute batch generation (required for batch mode)"),
    ] = False,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", "-v", help="Override vault path"),
    ] = None,
) -> None:
    """Generate Obsidian Spaced Repetition flashcards from notes.

    Single note (runs immediately, no --confirm needed):
        kai flashcards "AI/Attention Mechanisms.md"
        kai flashcards "AI/Attention.md" --count 10
        kai flashcards "AI/Attention.md" --force

    Batch (shows plan and estimated cost, requires --confirm):
        kai flashcards --since 7 --confirm
        kai flashcards --tag ai --confirm
        kai flashcards --folder "AI/LLMs" --count 8 --confirm

    Flashcard files are written to the Flashcards/ folder, mirroring the
    source note's path.  Cards use the SR plugin's "Question :: Answer" format.
    """
    from ..flashcard_extraction import (
        FlashcardError,
        compute_deck,
        estimate_flashcard_cost,
        find_flashcard_candidates,
        generate_flashcards,
        note_tags,
        write_flashcard_file,
    )

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        typer.echo("💡 Make sure you have a .env file with required settings.", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    flashcards_folder = settings.obsidian_flashcards_folder

    # ── Single-note mode ──────────────────────────────────────────────────────
    if note_path:
        full_path = vault_path / note_path
        if not full_path.exists():
            typer.echo(f"❌ Note not found: {full_path}", err=True)
            raise typer.Exit(1)

        rel = full_path.relative_to(vault_path)
        flashcard_path = vault_path / flashcards_folder / rel
        if flashcard_path.exists() and not force:
            rel_fc = flashcard_path.relative_to(vault_path)
            typer.echo(f"⚠️  Flashcard file already exists: {rel_fc}")
            typer.echo("   Use --force to overwrite (this resets Obsidian SR review history).")
            raise typer.Exit(0)

        if force and flashcard_path.exists():
            typer.echo("⚠️  --force: overwriting existing flashcard (SR review history reset).")

        deck = compute_deck(note_tags(full_path))
        typer.echo(f"🃏 Generating {count} flashcard(s) for: {note_path}  [deck: {deck}]")
        try:
            cards, cost_usd = generate_flashcards(
                full_path,
                count=count,
                model=settings.llm_model,
                api_key=settings.openrouter_api_key,
            )
        except FlashcardError as e:
            typer.echo(f"❌ Failed to generate flashcards: {e}", err=True)
            raise typer.Exit(1) from e

        written = write_flashcard_file(
            full_path,
            cards,
            vault_path,
            flashcards_folder=flashcards_folder,
            force=force,
            deck=deck,
        )
        if written:
            typer.echo(f"✅ Flashcard file written: {written.relative_to(vault_path)}")
            typer.echo(f"   Generated {len(cards)} card(s)  |  Cost: ${cost_usd:.4f}")
        return

    # ── Batch mode ────────────────────────────────────────────────────────────
    is_batch = tag or since or folder
    if not is_batch:
        typer.echo(
            "❌ Specify a note path, or use --tag / --since / --folder for batch mode.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo("🔍 Scanning for notes without flashcards...")
    candidates = find_flashcard_candidates(
        vault_path=vault_path,
        tag=tag,
        since_days=since,
        folder=folder,
        flashcards_folder=flashcards_folder,
        force=force,
    )

    if not candidates:
        typer.echo("✅ No notes found matching criteria (all may already have flashcard files)")
        return

    estimated_cost = estimate_flashcard_cost(candidates, count)
    typer.echo(f"\n📋 Found {len(candidates)} note(s) without flashcards:\n")

    for i, candidate in enumerate(candidates[:20], 1):
        rel = candidate.file_path.relative_to(vault_path)
        typer.echo(f"  {i}. {candidate.title[:60]}")
        typer.echo(f"     Path: {rel}")

    if len(candidates) > 20:
        typer.echo(f"\n   ... and {len(candidates) - 20} more")

    typer.echo(f"\n💰 Estimated cost: ${estimated_cost:.4f}")

    if not confirm:
        typer.echo("\n⚠️  Add --confirm to generate flashcards")
        return

    typer.echo(f"\n🃏 Generating flashcards for {len(candidates)} note(s)...")
    total_cost = 0.0
    generated = 0
    skipped = 0
    errors: list[str] = []

    for candidate in candidates:
        rel = candidate.file_path.relative_to(vault_path)
        deck = compute_deck(candidate.tags, tag_filter=tag)
        try:
            cards, cost_usd = generate_flashcards(
                candidate.file_path,
                count=count,
                model=settings.llm_model,
                api_key=settings.openrouter_api_key,
            )
            written = write_flashcard_file(
                candidate.file_path,
                cards,
                vault_path,
                flashcards_folder=flashcards_folder,
                force=force,
                deck=deck,
            )
            if written:
                dest = written.relative_to(vault_path)
                typer.echo(f"   ✓ {rel} → {dest} ({len(cards)} cards)")
                generated += 1
                total_cost += cost_usd
            else:
                typer.echo(f"   ⚠ Skipped {rel} (flashcard file already exists)")
                skipped += 1
        except FlashcardError as e:
            typer.echo(f"   ✗ Failed for {rel}: {e}", err=True)
            errors.append(f"{rel}: {e}")

    typer.echo("\n✅ Flashcard generation complete!")
    typer.echo(f"   Generated: {generated}/{len(candidates)}")
    typer.echo(f"   Skipped:   {skipped}")
    typer.echo(f"   Cost:      ${total_cost:.4f}")

    if errors:
        typer.echo(f"\n⚠️  Errors ({len(errors)}):")
        for error in errors[:5]:
            typer.echo(f"   - {error}")
        if len(errors) > 5:
            typer.echo(f"   ... and {len(errors) - 5} more")
