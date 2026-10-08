"""Edit topic (W2, T7): add or remove a topic's tags in .kai/topics.yaml.

Only plans the change; vault_writes applies it after approval. The file is
written back as plain YAML with one flow list of tags per topic, so it stays
easy to read and edit by hand. Comments are not kept; the preview shows any
line that would go.
"""

from pathlib import Path
from typing import Any

import yaml

from .topics import TopicsError, parse_topics
from .vault_writes import FileEdit, WriteError, read_for_edit

TOPICS_LABEL = ".kai/topics.yaml"


def _clean(tags: list[str]) -> list[str]:
    cleaned = (t.strip().lstrip("#").strip() for t in tags)
    return list(dict.fromkeys(t for t in cleaned if t))


def new_tags(current: list[str], add: list[str], remove: list[str]) -> list[str]:
    """The topic's tags after the change. Raises WriteError for a change that makes no sense."""
    add, remove = _clean(add), _clean(remove)
    unknown = [t for t in remove if t not in current]
    if unknown:
        raise WriteError(f"Not a tag of this topic: {', '.join(unknown)}")
    present = [t for t in add if t in current]
    if present:
        raise WriteError(f"Already a tag of this topic: {', '.join(present)}")
    tags = [t for t in current if t not in remove] + add
    if not tags:
        raise WriteError("A topic needs at least one tag.")
    if tags == current:
        raise WriteError("Nothing to change.")
    return tags


def dump_topics(data: dict[str, Any]) -> str:
    return yaml.safe_dump(
        data, sort_keys=False, default_flow_style=None, width=88, allow_unicode=True
    )


def plan_topic_tags(
    topics_path: Path, topic_id: str, add: list[str], remove: list[str]
) -> tuple[FileEdit, str]:
    """The edit to the topics file, and a one-line summary."""
    try:
        before, mtime = read_for_edit(topics_path)
        text = before.decode("utf-8")
        definitions = parse_topics(text)
    except (OSError, UnicodeDecodeError, TopicsError) as e:
        raise WriteError(f"Cannot read the topics file: {e}") from None
    topic = definitions.topics.get(topic_id)
    if topic is None:
        raise WriteError(f"Unknown topic: {topic_id}")
    tags = new_tags(topic.tags, add, remove)
    data = yaml.safe_load(text)
    data["topics"][topic_id]["tags"] = tags
    after = dump_topics(data)
    try:
        parse_topics(after)  # never write a file the app cannot read back
    except TopicsError as e:
        raise WriteError(str(e)) from None
    changes = [f"add {t}" for t in tags if t not in topic.tags] + [
        f"remove {t}" for t in topic.tags if t not in tags
    ]
    summary = f"Topic {topic.name}: {', '.join(changes)}"
    return FileEdit(topics_path, TOPICS_LABEL, before, after.encode("utf-8"), mtime), summary
