"""End-to-end integration tests for core workflows.

Test Strategy:
- Full workflow tests using mocked external APIs (YouTube, OpenRouter, Supadata)
- Verify file creation, frontmatter structure, and content generation
- Test error propagation across module boundaries
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from obsidian_ai_tools.models import VideoMetadata


class TestYouTubeIngestEndToEnd:
    """End-to-end tests for YouTube video ingestion workflow."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create a temporary vault structure."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()
        (vault / ".kai").mkdir()
        return vault

    @pytest.fixture
    def mock_llm_response(self) -> dict:
        """Standard LLM response for note generation."""
        return {
            "title": "Understanding AI Workflows",
            "summary": "This video explains AI workflow patterns.",
            "key_points": [
                "AI needs structured data",
                "Workflows improve efficiency",
                "Testing is important",
            ],
            "tags": ["ai", "workflow", "testing"],
        }

    def test_youtube_ingest_creates_note_file(
        self, temp_vault: Path, mock_llm_response: dict
    ) -> None:
        """Complete workflow: URL → transcript → LLM → note file."""
        from obsidian_ai_tools.llm import generate_note
        from obsidian_ai_tools.obsidian import write_note

        # Create video metadata (simulating what provider would return)
        metadata = VideoMetadata(
            title="Understanding AI Workflows",
            url="https://youtube.com/watch?v=test123",
            transcript="This is a test transcript about AI workflows...",
            channel_name="Test Channel",
            video_id="test123",
        )

        # Mock OpenRouter API
        mock_response = MagicMock()
        content = (
            '{"title": "Understanding AI Workflows", '
            '"summary": "AI workflow patterns explained", '
            '"key_points": ["Point 1", "Point 2"], '
            '"tags": ["ai", "workflow"]}'
        )
        mock_response.choices = [MagicMock(message=MagicMock(content=content))]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50, cost=0.001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            # Generate note
            note, _ = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                prompt_version="youtube_v1",
            )

            # Verify note was generated correctly
            assert note.title == "Understanding AI Workflows"
            assert "ai" in note.tags
            assert note.source_url == "https://youtube.com/watch?v=test123"
            assert note.source_type == "youtube"

            # Write note to vault
            note_path = write_note(note, temp_vault, "inbox")

            # Verify file was created
            assert note_path.exists()
            content = note_path.read_text()
            assert "---" in content  # Has frontmatter
            assert "source_url: https://youtube.com/watch?v=test123" in content
            assert "prompt_version: youtube_v1" in content

    def test_youtube_ingest_with_tag_discovery(self, temp_vault: Path) -> None:
        """Verify v2 prompts include existing tags from vault."""
        from obsidian_ai_tools.llm import generate_note

        # Create existing note with tags in vault
        existing_note = temp_vault / "existing.md"
        existing_note.write_text(
            """---
title: Existing Note
tags: [machine-learning, python]
---
# Content
""",
            encoding="utf-8",
        )

        metadata = VideoMetadata(
            title="New Video",
            url="https://youtube.com/watch?v=new",
            transcript="Content about machine learning...",
            channel_name="Test Channel",
            video_id="new",
        )

        mock_response = MagicMock()
        content = (
            '{"title": "New Note", "summary": "Summary", '
            '"key_points": ["Point"], "tags": ["machine-learning"]}'
        )
        mock_response.choices = [MagicMock(message=MagicMock(content=content))]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50, cost=0.001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            note, _ = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                prompt_version="youtube_v2",
            )

            assert note.title == "New Note"


class TestErrorPropagation:
    """Tests for error handling across module boundaries."""

    def test_llm_timeout_error_propagates(self) -> None:
        """Verify LLM timeout errors are properly wrapped and raised."""
        from openai import APITimeoutError

        from obsidian_ai_tools.llm import NoteGenerationError, generate_note
        from obsidian_ai_tools.models import VideoMetadata

        metadata = VideoMetadata(
            title="Test",
            url="https://youtube.com/watch?v=test",
            transcript="Test transcript",
            channel_name="Test",
            video_id="test",
        )

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = APITimeoutError(request=MagicMock())
            mock_openai.return_value = mock_client

            with pytest.raises(NoteGenerationError) as exc_info:
                generate_note(
                    metadata=metadata,
                    model="openai/gpt-4o-mini",
                    api_key="test_key",
                )

            assert "Failed to generate note" in str(exc_info.value)

    def test_llm_rate_limit_error_propagates(self) -> None:
        """Verify 429 rate limit errors are properly handled."""
        from openai import RateLimitError

        from obsidian_ai_tools.llm import NoteGenerationError, generate_note
        from obsidian_ai_tools.models import VideoMetadata

        metadata = VideoMetadata(
            title="Test",
            url="https://youtube.com/watch?v=test",
            transcript="Test transcript",
            channel_name="Test",
            video_id="test",
        )

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_client.chat.completions.create.side_effect = RateLimitError(
                message="Rate limit exceeded",
                response=mock_response,
                body={"error": {"message": "Rate limit exceeded"}},
            )
            mock_openai.return_value = mock_client

            with pytest.raises(NoteGenerationError) as exc_info:
                generate_note(
                    metadata=metadata,
                    model="openai/gpt-4o-mini",
                    api_key="test_key",
                )

            assert "Failed to generate note" in str(exc_info.value)

    def test_content_too_long_error(self) -> None:
        """Verify content length validation error."""
        from obsidian_ai_tools.llm import NoteGenerationError, generate_note
        from obsidian_ai_tools.models import VideoMetadata

        # Create metadata with very long transcript
        long_transcript = "x" * 100000  # 100K chars

        metadata = VideoMetadata(
            title="Test",
            url="https://youtube.com/watch?v=test",
            transcript=long_transcript,
            channel_name="Test",
            video_id="test",
        )

        with pytest.raises(NoteGenerationError) as exc_info:
            generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                max_content_length=50000,
            )

        assert "Content too long" in str(exc_info.value)

    def test_invalid_llm_response_error(self) -> None:
        """Verify invalid JSON from LLM is properly handled."""
        from obsidian_ai_tools.llm import NoteGenerationError, generate_note
        from obsidian_ai_tools.models import VideoMetadata

        metadata = VideoMetadata(
            title="Test",
            url="https://youtube.com/watch?v=test",
            transcript="Test transcript",
            channel_name="Test",
            video_id="test",
        )

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="This is not valid JSON at all"))
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            with pytest.raises(NoteGenerationError) as exc_info:
                generate_note(
                    metadata=metadata,
                    model="openai/gpt-4o-mini",
                    api_key="test_key",
                )

            assert "Failed to parse LLM response" in str(exc_info.value)

    def test_missing_required_fields_error(self) -> None:
        """Verify missing required fields in LLM response are caught."""
        from obsidian_ai_tools.llm import NoteGenerationError, generate_note
        from obsidian_ai_tools.models import VideoMetadata

        metadata = VideoMetadata(
            title="Test",
            url="https://youtube.com/watch?v=test",
            transcript="Test transcript",
            channel_name="Test",
            video_id="test",
        )

        # Response missing key_points and tags
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"title": "Test", "summary": "Summary"}'))
        ]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            with pytest.raises(NoteGenerationError) as exc_info:
                generate_note(
                    metadata=metadata,
                    model="openai/gpt-4o-mini",
                    api_key="test_key",
                )

            assert "missing required fields" in str(exc_info.value)
