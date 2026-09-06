"""Tests for the arXiv ingest provider."""

from unittest.mock import MagicMock, Mock, call, patch

import pytest
import requests

from obsidian_ai_tools.ingestion import default_prompt_version
from obsidian_ai_tools.llm import generate_note, load_prompt_template
from obsidian_ai_tools.models import ArticleMetadata, ArxivMetadata
from obsidian_ai_tools.providers.arxiv import ArxivProvider

API_URL = "https://export.arxiv.org/api/query?id_list=2404.12345"
ABS_URL = "https://arxiv.org/abs/2404.12345"
PDF_URL = "https://arxiv.org/pdf/2404.12345"

FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2404.12345v2</id>
    <updated>2024-04-25T10:00:00Z</updated>
    <published>2024-04-23T09:00:00Z</published>
    <title>A Study of
 Paper Titles with Line Breaks</title>
    <summary>We present a
 study of line breaks in arXiv metadata.</summary>
    <author><name>Alice Example</name></author>
    <author><name>Bob Sample</name></author>
    <author><name></name></author>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:primary_category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:doi>10.48550/arXiv.2404.12345</arxiv:doi>
  </entry>
</feed>"""


class TestArxivProviderName:
    """Provider identification."""

    def test_name_is_arxiv(self) -> None:
        assert ArxivProvider().name == "arxiv"


class TestArxivValidate:
    """Accepted and rejected source forms."""

    @pytest.mark.parametrize(
        "source",
        [
            "https://arxiv.org/abs/2404.12345",
            "http://arxiv.org/abs/2404.12345",
            "https://arxiv.org/abs/2404.12345v2",
            "https://arxiv.org/abs/2404.1234",
            "https://arxiv.org/pdf/2404.12345",
            "https://arxiv.org/pdf/2404.12345.pdf",
            "https://arxiv.org/pdf/2404.12345v2.pdf",
            "2404.12345",
            "2404.12345v5",
            "2404.1234",
            "hep-th/9901001",
            "https://arxiv.org/abs/hep-th/9901001",
            "https://arxiv.org/pdf/hep-th/9901001.pdf",
            "  https://arxiv.org/abs/2404.12345  ",
        ],
    )
    def test_validate_accepts(self, source: str) -> None:
        assert ArxivProvider().validate(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "",
            "https://arxiv.org/abs/2404.123",
            "https://arxiv.org/abs/2404.123456",
            "https://arxiv.org/abs/abc123",
            "https://arxiv.org/abs/2404.12345/extra",
            "https://arxiv.org/pdf/2404.12345.pdfx",
            "https://www.arxiv.org/abs/2404.12345",
            "https://example.com/abs/2404.12345",
            "arxiv.org/abs/2404.12345",
            "2404.123",
            "2404.123456",
            "hep-th/99010",
            "hep-th/99010012",
            "paper.pdf",
            "https://arxiv.org/pdf/2404.v99",
        ],
    )
    def test_validate_rejects(self, source: str) -> None:
        assert ArxivProvider().validate(source) is False


class TestArxivExtractId:
    """Normalization of any accepted source form to the bare paper ID."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("https://arxiv.org/abs/2404.12345", "2404.12345"),
            ("https://arxiv.org/abs/2404.12345v2", "2404.12345v2"),
            ("https://arxiv.org/pdf/2404.12345", "2404.12345"),
            ("https://arxiv.org/pdf/2404.12345.pdf", "2404.12345"),
            ("2404.12345", "2404.12345"),
            ("hep-th/9901001", "hep-th/9901001"),
        ],
    )
    def test_extract_id_normalizes(self, source: str, expected: str) -> None:
        assert ArxivProvider.extract_id(source) == expected

    def test_extract_id_rejects_invalid_source(self) -> None:
        with pytest.raises(ValueError, match="Not a valid arXiv source"):
            ArxivProvider.extract_id("https://example.com/2404.12345")


class TestArxivParseEntry:
    """Atom XML field mapping."""

    def test_parses_all_fields_exactly(self) -> None:
        result = ArxivProvider._parse_entry(FIXTURE_XML, "2404.12345")

        assert result.title == "A Study of Paper Titles with Line Breaks"
        assert result.abstract == "We present a study of line breaks in arXiv metadata."
        assert result.content == "We present a study of line breaks in arXiv metadata."
        assert result.authors == ["Alice Example", "Bob Sample"]
        assert result.author == "Alice Example, Bob Sample"
        assert result.categories == ["cs.CL", "cs.AI"]
        assert result.published_date == "2024-04-23T09:00:00Z"
        assert result.updated_date == "2024-04-25T10:00:00Z"
        assert result.doi == "10.48550/arXiv.2404.12345"
        assert result.url == ABS_URL
        assert result.site_name == "arXiv"
        assert result.source_type == "arxiv"

    def test_no_entry_raises(self) -> None:
        with pytest.raises(ValueError, match="No arXiv record found for 2404.12345"):
            ArxivProvider._parse_entry("<feed></feed>", "2404.12345")

    def test_missing_optional_fields_use_defaults(self) -> None:
        xml = (
            "<feed xmlns='http://www.w3.org/2005/Atom'>"
            "<entry><id>http://arxiv.org/abs/2404.12345</id></entry></feed>"
        )
        result = ArxivProvider._parse_entry(xml, "2404.12345")

        assert result.title == "2404.12345"
        assert result.authors == []
        assert result.author is None
        assert result.abstract == ""
        assert result.categories == []
        assert result.published_date is None
        assert result.updated_date is None
        assert result.doi is None


class TestArxivIngest:
    """_ingest behaviour: API call, recording, fallback."""

    def _api_response(self) -> Mock:
        response = Mock()
        response.text = FIXTURE_XML
        response.raise_for_status = Mock()
        return response

    def test_ingest_success_records_primary(self) -> None:
        provider = ArxivProvider()
        with (
            patch(
                "obsidian_ai_tools.providers.arxiv.requests.get",
                return_value=self._api_response(),
            ) as mock_get,
            patch("obsidian_ai_tools.providers.arxiv._limiter") as mock_limiter,
            patch("obsidian_ai_tools.providers.arxiv._record_attempt") as mock_attempt,
            patch("obsidian_ai_tools.providers.arxiv.time.monotonic", side_effect=[100.0, 100.0]),
        ):
            result = provider._ingest("https://arxiv.org/abs/2404.12345")

        assert result.title == "A Study of Paper Titles with Line Breaks"
        assert result.url == ABS_URL
        assert result.authors == ["Alice Example", "Bob Sample"]
        assert result.source_type == "arxiv"
        mock_get.assert_called_once_with(API_URL, timeout=30)
        mock_limiter.wait.assert_called_once_with(API_URL)
        mock_attempt.assert_called_once_with("arxiv", "primary", "success", 0.0, url=API_URL)

    def test_ingest_full_text_replaces_content_with_pdf_text(self) -> None:
        provider = ArxivProvider()
        pdf_meta = ArticleMetadata(
            url=PDF_URL, title="PDF Title", content="EXTRACTED PDF FULL TEXT"
        )
        mock_pdf_cls = Mock()
        mock_pdf_cls.return_value._ingest.return_value = pdf_meta
        with (
            patch(
                "obsidian_ai_tools.providers.arxiv.requests.get",
                return_value=self._api_response(),
            ),
            patch("obsidian_ai_tools.providers.arxiv._limiter"),
            patch("obsidian_ai_tools.providers.arxiv._record_attempt") as mock_attempt,
            patch(
                "obsidian_ai_tools.providers.arxiv.time.monotonic",
                side_effect=[100.0, 100.0, 101.0, 101.0],
            ),
            patch("obsidian_ai_tools.providers.arxiv.PDFProvider", mock_pdf_cls),
        ):
            result = provider._ingest("2404.12345", full_text=True)

        assert result.content == "EXTRACTED PDF FULL TEXT"
        assert result.title == "A Study of Paper Titles with Line Breaks"
        assert result.authors == ["Alice Example", "Bob Sample"]
        assert result.abstract == "We present a study of line breaks in arXiv metadata."
        mock_pdf_cls.return_value._ingest.assert_called_once_with(PDF_URL)
        assert mock_attempt.call_args_list == [
            call("arxiv", "primary", "success", 0.0, url=API_URL),
            call("arxiv", "fallback", "success", 0.0, url=ABS_URL),
        ]

    def test_ingest_full_text_failure_records_and_propagates(self) -> None:
        provider = ArxivProvider()
        mock_pdf_cls = Mock()
        mock_pdf_cls.return_value._ingest.side_effect = RuntimeError("pdf down")
        with (
            patch(
                "obsidian_ai_tools.providers.arxiv.requests.get",
                return_value=self._api_response(),
            ),
            patch("obsidian_ai_tools.providers.arxiv._limiter"),
            patch("obsidian_ai_tools.providers.arxiv._record_attempt") as mock_attempt,
            patch(
                "obsidian_ai_tools.providers.arxiv.time.monotonic",
                side_effect=[100.0, 100.0, 101.0, 101.0],
            ),
            patch("obsidian_ai_tools.providers.arxiv.PDFProvider", mock_pdf_cls),
        ):
            with pytest.raises(RuntimeError, match="pdf down"):
                provider._ingest("2404.12345", full_text=True)

        assert mock_attempt.call_args_list == [
            call("arxiv", "primary", "success", 0.0, url=API_URL),
            call("arxiv", "fallback", "failure", 0.0, "RuntimeError", ABS_URL),
        ]

    def test_ingest_api_connection_failure_raises_runtime_error(self) -> None:
        provider = ArxivProvider()
        with (
            patch(
                "obsidian_ai_tools.providers.arxiv.requests.get",
                side_effect=requests.ConnectionError("boom"),
            ),
            patch("obsidian_ai_tools.providers.arxiv._limiter"),
            patch("obsidian_ai_tools.providers.arxiv._record_attempt") as mock_attempt,
            patch("obsidian_ai_tools.providers.arxiv.time.monotonic", side_effect=[100.0, 101.0]),
        ):
            with pytest.raises(RuntimeError, match="arXiv API request failed for 2404.12345"):
                provider._ingest("2404.12345")

        mock_attempt.assert_called_once_with(
            "arxiv", "primary", "failure", 1.0, "ConnectionError", API_URL
        )

    def test_ingest_invalid_xml_raises_runtime_error(self) -> None:
        provider = ArxivProvider()
        response = Mock()
        response.text = "this is not xml"
        response.raise_for_status = Mock()
        with (
            patch("obsidian_ai_tools.providers.arxiv.requests.get", return_value=response),
            patch("obsidian_ai_tools.providers.arxiv._limiter"),
            patch("obsidian_ai_tools.providers.arxiv._record_attempt") as mock_attempt,
            patch("obsidian_ai_tools.providers.arxiv.time.monotonic", side_effect=[100.0, 101.0]),
        ):
            with pytest.raises(RuntimeError, match="invalid XML"):
                provider._ingest("2404.12345")

        mock_attempt.assert_called_once_with(
            "arxiv", "primary", "failure", 1.0, "ParseError", API_URL
        )

    def test_ingest_invalid_source_raises_value_error_without_network(self) -> None:
        provider = ArxivProvider()
        with patch("obsidian_ai_tools.providers.arxiv.requests.get") as mock_get:
            with pytest.raises(ValueError, match="Not a valid arXiv source"):
                provider._ingest("https://example.com/not-arxiv")

        mock_get.assert_not_called()


class TestArxivPipeline:
    """Integration with prompt selection, LLM, and the factory."""

    def test_default_prompt_version_is_arxiv_v1(self) -> None:
        assert default_prompt_version("arxiv") == "arxiv_v1"

    def test_prompt_template_loads_with_article_placeholders(self) -> None:
        template = load_prompt_template("arxiv_v1")
        assert "{title}" in template
        assert "{url}" in template
        assert "{author}" in template
        assert "{content}" in template
        assert "{EXISTING_TAGS}" in template

    def test_generate_note_uses_arxiv_source_type(self) -> None:
        metadata = ArxivMetadata(
            url=ABS_URL,
            title="A Study of Paper Titles",
            content="We present a study.",
            abstract="We present a study.",
            authors=["Alice Example"],
            author="Alice Example",
            categories=["cs.CL"],
            doi="10.48550/arXiv.2404.12345",
        )
        response = MagicMock()
        response.choices = [
            MagicMock(
                message=MagicMock(
                    content=(
                        '{"title": "Paper Note", "summary": "Summary", '
                        '"key_points": ["Point"], "tags": ["research"]}'
                    )
                )
            )
        ]
        response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, cost=0.001)

        with patch("obsidian_ai_tools.llm.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = response
            mock_openai.return_value = mock_client
            note, cost_info = generate_note(
                metadata=metadata,
                model="test-model",
                api_key="test-key",
                prompt_version="arxiv_v1",
            )

        assert note.source_type == "arxiv"
        assert note.prompt_version == "arxiv_v1"
        assert note.source_url == ABS_URL
        assert note.author == "Alice Example"
        assert cost_info.source_type == "arxiv"
        assert cost_info.source_url == ABS_URL

    def test_factory_selects_arxiv_before_pdf_and_web(self) -> None:
        from obsidian_ai_tools.providers.factory import ProviderFactory

        assert ProviderFactory.get_provider("https://arxiv.org/abs/2404.12345").name == "arxiv"
        assert ProviderFactory.get_provider("https://arxiv.org/pdf/2404.12345.pdf").name == "arxiv"
        assert ProviderFactory.get_provider("2404.12345").name == "arxiv"
        assert ProviderFactory.get_provider("https://example.com/article").name == "web"
