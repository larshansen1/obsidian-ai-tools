"""Tests for data models."""

from obsidian_ai_tools.models import Note


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
