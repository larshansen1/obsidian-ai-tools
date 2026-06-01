"""list-tags and tags commands."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from ..config import get_settings

if TYPE_CHECKING:
    from ..tag_hygiene import TagHygienePlan


def register(app: typer.Typer) -> None:
    app.command()(list_tags)
    app.command("tags")(tags_command)


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
    from ..indexer import build_index
    from ..search import list_all_tags, list_tags_by_folder

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path
    vault_index = build_index(vault_path, folder=None)

    if by_folder:
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
            typer.echo("")
    else:
        typer.echo("📋 Listing tags...")
        tag_counts = list_all_tags(vault_index)
        if not tag_counts:
            typer.echo("   No tags found")
            return
        typer.echo(f"   Found {len(tag_counts)} unique tag(s):\n")
        for tag, count in tag_counts.items():
            typer.echo(f"   {tag}: {count} note(s)")


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
        kai tags --apply plan.json --confirm # Apply plan with confirmation
        kai tags --check similar        # Run only similar tag check
    """
    from ..indexer import build_index
    from ..tag_hygiene import (
        TagHygienePlan,
        apply_plan,
        generate_plan,
    )

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        raise typer.Exit(1) from e

    vault_path = vault or settings.obsidian_vault_path

    if apply_file:
        typer.echo(f"📋 Loading plan from {apply_file}...")
        try:
            plan_obj = TagHygienePlan.from_file(apply_file)
        except Exception as e:
            typer.echo(f"❌ Failed to load plan: {e}", err=True)
            raise typer.Exit(1) from e

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

        if not confirm:
            typer.echo("\n⚠️  Add --confirm to apply fixes")
            return

        if not yes:
            proceed = typer.confirm(f"Apply {total_actions} action(s)?")
            if not proceed:
                typer.echo("❌ Cancelled")
                return

        typer.echo("🔧 Applying changes...")
        modified, skipped = apply_plan(plan_obj, create_backups=True)
        typer.echo(f"✅ Done: {modified} notes modified, {skipped} skipped")
        return

    if not plan:
        typer.echo("🔍 Analyzing vault tags...")
    vault_index = build_index(vault_path, folder=None)

    plan_obj = generate_plan(
        vault_index,
        similarity_threshold=threshold,
        min_cooccurrence=min_overlap,
    )

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

    if plan:
        typer.echo(plan_obj.to_json())
        return

    _display_tag_hygiene_report(plan_obj)

    if not plan_obj.consolidations:
        typer.echo("\n✅ No consolidations needed")
        return

    if not confirm:
        typer.echo("\n⚠️  Add --confirm to apply fixes")
        return

    _interactive_fix(plan_obj, yes)


def _display_tag_hygiene_report(plan: "TagHygienePlan") -> None:
    typer.echo("\n→ Tag Hygiene Report\n")

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

    if plan.high_cooccurrence:
        typer.echo("🔗 High Co-occurrence (often used together):\n")
        for cooc in plan.high_cooccurrence[:5]:
            pct = cooc.jaccard_similarity * 100
            typer.echo(
                f"  {cooc.tag_a} + {cooc.tag_b}: "
                f"{cooc.co_occurrence_count} notes ({pct:.0f}% overlap)"
            )
        if len(plan.high_cooccurrence) > 5:
            typer.echo(f"  ... and {len(plan.high_cooccurrence) - 5} more")
        typer.echo()

    if plan.orphan_tags:
        preview = [o.tag for o in plan.orphan_tags[:10]]
        typer.echo(f"👻 Orphan Tags (used once): {len(plan.orphan_tags)} total")
        typer.echo(f"   {', '.join(preview)}")
        if len(plan.orphan_tags) > 10:
            typer.echo(f"   ... and {len(plan.orphan_tags) - 10} more")
        typer.echo()

    typer.echo("─" * 50)
    typer.echo(
        f"📊 Summary: {len(plan.consolidations)} consolidation(s), "
        f"{len(plan.high_cooccurrence)} high-overlap pair(s), "
        f"{len(plan.orphan_tags)} orphan tag(s)"
    )


def _interactive_fix(plan: "TagHygienePlan", auto_yes: bool = False) -> None:
    from ..tag_hygiene import apply_plan

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
            consolidation.apply = True
            applied_count += 1
            typer.echo("   ✓ Marked for apply")

        typer.echo()

    if applied_count > 0:
        typer.echo(f"📝 Applying {applied_count} consolidation(s)...")
        modified, failed = apply_plan(plan, create_backups=True)
        typer.echo(f"✅ Done: {modified} notes modified")
    else:
        typer.echo("✅ No consolidations applied")
