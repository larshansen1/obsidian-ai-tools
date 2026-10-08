"""Tools the assistant can call to read the vault (C4).

Plain functions over `AiVault`, plus a registry (`TOOLS`) with JSON schemas.
The chat endpoint and the `compass tool` command call the same registry, so
the tools can later be served over MCP unchanged.

`AiVault` is the only way note text reaches a model (N4): it refuses every
note in an excluded folder.
"""

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .ai_policy import is_excluded
from .claims import build_claims_view
from .config import CompassSettings
from .coverage_gaps import (
    Gap,
    card,
    find_candidates,
    find_gaps,
    ingested_keys,
    summarize_gaps,
)
from .db import readonly
from .source_ai import SourceAi, WebHit
from .source_types import resolve_types
from .topic_map import Window, topic_stats
from .topic_notes import load_topic_notes
from .topics import TopicsFile, load_topics

SEARCH_LIMIT = 8
SNIPPET_CHARS = 200
READ_NOTE_CHARS = 6000
MIN_TERM_CHARS = 3
BODY_HIT_CAP = 5
TOP_TAGS_LIMIT = 25

_NOTES_SQL = """
SELECT n.path, n.title, n.created,
       COALESCE((SELECT list(tag ORDER BY tag) FROM note_tags t WHERE t.path = n.path), [])
FROM notes n
{where}
ORDER BY n.path
"""
_TOPIC_FILTER = "WHERE n.path IN (SELECT path FROM note_topics WHERE topic = ?)"
_ONE_NOTE_SQL = _NOTES_SQL.format(where="WHERE n.path = ?")
_TOPIC_TAGS_SQL = """
SELECT t.path, t.tag FROM note_tags t
WHERE t.path IN (SELECT path FROM note_topics WHERE topic = ?)
"""
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)


class VaultToolError(Exception):
    """A tool could not run. The message is shown to the model."""


def _cite(path: str) -> str:
    """The exact mark the chat turns into a link that opens the note."""
    return f"[[{path}]]"


def _body(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1)


def _terms(query: str) -> list[str]:
    words = re.findall(r"[\w-]+", query.lower())
    return [w for w in words if len(w) >= MIN_TERM_CHARS]


def _snippet(body: str, terms: list[str]) -> str:
    for line in body.splitlines():
        low = line.lower()
        if any(t in low for t in terms):
            return line.strip()[:SNIPPET_CHARS]
    return ""


def _score(terms: list[str], title: str, tags: list[str], body: str) -> int:
    """Title hit 3, tag hit 2, body hits 1 each (at most 5 per word)."""
    low_title, low_body = title.lower(), body.lower()
    low_tags = [t.lower() for t in tags]
    score = sum(3 for t in terms if t in low_title)
    score += sum(2 for t in terms if any(t in tag for tag in low_tags))
    return score + sum(min(low_body.count(t), BODY_HIT_CAP) for t in terms)


@dataclass(frozen=True)
class AiVault:
    """Read access to the vault for AI use, with excluded folders removed."""

    vault_path: Path
    db_path: Path
    definitions: TopicsFile
    # Paid web search and source typing; None means rules and cache only, no model calls.
    source_ai: SourceAi | None = None

    @classmethod
    def from_settings(cls, settings: CompassSettings) -> "AiVault":
        return cls(
            settings.obsidian_vault_path,
            settings.compass_db_path,
            load_topics(settings.compass_topics_path),
        )

    @property
    def excluded(self) -> list[str]:
        return self.definitions.ai_exclude_folders

    def _read(self, sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
        if not self.db_path.exists():
            raise VaultToolError("No topic data yet. Run `compass scan` first.")
        with readonly(self.db_path) as con:
            return con.execute(sql, params).fetchall()

    def _text(self, rel_path: str) -> str:
        return (self.vault_path / rel_path).read_text(encoding="utf-8", errors="replace")

    def body(self, path: str) -> str | None:
        """A note's text without frontmatter; None when it is excluded or unreadable (N4)."""
        if is_excluded(path, self.excluded):
            return None
        try:
            return _body(self._text(path))
        except OSError:
            return None

    def _scored(
        self, rows: list[tuple[Any, ...]], terms: list[str]
    ) -> list[tuple[int, str, str, date | None, str]]:
        scored = []
        for path, title, created, tags in rows:
            if is_excluded(path, self.excluded):
                continue
            try:
                body = _body(self._text(path))
            except OSError:
                continue
            score = _score(terms, title, tags, body)
            if score:
                scored.append((score, path, title, created, _snippet(body, terms)))
        return scored

    def search_notes(self, query: str, topic: str | None = None, limit: int = SEARCH_LIMIT) -> Any:
        terms = _terms(query)
        if not terms:
            raise VaultToolError("The query has no searchable words.")
        if topic is not None and topic not in self.definitions.topics:
            raise VaultToolError(f"Unknown topic: {topic}")
        sql = _NOTES_SQL.format(where=_TOPIC_FILTER if topic else "")
        scored = self._scored(self._read(sql, [topic] if topic else []), terms)
        scored.sort(key=lambda r: (-r[0], r[1]))
        return [
            {
                "path": p,
                "cite": _cite(p),
                "title": t,
                "created": c.isoformat() if c else None,
                "snippet": s,
            }
            for _score, p, t, c, s in scored[:limit]
        ]

    def read_note(self, path: str) -> Any:
        rows = self._read(_ONE_NOTE_SQL, [path])
        if not rows:
            raise VaultToolError(f"No such note: {path}")
        _path, title, created, tags = rows[0]
        if is_excluded(path, self.excluded):
            raise VaultToolError(f"{path} is excluded from AI use.")
        body = _body(self._text(path))
        return {
            "path": path,
            "cite": _cite(path),
            "title": title,
            "created": created.isoformat() if created else None,
            "tags": list(tags),
            "text": body[:READ_NOTE_CHARS],
            "truncated": len(body) > READ_NOTE_CHARS,
        }

    def topic_tags(self, topic: str, limit: int = TOP_TAGS_LIMIT) -> Any:
        """The tags on a topic's notes with how many notes carry each: its themes."""
        if topic not in self.definitions.topics:
            raise VaultToolError(f"Unknown topic: {topic}")
        rows = self._read(_TOPIC_TAGS_SQL, [topic])
        counts = Counter(tag for path, tag in rows if not is_excluded(path, self.excluded))
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [{"tag": tag, "notes": n} for tag, n in ranked[:limit]]

    def topic_claims(self, topic: str) -> Any:
        """The claims already read for a topic: question, sides, shared claims. No model call."""
        if topic not in self.definitions.topics:
            raise VaultToolError(f"Unknown topic: {topic}")
        if not self.db_path.exists():
            raise VaultToolError("No topic data yet. Run `compass scan` first.")
        with readonly(self.db_path) as con:
            view = build_claims_view(con, self.definitions, topic)
        if view.claim_count == 0:
            raise VaultToolError(
                "No claims have been read for this topic yet. "
                "Ask the user to open the topic page and choose Find claims."
            )
        return view.model_dump(mode="json")

    def topic_stats(self, topic: str, window: Window = "30", today: date | None = None) -> Any:
        topic_def = self.definitions.topics.get(topic)
        if topic_def is None:
            raise VaultToolError(f"Unknown topic: {topic}")
        if not self.db_path.exists():
            raise VaultToolError("No topic data yet. Run `compass scan` first.")
        with readonly(self.db_path) as con:
            notes = load_topic_notes(con).get(topic, [])
        # Counts only: no note text or titles leave through this tool.
        stats = topic_stats(
            topic,
            topic_def.name,
            notes,
            definitions=self.definitions,
            today=today or date.today(),
            window=window,
        )
        return stats.model_dump(mode="json")

    def _topic_gaps(self, topic: str) -> tuple[list[Gap], set[str]]:
        if topic not in self.definitions.topics:
            raise VaultToolError(f"Unknown topic: {topic}")
        if not self.db_path.exists():
            raise VaultToolError("No topic data yet. Run `compass scan` first.")
        with readonly(self.db_path) as con:
            return find_gaps(con, topic, self.body), ingested_keys(con)

    def coverage_gaps(self, topic: str) -> Any:
        """Sources the topic's notes cite that are not in the vault, counted by type."""
        gaps, _ = self._topic_gaps(topic)
        types, note = resolve_types(self.db_path, {g.url: g.title for g in gaps}, self.source_ai)
        summary = summarize_gaps(topic, gaps, types)
        summary["notes_for_model"] = [note] if note else []
        return summary

    def _search(self, topic: str) -> Callable[[], list[WebHit]] | None:
        ai = self.source_ai
        if ai is None:
            return None
        name = self.definitions.topics[topic].name
        themes = [t["tag"] for t in self.topic_tags(topic, limit=5)]
        return lambda: ai.search(name, themes)

    def source_candidates(self, topic: str) -> Any:
        """Citations, then web results, each confirmed online (C7), checked after the DB closes."""
        citations, ingested = self._topic_gaps(topic)
        shown, not_found, notes = find_candidates(citations, ingested, self._search(topic))
        types, note = resolve_types(self.db_path, {g.url: g.title for g in shown}, self.source_ai)
        name = self.definitions.topics[topic].name
        return {
            "candidates": [card(g, types[g.url], name) for g in shown],
            "not_found": not_found,
            "notes_for_model": notes + ([note] if note else []),
        }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[AiVault, dict[str, Any]], Any]

    def as_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _search(vault: AiVault, args: dict[str, Any]) -> Any:
    return vault.search_notes(str(args.get("query", "")), args.get("topic"))


def _read(vault: AiVault, args: dict[str, Any]) -> Any:
    return vault.read_note(str(args.get("path", "")))


def _tags(vault: AiVault, args: dict[str, Any]) -> Any:
    return vault.topic_tags(str(args.get("topic", "")))


def _claims(vault: AiVault, args: dict[str, Any]) -> Any:
    return vault.topic_claims(str(args.get("topic", "")))


def _stats(vault: AiVault, args: dict[str, Any]) -> Any:
    window = args.get("window", "30")
    if window not in ("30", "90", "all"):
        raise VaultToolError("window must be 30, 90 or all")
    return vault.topic_stats(str(args.get("topic", "")), window)


def _gaps(vault: AiVault, args: dict[str, Any]) -> Any:
    return vault.coverage_gaps(str(args.get("topic", "")))


def _candidates(vault: AiVault, args: dict[str, Any]) -> Any:
    return vault.source_candidates(str(args.get("topic", "")))


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "search_notes",
            "Search the vault's notes by words. Returns the best matches with a snippet.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "topic": {"type": "string", "description": "Topic id to search within"},
                },
                "required": ["query"],
            },
            _search,
        ),
        ToolSpec(
            "read_note",
            "Read one note by its vault path (a path returned by search_notes).",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            _read,
        ),
        ToolSpec(
            "topic_tags",
            "The tags on a topic's notes with note counts. Use it first for questions about "
            "a topic's themes, subtopics or what it covers.",
            {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "Topic id"}},
                "required": ["topic"],
            },
            _tags,
        ),
        ToolSpec(
            "topic_claims",
            "Claims already read from a topic's notes: the chosen question with supporting and "
            "pushing-back claims, and claims shared by several notes matched to evergreens.",
            {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "Topic id"}},
                "required": ["topic"],
            },
            _claims,
        ),
        ToolSpec(
            "topic_stats",
            "Numbers for one topic: note count, momentum, evergreens, linked notes, next step.",
            {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic id"},
                    "window": {"type": "string", "enum": ["30", "90", "all"]},
                },
                "required": ["topic"],
            },
            _stats,
        ),
        ToolSpec(
            "coverage_gaps",
            "Coverage gaps for a topic: how many sources its notes cite that are not in the "
            "vault yet, counted by type (study, report, essay, talk).",
            {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "Topic id"}},
                "required": ["topic"],
            },
            _gaps,
        ),
        ToolSpec(
            "source_candidates",
            "Sources to read next for a topic: sources its notes cite that are not in the vault "
            "yet, each confirmed to exist online, with title, type and why it fills a gap.",
            {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "Topic id"}},
                "required": ["topic"],
            },
            _candidates,
        ),
    )
}


def run_tool(vault: AiVault, name: str, arguments: dict[str, Any]) -> Any:
    """Run one tool. Errors come back as `{"error": ...}` so the model can recover."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return spec.run(vault, arguments)
    except VaultToolError as e:
        return {"error": str(e)}


def tool_schemas() -> list[dict[str, Any]]:
    return [spec.as_openai() for spec in TOOLS.values()]


def main(argv: list[str] | None = None) -> None:
    """Script entry: `python -m vault_compass.vault_tools search_notes '{"query": "x"}'`."""
    import sys
    from dataclasses import replace

    from .ai_cost import month_spend
    from .config import get_compass_settings
    from .source_ai import ActionBudget, OpenRouterSourceAi

    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        raise SystemExit("usage: vault_tools <tool> '<json arguments>'")
    settings = get_compass_settings()
    vault = AiVault.from_settings(settings)
    if settings.openrouter_api_key:
        budget = ActionBudget(settings, month_spend(settings.compass_db_path))
        vault = replace(vault, source_ai=OpenRouterSourceAi(settings, budget))
    print(json.dumps(run_tool(vault, args[0], json.loads(args[1])), indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
