"""arXiv paper ingestion provider.

Fetches paper metadata + abstract from the arXiv export API (Atom XML).
With ``full_text=True`` the abstract is replaced by the extracted PDF text,
reusing the existing PDFProvider instead of duplicating PDF plumbing.
"""

import logging
import re
import time
import xml.etree.ElementTree as ET  # nosec B405
from typing import Any

import requests

from ..models import ArxivMetadata
from . import _limiter, _record_attempt
from .base import BaseProvider
from .pdf import PDFProvider

logger = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"

# Modern IDs (e.g. 2404.12345v2) plus legacy IDs (e.g. hep-th/9901001).
_ARXIV_ID = r"\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+\/\d{7}"
_ABS_RE = re.compile(rf"^https?://arxiv\.org/abs/({_ARXIV_ID})$", re.IGNORECASE)
_PDF_RE = re.compile(rf"^https?://arxiv\.org/pdf/({_ARXIV_ID})(\.pdf)?$", re.IGNORECASE)
_BARE_ID_RE = re.compile(rf"^({_ARXIV_ID})$", re.IGNORECASE)

_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


class ArxivProvider(BaseProvider):
    """Provider for ingesting arXiv papers via the export API."""

    @property
    def name(self) -> str:
        return "arxiv"

    def validate(self, source: str) -> bool:
        """True for arXiv abs/pdf URLs and bare arXiv IDs; everything else is False."""
        source = source.strip()
        return any(pattern.match(source) for pattern in (_ABS_RE, _PDF_RE, _BARE_ID_RE))

    @staticmethod
    def extract_id(source: str) -> str:
        """Extract the arXiv paper ID from any accepted source form.

        Raises:
            ValueError: If the source is not a recognized arXiv form.
        """
        source = source.strip()
        for pattern in (_ABS_RE, _PDF_RE, _BARE_ID_RE):
            match = pattern.match(source)
            if match:
                return match.group(1)
        raise ValueError(f"Not a valid arXiv source: {source}")

    def _ingest(self, source: str, full_text: bool = False, **kwargs: Any) -> ArxivMetadata:
        """Fetch paper metadata (abstract as content), optionally PDF full text.

        Args:
            source: arXiv abs/pdf URL or bare paper ID.
            full_text: When True, replace the abstract content with the text
                extracted from the paper's PDF (reuses PDFProvider).
            **kwargs: Unused; accepted for BaseProvider compatibility.

        Returns:
            ArxivMetadata with API metadata and abstract (or PDF) content.

        Raises:
            ValueError: If the source is not a valid arXiv identifier.
            RuntimeError: If the arXiv API call fails transiently.
        """
        paper_id = self.extract_id(source)
        metadata = self._fetch_metadata(paper_id)

        if full_text:
            _t1 = time.monotonic()
            pdf_url = f"https://arxiv.org/pdf/{paper_id}"
            try:
                pdf_meta = PDFProvider()._ingest(pdf_url)
                metadata.content = pdf_meta.content
                _record_attempt(
                    "arxiv", "fallback", "success", time.monotonic() - _t1, url=metadata.url
                )
            except Exception as exc:
                _record_attempt(
                    "arxiv",
                    "fallback",
                    "failure",
                    time.monotonic() - _t1,
                    type(exc).__name__,
                    metadata.url,
                )
                raise
        return metadata

    def _fetch_metadata(self, paper_id: str) -> ArxivMetadata:
        """Query the arXiv export API for a single paper's Atom entry."""
        api_url = f"{ARXIV_API_URL}?id_list={paper_id}"
        _limiter.wait(api_url)
        _t0 = time.monotonic()
        try:
            response = requests.get(api_url, timeout=30)
            response.raise_for_status()
            metadata = self._parse_entry(response.text, paper_id)
            _record_attempt("arxiv", "primary", "success", time.monotonic() - _t0, url=api_url)
            return metadata
        except ET.ParseError as exc:
            _record_attempt(
                "arxiv", "primary", "failure", time.monotonic() - _t0, "ParseError", api_url
            )
            raise RuntimeError(f"arXiv API returned invalid XML for {paper_id}") from exc
        except requests.RequestException as exc:
            _record_attempt(
                "arxiv",
                "primary",
                "failure",
                time.monotonic() - _t0,
                type(exc).__name__,
                api_url,
            )
            raise RuntimeError(f"arXiv API request failed for {paper_id}: {exc}") from exc

    @staticmethod
    def _parse_entry(xml_text: str, paper_id: str) -> ArxivMetadata:
        """Parse the first Atom <entry> from an arXiv API response."""
        root = ET.fromstring(xml_text)  # nosec B314
        entry = root.find("a:entry", _NS)
        if entry is None:
            raise ValueError(f"No arXiv record found for {paper_id}")

        def text(tag: str, namespace: str = "a") -> str:
            element = entry.find(f"{namespace}:{tag}", _NS)
            if element is None or not element.text:
                return ""
            return " ".join(element.text.split())

        authors = [
            author.findtext("a:name", default="", namespaces=_NS)
            for author in entry.findall("a:author", _NS)
        ]
        authors = [name for name in (name.strip() for name in authors) if name]
        categories = [category.get("term", "") for category in entry.findall("a:category", _NS)]
        categories = [term for term in categories if term]

        summary = text("summary")
        return ArxivMetadata(
            title=text("title") or paper_id,
            url=f"https://arxiv.org/abs/{paper_id}",
            author=", ".join(authors) or None,
            authors=authors,
            abstract=summary,
            content=summary,
            categories=categories,
            site_name="arXiv",
            published_date=text("published") or None,
            updated_date=text("updated") or None,
            doi=text("doi", "arxiv") or None,
        )
