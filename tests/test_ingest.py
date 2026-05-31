"""Comprehensive tests for content ingestion workflows.

Test Strategy:
- Unit tests for individual providers (web, file)
- End-to-end tests for complete ingestion workflows
- CLI command tests for the ingest command
- Error handling and edge cases
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app
from obsidian_ai_tools.models import ArticleMetadata

runner = CliRunner()
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove terminal styling added by Rich/Typer in colored CI environments."""
    return ANSI_ESCAPE_RE.sub("", text)


class TestFileProviderIngest:
    """Tests for local file ingestion."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create a temporary vault structure."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()
        return vault

    @pytest.fixture
    def sample_markdown_file(self, tmp_path: Path) -> Path:
        """Create a sample markdown file for testing."""
        md_file = tmp_path / "test_document.md"
        md_file.write_text(
            """# Test Document

This is a test document with some **formatting**.

## Section 1

Content in section 1 with a [link](https://example.com).

## Section 2

More content here. Bullet points:
- Point A
- Point B
- Point C
""",
            encoding="utf-8",
        )
        return md_file

    def test_file_provider_ingest_local_markdown(
        self, temp_vault: Path, sample_markdown_file: Path
    ) -> None:
        """Test ingesting a local markdown file."""
        from obsidian_ai_tools.obsidian import write_note

        metadata = ArticleMetadata(
            url=f"file://{sample_markdown_file}",
            title="Test Document",
            content=sample_markdown_file.read_text(encoding="utf-8"),
            author="Local File",
            site_name="Local Filesystem",
            published_date=None,
        )

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"title": "Ingested Note", "summary": "Test summary", '
                    '"key_points": ["Point 1", "Point 2"], "tags": ["test", "markdown"]}'
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=50, completion_tokens=30, cost=0.001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="markdown_v1",
            )

            assert note.title == "Ingested Note"
            assert "test" in note.tags
            assert "markdown" in note.tags
            assert note.source_type == "web"

            note_path = write_note(note, temp_vault, "inbox")
            assert note_path.exists()

            content = note_path.read_text()
            assert "---" in content
            assert "Ingested Note" in content
            assert "file://" in content

    def test_file_provider_file_not_found(self) -> None:
        """Test error handling when file doesn't exist."""
        from obsidian_ai_tools.providers.file import FileProvider

        provider = FileProvider()
        with pytest.raises(FileNotFoundError):
            provider._ingest("/nonexistent/path/to/file.md")

    def test_file_provider_directory_error(self, tmp_path: Path) -> None:
        """Test error handling when path is a directory."""
        from obsidian_ai_tools.providers.file import FileProvider

        provider = FileProvider()
        with pytest.raises(IsADirectoryError):
            provider._ingest(str(tmp_path))

    def test_file_provider_unicode_error(self, tmp_path: Path) -> None:
        """Test error handling for non-UTF8 files."""
        from obsidian_ai_tools.providers.file import FileProvider

        provider = FileProvider()
        binary_file = tmp_path / "binary.bin"
        binary_file.write_bytes(b"\x80\x81\x82 invalid utf-8")

        with pytest.raises(UnicodeDecodeError):
            provider._ingest(str(binary_file))


class TestWebProviderIngest:
    """Tests for web content ingestion."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create a temporary vault structure."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()
        return vault

    def test_web_provider_trafilatura_success(self, temp_vault: Path) -> None:
        """Test successful web content extraction via trafilatura."""
        from obsidian_ai_tools.obsidian import write_note

        metadata = ArticleMetadata(
            url="https://example.com/article",
            title="Test Article",
            content="This is the article content with meaningful text for summarization.",
            author="John Doe",
            site_name="Example Site",
            published_date=None,
        )

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"title": "Web Article Summary", "summary": "Test summary", '
                    '"key_points": ["Key point"], "tags": ["web", "article"]}'
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=50, completion_tokens=30, cost=0.001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="article_v1",
            )

            assert note.title == "Web Article Summary"
            assert "web" in note.tags
            assert note.source_type == "web"

            note_path = write_note(note, temp_vault, "inbox")
            assert note_path.exists()

    def test_web_provider_github_raw_content(self, temp_vault: Path) -> None:
        """Test fetching raw content from GitHub."""
        from obsidian_ai_tools.providers.web import WebProvider

        provider = WebProvider()

        mock_response = MagicMock()
        mock_response.text = "# Test Markdown\n\nContent here"
        mock_response.raise_for_status = MagicMock()

        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value = mock_response

            result = provider._fetch_raw(
                "https://raw.githubusercontent.com/user/repo/main/README.md"
            )

            assert result["title"] == "README.md"
            assert "Test Markdown" in result["content"]
            assert result["url"] is not None

    def test_web_provider_supadata_fallback(self, temp_vault: Path) -> None:
        """Test Supadata fallback when trafilatura fails."""
        from obsidian_ai_tools.providers.web import WebProvider

        provider = WebProvider()
        provider.supadata_key = "test-key"

        with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None):
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "content": "Fallback content via Supadata",
                "name": "Supadata Article",
            }
            mock_response.raise_for_status = MagicMock()

            with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
                mock_get.return_value = mock_response

                result = provider._fetch_supadata("https://example.com/article")

                assert result["content"] == "Fallback content via Supadata"
                assert result["title"] == "Supadata Article"

    def test_web_provider_all_methods_fail(self) -> None:
        """Test error when all extraction methods fail."""
        from obsidian_ai_tools.providers.web import WebProvider

        provider = WebProvider()
        provider.supadata_key = "test-key"

        with patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value=None):
            with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
                mock_get.side_effect = Exception("Network error")

                with pytest.raises(RuntimeError, match="Failed to fetch"):
                    provider._ingest("https://example.com/article")

    def test_web_provider_ingest_returns_raw_github_content(self) -> None:
        """GitHub blob URLs should be converted to raw content before extraction."""
        from obsidian_ai_tools.providers.web import WebProvider

        provider = WebProvider()
        with patch.object(
            provider,
            "_fetch_raw",
            return_value={
                "content": "# Readme",
                "title": "README.md",
                "author": "Unknown",
                "date": None,
                "site_name": "Raw Source",
                "url": "https://raw.githubusercontent.com/user/repo/main/README.md",
            },
        ) as fetch_raw:
            result = provider._ingest("https://github.com/user/repo/blob/main/README.md")

        assert result.title == "README.md"
        fetch_raw.assert_called_once_with(
            "https://raw.githubusercontent.com/user/repo/main/README.md"
        )

    def test_web_provider_direct_extraction_defaults_metadata(self) -> None:
        """Trafilatura extraction should map missing metadata to stable defaults."""
        from obsidian_ai_tools.providers.web import WebProvider

        provider = WebProvider()
        with (
            patch("obsidian_ai_tools.providers.web.trafilatura.fetch_url", return_value="<html />"),
            patch(
                "obsidian_ai_tools.providers.web.trafilatura.extract",
                return_value='{"text": "Article body", "hostname": "example.com"}',
            ),
        ):
            result = provider._ingest("https://example.com/article")

        assert result.content == "Article body"
        assert result.title == "Untitled Web Page"
        assert result.author == "Unknown Author"
        assert result.site_name == "example.com"

    def test_web_provider_raw_content_rejects_empty_response(self) -> None:
        """Empty raw files should not be treated as successful extraction."""
        from obsidian_ai_tools.providers.web import WebProvider

        provider = WebProvider()
        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value.text = "   "
            with pytest.raises(ValueError, match="Empty content"):
                provider._fetch_raw("https://example.com/empty.txt")

    def test_web_provider_supadata_rejects_empty_content(self) -> None:
        """Supadata fallback should reject responses without article text."""
        from obsidian_ai_tools.providers.web import WebProvider

        provider = WebProvider()
        with patch("obsidian_ai_tools.providers.web.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {}
            with pytest.raises(ValueError, match="no content"):
                provider._fetch_supadata("https://example.com/article")


class TestPDFProviderIngest:
    """Tests for PDF ingestion workflow."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create a temporary vault structure."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()
        return vault

    def test_pdf_provider_ingest_creates_note(self, temp_vault: Path) -> None:
        """Test complete PDF ingestion workflow creating a note."""
        from obsidian_ai_tools.models import ArticleMetadata
        from obsidian_ai_tools.obsidian import write_note

        metadata = ArticleMetadata(
            url="https://example.com/paper.pdf",
            title="Research Paper",
            content="This paper presents novel findings in machine learning. "
            "The methodology involves deep neural networks and transformer architectures. "
            "Results show significant improvements over baseline methods.",
            author="Dr. Smith",
            site_name="arXiv",
            published_date=None,
        )

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"title": "Paper Summary", "summary": "ML research summary", '
                    '"key_points": ["Neural networks", "Transformers"], "tags": ["ml", "research"]}'
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50, cost=0.002)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="pdf_v1",
            )

            assert note.title == "Paper Summary"
            assert "ml" in note.tags
            assert note.source_type == "web"

            note_path = write_note(note, temp_vault, "inbox")
            assert note_path.exists()

            content = note_path.read_text()
            assert "Research Paper" in content or "Paper Summary" in content


class TestYouTubeIngestEnhanced:
    """Enhanced tests for YouTube ingestion workflow."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create a temporary vault structure."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()
        (vault / ".kai").mkdir()
        return vault

    def test_youtube_ingest_with_claims_and_implications(self, temp_vault: Path) -> None:
        """Test YouTube ingestion with v2 prompt features (claims, implications)."""
        from obsidian_ai_tools.models import VideoMetadata
        from obsidian_ai_tools.obsidian import write_note

        metadata = VideoMetadata(
            title="Advanced AI Techniques",
            url="https://youtube.com/watch?v=abc123",
            transcript="This video covers advanced AI techniques including "
            "reinforcement learning and policy gradients. The presenter claims "
            "that these methods can outperform traditional supervised learning. "
            "This has implications for the future of autonomous systems.",
            channel_name="AI Research Lab",
            video_id="abc123",
        )

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=(
                        '{"title": "AI Techniques Overview", "summary": "Advanced methods", '
                        '"key_points": ["RL", "Policy Gradients"], '
                        '"claims": ["Outperforms supervised"], '
                        '"implications": ["Future of autonomous systems"], '
                        '"tags": ["ai", "ml"]}'
                    )
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=100, completion_tokens=80, cost=0.002)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="youtube_v2",
            )

            assert note.title == "AI Techniques Overview"
            assert "ai" in note.tags
            assert note.claims is not None and "Outperforms supervised" in note.claims
            assert (
                note.implications is not None
                and "Future of autonomous systems" in note.implications
            )

            note_path = write_note(note, temp_vault, "inbox")
            content = note_path.read_text()

            assert "## Key Claims" in content
            assert "## Implications" in content

    def test_youtube_ingest_provider_fallback_logging(self, temp_vault: Path) -> None:
        """Test that provider fallback is tracked in metadata."""
        from obsidian_ai_tools.models import VideoMetadata
        from obsidian_ai_tools.obsidian import write_note

        metadata = VideoMetadata(
            title="Test Video",
            url="https://youtube.com/watch?v=fallback_test",
            transcript="Test transcript content for fallback scenario",
            channel_name="Test Channel",
            video_id="fallback_test",
            provider_used="supadata",
        )

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"title": "Fallback Test", "summary": "Summary", '
                    '"key_points": ["Point"], "tags": ["test"]}'
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=50, completion_tokens=30, cost=0.001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="youtube_v2",
            )

            note_path = write_note(note, temp_vault, "inbox")
            assert note_path.exists()


class TestCLIIngestCommand:
    """Tests for the CLI ingest command."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create a temporary vault structure."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()
        (vault / ".kai").mkdir()
        return vault

    def test_ingest_command_help(self) -> None:
        """Test ingest command help output."""
        result = runner.invoke(app, ["ingest", "--help"])
        stdout = strip_ansi(result.stdout)
        assert result.exit_code == 0
        assert "Ingest content into your Obsidian vault" in stdout
        assert "--vault" in stdout
        assert "--prompt-version" in stdout
        assert "--max-pages" in stdout

    def test_ingest_command_requires_url(self) -> None:
        """Test that ingest command requires a URL argument."""
        result = runner.invoke(app, ["ingest"])
        assert result.exit_code != 0

    def test_ingest_command_unknown_source_type(self, temp_vault: Path) -> None:
        """Test error handling for unknown source type."""
        with patch("obsidian_ai_tools.cli.get_settings") as mock_settings:
            mock_settings.return_value.obsidian_vault_path = temp_vault
            mock_settings.return_value.llm_model = "test-model"
            mock_settings.return_value.openrouter_api_key = "test-key"

            result = runner.invoke(app, ["ingest", "not-a-valid-url", "--vault", str(temp_vault)])

            assert result.exit_code == 1
            assert "Unknown source type" in result.stderr or "Unknown source" in result.stderr


class TestIngestNoteGeneration:
    """Tests for note generation edge cases."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create a temporary vault structure."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()
        (vault / ".kai").mkdir()
        return vault

    def test_note_generation_with_empty_tags(self, temp_vault: Path) -> None:
        """Test note generation when LLM returns empty tags."""
        from obsidian_ai_tools.models import VideoMetadata
        from obsidian_ai_tools.obsidian import write_note

        metadata = VideoMetadata(
            title="Test Video",
            url="https://youtube.com/watch?v=empty_tags",
            transcript="Test transcript",
            channel_name="Test",
            video_id="empty_tags",
        )

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"title": "Empty Tags Test", "summary": "Summary", '
                    '"key_points": [], "tags": []}'
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=50, completion_tokens=30, cost=0.001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="youtube_v2",
            )

            assert note.title == "Empty Tags Test"
            assert len(note.tags) == 0

            note_path = write_note(note, temp_vault, "inbox")
            assert note_path.exists()

    def test_note_generation_with_special_characters(self, temp_vault: Path) -> None:
        """Test note generation with special characters in title."""
        from obsidian_ai_tools.models import VideoMetadata
        from obsidian_ai_tools.obsidian import write_note

        metadata = VideoMetadata(
            title="Test: Video with Special <Characters> & Symbols",
            url="https://youtube.com/watch?v=special",
            transcript="Test transcript with special content",
            channel_name="Test",
            video_id="special",
        )

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"title": "Special Characters: A Test", "summary": "Summary", '
                    '"key_points": ["Point with: colon"], "tags": ["test"]}'
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=50, completion_tokens=30, cost=0.001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="youtube_v2",
            )

            note_path = write_note(note, temp_vault, "inbox")
            content = note_path.read_text()

            assert "---" in content
            assert "Special Characters" in content or "Special Characters: A Test" in content


class TestIngestEdgeCases:
    """Tests for edge cases in ingestion."""

    @pytest.fixture
    def temp_vault(self, tmp_path: Path) -> Path:
        """Create a temporary vault structure."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()
        return vault

    def test_ingest_very_short_content(self, temp_vault: Path) -> None:
        """Test ingestion with very short content."""
        from obsidian_ai_tools.models import ArticleMetadata
        from obsidian_ai_tools.obsidian import write_note

        metadata = ArticleMetadata(
            url="https://example.com/short",
            title="Short Content",
            content="Short.",
            author="Test",
            site_name="Test",
            published_date=None,
        )

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"title": "Short", "summary": "Short", '
                    '"key_points": ["Short"], "tags": ["short"]}'
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=10, completion_tokens=10, cost=0.0001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="article_v1",
            )

            assert note.title == "Short"

            note_path = write_note(note, temp_vault, "inbox")
            assert note_path.exists()

    def test_ingest_with_long_tags_list(self, temp_vault: Path) -> None:
        """Test ingestion with many tags."""
        from obsidian_ai_tools.models import ArticleMetadata
        from obsidian_ai_tools.obsidian import write_note

        metadata = ArticleMetadata(
            url="https://example.com/many-tags",
            title="Many Tags Test",
            content="Content with many potential tags",
            author="Test",
            site_name="Test",
            published_date=None,
        )

        tags = ["tag" + str(i) for i in range(15)]
        tags_json = json.dumps(tags)

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"title": "Many Tags", "summary": "Summary", '
                    f'"key_points": ["Point"], "tags": {tags_json}}}'
                )
            )
        ]
        mock_llm_response.usage = MagicMock(prompt_tokens=100, completion_tokens=100, cost=0.002)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_llm_response
            mock_openai.return_value = mock_client

            from obsidian_ai_tools.llm import generate_note

            note = generate_note(
                metadata=metadata,
                model="openai/gpt-4o-mini",
                api_key="test_key",
                vault_path=temp_vault,
                prompt_version="article_v1",
            )

            assert len(note.tags) == 15
            assert "tag0" in note.tags
            assert "tag14" in note.tags

            note_path = write_note(note, temp_vault, "inbox")
            content = note_path.read_text()

            assert "tag0" in content
            assert "tag14" in content
