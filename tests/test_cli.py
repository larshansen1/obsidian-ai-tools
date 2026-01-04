"""Integration tests for the CLI."""

from pathlib import Path

from typer.testing import CliRunner

from obsidian_ai_tools.cli import app

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
