"""Tests for LLM integration functionality."""

import pytest

from obsidian_ai_tools.llm import (
    NoteGenerationError,
    build_prompt,
    parse_llm_response,
)
from obsidian_ai_tools.models import VideoMetadata


class TestBuildPrompt:
    """Tests for build_prompt function."""

    @pytest.fixture
    def sample_metadata(self) -> VideoMetadata:
        """Create sample video metadata."""
        return VideoMetadata(
            video_id="test123",
            title="Test Video",
            url="https://youtube.com/watch?v=test123",
            transcript="This is a test transcript.",
            channel_name="Test Channel",
        )

    @pytest.fixture
    def sample_template(self) -> str:
        """Create sample prompt template."""
        return """Title: {title}
URL: {url}
Transcript: {transcript}"""

    def test_prompt_formatting(self, sample_metadata: VideoMetadata, sample_template: str) -> None:
        """Test that prompt is formatted correctly."""
        result = build_prompt(sample_metadata, sample_template)
        assert "Test Video" in result
        assert "https://youtube.com/watch?v=test123" in result
        assert "This is a test transcript." in result


class TestParseLLMResponse:
    """Tests for parse_llm_response function."""

    def test_parse_plain_json(self) -> None:
        """Test parsing plain JSON response."""
        response = '{"title": "Test", "tags": ["tag1", "tag2"]}'
        result = parse_llm_response(response)
        assert result["title"] == "Test"
        assert result["tags"] == ["tag1", "tag2"]

    def test_parse_json_in_code_block(self) -> None:
        """Test parsing JSON wrapped in ```json code block."""
        response = '```json\n{"title": "Test", "tags": ["tag1"]}\n```'
        result = parse_llm_response(response)
        assert result["title"] == "Test"

    def test_parse_json_in_generic_code_block(self) -> None:
        """Test parsing JSON wrapped in ``` code block."""
        response = '```\n{"title": "Test"}\n```'
        result = parse_llm_response(response)
        assert result["title"] == "Test"

    def test_invalid_json_raises_error(self) -> None:
        """Test that invalid JSON raises NoteGenerationError."""
        with pytest.raises(NoteGenerationError):
            parse_llm_response("This is not JSON")

    def test_parse_with_extra_text(self) -> None:
        """Test parsing JSON with surrounding text."""
        response = 'Here is the result:\n```json\n{"title": "Test"}\n```\nDone!'
        result = parse_llm_response(response)
        assert result["title"] == "Test"


class TestLLMResponseEdgeCases:
    """Comprehensive edge case tests for LLM response parsing."""

    def test_wrong_type_in_tags_field(self) -> None:
        """Test handling when tags field is string instead of array."""
        response = '{"title": "Test", "tags": "single-tag"}'
        result = parse_llm_response(response)
        # Parser should succeed - validation happens at model level
        assert result["tags"] == "single-tag"

    def test_missing_required_fields(self) -> None:
        """Test response missing expected fields."""
        response = '{"title": "Test"}'
        result = parse_llm_response(response)
        # Parser succeeds - field validation is model's responsibility
        assert result["title"] == "Test"
        assert "tags" not in result

    def test_extra_unknown_fields(self) -> None:
        """Test response with extra fields not in schema."""
        response = '{"title": "Test", "tags": [], "extra_field": "value", "another": 123}'
        result = parse_llm_response(response)
        assert result["title"] == "Test"
        assert result["extra_field"] == "value"
        assert result["another"] == 123

    def test_nested_json_in_markdown(self) -> None:
        """Test extracting JSON from complex markdown with multiple code blocks."""
        response = """
        Here's some explanation.

        ```python
        # This is code, not JSON
        print("hello")
        ```

        And here's the actual result:

        ```json
        {"title": "Real Result", "tags": ["test"]}
        ```

        Some more text.
        """
        result = parse_llm_response(response)
        assert result["title"] == "Real Result"

    def test_multiple_json_blocks_uses_first(self) -> None:
        """Test that first JSON block is used when multiple present."""
        response = """
        ```json
        {"title": "First"}
        ```

        ```json
        {"title": "Second"}
        ```
        """
        result = parse_llm_response(response)
        assert result["title"] == "First"

    def test_json_with_unicode_characters(self) -> None:
        """Test parsing JSON with unicode characters."""
        response = '{"title": "Test 测试 🎉", "tags": ["日本語", "emoji🚀"]}'
        result = parse_llm_response(response)
        assert "测试" in result["title"]
        assert "🎉" in result["title"]
        assert "日本語" in result["tags"]

    def test_json_with_escaped_quotes(self) -> None:
        """Test JSON with escaped quotes in strings."""
        response = '{"title": "He said \\"Hello\\"", "summary": "It\'s working"}'
        result = parse_llm_response(response)
        assert result["title"] == 'He said "Hello"'

    def test_json_with_newlines_in_strings(self) -> None:
        """Test JSON with newline characters in strings."""
        response = '{"title": "Multi\\nLine\\nTitle", "summary": "Line 1\\nLine 2"}'
        result = parse_llm_response(response)
        assert "\n" in result["title"]
        assert result["title"].count("\n") == 2

    def test_empty_response(self) -> None:
        """Test handling of empty response."""
        with pytest.raises(NoteGenerationError):
            parse_llm_response("")

    def test_whitespace_only_response(self) -> None:
        """Test handling of whitespace-only response."""
        with pytest.raises(NoteGenerationError):
            parse_llm_response("   \n\n  \t  ")

    def test_json_with_trailing_comma(self) -> None:
        """Test invalid JSON with trailing comma."""
        response = '{"title": "Test", "tags": ["tag1",]}'
        # Should raise error - trailing comma is invalid JSON
        with pytest.raises(NoteGenerationError):
            parse_llm_response(response)

    def test_json_with_comments(self) -> None:
        """Test JSON with JavaScript-style comments (invalid in strict JSON)."""
        response = """
        {
            "title": "Test",  // This is a title
            "tags": ["tag1"]  /* Multi-line comment */
        }
        """
        # Should raise error - comments not allowed in JSON
        with pytest.raises(NoteGenerationError):
            parse_llm_response(response)

    def test_incomplete_json(self) -> None:
        """Test truncated/incomplete JSON."""
        response = '{"title": "Test", "tags": ["tag1", '
        with pytest.raises(NoteGenerationError):
            parse_llm_response(response)

    def test_json_array_instead_of_object(self) -> None:
        """Test when LLM returns array instead of object."""
        response = '[{"title": "Test1"}, {"title": "Test2"}]'
        result = parse_llm_response(response)
        # Parser returns the array as-is
        assert isinstance(result, list)
        assert len(result) == 2

    def test_null_values_in_json(self) -> None:
        """Test JSON with null values."""
        response = '{"title": "Test", "author": null, "tags": null}'
        result = parse_llm_response(response)
        assert result["title"] == "Test"
        assert result["author"] is None
        assert result["tags"] is None

    def test_very_long_response(self) -> None:
        """Test handling of very long JSON response."""
        # Simulate a response near token limit
        long_text = "word " * 10000  # ~50k characters
        response = f'{{"title": "Test", "summary": "{long_text}"}}'
        result = parse_llm_response(response)
        assert result["title"] == "Test"
        assert len(result["summary"]) > 40000


class TestBuildPromptEdgeCases:
    """Edge case tests for prompt building."""

    def test_prompt_with_missing_template_variable(self) -> None:
        """Test template missing a required variable."""
        metadata = VideoMetadata(
            video_id="test",
            title="Test",
            url="https://test.com",
            transcript="transcript",
            channel_name="Channel",
        )
        template = "Title: {title}\nNonexistent: {nonexistent_field}"

        # Should raise KeyError for missing field
        with pytest.raises(KeyError):
            build_prompt(metadata, template)

    def test_prompt_with_very_long_transcript(self) -> None:
        """Test prompt building with very long transcript."""
        long_transcript = "word " * 50000  # Very long transcript
        metadata = VideoMetadata(
            video_id="test",
            title="Test",
            url="https://test.com",
            transcript=long_transcript,
            channel_name="Channel",
        )
        template = "Transcript: {transcript}"

        result = build_prompt(metadata, template)
        assert len(result) > 200000  # Should include full transcript

    def test_prompt_with_special_characters(self) -> None:
        """Test prompt with special characters in metadata."""
        metadata = VideoMetadata(
            video_id="test",
            title="Test: <Special> & {Chars} 'Quotes'",
            url="https://test.com?a=1&b=2",
            transcript='Transcript with "quotes" and {braces}',
            channel_name="Channel",
        )
        template = "Title: {title}\nTranscript: {transcript}"

        result = build_prompt(metadata, template)
        assert "<Special>" in result
        assert "{Chars}" in result
        assert '"quotes"' in result
