"""Link notes (W1): add a wikilink to an evergreen under "## Related" in each source note (Q10).

Only plans the change; vault_writes applies it after approval.
"""

from pathlib import Path

from .ai_policy import is_excluded
from .db import readonly
from .relations import link_index, link_targets
from .vault_writes import FileEdit, WriteError, read_for_edit

RELATED_HEADING = "## Related"

_EVERGREEN_SQL = "SELECT is_evergreen FROM notes WHERE path = ?"


def vault_note(vault: Path, rel: str) -> Path:
    """The file for a vault-relative note path. Refuses anything outside the vault."""
    parts = Path(rel).parts
    if (
        Path(rel).is_absolute()
        or not rel.endswith(".md")
        or any(p == ".." or p.startswith(".") for p in parts)
    ):
        raise WriteError(f"Not a note path: {rel}")
    root = vault.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise WriteError(f"No such note: {rel}")
    return path


def link_text(evergreen: str, paths: list[str]) -> str:
    """The shortest link text Obsidian resolves to `evergreen`, e.g. "Name" or "sub/Name"."""
    index = link_index(paths)
    parts = evergreen[: -len(".md")].split("/")
    for i in range(len(parts) - 1, -1, -1):
        tail = "/".join(parts[i:])
        if index.get(tail.lower()) == evergreen:
            return tail
    return evergreen[: -len(".md")]


def _is_heading(line: str) -> bool:
    return line.startswith("# ") or line.startswith("## ")


def add_related_links(text: str, links: list[str]) -> str:
    """Add `- [[link]]` lines under the note's "## Related" heading, adding it at the end
    if missing. Keeps the note's line endings and every other line as it was.
    """
    nl = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(nl)
    lines = text[: -len(nl)].split(nl) if trailing else (text.split(nl) if text else [])
    bullets = [f"- [[{link}]]" for link in links]
    heads = [i for i, line in enumerate(lines) if line.rstrip() == RELATED_HEADING]
    if heads:
        start = heads[-1]
        end = next((i for i in range(start + 1, len(lines)) if _is_heading(lines[i])), len(lines))
        at = max(i for i in range(start, end) if lines[i].strip()) + 1
        lines[at:at] = bullets
    else:
        gap = [""] if lines and lines[-1].strip() else []
        lines += [*gap, RELATED_HEADING, "", *bullets]
    out = nl.join(lines)
    return out + nl if trailing or not text else out


def _note_paths(db_path: Path) -> list[str]:
    with readonly(db_path) as con:
        return [p for (p,) in con.execute("SELECT path FROM notes").fetchall()]


def _check_evergreen(db_path: Path, evergreen: str) -> None:
    with readonly(db_path) as con:
        row = con.execute(_EVERGREEN_SQL, [evergreen]).fetchone()
    if row is None or not row[0]:
        raise WriteError(f"Not an evergreen note: {evergreen}")


def plan_links(
    vault: Path, db_path: Path, excluded: list[str], evergreen: str, sources: list[str]
) -> tuple[list[FileEdit], str]:
    """File edits linking each source note to `evergreen`, and a one-line summary.

    Notes that already link to it are left out; it is an error if none are left.
    """
    if not db_path.exists():
        raise WriteError("No topic data yet. Run `compass scan` first.")
    _check_evergreen(db_path, evergreen)
    paths = _note_paths(db_path)
    index = link_index(paths)
    link = link_text(evergreen, paths)
    edits: list[FileEdit] = []
    for rel in dict.fromkeys(sources):
        if rel == evergreen or is_excluded(rel, excluded):
            raise WriteError(f"Cannot link this note: {rel}")
        path = vault_note(vault, rel)
        before, mtime = read_for_edit(path)
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError:
            raise WriteError(f"Not a UTF-8 text note: {rel}") from None
        if any(index.get(t.lower()) == evergreen for t in link_targets(text)):
            continue
        after = add_related_links(text, [link]).encode("utf-8")
        edits.append(FileEdit(path, rel, before, after, mtime))
    if not edits:
        raise WriteError(f"Every note already links to {evergreen}.")
    noun = "note" if len(edits) == 1 else "notes"
    return edits, f"Link {len(edits)} {noun} to [[{link}]]"
