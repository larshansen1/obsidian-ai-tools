"""LLM integration for note generation via OpenRouter."""

import json
from pathlib import Path
from typing import Any

from openai import OpenAI

from .models import ArticleMetadata, CostInfo, Note, VideoMetadata


class LLMError(Exception):
    """Base exception for LLM-related errors."""

    pass


class PromptTemplateError(LLMError):
    """Raised when prompt template is invalid or missing."""

    pass


class NoteGenerationError(LLMError):
    """Raised when note generation fails."""

    pass


def load_prompt_template(template_name: str = "youtube_v1") -> str:
    """Load prompt template from prompts directory.

    Args:
        template_name: Name of the prompt template (without .md extension)

    Returns:
        Prompt template content

    Raises:
        PromptTemplateError: If template cannot be loaded
    """
    template_path = Path(__file__).parent.parent.parent / "prompts" / f"{template_name}.md"

    if not template_path.exists():
        raise PromptTemplateError(f"Prompt template not found: {template_path}")

    try:
        return template_path.read_text(encoding="utf-8")
    except Exception as e:
        raise PromptTemplateError(f"Failed to read prompt template: {e}") from e


def build_prompt(
    metadata: VideoMetadata | ArticleMetadata, template: str, existing_tags: str | None = None
) -> str:
    """Build complete prompt by injecting metadata into template.

    Args:
        metadata: Content metadata (video or article)
        template: Prompt template string with placeholders
        existing_tags: Optional formatted string of existing vault tags

    Returns:
        Complete prompt ready for LLM
    """
    # Format existing tags if provided, otherwise use empty string
    tags_section = existing_tags if existing_tags else "No existing tags available."

    if isinstance(metadata, VideoMetadata):
        return template.format(
            title=metadata.title,
            url=metadata.url,
            transcript=metadata.transcript,
            EXISTING_TAGS=tags_section,
        )
    else:  # ArticleMetadata
        return template.format(
            title=metadata.title,
            url=metadata.url,
            author=metadata.author or "Unknown",
            site_name=metadata.site_name or "Unknown Site",
            content=metadata.content,
            EXISTING_TAGS=tags_section,
        )


def parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse LLM response (expects JSON format).

    Args:
        response_text: Raw LLM response text

    Returns:
        Parsed JSON as dictionary

    Raises:
        NoteGenerationError: If response cannot be parsed
    """
    try:
        # Try to extract JSON from markdown code blocks if present
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            json_str = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            json_str = response_text[start:end].strip()
        else:
            json_str = response_text.strip()

        return json.loads(json_str)  # type: ignore[no-any-return]

    except json.JSONDecodeError as e:
        raise NoteGenerationError(f"Failed to parse LLM response as JSON: {e}") from e


def generate_note(
    metadata: VideoMetadata | ArticleMetadata,
    model: str,
    api_key: str,
    existing_tags: str | None = None,
    max_content_length: int = 50000,
    prompt_version: str = "youtube_v1",
    base_url: str = "https://openrouter.ai/api/v1",
) -> tuple[Note, CostInfo]:
    """Generate Obsidian note from content using OpenRouter.

    Args:
        metadata: Content metadata (video or article)
        model: OpenRouter model identifier
        api_key: OpenRouter API key
        existing_tags: Optional formatted string of existing vault tags for prompt context
        max_content_length: Maximum content length to process
        prompt_version: Prompt template version to use

    Returns:
        Tuple of (generated Note, LLM cost information)

    Raises:
        NoteGenerationError: If note generation fails
        PromptTemplateError: If prompt template is invalid
    """
    if isinstance(metadata, VideoMetadata):
        content = metadata.transcript
        source_type = "youtube"
        author = metadata.channel_name
        source_references: list[str] = []
    else:
        content = metadata.content
        source_type = metadata.source_type
        author = metadata.author if metadata.author else "Unknown"
        source_references = metadata.source_references

    if len(content) > max_content_length:
        raise NoteGenerationError(
            f"Content too long ({len(content)} chars). "
            f"Maximum: {max_content_length} chars. "
            "This will be supported in future versions."
        )

    template = load_prompt_template(prompt_version)
    prompt = build_prompt(metadata, template, existing_tags)

    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            extra_body={"usage": {"include": True}},
        )

        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        total_cost = 0.0
        if usage and hasattr(usage, "cost"):
            total_cost = float(usage.cost)

        cost_info = CostInfo(
            model=model,
            source_type=source_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_cost_usd=total_cost,
            source_url=metadata.url,
        )

        response_text = response.choices[0].message.content
        if not response_text:
            raise NoteGenerationError("LLM returned empty response")

        note_data = parse_llm_response(response_text)

        required_fields = ["title", "summary", "key_points", "tags"]
        missing_fields = [f for f in required_fields if f not in note_data]
        if missing_fields:
            raise NoteGenerationError(f"LLM response missing required fields: {missing_fields}")

        if not isinstance(note_data["tags"], list):
            raise NoteGenerationError(f"Tags must be a list, got {type(note_data['tags'])}")

        note = Note(
            title=note_data["title"],
            summary=note_data["summary"],
            key_points=note_data["key_points"],
            claims=note_data.get("claims"),
            implications=note_data.get("implications"),
            tags=note_data["tags"],
            author=author,
            source_url=metadata.url,
            source_type=source_type,
            source_references=source_references,
            model=model,
            prompt_version=prompt_version,
        )

        return note, cost_info

    except Exception as e:
        if isinstance(e, (NoteGenerationError, PromptTemplateError)):
            raise
        raise NoteGenerationError(f"Failed to generate note: {e}") from e
