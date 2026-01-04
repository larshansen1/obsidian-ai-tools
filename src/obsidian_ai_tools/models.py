"""Data models for the ingestion pipeline."""

from datetime import datetime

from pydantic import BaseModel, Field


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
    fetched_at: datetime = Field(
        default_factory=datetime.now,
        description="Timestamp when article was fetched",
    )


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
    created_at: datetime = Field(
        default_factory=datetime.now, description="Note creation timestamp"
    )
    model: str = Field(..., description="LLM model used for generation")
    prompt_version: str = Field(default="youtube_v1", description="Prompt template version")

    def _yaml_escape(self, value: str) -> str:
        """Escape a string value for YAML.

        Quotes the value if it contains special characters that would break YAML parsing.
        """
        # Characters that require quoting in YAML
        if any(
            char in value
            for char in [
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
        ):
            # Use double quotes and escape any existing double quotes
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return value

    def to_markdown(self) -> str:
        """Convert note to Obsidian-formatted markdown with frontmatter."""
        # Format tags for frontmatter (list format)
        tags_yaml = "\n".join(f"  - {tag}" for tag in self.tags)

        # Escape title for YAML (may contain colons, etc.)
        escaped_title = self._yaml_escape(self.title)

        # Build frontmatter with title, tags, created as first three attributes
        frontmatter = f"""---
title: {escaped_title}
tags:
{tags_yaml}
created: {self.created_at.isoformat()}
"""

        # Add author if available (also needs escaping)
        if self.author:
            escaped_author = self._yaml_escape(self.author)
            frontmatter += f"author: {escaped_author}\n"

        # Add remaining metadata
        frontmatter += f"""type: source-note
source_type: {self.source_type}
source_url: {self.source_url}
model: {self.model}
prompt_version: {self.prompt_version}
---
"""

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

        return frontmatter + "\n" + body
