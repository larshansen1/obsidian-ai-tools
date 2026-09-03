"""Tests for data models."""

from datetime import datetime

import pytest

from obsidian_ai_tools.models import Note

SPECIAL_YAML_CHARS = [
    ":",
    "#",
    "{",
    "}",
    "[",
    "]",
    ",",
    "&",
    "*",
    "?",
    "|",
    "-",
    "<",
    ">",
    "=",
    "!",
    "%",
    "@",
    "`",
    '"',
    "'",
]


class TestNote:
    """Tests for Note model."""

    def test_to_markdown_includes_frontmatter(self) -> None:
        """Test that markdown output includes frontmatter."""
        note = Note(
            title="Test Note",
            summary="Test summary",
            key_points=["Point 1", "Point 2"],
            tags=["test", "example"],
            source_url="https://example.com",
            model="test-model",
        )
        markdown = note.to_markdown()
        assert markdown.startswith("---")
        assert "type: source-note" in markdown
        assert "source_url: https://example.com" in markdown

    def test_to_markdown_includes_content(self) -> None:
        """Test that markdown output includes content sections."""
        note = Note(
            title="Test Note",
            summary="Test summary",
            key_points=["Point 1", "Point 2"],
            tags=["test"],
            source_url="https://example.com",
            model="test-model",
        )
        markdown = note.to_markdown()
        assert "# Test Note" in markdown
        assert "## Summary" in markdown
        assert "Test summary" in markdown
        assert "## Key Points" in markdown
        assert "- Point 1" in markdown
        assert "- Point 2" in markdown

    def test_to_markdown_includes_tags(self) -> None:
        """Test that tags are included in frontmatter."""
        note = Note(
            title="Test Note",
            summary="Test summary",
            tags=["ai", "llm", "testing"],
            source_url="https://example.com",
            model="test-model",
        )
        markdown = note.to_markdown()
        assert "  - ai" in markdown
        assert "  - llm" in markdown
        assert "  - testing" in markdown

    def test_to_markdown_frontmatter_order(self) -> None:
        """Test that title, tags, created are first three frontmatter attributes."""
        note = Note(
            title="Test Note",
            summary="Test summary",
            tags=["ai", "testing"],
            source_url="https://example.com",
            model="test-model",
        )
        markdown = note.to_markdown()

        # Extract frontmatter section
        frontmatter_end = markdown.find("---", 3)
        frontmatter = markdown[3:frontmatter_end]

        # Find positions of key attributes
        title_pos = frontmatter.find("title:")
        tags_pos = frontmatter.find("tags:")
        created_pos = frontmatter.find("created:")

        # Verify they appear in correct order
        assert title_pos < tags_pos, "title should come before tags"
        assert tags_pos < created_pos, "tags should come before created"

    def test_to_markdown_includes_author(self) -> None:
        """Test that author field is included when provided."""
        note = Note(
            title="Test Note",
            summary="Test summary",
            author="Test Channel",
            tags=["test"],
            source_url="https://example.com",
            model="test-model",
        )
        markdown = note.to_markdown()
        assert "author: Test Channel" in markdown

    def test_to_markdown_omits_author_when_none(self) -> None:
        """Test that author field is omitted when None."""
        note = Note(
            title="Test Note",
            summary="Test summary",
            author=None,
            tags=["test"],
            source_url="https://example.com",
            model="test-model",
        )
        markdown = note.to_markdown()
        assert "author:" not in markdown

    def test_to_markdown_includes_source_references(self) -> None:
        """Test that source file references are included when provided."""
        note = Note(
            title="Test Note",
            summary="Test summary",
            tags=["test"],
            source_url="https://github.com/user/repo",
            source_type="github",
            source_references=[
                "[README.md](https://github.com/user/repo/blob/main/README.md)",
                "[docs/usage.md](https://github.com/user/repo/blob/main/docs/usage.md)",
            ],
            model="test-model",
        )

        markdown = note.to_markdown()

        assert "source_type: github" in markdown
        assert "## Source Files" in markdown
        assert "- [README.md](https://github.com/user/repo/blob/main/README.md)" in markdown
        assert "- [docs/usage.md](https://github.com/user/repo/blob/main/docs/usage.md)" in markdown

    def test_github_note_uses_repository_sections(self) -> None:
        """GitHub notes should render repo sections instead of generic key points."""
        note = Note(
            title="Test Repo",
            summary="Repository summary",
            key_points=[
                "Purpose: Explain the project goal",
                "Architecture Read: Bounded GitHub documentation flows into a note renderer",
                "Design Principles and Tradeoffs: Local-first and bounded",
                "Technology and Runtime: Python and GitHub API",
                "Usage Surface: CLI ingestion",
                "Security Posture: Configure GITHUB_TOKEN for private repos",
                "Operational Maturity: Usable personal tool with focused tests",
                "Caveats and Unknowns: Documentation may be incomplete",
            ],
            claims=["The docs claim bounded repo ingestion"],
            implications=["Repo notes are easier to scan"],
            tags=["github"],
            source_url="https://github.com/user/repo",
            source_type="github",
            source_references=["[README.md](https://github.com/user/repo/blob/main/README.md)"],
            model="test-model",
        )

        markdown = note.to_markdown()

        assert "## Key Points" not in markdown
        assert "## Purpose" in markdown
        assert "- Explain the project goal" in markdown
        assert "## Architecture Read" in markdown
        assert "## Design Principles and Tradeoffs" in markdown
        assert "## Technology and Runtime" in markdown
        assert "## Usage Surface" in markdown
        assert "## Security Posture" in markdown
        assert "## Operational Maturity" in markdown
        assert "## Caveats and Unknowns" in markdown
        assert "## Evidence Highlights" in markdown
        assert "## Adoption Fit" in markdown
        assert "[GitHub Repository](https://github.com/user/repo)" in markdown


class TestYamlEscape:
    """Tests for YAML frontmatter value escaping."""

    @pytest.mark.parametrize("char", SPECIAL_YAML_CHARS)
    def test_quotes_value_containing_only_special_char(self, char: str) -> None:
        """Any single special character on its own still forces quoting."""
        note = Note(title="T", summary="S", source_url="u", model="m")
        value = f"head{char}tail"
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        assert note._yaml_escape(value) == f'"{escaped}"'

    def test_escapes_backslash_before_quote(self) -> None:
        """Backslashes are doubled first so escaped quotes stay intact."""
        note = Note(title="T", summary="S", source_url="u", model="m")
        assert note._yaml_escape('a"b\\c: d') == '"a\\"b\\\\c: d"'

    def test_leaves_plain_values_unquoted(self) -> None:
        """Values without special characters pass through untouched."""
        note = Note(title="T", summary="S", source_url="u", model="m")
        assert note._yaml_escape("plain value") == "plain value"


class TestNoteExactMarkdown:
    """Tests pinning the exact rendered markdown for each source type."""

    def test_web_note_exact_output(self) -> None:
        """Web notes render exact frontmatter plus body sections in order."""
        note = Note(
            title='Web: "Quoted" Note',
            summary="Summary text",
            key_points=["Point one", "Point two"],
            claims=["Claim A", "Claim B"],
            implications=["Implication 1"],
            tags=["ai", "testing"],
            author='Author "Name"',
            source_url="https://example.com/article",
            source_type="web",
            source_references=["[file](https://example.com/file)"],
            model="test-model",
            prompt_version="article_v1",
            created_at=datetime(2025, 1, 1, 12, 30, 45),
        )

        frontmatter = (
            "---\n"
            'title: "Web: \\"Quoted\\" Note"\n'
            "tags:\n"
            "  - ai\n"
            "  - testing\n"
            "created: 2025-01-01T12:30:45\n"
            'author: "Author \\"Name\\""\n'
            "type: source-note\n"
            "source_type: web\n"
            "source_url: https://example.com/article\n"
            "model: test-model\n"
            "prompt_version: article_v1\n"
            "---\n"
        )
        body = (
            '# Web: "Quoted" Note\n'
            "\n"
            "## Summary\n"
            "\n"
            "Summary text\n"
            "\n"
            "## Key Claims\n"
            "\n"
            "- Claim A\n"
            "- Claim B\n"
            "\n"
            "## Key Points\n"
            "\n"
            "- Point one\n"
            "- Point two\n"
            "\n"
            "## Implications\n"
            "\n"
            "- Implication 1\n"
            "\n"
            "## Source\n"
            "\n"
            "[Original Source](https://example.com/article)\n"
            "\n"
            "## Source Files\n"
            "\n"
            "- [file](https://example.com/file)\n"
        )

        assert note.to_markdown() == frontmatter + "\n" + body

    def test_youtube_note_exact_output(self) -> None:
        """YouTube notes render the video link label and v2 fields."""
        note = Note(
            title="Video Note",
            summary="About videos",
            tags=["x"],
            source_url="https://youtube.com/watch?v=abc",
            source_type="youtube",
            model="m",
            prompt_version="youtube_v2",
            created_at=datetime(2025, 2, 3, 4, 5, 6),
        )

        frontmatter = (
            "---\n"
            "title: Video Note\n"
            "tags:\n"
            "  - x\n"
            "created: 2025-02-03T04:05:06\n"
            "type: source-note\n"
            "source_type: youtube\n"
            "source_url: https://youtube.com/watch?v=abc\n"
            "model: m\n"
            "prompt_version: youtube_v2\n"
            "---\n"
        )
        body = (
            "# Video Note\n"
            "\n"
            "## Summary\n"
            "\n"
            "About videos\n"
            "\n"
            "## Key Points\n"
            "\n"
            "\n"
            "## Source\n"
            "\n"
            "[Original Video](https://youtube.com/watch?v=abc)\n"
        )

        assert note.to_markdown() == frontmatter + "\n" + body

    def test_github_note_exact_output(self) -> None:
        """GitHub notes join the exact same frontmatter to the repo body."""
        note = Note(
            title="Test Repo",
            summary="Repository summary",
            key_points=["architecture: Bounded flows"],
            tags=["github"],
            source_url="https://github.com/user/repo",
            source_type="github",
            model="test-model",
            prompt_version="github_repo_v1",
            created_at=datetime(2025, 3, 4, 10, 0, 0),
        )

        frontmatter = (
            "---\n"
            "title: Test Repo\n"
            "tags:\n"
            "  - github\n"
            "created: 2025-03-04T10:00:00\n"
            "type: source-note\n"
            "source_type: github\n"
            "source_url: https://github.com/user/repo\n"
            "model: test-model\n"
            "prompt_version: github_repo_v1\n"
            "---\n"
        )
        body = (
            "# Test Repo\n"
            "\n"
            "## Summary\n"
            "\n"
            "Repository summary\n"
            "\n"
            "## Architecture Read\n"
            "\n"
            "- Bounded flows\n"
            "\n"
            "## Source\n"
            "\n"
            "[GitHub Repository](https://github.com/user/repo)\n"
        )

        assert note.to_markdown() == frontmatter + "\n" + body


class TestGithubSections:
    """Tests for the GitHub section extraction and body rendering."""

    def test_short_alias_key_points_render_exact_body(self) -> None:
        """Every short alias maps to its canonical section heading."""
        note = Note(
            title="Short Aliases",
            summary="Repo summary",
            key_points=[
                "purpose: goal",
                "architecture: flows",
                "principles: local first",
                "design principles: keep small",
                "technology: python",
                "usage: cli",
                "setup: pip install",
                "setup and operations: run the service",
                "security: token",
                "maturity: usable",
                "caveats: incomplete",
                "plain point",
            ],
            tags=["github"],
            source_url="https://github.com/user/repo",
            source_type="github",
            model="test-model",
        )

        assert note._github_body() == (
            "# Short Aliases\n"
            "\n"
            "## Summary\n"
            "\n"
            "Repo summary\n"
            "\n"
            "## Purpose\n"
            "\n"
            "- goal\n"
            "\n"
            "## Architecture Read\n"
            "\n"
            "- flows\n"
            "\n"
            "## Design Principles and Tradeoffs\n"
            "\n"
            "- keep small\n"
            "\n"
            "## Principles\n"
            "\n"
            "- local first\n"
            "\n"
            "## Technology\n"
            "\n"
            "- python\n"
            "\n"
            "## Usage Surface\n"
            "\n"
            "- cli\n"
            "\n"
            "## Security Posture\n"
            "\n"
            "- token\n"
            "\n"
            "## Operational Maturity\n"
            "\n"
            "- usable\n"
            "\n"
            "## Setup and Operations\n"
            "\n"
            "- pip install\n"
            "- run the service\n"
            "\n"
            "## Caveats\n"
            "\n"
            "- incomplete\n"
            "\n"
            "## Additional Notes\n"
            "\n"
            "- plain point\n"
            "\n"
            "## Source\n"
            "\n"
            "[GitHub Repository](https://github.com/user/repo)\n"
        )

    def test_body_includes_evidence_adoption_and_source_files(self) -> None:
        """Claims, implications and references render at the tail of the body."""
        note = Note(
            title="Test Repo",
            summary="Repository summary",
            key_points=["architecture: Bounded flows", "plain note"],
            claims=["The docs claim bounded repo ingestion"],
            implications=["Repo notes are easier to scan"],
            tags=["github"],
            source_url="https://github.com/user/repo",
            source_type="github",
            source_references=["[README.md](https://github.com/user/repo/blob/main/README.md)"],
            model="test-model",
        )

        assert note._github_body() == (
            "# Test Repo\n"
            "\n"
            "## Summary\n"
            "\n"
            "Repository summary\n"
            "\n"
            "## Architecture Read\n"
            "\n"
            "- Bounded flows\n"
            "\n"
            "## Additional Notes\n"
            "\n"
            "- plain note\n"
            "\n"
            "## Evidence Highlights\n"
            "\n"
            "- The docs claim bounded repo ingestion\n"
            "\n"
            "## Adoption Fit\n"
            "\n"
            "- Repo notes are easier to scan\n"
            "\n"
            "## Source\n"
            "\n"
            "[GitHub Repository](https://github.com/user/repo)\n"
            "\n"
            "## Source Files\n"
            "\n"
            "- [README.md](https://github.com/user/repo/blob/main/README.md)\n"
        )

    def test_labels_split_on_first_colon_only(self) -> None:
        """Details may contain colons; only the first colon separates label."""
        note = Note(
            title="T",
            summary="S",
            key_points=["Purpose: first: second"],
            source_url="https://github.com/user/repo",
            source_type="github",
            model="m",
        )

        assert note._github_sections() == {"Purpose": ["first: second"]}

    def test_unrecognized_points_land_in_additional_notes(self) -> None:
        """Unknown labels and unlabeled points collect under Additional Notes."""
        note = Note(
            title="T",
            summary="S",
            key_points=["plain point", "novel idea: with detail"],
            source_url="https://github.com/user/repo",
            source_type="github",
            model="m",
        )

        assert note._github_sections() == {
            "Additional Notes": ["plain point", "novel idea: with detail"]
        }
