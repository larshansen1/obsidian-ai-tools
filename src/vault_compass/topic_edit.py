"""Edit topic (W2, T7): add or remove a topic's tags in .kai/topics.yaml.

Only plans the change; vault_writes applies it after approval. Only the
edited topic's tag list is rewritten, so the rest of the file (layout,
comments) stays exactly as it was typed. If that cannot be done safely the
whole file is written back as plain YAML instead; the preview shows either.
"""

from pathlib import Path
from typing import Any

import yaml

from .topics import TopicsError, TopicsFile, parse_topics
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


WIDTH = 88


def flow_list(tags: list[str], column: int) -> str:
    """`[a, b, c]` starting at `column`, wrapped under the first tag past WIDTH."""
    lines: list[str] = []
    line = "["
    for i, tag in enumerate(tags):
        piece = tag + ("]" if i == len(tags) - 1 else ",")
        if line != "[" and column + len(line) + 1 + len(piece) > WIDTH:
            lines.append(line)
            line = " " * (column + 1) + piece
        else:
            line += ("" if line == "[" else " ") + piece
    lines.append(line)
    return "\n".join(lines)


def _child(node: yaml.Node, key: str) -> yaml.Node:
    if not isinstance(node, yaml.MappingNode):
        raise KeyError(key)
    for k, v in node.value:
        if k.value == key:
            child: yaml.Node = v
            return child
    raise KeyError(key)


def replace_tags(text: str, topic_id: str, tags: list[str]) -> str | None:
    """`text` with only this topic's tag list replaced, or None if it cannot find it."""
    try:
        node = _child(_child(_child(yaml.compose(text), "topics"), topic_id), "tags")
    except (KeyError, yaml.YAMLError):
        return None
    start, end = node.start_mark.index, node.end_mark.index
    while end > start and text[end - 1].isspace():
        end -= 1
    return text[:start] + flow_list(tags, node.start_mark.column) + text[end:]


def _edited_text(text: str, topic_id: str, tags: list[str], expected: TopicsFile) -> str:
    """The new file text: the small edit when it reads back as `expected`, else a full dump."""
    small = replace_tags(text, topic_id, tags)
    if small is not None:
        try:
            if parse_topics(small) == expected:
                return small
        except TopicsError:
            pass  # e.g. a tag that needs quoting: the full dump quotes it
    data = yaml.safe_load(text)
    data["topics"][topic_id]["tags"] = tags
    return dump_topics(data)


def dump_topics(data: dict[str, Any]) -> str:
    return yaml.safe_dump(
        data, sort_keys=False, default_flow_style=None, width=WIDTH, allow_unicode=True
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
    expected = definitions.model_copy(deep=True)
    expected.topics[topic_id].tags = tags
    after = _edited_text(text, topic_id, tags, expected)
    try:
        parse_topics(after)  # never write a file the app cannot read back
    except TopicsError as e:
        raise WriteError(str(e)) from None
    changes = [f"add {t}" for t in tags if t not in topic.tags] + [
        f"remove {t}" for t in topic.tags if t not in tags
    ]
    summary = f"Topic {topic.name}: {', '.join(changes)}"
    return FileEdit(topics_path, TOPICS_LABEL, before, after.encode("utf-8"), mtime), summary
