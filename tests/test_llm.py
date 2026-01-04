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
