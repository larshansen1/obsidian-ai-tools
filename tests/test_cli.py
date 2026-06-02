"""Integration tests for the CLI."""

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from typer.testing import CliRunner

from obsidian_ai_tools.cli import app
from obsidian_ai_tools.observability import _set_db_for_test

runner = CliRunner()


def test_version_command() -> None:
    """Test the version command."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "obsidian-ai-tools" in result.stdout


def test_help_command() -> None:
    """Test the help command."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Knowledge AI Tools" in result.stdout


def test_list_tags_empty_mock_vault(tmp_path: Path) -> None:
    """Test list-tags command with an empty mock vault."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    # Run the command with the mock vault
    # Note: we override the vault path using the --vault option
    result = runner.invoke(app, ["list-tags", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "No tags found" in result.stdout


def test_list_tags_with_content(tmp_path: Path) -> None:
    """Test list-tags command with indexed content."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    # Create a note with tags
    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()
    note_path = inbox_path / "test_note.md"
    note_path.write_text(
        """---
title: Test Note
tags: [test, cli]
---
# Test Content
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["list-tags", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Found 2 unique tag(s)" in result.stdout
    assert "test: 1 note(s)" in result.stdout
    assert "cli: 1 note(s)" in result.stdout


def test_list_tags_by_folder_empty_vault(tmp_path: Path) -> None:
    """Test list-tags --by-folder with an empty vault."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    result = runner.invoke(app, ["list-tags", "--by-folder", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "No tags found" in result.stdout


def test_list_tags_by_folder_single_folder(tmp_path: Path) -> None:
    """Test list-tags --by-folder with notes in a single folder."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()

    # Create two notes with overlapping tags
    (inbox_path / "note1.md").write_text(
        """---
title: Note 1
tags: [ai, python]
---
# Test Content
""",
        encoding="utf-8",
    )

    (inbox_path / "note2.md").write_text(
        """---
title: Note 2
tags: [ai, research]
---
# Test Content
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["list-tags", "--by-folder", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Listing tags by folder" in result.stdout
    assert "Found tags in 1 folder(s)" in result.stdout
    assert "📁 inbox/" in result.stdout
    assert "ai: 2 note(s)" in result.stdout
    assert "python: 1 note(s)" in result.stdout
    assert "research: 1 note(s)" in result.stdout


def test_list_tags_by_folder_multiple_folders(tmp_path: Path) -> None:
    """Test list-tags --by-folder with notes across multiple folders."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    # Create notes in inbox
    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()
    (inbox_path / "note1.md").write_text(
        """---
title: Inbox Note
tags: [ai, research]
---
# Content
""",
        encoding="utf-8",
    )

    # Create notes in projects/ml
    projects_path = vault_path / "projects" / "ml"
    projects_path.mkdir(parents=True)
    (projects_path / "note2.md").write_text(
        """---
title: ML Project
tags: [ai, python, llm]
---
# Content
""",
        encoding="utf-8",
    )

    # Create notes in archive
    archive_path = vault_path / "archive"
    archive_path.mkdir()
    (archive_path / "note3.md").write_text(
        """---
title: Archived Note
tags: [testing]
---
# Content
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["list-tags", "--by-folder", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Listing tags by folder" in result.stdout
    assert "Found tags in 3 folder(s)" in result.stdout

    # Check that all folders are present
    assert "📁 archive/" in result.stdout
    assert "📁 inbox/" in result.stdout
    assert "📁 projects/ml/" in result.stdout

    # Verify tag counts per folder
    # Note: We check for presence of tags in output
    assert "testing: 1 note(s)" in result.stdout
    assert "research: 1 note(s)" in result.stdout
    assert "llm: 1 note(s)" in result.stdout


def test_list_tags_by_folder_same_tag_in_multiple_folders(tmp_path: Path) -> None:
    """Test that same tag appears separately in different folders."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()
    (inbox_path / "note1.md").write_text(
        """---
title: Inbox AI Note
tags: [ai]
---
# Content
""",
        encoding="utf-8",
    )

    projects_path = vault_path / "projects"
    projects_path.mkdir()
    (projects_path / "note2.md").write_text(
        """---
title: Project AI Note
tags: [ai]
---
# Content
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["list-tags", "--by-folder", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Found tags in 2 folder(s)" in result.stdout
    assert "📁 inbox/" in result.stdout
    assert "📁 projects/" in result.stdout
    # Both folders should show ai tag
    assert result.stdout.count("ai: 1 note(s)") == 2


def test_search_help() -> None:
    """Test search command help."""
    result = runner.invoke(app, ["search", "--help"])
    assert result.exit_code == 0
    assert "Search your Obsidian vault" in result.stdout


def test_rebuild_index_command(tmp_path: Path) -> None:
    """Test rebuild-index command creates both indexes and scans entire vault."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    # Create test notes in inbox folder
    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()
    note1 = inbox_path / "note1.md"
    note1.write_text(
        """---
title: First Note
tags: [ai, python]
---
# First Note Content
""",
        encoding="utf-8",
    )

    # Create notes in other folders to verify recursive scanning
    projects_path = vault_path / "projects"
    projects_path.mkdir()
    note2 = projects_path / "note2.md"
    note2.write_text(
        """---
title: Second Note
tags: [llm]
---
# Second Note Content
""",
        encoding="utf-8",
    )

    archive_path = vault_path / "archive"
    archive_path.mkdir()
    note3 = archive_path / "note3.md"
    note3.write_text(
        """---
title: Third Note
tags: [testing]
---
# Third Note Content
""",
        encoding="utf-8",
    )

    # Run rebuild-index command
    result = runner.invoke(app, ["rebuild-index", "--vault", str(vault_path)])

    # Verify command succeeded
    assert result.exit_code == 0
    assert "Rebuilding indexes" in result.stdout
    # Should find all 3 notes across all folders
    assert "Indexed 3 note(s)" in result.stdout
    assert "Index rebuild complete" in result.stdout

    # Verify indexes were created
    vault_index_path = vault_path / ".kai" / "vault_index.json"
    whoosh_index_path = vault_path / ".kai" / "whoosh_index"

    assert vault_index_path.exists(), "Vault index should be created"
    assert whoosh_index_path.exists(), "Whoosh index directory should be created"
    assert whoosh_index_path.is_dir(), "Whoosh index should be a directory"


def test_process_inbox_requires_confirm(tmp_path: Path) -> None:
    """Test process-inbox requires --confirm flag."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()

    note_path = inbox_path / "test.md"
    note_path.write_text(
        """---
title: Test Note
tags: [ai, python]
---
# Test Content
""",
        encoding="utf-8",
    )

    rules_path = vault_path / "folder_rules.json"
    rules_path.write_text('{"ai": "AI", "python": "Python"}', encoding="utf-8")

    result = runner.invoke(app, ["process-inbox", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Add --confirm to execute moves" in result.stdout


def test_process_inbox_confirm_without_prompt(tmp_path: Path) -> None:
    """Test process-inbox with --confirm --yes skips confirmation."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()

    note_path = inbox_path / "test.md"
    note_path.write_text(
        """---
title: Test Note
tags: [ai]
---
# Test Content
""",
        encoding="utf-8",
    )

    rules_path = vault_path / "folder_rules.json"
    rules_path.write_text('{"ai": "AI"}', encoding="utf-8")

    result = runner.invoke(app, ["process-inbox", "--confirm", "--yes", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Moved test.md" in result.stdout or "Moved 1/1" in result.stdout


def test_reading_list_clear_requires_confirm(tmp_path: Path) -> None:
    """Test reading-list clear requires --confirm flag."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    kai_dir = vault_path / ".kai"
    kai_dir.mkdir()

    # Create a valid reading list entry with all required fields
    reading_list_path = kai_dir / "reading_list.jsonl"
    reading_list_path.write_text(
        '{"url": "https://example.com", "preview": {"url": "https://example.com", '
        '"source_type": "web", "title": "Test", "content_length": 100, '
        '"estimated_cost_usd": 0.01, "key_topics": []}, "status": "ingested"}\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["reading-list", "clear", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Add --confirm to clear items" in result.stdout


def test_reading_list_list_renders_saved_entries(tmp_path: Path) -> None:
    """Test reading-list list renders saved preview details."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    entry = MagicMock(
        url="https://example.com/article",
        status="pending",
        preview=MagicMock(title="Saved Article", estimated_cost_usd=0.01),
    )

    with (
        patch(
            "obsidian_ai_tools.commands.preview.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.preview.load_reading_list", return_value=[entry]),
    ):
        result = runner.invoke(app, ["reading-list", "list", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Reading List (1 item(s))" in result.output
    assert "Saved Article" in result.output
    assert "Status: pending" in result.output


def test_reading_list_ingest_handles_no_pending_entries(tmp_path: Path) -> None:
    """Test reading-list ingest exits cleanly when nothing is pending."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    entry = MagicMock(status="ingested")

    with (
        patch(
            "obsidian_ai_tools.commands.preview.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.preview.load_reading_list", return_value=[entry]),
    ):
        result = runner.invoke(app, ["reading-list", "ingest", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "No pending items" in result.output


def test_reading_list_ingest_all_marks_successful_entries(tmp_path: Path) -> None:
    """Test reading-list ingest --all marks each successful URL as ingested."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    entries = [
        MagicMock(url="https://example.com/one", status="pending", preview=MagicMock(title="One")),
        MagicMock(url="https://example.com/two", status="pending", preview=MagicMock(title="Two")),
    ]
    nested_runner = MagicMock()
    nested_runner.invoke.return_value.exit_code = 0

    with (
        patch(
            "obsidian_ai_tools.commands.preview.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.preview.load_reading_list", return_value=entries),
        patch("obsidian_ai_tools.preview.update_reading_list_status") as update_status,
        patch("typer.testing.CliRunner", return_value=nested_runner),
    ):
        result = runner.invoke(
            app,
            ["reading-list", "ingest", "--all", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "Ingested 2 item(s). 0 pending remaining." in result.output
    assert update_status.call_count == 2


def test_reading_list_clear_confirm_yes_removes_matching_entries(tmp_path: Path) -> None:
    """Test reading-list clear --confirm --yes rewrites the reading list."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    kai_dir = vault_path / ".kai"
    kai_dir.mkdir()
    reading_list_path = kai_dir / "reading_list.jsonl"
    reading_list_path.write_text(
        '{"url": "https://example.com", "preview": {"url": "https://example.com", '
        '"source_type": "web", "title": "Test", "content_length": 100, '
        '"estimated_cost_usd": 0.01, "key_topics": []}, "status": "ingested"}\n',
        encoding="utf-8",
    )

    with patch(
        "obsidian_ai_tools.commands.preview.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(
            app,
            ["reading-list", "clear", "--confirm", "--yes", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "Cleared 1 item(s). 0 remaining." in result.stdout
    assert reading_list_path.read_text(encoding="utf-8") == ""


def test_tags_requires_confirm(tmp_path: Path) -> None:
    """Test tags command requires --confirm to apply fixes."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()

    note1 = inbox_path / "note1.md"
    note1.write_text(
        """---
title: Test Note
tags: [neurodivergent, neurodivergence]
---
# Content
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["tags", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Add --confirm to apply fixes" in result.stdout


def test_tags_apply_requires_confirm(tmp_path: Path) -> None:
    """Test tags --apply requires --confirm before applying a reviewed plan."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "consolidations": [
                    {
                        "action": "merge",
                        "from_tags": ["python3"],
                        "to_tag": "python",
                        "affected_notes": [],
                        "note_count": 0,
                        "apply": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "obsidian_ai_tools.commands.tags.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.tag_hygiene.apply_plan") as apply_plan,
    ):
        result = runner.invoke(
            app,
            ["tags", "--apply", str(plan_path), "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "Add --confirm to apply fixes" in result.stdout
    apply_plan.assert_not_called()


def test_tags_apply_confirm_yes_applies_reviewed_plan(tmp_path: Path) -> None:
    """Test tags --apply --confirm --yes executes a reviewed plan."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "consolidations": [
                    {
                        "action": "merge",
                        "from_tags": ["python3"],
                        "to_tag": "python",
                        "affected_notes": [],
                        "note_count": 0,
                        "apply": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "obsidian_ai_tools.commands.tags.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.tag_hygiene.apply_plan", return_value=(1, 0)) as apply_plan,
    ):
        result = runner.invoke(
            app,
            [
                "tags",
                "--apply",
                str(plan_path),
                "--confirm",
                "--yes",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 0
    assert "Done: 1 notes modified, 0 skipped" in result.stdout
    apply_plan.assert_called_once()


def test_connect_auto_link_requires_confirm(tmp_path: Path) -> None:
    """Test connect --auto-link requires --confirm flag."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()

    note1 = inbox_path / "note1.md"
    note1.write_text(
        """---
title: AI Concepts
tags: [ai]
---
# Artificial Intelligence

Machine learning and neural networks are key technologies.
Deep learning uses multi-layer architectures for complex tasks.
Artificial intelligence enables machines to learn from data.
Machine learning algorithms improve through training on large datasets.
Neural networks are inspired by biological brain structures.
Deep learning achieves state-of-the-art results in many domains.
AI applications include natural language processing and computer vision.
The combination of machine learning and deep learning drives innovation.
This first note focuses on AI fundamental concepts.
""",
        encoding="utf-8",
    )

    note2 = inbox_path / "note2.md"
    note2.write_text(
        """---
title: ML Topics
tags: [ml]
---
# Machine Learning

Machine learning and neural networks are key technologies.
Deep learning uses multi-layer architectures for complex tasks.
Artificial intelligence enables machines to learn from data.
Machine learning algorithms improve through training on large datasets.
Neural networks are inspired by biological brain structures.
Deep learning achieves state-of-the-art results in many domains.
AI applications include natural language processing and computer vision.
The combination of machine learning and deep learning drives innovation.
This second note covers machine learning applications.
""",
        encoding="utf-8",
    )

    note3 = inbox_path / "note3.md"
    note3.write_text(
        """---
title: Data Science
tags: [ds]
---
# Data Science Overview

Statistics and data analysis form the foundation of data science.
Python and R are popular programming languages for data science.
Data visualization helps communicate insights from data.
This note is about data science in general and is different content.
""",
        encoding="utf-8",
    )

    note3 = inbox_path / "note3.md"
    note3.write_text(
        """---
title: Data Science
tags: [ds]
---
# Data Science Overview

Statistics and data analysis form the foundation of data science.
Python and R are popular programming languages for data science.
Data visualization helps communicate insights from data.
This note is about data science in general and is different content.
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "connect",
            "--folder",
            "inbox",
            "--auto-link",
            "--threshold",
            "0.05",
            "--vault",
            str(vault_path),
        ],
    )

    assert result.exit_code == 0
    assert "Add --confirm to insert links" in result.stdout


def test_connect_folder_auto_link_dry_run_lists_links(tmp_path: Path) -> None:
    """Test connect --auto-link --dry-run previews grouped wikilinks."""
    vault_path = tmp_path / "mock_vault"
    folder = vault_path / "inbox"
    folder.mkdir(parents=True)
    source_note = folder / "note.md"
    suggestion = MagicMock(
        source_note=source_note,
        target_title="Related Note",
        similarity_score=0.75,
        keywords_shared=["python"],
    )
    linker = MagicMock()
    linker.find_all_connections.return_value = [suggestion]
    indexed_note = {
        "file_path": source_note,
        "title": "Source Note",
        "content": "python testing",
        "modified_time": 1.0,
    }

    with (
        patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.indexer.scan_vault", return_value=[indexed_note]),
        patch("obsidian_ai_tools.concept_linking.ConceptLinker", return_value=linker),
    ):
        result = runner.invoke(
            app,
            [
                "connect",
                "--folder",
                "inbox",
                "--auto-link",
                "--dry-run",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 0
    assert "DRY RUN - Would insert links into 1 notes" in result.output
    assert "[[Related Note]]" in result.output
    linker.insert_wikilinks.assert_not_called()


def test_connect_folder_auto_link_confirm_yes_inserts_links(tmp_path: Path) -> None:
    """Test connect --auto-link --confirm --yes inserts grouped wikilinks."""
    vault_path = tmp_path / "mock_vault"
    folder = vault_path / "inbox"
    folder.mkdir(parents=True)
    source_note = folder / "note.md"
    suggestion = MagicMock(
        source_note=source_note,
        target_title="Related Note",
        similarity_score=0.75,
        keywords_shared=[],
    )
    linker = MagicMock()
    linker.find_all_connections.return_value = [suggestion]
    linker.insert_wikilinks.return_value = ["[[Related Note]]"]
    indexed_note = {
        "file_path": source_note,
        "title": "Source Note",
        "content": "python testing",
        "modified_time": 1.0,
    }

    with (
        patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.indexer.scan_vault", return_value=[indexed_note]),
        patch("obsidian_ai_tools.concept_linking.ConceptLinker", return_value=linker),
    ):
        result = runner.invoke(
            app,
            [
                "connect",
                "--folder",
                "inbox",
                "--auto-link",
                "--confirm",
                "--yes",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 0
    assert "Inserted 1 link(s) into 1 note(s)" in result.output
    linker.insert_wikilinks.assert_called_once_with(source_note, [suggestion], dry_run=False)


def test_refresh_requires_confirm(tmp_path: Path) -> None:
    """Test refresh command requires --confirm flag before execution."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    dummy_settings = _make_dummy_settings(vault_path)

    # Create a dummy refresh candidate so the command reaches the confirm gate
    candidate = MagicMock()
    candidate.file_path = vault_path / "inbox" / "test.md"
    candidate.title = "Test Note"
    candidate.current_prompt_version = "youtube_v1"
    candidate.target_prompt_version = "youtube_v2"

    with (
        patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=dummy_settings,
        ),
        patch(
            "obsidian_ai_tools.refresh.find_refresh_candidates",
            return_value=[candidate],
        ),
        patch(
            "obsidian_ai_tools.refresh.estimate_refresh_cost",
            return_value=1.23,
        ),
    ):
        result = runner.invoke(
            app,
            ["refresh", "-p", "youtube_v2", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "Add --confirm to execute refresh" in result.output


def test_refresh_confirm_prompts_before_execution(tmp_path: Path) -> None:
    """Test refresh --confirm prompts before refreshing candidates."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    candidate = MagicMock()
    candidate.file_path = vault_path / "inbox" / "test.md"
    candidate.title = "Test Note"
    candidate.current_prompt_version = "youtube_v1"
    candidate.target_prompt_version = "youtube_v2"

    with (
        patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.refresh.find_refresh_candidates", return_value=[candidate]),
        patch("obsidian_ai_tools.refresh.estimate_refresh_cost", return_value=1.23),
        patch("obsidian_ai_tools.refresh.refresh_batch") as refresh_batch,
    ):
        result = runner.invoke(
            app,
            ["refresh", "-p", "youtube_v2", "--confirm", "--vault", str(vault_path)],
            input="n\n",
        )

    assert result.exit_code == 0
    assert "Refresh 1 note(s)?" in result.output
    assert "Cancelled" in result.output
    refresh_batch.assert_not_called()


def test_refresh_confirm_yes_executes_batch(tmp_path: Path) -> None:
    """Test refresh --confirm --yes executes without prompting."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    candidate = MagicMock()
    candidate.file_path = vault_path / "inbox" / "test.md"
    candidate.title = "Test Note"
    candidate.current_prompt_version = "youtube_v1"
    candidate.target_prompt_version = "youtube_v2"
    summary = MagicMock(refreshed=1, total_candidates=1, skipped=0, total_cost_usd=0.02, errors=[])

    with (
        patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.refresh.find_refresh_candidates", return_value=[candidate]),
        patch("obsidian_ai_tools.refresh.estimate_refresh_cost", return_value=0.02),
        patch("obsidian_ai_tools.refresh.refresh_batch", return_value=summary) as refresh_batch,
    ):
        result = runner.invoke(
            app,
            ["refresh", "-p", "youtube_v2", "--confirm", "--yes", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "Refresh complete" in result.output
    assert "Refreshed: 1/1" in result.output
    refresh_batch.assert_called_once()


def test_refresh_dry_run_lists_candidates_without_execution(tmp_path: Path) -> None:
    """Test refresh --dry-run reports candidates without calling refresh_batch."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()
    candidate = MagicMock()
    candidate.file_path = vault_path / "inbox" / "test.md"
    candidate.title = "Test Note"
    candidate.current_prompt_version = "youtube_v1"
    candidate.target_prompt_version = "youtube_v2"

    with (
        patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch("obsidian_ai_tools.refresh.find_refresh_candidates", return_value=[candidate]),
        patch("obsidian_ai_tools.refresh.estimate_refresh_cost", return_value=0.02),
        patch("obsidian_ai_tools.refresh.refresh_batch") as refresh_batch,
    ):
        result = runner.invoke(
            app,
            ["refresh", "-p", "youtube_v2", "--dry-run", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "DRY RUN - No changes made" in result.output
    refresh_batch.assert_not_called()


def _make_dummy_settings(vault_path: Path) -> MagicMock:
    """Create dummy settings object for CLI tests."""
    settings = MagicMock()
    settings.obsidian_vault_path = vault_path
    settings.obsidian_inbox_folder = "inbox"
    settings.obsidian_flashcards_folder = "Flashcards"
    settings.llm_model = "test-model"
    settings.openrouter_api_key = "test-key"
    return settings


def test_update_rules_previews_suggestions_without_writing(tmp_path: Path) -> None:
    """Test update-rules previews suggestions and requires --confirm to write."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    inbox = vault_path / "inbox"
    inbox.mkdir()

    (vault_path / "folder_rules.json").write_text('{"ai": "AI"}\n', encoding="utf-8")
    (inbox / "python.md").write_text(
        """---
title: Python Note
tags: [python, programming]
---
# Content
""",
        encoding="utf-8",
    )

    with patch(
        "obsidian_ai_tools.commands.vault.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(
            app,
            ["update-rules", "--include-singletons", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "Found 2 rule suggestion(s)" in result.output
    assert '"python": "Python"' in result.output
    assert "Add --confirm to update folder_rules.json" in result.output
    assert (vault_path / "folder_rules.json").read_text(encoding="utf-8") == '{"ai": "AI"}\n'


def test_update_rules_confirm_yes_writes_rules_file(tmp_path: Path) -> None:
    """Test update-rules --confirm --yes writes suggested rules."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    inbox = vault_path / "inbox"
    inbox.mkdir()

    (vault_path / "folder_rules.json").write_text('{"ai": "AI"}\n', encoding="utf-8")
    (inbox / "python.md").write_text(
        """---
title: Python Note
tags: [python]
---
# Content
""",
        encoding="utf-8",
    )

    with patch(
        "obsidian_ai_tools.commands.vault.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(
            app,
            [
                "update-rules",
                "--include-singletons",
                "--confirm",
                "--yes",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 0
    assert "Updated folder_rules.json" in result.output

    rules = json.loads((vault_path / "folder_rules.json").read_text(encoding="utf-8"))
    assert rules == {"ai": "AI", "python": "Python"}


def test_update_rules_default_skips_singletons(tmp_path: Path) -> None:
    """Test update-rules defaults to recurring tag suggestions only."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    inbox = vault_path / "inbox"
    inbox.mkdir()

    (inbox / "python.md").write_text(
        """---
title: Python Note
tags: [python]
---
# Content
""",
        encoding="utf-8",
    )

    with patch(
        "obsidian_ai_tools.commands.vault.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(app, ["update-rules", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "No rule suggestions" in result.output


def test_search_requires_criteria(tmp_path: Path) -> None:
    """Test search command requires at least one criterion."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    with patch(
        "obsidian_ai_tools.commands.search.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(app, ["search", "--vault", str(vault_path)])

    assert result.exit_code == 1
    assert "No search criteria provided" in result.output


def test_search_invalid_after_date(tmp_path: Path) -> None:
    """Test search command validates --after date format."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    with patch(
        "obsidian_ai_tools.commands.search.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(
            app,
            [
                "search",
                "--after",
                "not-a-date",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 1
    assert "Invalid date format for --after" in result.output


def test_search_no_results(tmp_path: Path) -> None:
    """Test search command handles no results cleanly."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    dummy_settings = _make_dummy_settings(vault_path)

    with (
        patch(
            "obsidian_ai_tools.commands.search.get_settings",
            return_value=dummy_settings,
        ),
        patch("obsidian_ai_tools.indexer.build_index", return_value=MagicMock()),
        patch(
            "obsidian_ai_tools.search.build_whoosh_index",
            return_value=None,
        ),
        patch("obsidian_ai_tools.search.search_notes", return_value=[]),
    ):
        result = runner.invoke(
            app,
            [
                "search",
                "--keyword",
                "ai",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 0
    assert "No results found" in result.output


def test_search_with_results(tmp_path: Path) -> None:
    """Test search command prints formatted results with previews."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    class DummyNote:
        def __init__(self, file_path: Path) -> None:
            self.file_path = file_path
            self.title = "Test Note"
            self.tags = ["ai", "cli"]
            self.created = None
            self.author = "Tester"

    class DummyResult:
        def __init__(self, note: DummyNote, highlights: str) -> None:
            self.note = note
            self.highlights = highlights
            self.explanation = None
            self.outgoing_links: list[str] = []

    dummy_settings = _make_dummy_settings(vault_path)
    note = DummyNote(vault_path / "inbox" / "test.md")
    results = [DummyResult(note, "<b>Preview</b> content")]  # HTML to be stripped

    with (
        patch(
            "obsidian_ai_tools.commands.search.get_settings",
            return_value=dummy_settings,
        ),
        patch("obsidian_ai_tools.indexer.build_index", return_value=MagicMock()),
        patch(
            "obsidian_ai_tools.search.build_whoosh_index",
            return_value=None,
        ),
        patch("obsidian_ai_tools.search.search_notes", return_value=results),
    ):
        result = runner.invoke(
            app,
            [
                "search",
                "--keyword",
                "ai",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 0
    assert "Found 1 result(s)" in result.output
    assert "Test Note" in result.output
    # Preview line should have HTML stripped
    assert "Preview: Preview content" in result.output


def test_stats_recent_no_records(tmp_path: Path) -> None:
    """Test stats --recent handles empty observability data."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    dummy_settings = _make_dummy_settings(vault_path)

    mock_db = MagicMock()
    mock_db.get_recent_costs.return_value = []
    _set_db_for_test(mock_db)
    try:
        with patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=dummy_settings,
        ):
            result = runner.invoke(app, ["stats", "--recent"])
    finally:
        _set_db_for_test(None)

    assert result.exit_code == 0
    assert "No cost records found" in result.output


def test_stats_summary_with_data(tmp_path: Path) -> None:
    """Test stats summary output with sample observability data."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    dummy_settings = _make_dummy_settings(vault_path)

    summary = {
        "total_cost": 1.2345,
        "by_source_type": [
            ("youtube", 0.8, 10),
            ("web", 0.4, 5),
        ],
        "by_model": [("gpt-4", 1.0)],
        "by_operation": [("ingest", 1.2345)],
        "recent_cost_7days": 0.5,
    }

    mock_db = MagicMock()
    mock_db.get_cost_summary.return_value = summary
    _set_db_for_test(mock_db)
    try:
        with patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=dummy_settings,
        ):
            result = runner.invoke(app, ["stats"])
    finally:
        _set_db_for_test(None)

    assert result.exit_code == 0
    assert "Cost Summary" in result.output
    assert "$1.23" in result.output or "$1.2345" in result.output
    assert "youtube" in result.output
    assert "gpt-4" in result.output


def test_quality_summary_with_data(tmp_path: Path) -> None:
    """Test quality command outputs metrics from observability DB."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    dummy_settings = _make_dummy_settings(vault_path)

    quality_summary = {
        "total_ingestions": 20,
        "success_rate": 95.0,
        "successes": 19,
        "by_source": [
            {
                "source_type": "youtube",
                "successes": 10,
                "total": 10,
                "avg_duration": 1.2,
            },
            {
                "source_type": "web",
                "successes": 9,
                "total": 10,
                "avg_duration": 0.8,
            },
        ],
        "common_errors": [["TimeoutError", 1]],
    }

    mock_db = MagicMock()
    mock_db.get_quality_summary.return_value = quality_summary
    _set_db_for_test(mock_db)
    try:
        with patch(
            "obsidian_ai_tools.commands.vault.get_settings",
            return_value=dummy_settings,
        ):
            result = runner.invoke(app, ["quality"])
    finally:
        _set_db_for_test(None)

    assert result.exit_code == 0
    assert "Quality Metrics" in result.output
    assert "Total Ingestions: 20" in result.output
    assert "youtube" in result.output
    assert "TimeoutError" in result.output


def test_serve_background_starts_detached_process(tmp_path: Path) -> None:
    """Test serve --background starts a detached child and records its PID."""
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4321

        result = runner.invoke(app, ["serve", "--background", "--port", "9000"])

    assert result.exit_code == 0
    assert "started in the background on http://127.0.0.1:9000" in result.output
    assert (tmp_path / ".kai" / "server.pid").read_text(encoding="utf-8") == "4321\n"
    mock_popen.assert_called_once()
    command = mock_popen.call_args.args[0]
    assert command[-4:] == ["--host", "127.0.0.1", "--port", "9000"]
    assert mock_popen.call_args.kwargs["start_new_session"] is True


def test_serve_status_reports_running_background_process(tmp_path: Path) -> None:
    """Test serve --status reads the PID file and checks that process."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    (state_dir / "server.pid").write_text("4321\n", encoding="utf-8")

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.os.kill") as mock_kill,
    ):
        result = runner.invoke(app, ["serve", "--status"])

    assert result.exit_code == 0
    assert "running in the background (PID 4321)" in result.output
    mock_kill.assert_called_once_with(4321, 0)


def test_serve_status_log_shows_recent_background_output(tmp_path: Path) -> None:
    """Test serve --status --log prints only the most recent log lines."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    (state_dir / "server.log").write_text(
        "\n".join(f"log line {number}" for number in range(25)),
        encoding="utf-8",
    )

    with patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path):
        result = runner.invoke(app, ["serve", "--status", "--log"])

    assert result.exit_code == 0
    assert "kai server is not running in the background." in result.output
    assert "Recent log output" in result.output
    assert "log line 4" not in result.output
    assert "log line 5" in result.output
    assert "log line 24" in result.output


def test_serve_log_requires_status() -> None:
    """Test serve --log is only accepted as a status modifier."""
    result = runner.invoke(app, ["serve", "--log"])

    assert result.exit_code == 1
    assert "--log must be used with --status" in result.output


def test_serve_stop_terminates_background_process(tmp_path: Path) -> None:
    """Test serve --stop terminates the recorded background process."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    pid_path = state_dir / "server.pid"
    pid_path.write_text("4321\n", encoding="utf-8")

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.os.kill") as mock_kill,
    ):
        result = runner.invoke(app, ["serve", "--stop"])

    assert result.exit_code == 0
    assert "kai server stopped (PID 4321)" in result.output
    assert not pid_path.exists()
    assert mock_kill.call_args_list == [call(4321, 0), call(4321, 15)]


# ── flashcards command ────────────────────────────────────────────────────────


def test_flashcards_single_note_generates_and_writes(tmp_path: Path) -> None:
    """Single-note invocation generates cards and reports the output path."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    note = vault_path / "AI" / "attention.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: Attention\n---\nContent.", encoding="utf-8")

    flashcard_path = vault_path / "Flashcards" / "AI" / "attention.md"
    cards = [{"question": "What?", "answer": "This."}]

    with (
        patch(
            "obsidian_ai_tools.commands.flashcards.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.generate_flashcards",
            return_value=(cards, 0.001),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.write_flashcard_file",
            return_value=flashcard_path,
        ) as mock_write,
    ):
        result = runner.invoke(app, ["flashcards", "AI/attention.md", "--vault", str(vault_path)])

    assert result.exit_code == 0, result.output
    assert "Flashcard file written" in result.output
    assert "1 card(s)" in result.output
    mock_write.assert_called_once()


def test_flashcards_single_note_skips_when_exists_without_force(tmp_path: Path) -> None:
    """Single-note mode exits without generating when flashcard already exists."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    note = vault_path / "note.md"
    note.write_text("---\ntitle: Note\n---\nContent.", encoding="utf-8")
    existing = vault_path / "Flashcards" / "note.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("old flashcard", encoding="utf-8")

    with patch(
        "obsidian_ai_tools.commands.flashcards.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(app, ["flashcards", "note.md", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "already exists" in result.output
    assert "Use --force" in result.output


def test_flashcards_batch_dry_run_shows_plan_without_generating(tmp_path: Path) -> None:
    """Batch mode without --confirm prints plan and exits without making LLM calls."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    candidate = MagicMock()
    candidate.file_path = vault_path / "note.md"
    candidate.title = "Test Note"

    with (
        patch(
            "obsidian_ai_tools.commands.flashcards.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.find_flashcard_candidates",
            return_value=[candidate],
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.estimate_flashcard_cost",
            return_value=0.005,
        ),
        patch("obsidian_ai_tools.flashcard_extraction.generate_flashcards") as mock_gen,
    ):
        result = runner.invoke(app, ["flashcards", "--tag", "ai", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Add --confirm" in result.output
    assert "Estimated cost" in result.output
    mock_gen.assert_not_called()


def test_flashcards_batch_confirm_runs_generation(tmp_path: Path) -> None:
    """Batch mode with --confirm calls generate and write for each candidate."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    note = vault_path / "note.md"
    note.write_text("---\ntitle: Note\n---\nContent.", encoding="utf-8")

    candidate = MagicMock()
    candidate.file_path = note
    candidate.title = "Note"

    flashcard_path = vault_path / "Flashcards" / "note.md"
    cards = [{"question": "Q?", "answer": "A."}]

    with (
        patch(
            "obsidian_ai_tools.commands.flashcards.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.find_flashcard_candidates",
            return_value=[candidate],
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.estimate_flashcard_cost",
            return_value=0.005,
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.generate_flashcards",
            return_value=(cards, 0.005),
        ) as mock_gen,
        patch(
            "obsidian_ai_tools.flashcard_extraction.write_flashcard_file",
            return_value=flashcard_path,
        ) as mock_write,
    ):
        result = runner.invoke(
            app,
            ["flashcards", "--tag", "ai", "--confirm", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0, result.output
    assert "Flashcard generation complete" in result.output
    assert "Generated: 1/1" in result.output
    mock_gen.assert_called_once()
    mock_write.assert_called_once()


def test_flashcards_no_args_shows_error(tmp_path: Path) -> None:
    """Running kai flashcards with no note path and no batch flags exits with error."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    with patch(
        "obsidian_ai_tools.commands.flashcards.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(app, ["flashcards", "--vault", str(vault_path)])

    assert result.exit_code == 1
    assert "Specify a note path" in result.output


def test_flashcards_single_note_force_warns_about_history_reset(tmp_path: Path) -> None:
    """--force on an existing flashcard file prints a SR history warning."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    note = vault_path / "note.md"
    note.write_text("---\ntitle: Note\n---\nContent.", encoding="utf-8")
    existing = vault_path / "Flashcards" / "note.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("old flashcard", encoding="utf-8")

    cards = [{"question": "Q?", "answer": "A."}]
    with (
        patch(
            "obsidian_ai_tools.commands.flashcards.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.generate_flashcards",
            return_value=(cards, 0.001),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.write_flashcard_file",
            return_value=existing,
        ),
    ):
        result = runner.invoke(
            app, ["flashcards", "note.md", "--force", "--vault", str(vault_path)]
        )

    assert result.exit_code == 0
    assert "SR review history" in result.output


def test_flashcards_single_note_generation_error_exits(tmp_path: Path) -> None:
    """FlashcardError during single-note generation exits with code 1."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    note = vault_path / "note.md"
    note.write_text("---\ntitle: Note\n---\nContent.", encoding="utf-8")

    with (
        patch(
            "obsidian_ai_tools.commands.flashcards.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.generate_flashcards",
            side_effect=__import__(
                "obsidian_ai_tools.flashcard_extraction", fromlist=["FlashcardError"]
            ).FlashcardError("LLM failed"),
        ),
    ):
        result = runner.invoke(app, ["flashcards", "note.md", "--vault", str(vault_path)])

    assert result.exit_code == 1
    assert "Failed to generate flashcards" in result.output


def test_flashcards_batch_generation_error_reported(tmp_path: Path) -> None:
    """FlashcardError during batch generation is reported but does not abort the run."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    from obsidian_ai_tools.flashcard_extraction import FlashcardError

    candidate = MagicMock()
    candidate.file_path = vault_path / "note.md"
    candidate.title = "Note"
    candidate.tags = ["ai"]

    with (
        patch(
            "obsidian_ai_tools.commands.flashcards.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.find_flashcard_candidates",
            return_value=[candidate],
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.estimate_flashcard_cost",
            return_value=0.001,
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.generate_flashcards",
            side_effect=FlashcardError("timeout"),
        ),
    ):
        result = runner.invoke(
            app,
            ["flashcards", "--tag", "ai", "--confirm", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "Generated: 0/1" in result.output


def test_flashcards_batch_write_returns_none_counts_as_skipped(tmp_path: Path) -> None:
    """When write_flashcard_file returns None the note counts as skipped."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    candidate = MagicMock()
    candidate.file_path = vault_path / "note.md"
    candidate.title = "Note"
    candidate.tags = ["ai"]
    cards = [{"question": "Q?", "answer": "A."}]

    with (
        patch(
            "obsidian_ai_tools.commands.flashcards.get_settings",
            return_value=_make_dummy_settings(vault_path),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.find_flashcard_candidates",
            return_value=[candidate],
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.estimate_flashcard_cost",
            return_value=0.001,
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.generate_flashcards",
            return_value=(cards, 0.001),
        ),
        patch(
            "obsidian_ai_tools.flashcard_extraction.write_flashcard_file",
            return_value=None,
        ),
    ):
        result = runner.invoke(
            app,
            ["flashcards", "--tag", "ai", "--confirm", "--vault", str(vault_path)],
        )

    assert result.exit_code == 0
    assert "Skipped:   1" in result.output
