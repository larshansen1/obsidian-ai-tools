"""Draft new evergreen (T8): start a note from selected claims, saved only after approval.

The draft is plain text the user edits in the browser; nothing touches the vault
until the planned file is approved through the write layer (vault_writes).
"""

import re
import unicodedata
from datetime import date
from pathlib import Path

import duckdb
import yaml

from .db import readonly
from .note_links import link_text
from .notes import EVERGREEN_FOLDER
from .topics import TopicsFile
from .vault_writes import FileEdit, WriteError

SOURCES_HEADING = "## Claims this draws on"
MAX_CLAIMS = 50
MAX_TITLE = 200

# Letters NFKD does not split into a base letter and a mark.
_LETTERS = str.maketrans({"æ": "ae", "ø": "o", "ß": "ss", "œ": "oe", "đ": "d", "ł": "l"})

_CLAIMS_SQL = "SELECT claim_id, path, text FROM note_claims WHERE list_contains(?, claim_id)"


def slugify(title: str) -> str:
    """'Masking is a defense!' -> 'masking-is-a-defense', the vault's evergreen file names."""
    text = unicodedata.normalize("NFKD", title.lower().translate(_LETTERS))
    ascii_text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:80].rstrip("-")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def draft_body(db_path: Path, claim_ids: list[str]) -> str:
    """The draft's starting text: the chosen claims, each linked to the note it came from."""
    ids = list(dict.fromkeys(claim_ids))
    if not ids:
        raise WriteError("Pick at least one claim.")
    if len(ids) > MAX_CLAIMS:
        raise WriteError(f"Pick at most {MAX_CLAIMS} claims.")
    if not db_path.exists():
        raise WriteError("No topic data yet. Run `compass scan` first.")
    with readonly(db_path) as con:
        try:
            rows = con.execute(_CLAIMS_SQL, [ids]).fetchall()
        except duckdb.CatalogException:
            rows = []
        found = {cid: (path, text) for cid, path, text in rows}
        paths = [p for (p,) in con.execute("SELECT path FROM notes").fetchall()]
    missing = [cid for cid in ids if cid not in found]
    if missing:
        raise WriteError(f"Unknown claim: {missing[0]}. Read the notes again.")
    bullets = [f"- {_one_line(found[c][1])} ([[{link_text(found[c][0], paths)}]])" for c in ids]
    return "\n".join([SOURCES_HEADING, "", *bullets]) + "\n"


def evergreen_text(title: str, tag: str, body: str, today: date) -> str:
    """The note in the vault's evergreen format: frontmatter, a title heading, then the body."""
    front = yaml.safe_dump(
        {
            "title": title,
            "type": "evergreen",
            "created": today,
            "tags": ["evergreen", tag],
        },
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    )
    text = body.replace("\r\n", "\n").strip("\n")
    head = f"---\n{front}---\n\n# {title}\n"
    return f"{head}\n{text}\n" if text else head


def plan_evergreen(
    vault: Path, definitions: TopicsFile, topic: str, title: str, body: str, today: date
) -> tuple[FileEdit, str]:
    """A new note in notes/evergreen for approval, and a one-line summary.

    It carries the topic's first tag, so it counts as an evergreen of that topic.
    """
    if topic not in definitions.topics:
        raise WriteError(f"Unknown topic: {topic}")
    name = _one_line(title)
    if not name:
        raise WriteError("Give the evergreen a title.")
    if len(name) > MAX_TITLE:
        raise WriteError(f"Keep the title under {MAX_TITLE} characters.")
    slug = slugify(name)
    if not slug:
        raise WriteError("The title needs at least one letter or number.")
    rel = f"{EVERGREEN_FOLDER}/{slug}.md"
    path = vault.resolve() / rel
    if path.exists():
        raise WriteError(f"A note named {rel} already exists. Pick another title.")
    if not path.parent.is_dir():
        raise WriteError(f"The folder {EVERGREEN_FOLDER} is missing from the vault.")
    tag = definitions.topics[topic].tags[0]
    after = evergreen_text(name, tag, body, today).encode("utf-8")
    return FileEdit(path, rel, None, after, None), f"New evergreen: {name}"
