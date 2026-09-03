"""Tests for LLM integration functionality."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from obsidian_ai_tools.llm import (
    NoteGenerationError,
    PromptTemplateError,
    build_prompt,
    generate_note,
    load_prompt_template,
    parse_llm_response,
)
from obsidian_ai_tools.models import ArticleMetadata, VideoMetadata


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

    def test_prompt_includes_github_repository_metadata(self) -> None:
        """GitHub repo metadata should format through article-style templates."""
        metadata = ArticleMetadata(
            title="user/repo repository documentation",
            url="https://github.com/user/repo",
            author="user",
            site_name="GitHub Repository",
            content="Purpose: Test repository",
            source_type="github",
            source_references=["[README.md](https://github.com/user/repo/blob/main/README.md)"],
        )
        template = "Title: {title}\nURL: {url}\nContent: {content}"

        result = build_prompt(metadata, template)

        assert "user/repo repository documentation" in result
        assert "https://github.com/user/repo" in result
        assert "Purpose: Test repository" in result


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


class TestGenerateNote:
    """Tests for note assembly from LLM responses."""

    def test_generate_note_preserves_github_source_type_and_references(self) -> None:
        """GitHub metadata should produce github notes with deterministic file references."""
        metadata = ArticleMetadata(
            title="user/repo repository documentation",
            url="https://github.com/user/repo",
            content="Repository docs",
            author="user",
            site_name="GitHub Repository",
            source_type="github",
            source_references=["[README.md](https://github.com/user/repo/blob/main/README.md)"],
        )
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=(
                        '{"title": "Repo", "summary": "Summary", '
                        '"key_points": ["Purpose"], "tags": ["repo"]}'
                    )
                )
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, cost=0.001)

        with (
            patch("obsidian_ai_tools.llm.load_prompt_template", return_value="{content}"),
            patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
        ):
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client
            note, cost_info = generate_note(
                metadata=metadata,
                model="test-model",
                api_key="test-key",
                prompt_version="github_repo_v1",
            )

        assert note.source_type == "github"
        assert cost_info.source_type == "github"
        assert note.source_references == metadata.source_references

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


class TestLoadPromptTemplate:
    """Template loading from the prompts directory."""

    def test_loads_default_youtube_template(self) -> None:
        """Calling without args must load the real youtube_v1 template."""
        content = load_prompt_template()

        assert "YouTube video transcripts" in content
        assert "{title}" in content
        assert "{transcript}" in content

    def test_missing_template_raises_exact_message(self) -> None:
        """A missing template file must mention the template path."""
        with pytest.raises(PromptTemplateError) as exc:
            load_prompt_template("does_not_exist_template_xyz")

        assert "Prompt template not found:" in str(exc.value)
        assert "does_not_exist_template_xyz.md" in str(exc.value)

    def test_read_failure_raises_exact_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """I/O failures while reading must surface as PromptTemplateError."""
        from pathlib import Path

        def failing_read(self: Path, *args: object, **kwargs: object) -> str:
            raise OSError("disk error")

        monkeypatch.setattr(Path, "read_text", failing_read)

        with pytest.raises(PromptTemplateError, match="Failed to read prompt template: disk error"):
            load_prompt_template()


class TestBuildPromptTagsPlaceholder:
    """The EXISTING_TAGS placeholder contract."""

    @pytest.fixture
    def video(self) -> VideoMetadata:
        return VideoMetadata(
            video_id="v1",
            title="Tag Video",
            url="https://youtube.com/watch?v=v1",
            transcript="A transcript body.",
            channel_name="Channel",
        )

    def test_default_tags_message_when_none(self, video: VideoMetadata) -> None:
        """No existing tags yields the exact fallback sentence."""
        template = "Title: {title}\nTags: {EXISTING_TAGS}"

        result = build_prompt(video, template)

        assert "No existing tags available." in result

    def test_provided_tags_are_injected(self, video: VideoMetadata) -> None:
        """Provided tags replace the fallback sentence."""
        template = "Tags: {EXISTING_TAGS}"

        result = build_prompt(video, template, existing_tags="tag-one, tag-two")

        assert "tag-one, tag-two" in result
        assert "No existing tags available." not in result

    def test_article_tags_placeholder(self) -> None:
        """Article metadata also fills the EXISTING_TAGS placeholder."""
        metadata = ArticleMetadata(
            title="Article", url="https://example.com/a", content="Body text"
        )
        template = "Tags: {EXISTING_TAGS}"

        assert build_prompt(metadata, template) == "Tags: No existing tags available."

    def test_article_author_and_site_name_fallback_to_exact_defaults(self) -> None:
        """Missing author/site_name use the exact 'Unknown'/'Unknown Site' defaults."""
        metadata = ArticleMetadata(title="No Author", url="https://example.com/a", content="Body")
        template = "Author: {author}\nSite: {site_name}"

        result = build_prompt(metadata, template)

        assert result == "Author: Unknown\nSite: Unknown Site"

    def test_article_custom_author_and_site_name_are_kept(self) -> None:
        """Provided author/site_name must not be replaced by defaults."""
        metadata = ArticleMetadata(
            title="Authored",
            url="https://example.com/a",
            content="Body",
            author="Jane Doe",
            site_name="Example Docs",
        )
        template = "Author: {author}\nSite: {site_name}"

        result = build_prompt(metadata, template)

        assert result == "Author: Jane Doe\nSite: Example Docs"


class TestParseLLMResponseFences:
    """Fence boundary and multi-fence selection in parse_llm_response."""

    def test_json_fence_directly_adjacent_to_content(self) -> None:
        """No whitespace between fence and JSON must still parse."""
        response = '```json{"a": 1}```'

        result = parse_llm_response(response)

        assert result == {"a": 1}

    def test_generic_fence_directly_adjacent_to_content(self) -> None:
        """Generic fence with JSON starting immediately after it must parse."""
        response = '```{"a": 1}```'

        result = parse_llm_response(response)

        assert result == {"a": 1}

    def test_multiple_fences_use_first_closing_fence(self) -> None:
        """JSON extraction must stop at the first closing fence, not the last."""
        response = '```json\n{"title": "First"}\n```\nmore text\n```json\n{"title": "Second"}\n```'

        result = parse_llm_response(response)

        assert result == {"title": "First"}


class TestGenerateNoteExactConstruction:
    """Exact OpenAI arguments, model calls, and Note/CostInfo assembly."""

    VALID_RESPONSE = (
        '{"title": "Note Title", "summary": "The summary", '
        '"key_points": ["one", "two"], "tags": ["tag1", "tag2"], '
        '"claims": ["claim"], "implications": ["impl"]}'
    )
    TEMPLATE = "Title: {title}\nURL: {url}\nTranscript: {transcript}\nTags: {EXISTING_TAGS}"

    def _make_video(self) -> VideoMetadata:
        return VideoMetadata(
            video_id="vid1",
            title="Video Title",
            url="https://youtube.com/watch?v=vid1",
            transcript="This is a test transcript.",
            channel_name="My Channel",
        )

    def _suite(
        self,
        response: MagicMock,
        metadata: VideoMetadata | ArticleMetadata,
        template: str | None = None,
    ) -> tuple[Any, ...]:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response
        with (
            patch(
                "obsidian_ai_tools.llm.load_prompt_template",
                return_value=template if template is not None else self.TEMPLATE,
            ) as mock_load,
            patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
        ):
            mock_openai.return_value = mock_client
            note, cost_info = generate_note(
                metadata=metadata,
                model="model-x",
                api_key="key-1",
                existing_tags="tag_a",
                prompt_version="pv2",
                base_url="https://llm.example/v1",
            )
        return note, cost_info, mock_openai, mock_client, mock_load

    def test_openai_constructor_and_create_call_are_exact(self) -> None:
        """OpenAI args, messages, temperature, and extra_body must be exact."""
        metadata = self._make_video()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self.VALID_RESPONSE))]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=25, cost="0.0042")

        note, cost_info, mock_openai, mock_client, mock_load = self._suite(mock_response, metadata)

        mock_openai.assert_called_once_with(base_url="https://llm.example/v1", api_key="key-1")
        mock_load.assert_called_once_with("pv2")
        expected_prompt = (
            "Title: Video Title\nURL: https://youtube.com/watch?v=vid1\n"
            "Transcript: This is a test transcript.\nTags: tag_a"
        )
        mock_client.chat.completions.create.assert_called_once_with(
            model="model-x",
            messages=[{"role": "user", "content": expected_prompt}],
            temperature=0.7,
            extra_body={"usage": {"include": True}},
        )

    def test_video_note_and_cost_fields_are_exact(self) -> None:
        """Note fields (incl. author from channel) and cost details must be exact."""
        metadata = self._make_video()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self.VALID_RESPONSE))]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=25, cost="0.0042")

        note, cost_info, _, _, _ = self._suite(mock_response, metadata)

        assert note.title == "Note Title"
        assert note.summary == "The summary"
        assert note.key_points == ["one", "two"]
        assert note.claims == ["claim"]
        assert note.implications == ["impl"]
        assert note.tags == ["tag1", "tag2"]
        assert note.author == "My Channel"
        assert note.source_url == metadata.url
        assert note.source_type == "youtube"
        assert note.source_references == []
        assert note.model == "model-x"
        assert note.prompt_version == "pv2"

        assert cost_info.model == "model-x"
        assert cost_info.source_type == "youtube"
        assert cost_info.input_tokens == 100
        assert cost_info.output_tokens == 25
        assert cost_info.total_cost_usd == 0.0042
        assert cost_info.source_url == metadata.url

    def test_article_author_falls_back_to_unknown(self) -> None:
        """Articles without an author default to 'Unknown' in the note."""
        metadata = ArticleMetadata(
            title="An Article", url="https://example.com/post", content="Body text"
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self.VALID_RESPONSE))]
        mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)

        note, cost_info, _, _, _ = self._suite(
            mock_response, metadata, template="Title: {title}\nContent: {content}"
        )

        assert note.author == "Unknown"
        assert note.source_type == "web"
        assert cost_info.input_tokens == 1
        assert cost_info.total_cost_usd == 0.0

    def test_cost_from_real_cost_attribute(self) -> None:
        """A numeric cost attribute is converted and preserved exactly."""
        metadata = self._make_video()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self.VALID_RESPONSE))]
        mock_response.usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3, cost=0.0009)

        note, cost_info, _, _, _ = self._suite(mock_response, metadata)

        assert cost_info.input_tokens == 7
        assert cost_info.output_tokens == 3
        assert cost_info.total_cost_usd == 0.0009
        assert note.title == "Note Title"

    def test_usage_none_defaults_to_zero(self) -> None:
        """Missing usage data must not crash and yields zeroed costs."""
        metadata = self._make_video()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self.VALID_RESPONSE))]
        mock_response.usage = None

        note, cost_info, _, _, _ = self._suite(mock_response, metadata)

        assert cost_info.input_tokens == 0
        assert cost_info.output_tokens == 0
        assert cost_info.total_cost_usd == 0.0
        assert note.title == "Note Title"

    def test_content_exactly_max_length_is_accepted(self) -> None:
        """Content of exactly max_content_length passes the guard."""
        metadata = VideoMetadata(
            video_id="edge",
            title="T",
            url="https://youtube.com/watch?v=edge",
            transcript="x" * 50000,
            channel_name="C",
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self.VALID_RESPONSE))]
        mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)

        note, _, _, _, _ = self._suite(mock_response, metadata)

        assert note.title == "Note Title"


class TestGenerateNoteErrors:
    """Exact failure messages from generate_note."""

    @staticmethod
    def make_video(**overrides: str) -> VideoMetadata:
        values = dict(
            video_id="vid",
            title="T",
            url="https://youtube.com/watch?v=vid",
            transcript="A transcript body.",
            channel_name="C",
        )
        values.update(overrides)
        return VideoMetadata(**values)

    def test_content_too_long_exact_message(self) -> None:
        metadata = self.make_video(transcript="x" * 50001)

        with pytest.raises(NoteGenerationError) as exc:
            generate_note(metadata=metadata, model="m", api_key="k")

        assert str(exc.value) == (
            "Content too long (50001 chars). "
            "Maximum: 50000 chars. "
            "This will be supported in future versions."
        )

    def test_empty_response_exact_message(self) -> None:
        metadata = self.make_video()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=""))]
        mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with (
            patch("obsidian_ai_tools.llm.load_prompt_template", return_value="{title}"),
            patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
        ):
            mock_openai.return_value = mock_client
            with pytest.raises(NoteGenerationError) as exc:
                generate_note(metadata=metadata, model="m", api_key="k")

        assert str(exc.value) == "LLM returned empty response"

    def test_missing_fields_exact_message(self) -> None:
        metadata = self.make_video()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"title": "T"}'))]
        mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with (
            patch("obsidian_ai_tools.llm.load_prompt_template", return_value="{title}"),
            patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
        ):
            mock_openai.return_value = mock_client
            with pytest.raises(NoteGenerationError) as exc:
                generate_note(metadata=metadata, model="m", api_key="k")

        assert str(exc.value) == (
            "LLM response missing required fields: ['summary', 'key_points', 'tags']"
        )

    def test_tags_not_list_exact_message(self) -> None:
        metadata = self.make_video()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=('{"title": "T", "summary": "S", "key_points": [], "tags": "single"}')
                )
            )
        ]
        mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with (
            patch("obsidian_ai_tools.llm.load_prompt_template", return_value="{title}"),
            patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
        ):
            mock_openai.return_value = mock_client
            with pytest.raises(NoteGenerationError) as exc:
                generate_note(metadata=metadata, model="m", api_key="k")

        assert str(exc.value) == "Tags must be a list, got <class 'str'>"

    def test_unexpected_error_is_wrapped_exactly(self) -> None:
        metadata = self.make_video()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("boom")

        with (
            patch("obsidian_ai_tools.llm.load_prompt_template", return_value="{title}"),
            patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
        ):
            mock_openai.return_value = mock_client
            with pytest.raises(NoteGenerationError) as exc:
                generate_note(metadata=metadata, model="m", api_key="k")

        assert str(exc.value) == "Failed to generate note: boom"

    def test_parse_error_is_not_double_wrapped(self) -> None:
        """NoteGenerationError from parsing propagates unchanged."""
        metadata = self.make_video()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="not json"))]
        mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with (
            patch("obsidian_ai_tools.llm.load_prompt_template", return_value="{title}"),
            patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
        ):
            mock_openai.return_value = mock_client
            with pytest.raises(NoteGenerationError) as exc:
                generate_note(metadata=metadata, model="m", api_key="k")

        assert "Failed to parse LLM response as JSON" in str(exc.value)

    def test_prompt_template_error_propagates(self) -> None:
        """PromptTemplateError from template loading is not wrapped."""
        metadata = self.make_video()

        with (
            patch(
                "obsidian_ai_tools.llm.load_prompt_template",
                side_effect=PromptTemplateError("template missing"),
            ),
            patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
        ):
            with pytest.raises(PromptTemplateError, match="template missing"):
                generate_note(metadata=metadata, model="m", api_key="k")

        mock_openai.assert_not_called()
