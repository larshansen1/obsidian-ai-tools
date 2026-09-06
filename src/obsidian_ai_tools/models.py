"""Data models for the ingestion pipeline."""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class CostInfo:
    """LLM cost data returned by generate_note to its caller."""

    model: str
    source_type: str
    input_tokens: int
    output_tokens: int
    total_cost_usd: float
    source_url: str


class VideoMetadata(BaseModel):
    """YouTube video metadata and transcript."""

    video_id: str = Field(..., description="YouTube video ID")
    title: str = Field(..., description="Video title")
    url: str = Field(..., description="Original YouTube URL")
    transcript: str = Field(..., description="Full transcript text")
    channel_name: str = Field(..., description="YouTube channel name/owner")
    source_language: str = Field(default="en", description="Transcript source language code")
    provider_used: str | None = Field(
        default=None, description="Transcript provider used (direct/supadata/decodo)"
    )


class ArticleMetadata(BaseModel):
    """Web article metadata and content."""

    url: str = Field(..., description="Article URL")
    title: str = Field(..., description="Article title")
    content: str = Field(..., description="Full article text")
    author: str | None = Field(None, description="Article author")
    site_name: str | None = Field(None, description="Website name")
    published_date: str | None = Field(None, description="Publication date")
    source_type: str = Field(default="web", description="Content source type")
    source_references: list[str] = Field(
        default_factory=list,
        description="Markdown-formatted source references used to build this content",
    )
    fetched_at: datetime = Field(
        default_factory=datetime.now,
        description="Timestamp when article was fetched",
    )


class ArxivMetadata(ArticleMetadata):
    """arXiv paper metadata from the export API.

    Subclasses ArticleMetadata so the existing pipeline (build_prompt,
    generate_note) reads .content/.url/.title/.author/.site_name as usual.
    """

    authors: list[str] = Field(default_factory=list, description="Paper authors")
    abstract: str = Field(default="", description="Paper abstract text")
    categories: list[str] = Field(default_factory=list, description="arXiv subject categories")
    updated_date: str | None = Field(None, description="arXiv last-updated timestamp")
    doi: str | None = Field(None, description="Digital Object Identifier of the paper")
    source_type: str = Field(default="arxiv", description="Content source type")


class Note(BaseModel):
    """Structured note for Obsidian."""

    title: str = Field(..., description="Note title")
    summary: str = Field(..., description="Brief summary of content")
    key_points: list[str] = Field(default_factory=list, description="Key takeaways and insights")
    claims: list[str] | None = Field(None, description="Specific claims or predictions")
    implications: list[str] | None = Field(None, description="Why this matters and consequences")
    tags: list[str] = Field(default_factory=list, description="Topic tags")
    author: str | None = Field(None, description="Content author/creator")
    source_url: str = Field(..., description="Original source URL")
    source_type: str = Field(default="youtube", description="Content source type")
    source_references: list[str] = Field(
        default_factory=list,
        description="Markdown-formatted source references used to build this note",
    )
    created_at: datetime = Field(
        default_factory=datetime.now, description="Note creation timestamp"
    )
    model: str = Field(..., description="LLM model used for generation")
    prompt_version: str = Field(default="youtube_v1", description="Prompt template version")

    def _github_sections(self) -> dict[str, list[str]]:
        section_aliases = {
            "purpose": "Purpose",
            "architecture": "Architecture Read",
            "architecture read": "Architecture Read",
            "principles": "Principles",
            "design principles": "Design Principles and Tradeoffs",
            "design principles and tradeoffs": "Design Principles and Tradeoffs",
            "technology": "Technology",
            "technology and runtime": "Technology and Runtime",
            "usage": "Usage Surface",
            "usage surface": "Usage Surface",
            "setup": "Setup and Operations",
            "setup and operations": "Setup and Operations",
            "security": "Security Posture",
            "security posture": "Security Posture",
            "maturity": "Operational Maturity",
            "operational maturity": "Operational Maturity",
            "caveats": "Caveats",
            "caveats and unknowns": "Caveats and Unknowns",
        }
        sections: dict[str, list[str]] = {}
        for point in self.key_points:
            label, separator, detail = point.partition(":")
            section = section_aliases.get(label.strip().lower()) if separator else None
            if section is None:
                sections.setdefault("Additional Notes", []).append(point)
            elif detail.strip():
                sections.setdefault(section, []).append(detail.strip())
        return sections

    def _github_body(self) -> str:
        body = f"""# {self.title}

## Summary

{self.summary}

"""
        sections = self._github_sections()
        section_order = [
            "Purpose",
            "Architecture Read",
            "Design Principles and Tradeoffs",
            "Principles",
            "Technology and Runtime",
            "Technology",
            "Usage Surface",
            "Security Posture",
            "Operational Maturity",
            "Setup and Operations",
            "Caveats and Unknowns",
            "Caveats",
            "Additional Notes",
        ]
        for section in section_order:
            if section not in sections:
                continue
            body += f"## {section}\n\n"
            for item in sections[section]:
                body += f"- {item}\n"
            body += "\n"

        if self.claims:
            body += "## Evidence Highlights\n\n"
            for claim in self.claims:
                body += f"- {claim}\n"
            body += "\n"

        if self.implications:
            body += "## Adoption Fit\n\n"
            for impl in self.implications:
                body += f"- {impl}\n"
            body += "\n"

        body += f"""## Source

[GitHub Repository]({self.source_url})
"""
        if self.source_references:
            body += "\n## Source Files\n\n"
            for reference in self.source_references:
                body += f"- {reference}\n"
        return body

    def to_markdown(self) -> str:
        """Convert note to Obsidian-formatted markdown with frontmatter."""
        fm = {
            "title": self.title,
            "tags": self.tags,
            "created": self.created_at.isoformat(),
        }
        if self.author:
            fm["author"] = self.author
        fm["type"] = "source-note"
        fm["source_type"] = self.source_type
        fm["source_url"] = self.source_url
        fm["model"] = self.model
        fm["prompt_version"] = self.prompt_version

        from ._vault_store import VaultStore

        frontmatter = VaultStore.format_frontmatter(fm)

        if self.source_type == "github":
            return frontmatter + "\n" + self._github_body()

        # Build body
        body = f"""# {self.title}

## Summary

{self.summary}

"""

        # Add Claims section if present (v2 feature)
        if self.claims:
            body += "## Key Claims\n\n"
            for claim in self.claims:
                body += f"- {claim}\n"
            body += "\n"

        # Add Key Points
        body += "## Key Points\n\n"
        for point in self.key_points:
            body += f"- {point}\n"
        body += "\n"

        # Add Implications section if present (v2 feature)
        if self.implications:
            body += "## Implications\n\n"
            for impl in self.implications:
                body += f"- {impl}\n"
            body += "\n"

        # Link text depends on source type
        link_text = "Original Video" if self.source_type == "youtube" else "Original Source"

        body += f"""## Source

[{link_text}]({self.source_url})
"""
        if self.source_references:
            body += "\n## Source Files\n\n"
            for reference in self.source_references:
                body += f"- {reference}\n"

        return frontmatter + "\n" + body
