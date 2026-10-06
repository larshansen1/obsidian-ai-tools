"""Topic definitions: hand-made tag groups in {vault}/.kai/topics.yaml (D4, ADR 0003).

The file is the only source of truth. Compass reads it on every scan, builds
the topic_tags table and the note_topics view, and lists tags that map to no
topic. It never rewrites an existing file.
"""

from datetime import date
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
)

TOPIC_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"

# Seed derived from the vault's most used tags (the mockup is not in the repo).
# Edit freely: the unmapped-tags list shows what is still uncovered.
SEED_TOPICS_YAML = """\
topics:
  ai-assisted-development:
    name: AI-assisted development
    tags: [agentic-engineering, coding-agents, agentic-ai, ai-assisted-development,
           claude-code, agent-harnesses, prompt-engineering, developer-tools]
  ai-and-llms:
    name: AI and LLMs
    tags: [ai, llm, claude, openai, anthropic, ai-safety, ai-development, agents,
           mcp, benchmarks]
  software-and-security:
    name: Software and security
    tags: [software-engineering, software-development, software-architecture,
           architecture, code-quality, programming, devops, open-source,
           infrastructure, security, cybersecurity, privacy]
  neurodiversity:
    name: Neurodiversity
    tags: [neurodiversity, neurodivergent, autism, adhd, audhd, masking,
           double-empathy-problem, sensory-processing, executive-function,
           disability, accessibility]
  mental-health:
    name: Mental health
    tags: [mental-health, psychology, trauma, trauma-therapy, trauma-healing,
           therapy, emotional-regulation, nervous-system, burnout,
           self-compassion, memory-reconsolidation, neuroscience]
  relationships-and-identity:
    name: Relationships and identity
    tags: [relationships, dating, attachment-theory, attachment, communication,
           social-skills, boundaries, authenticity, identity, self-worth,
           masculinity]
  productivity-and-career:
    name: Productivity and career
    tags: [productivity, personal-development, career-advice, career-development,
           professional-development, decision-making, motivation, management,
           technical-leadership, strategy, automation]
  sport-and-health:
    name: Sport and health
    tags: [cycling, cycling-gear, endurance-training, sports-science, training,
           exercise-physiology, performance, health, hearing-aids, medical-devices]
  geopolitics:
    name: Geopolitics
    tags: [geopolitics, foreign-policy, nato, military-strategy, history, germany]
min_notes: 10
trend_start: 2026-04-01
ai_exclude_folders: [notes/reflections]
"""


class TopicsError(Exception):
    """The topics file is missing, unreadable or invalid. The message is user-facing."""


class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[StrictStr, Field(min_length=1)]
    tags: Annotated[list[StrictStr], Field(min_length=1)]

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, tags: list[str]) -> list[str]:
        cleaned = [tag.strip().lstrip("#").strip() for tag in tags]
        if any(not tag for tag in cleaned):
            raise ValueError("tags must not be blank")
        duplicates = sorted({tag for tag in cleaned if cleaned.count(tag) > 1})
        if duplicates:
            raise ValueError(f"duplicate tags: {', '.join(duplicates)}")
        return cleaned


class TopicsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: Annotated[
        dict[Annotated[str, Field(pattern=TOPIC_ID_PATTERN)], Topic], Field(min_length=1)
    ]
    min_notes: Annotated[int, Field(ge=0)] = 10
    trend_start: date = date(2026, 4, 1)
    ai_exclude_folders: list[StrictStr] = Field(default_factory=lambda: ["notes/reflections"])

    @field_validator("ai_exclude_folders")
    @classmethod
    def _clean_folders(cls, folders: list[str]) -> list[str]:
        return [f.strip().strip("/") for f in folders if f.strip().strip("/")]

    def topic_tag_pairs(self) -> list[tuple[str, str]]:
        """(topic id, tag) rows, in file order."""
        return [(tid, tag) for tid, topic in self.topics.items() for tag in topic.tags]


def parse_topics(text: str) -> TopicsFile:
    """Validate topics YAML text. Raises TopicsError with a readable message."""
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise TopicsError(f"topics file is not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise TopicsError("topics file must be a mapping with a 'topics' key")
    try:
        return TopicsFile.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise TopicsError(f"topics file is invalid: {problems}") from None


def load_topics(path: Path) -> TopicsFile:
    """Read and validate the topics file at `path`."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise TopicsError(f"cannot read topics file {path}: {e}") from None
    return parse_topics(text)


def seed_topics_file(path: Path) -> bool:
    """Write the seed file if `path` does not exist. Returns True when it wrote one."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SEED_TOPICS_YAML, encoding="utf-8")
    return True
