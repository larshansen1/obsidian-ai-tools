"""Edit topic (W2, T7): add or remove a topic's tags in .kai/topics.yaml.

Also takes single notes out of a topic, or puts them back (#189): the topic's
`exclude` list of note paths.

Only plans the change; vault_writes applies it after approval. Only the
edited topic's tag list is rewritten, so the rest of the file (layout,
comments) stays exactly as it was typed. If that cannot be done safely the
whole file is written back as plain YAML instead; the preview shows either.
"""

import json
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


def _child(node: yaml.Node | None, key: str) -> yaml.Node:
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


def new_exclude(current: list[str], exclude: list[str], restore: list[str]) -> list[str]:
    """The topic's excluded notes after the change. Raises WriteError for a change that
    makes no sense."""
    exclude = list(dict.fromkeys(p.strip() for p in exclude if p.strip()))
    restore = list(dict.fromkeys(p.strip() for p in restore if p.strip()))
    unknown = [p for p in restore if p not in current]
    if unknown:
        raise WriteError(f"Not taken out of this topic: {', '.join(unknown)}")
    present = [p for p in exclude if p in current]
    if present:
        raise WriteError(f"Already taken out of this topic: {', '.join(present)}")
    paths = [p for p in current if p not in restore] + exclude
    if paths == current:
        raise WriteError("Nothing to change.")
    return paths


def _quoted(path: str) -> str:
    # A JSON string is a YAML double-quoted scalar, so any path is safe: colons, quotes, "#".
    return json.dumps(path, ensure_ascii=False)


def _key_node(node: yaml.Node, key: str) -> yaml.Node:
    if not isinstance(node, yaml.MappingNode):
        raise KeyError(key)
    for k, _ in node.value:
        if k.value == key:
            found: yaml.Node = k
            return found
    raise KeyError(key)


def replace_exclude(text: str, topic_id: str, paths: list[str]) -> str | None:
    """`text` with only this topic's exclude list set, or None if the topic cannot be found.

    An existing list is replaced in its own style. A topic without one gets the key on a
    new line right under its tags, one path per line.
    """
    try:
        topic = _child(_child(yaml.compose(text), "topics"), topic_id)
        tags_key, tags = _key_node(topic, "tags"), _child(topic, "tags")
    except (KeyError, yaml.YAMLError):
        return None
    try:
        node = _child(topic, "exclude")
    except KeyError:
        end = tags.end_mark.index
        while end > 0 and text[end - 1].isspace():
            end -= 1
        indent = " " * tags_key.start_mark.column
        items = "".join(f"\n{indent}  - {_quoted(p)}" for p in paths)
        return f"{text[:end]}\n{indent}exclude:{items}{text[end:]}"
    start, end = node.start_mark.index, node.end_mark.index
    while end > start and text[end - 1].isspace():
        end -= 1
    block = isinstance(node, yaml.SequenceNode) and not node.flow_style
    if not paths:
        new = "[]"
    elif block:
        new = ("\n" + " " * node.start_mark.column).join(f"- {_quoted(p)}" for p in paths)
    else:
        new = "[" + ", ".join(_quoted(p) for p in paths) + "]"
    return text[:start] + new + text[end:]


def _excluded_text(text: str, topic_id: str, paths: list[str], expected: TopicsFile) -> str:
    """The new file text: the small edit when it reads back as `expected`, else a full dump."""
    small = replace_exclude(text, topic_id, paths)
    if small is not None:
        try:
            if parse_topics(small) == expected:
                return small
        except TopicsError:
            pass
    data = yaml.safe_load(text)
    data["topics"][topic_id]["exclude"] = paths
    return dump_topics(data)


def plan_topic_notes(
    topics_path: Path, topic_id: str, exclude: list[str], restore: list[str]
) -> tuple[FileEdit, str]:
    """The edit that takes notes out of a topic or puts them back, and a one-line summary."""
    try:
        before, mtime = read_for_edit(topics_path)
        text = before.decode("utf-8")
        definitions = parse_topics(text)
    except (OSError, UnicodeDecodeError, TopicsError) as e:
        raise WriteError(f"Cannot read the topics file: {e}") from None
    topic = definitions.topics.get(topic_id)
    if topic is None:
        raise WriteError(f"Unknown topic: {topic_id}")
    paths = new_exclude(topic.exclude, exclude, restore)
    expected = definitions.model_copy(deep=True)
    expected.topics[topic_id].exclude = paths
    after = _excluded_text(text, topic_id, paths, expected)
    try:
        parse_topics(after)  # never write a file the app cannot read back
    except TopicsError as e:
        raise WriteError(str(e)) from None
    changes = [f"take out {p}" for p in paths if p not in topic.exclude] + [
        f"put back {p}" for p in topic.exclude if p not in paths
    ]
    summary = f"Topic {topic.name}: {', '.join(changes)}"
    return FileEdit(topics_path, TOPICS_LABEL, before, after.encode("utf-8"), mtime), summary
