"""Tests for vault overview generation and formatting."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_ai_tools.indexer import NoteMetadata, VaultIndex
from obsidian_ai_tools.overview import (
    _build_overview,
    format_overview_compact,
    format_overview_json,
    format_overview_markdown,
    format_overview_terminal,
    generate_overview,
)


def _make_note(
    path: Path,
    title: str,
    content: str = "content",
    tags: list[str] | None = None,
) -> NoteMetadata:
    return NoteMetadata(
        file_path=path,
        title=title,
        tags=tags or [],
        created=datetime(2026, 1, 1),
        author=None,
        source_url=None,
        source_type=None,
        content=content,
        modified_time=0.0,
    )


@pytest.fixture
def multi_folder_index(tmp_path: Path) -> tuple[VaultIndex, Path]:
    """VaultIndex with notes in 3 distinct folders."""
    vault = tmp_path / "vault"
    vault.mkdir()

    notes = [
        _make_note(
            vault / "ai" / "transformers.md",
            "Transformers",
            "attention transformer bert gpt language model neural network",
            ["ai", "llm"],
        ),
        _make_note(
            vault / "ai" / "rag.md",
            "RAG",
            "retrieval augmented generation embedding vector search",
            ["ai", "rag"],
        ),
        _make_note(
            vault / "dev" / "python.md",
            "Python",
            "python programming async await decorator class",
            ["python", "backend"],
        ),
        _make_note(
            vault / "dev" / "docker.md",
            "Docker",
            "docker container kubernetes deployment infrastructure",
            ["devops", "backend"],
        ),
        _make_note(
            vault / "notes" / "misc.md",
            "Misc",
            "random thoughts ideas brainstorm",
            [],
        ),
    ]
    index = VaultIndex(notes=notes, index_path=vault / ".kai" / "index.json")
    return index, vault


class TestGenerateOverview:
    def test_generate_overview_calls_build_index(self, tmp_path: Path) -> None:
        """generate_overview delegates to build_index."""
        vault = tmp_path / "vault"
        vault.mkdir()
        fake_index = VaultIndex(notes=[], index_path=vault / ".kai" / "index.json")

        with patch("obsidian_ai_tools.overview.build_index", return_value=fake_index) as mock_build:
            result = generate_overview(vault_path=vault, top_n=5)
            mock_build.assert_called_once_with(vault, folder=None, force_rebuild=False)
            assert result.total_notes == 0

    def test_empty_vault_returns_zero_folders(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        empty_index = VaultIndex(notes=[], index_path=vault / ".kai" / "index.json")

        result = _build_overview(empty_index, vault, top_n=5)

        assert result.total_notes == 0
        assert result.total_folders == 0
        assert result.folders == []

    def test_counts_folders_correctly(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        result = _build_overview(index, vault, top_n=5)
        assert result.total_folders == 3

    def test_note_counts_per_folder(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        result = _build_overview(index, vault, top_n=5)

        folder_map = {fs.folder: fs.note_count for fs in result.folders}
        assert folder_map["ai"] == 2
        assert folder_map["dev"] == 2
        assert folder_map["notes"] == 1

    def test_total_notes_correct(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        result = _build_overview(index, vault, top_n=5)
        assert result.total_notes == 5

    def test_keywords_are_strings(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        result = _build_overview(index, vault, top_n=5)
        for fs in result.folders:
            assert all(isinstance(k, str) for k in fs.top_keywords)

    def test_top_tags_sorted_by_count(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        result = _build_overview(index, vault, top_n=5)
        dev_folder = next(fs for fs in result.folders if fs.folder == "dev")
        counts = [count for _, count in dev_folder.top_tags]
        assert counts == sorted(counts, reverse=True)

    def test_folders_sorted_alphabetically(
        self, multi_folder_index: tuple[VaultIndex, Path]
    ) -> None:
        index, vault = multi_folder_index
        result = _build_overview(index, vault, top_n=5)
        names = [fs.folder for fs in result.folders]
        assert names == sorted(names)

    def test_note_without_tags_no_top_tags(
        self, multi_folder_index: tuple[VaultIndex, Path]
    ) -> None:
        index, vault = multi_folder_index
        result = _build_overview(index, vault, top_n=5)
        notes_folder = next(fs for fs in result.folders if fs.folder == "notes")
        assert notes_folder.top_tags == []


class TestFormatOverviewTerminal:
    def test_contains_folder_names(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        output = format_overview_terminal(overview)
        assert "ai" in output
        assert "dev" in output
        assert "notes" in output

    def test_contains_note_counts(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        output = format_overview_terminal(overview)
        assert "5 notes" in output

    def test_returns_string(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        assert isinstance(format_overview_terminal(overview), str)


class TestFormatOverviewMarkdown:
    def test_has_frontmatter(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        output = format_overview_markdown(overview)
        assert output.startswith("---")
        assert "title:" in output

    def test_contains_folder_headers(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        output = format_overview_markdown(overview)
        assert "## ai" in output
        assert "## dev" in output


class TestFormatOverviewJson:
    def test_is_valid_json(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        output = format_overview_json(overview)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_json_structure(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        data = json.loads(format_overview_json(overview))
        assert "total_notes" in data
        assert "total_folders" in data
        assert "folders" in data
        assert "generated_at" in data
        assert isinstance(data["folders"], list)
        folder = data["folders"][0]
        assert "folder" in folder
        assert "note_count" in folder
        assert "top_keywords" in folder
        assert "top_tags" in folder


class TestFormatOverviewCompact:
    def test_one_line_per_folder(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        output = format_overview_compact(overview)
        # First line: vault summary; second: "---"; then one per folder
        lines = output.split("\n")
        assert lines[0].startswith("vault:")
        assert lines[1] == "---"
        # Remaining lines should equal number of folders
        folder_lines = lines[2:]
        assert len(folder_lines) == overview.total_folders

    def test_pipe_delimited(self, multi_folder_index: tuple[VaultIndex, Path]) -> None:
        index, vault = multi_folder_index
        overview = _build_overview(index, vault, top_n=5)
        output = format_overview_compact(overview)
        folder_lines = output.split("\n")[2:]
        for line in folder_lines:
            # Each line with keywords/tags should be pipe-delimited
            assert "|" in line or line.strip().endswith("notes)")
