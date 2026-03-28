"""Vault terrain map: per-folder keyword and tag overview."""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .indexer import VaultIndex, build_index


class FolderSummary(BaseModel):
    """Summary of a single vault folder."""

    folder: str = Field(..., description="Relative folder path; '(root)' for vault root")
    note_count: int = Field(..., description="Number of notes in this folder")
    top_keywords: list[str] = Field(..., description="Top TF-IDF keywords (inter-folder IDF)")
    top_tags: list[tuple[str, int]] = Field(
        default_factory=list, description="(tag, count) sorted by count desc"
    )


class VaultOverview(BaseModel):
    """Structured overview of the entire vault."""

    vault_path: Path = Field(..., description="Vault root path")
    total_notes: int = Field(..., description="Total notes across all folders")
    total_folders: int = Field(..., description="Number of distinct folders")
    folders: list[FolderSummary] = Field(
        default_factory=list, description="Per-folder summaries, sorted alphabetically"
    )
    generated_at: datetime = Field(default_factory=datetime.now)


def generate_overview(vault_path: Path, top_n: int = 8) -> VaultOverview:
    """Generate a folder-level overview of the vault.

    Uses inter-folder TF-IDF so that terms common across many folders are
    suppressed and folder-distinctive vocabulary rises to the top.

    Args:
        vault_path: Path to the Obsidian vault root
        top_n: Number of keywords to extract per folder

    Returns:
        VaultOverview with per-folder summaries
    """
    vault_index = build_index(vault_path, folder=None, force_rebuild=False)
    return _build_overview(vault_index, vault_path, top_n)


def _build_overview(vault_index: VaultIndex, vault_path: Path, top_n: int) -> VaultOverview:
    """Build overview from an existing VaultIndex (testable without disk access)."""
    # Group notes by folder relative to vault root
    folder_notes: dict[str, list] = {}
    for note in vault_index.notes:
        try:
            rel = note.file_path.relative_to(vault_path)
            folder = str(rel.parent)
            if folder == ".":
                folder = "(root)"
        except ValueError:
            folder = "(root)"
        folder_notes.setdefault(folder, []).append(note)

    if not folder_notes:
        return VaultOverview(
            vault_path=vault_path,
            total_notes=0,
            total_folders=0,
            folders=[],
        )

    sorted_folders = sorted(folder_notes.keys())

    # Build one corpus document per folder for inter-folder TF-IDF
    folder_corpus = [
        " ".join(note.title + " " + note.content for note in folder_notes[f])
        for f in sorted_folders
    ]

    keywords_per_folder = _extract_folder_keywords(folder_corpus, sorted_folders, top_n)

    summaries: list[FolderSummary] = []
    for folder in sorted_folders:
        notes = folder_notes[folder]

        # Count tags
        tag_counts: dict[str, int] = {}
        for note in notes:
            for tag in note.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)

        summaries.append(
            FolderSummary(
                folder=folder,
                note_count=len(notes),
                top_keywords=keywords_per_folder.get(folder, []),
                top_tags=list(top_tags),
            )
        )

    return VaultOverview(
        vault_path=vault_path,
        total_notes=len(vault_index.notes),
        total_folders=len(sorted_folders),
        folders=summaries,
    )


def _extract_folder_keywords(
    corpus: list[str],
    folder_names: list[str],
    top_n: int,
) -> dict[str, list[str]]:
    """Extract top-N TF-IDF keywords per folder using inter-folder IDF.

    Falls back to per-document TF-IDF when there is only one folder
    (IDF is undefined with a single document).
    """
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-untyped]

    if not corpus:
        return {}

    if len(corpus) == 1:
        # Single folder: use per-note TF-IDF within that folder's content
        vectorizer = TfidfVectorizer(stop_words="english", max_features=10000)
        try:
            tfidf = vectorizer.fit_transform(corpus)
            feature_names = vectorizer.get_feature_names_out()
            row = tfidf[0].toarray()[0]
            top_indices = row.argsort()[::-1][:top_n]
            keywords = [feature_names[i] for i in top_indices if row[i] > 0]
        except ValueError:
            keywords = []
        return {folder_names[0]: keywords}

    vectorizer = TfidfVectorizer(stop_words="english", max_features=10000)
    try:
        tfidf = vectorizer.fit_transform(corpus)
        feature_names = vectorizer.get_feature_names_out()
    except ValueError:
        return {name: [] for name in folder_names}

    result: dict[str, list[str]] = {}
    for i, folder in enumerate(folder_names):
        row = tfidf[i].toarray()[0]
        top_indices = row.argsort()[::-1][:top_n]
        result[folder] = [feature_names[j] for j in top_indices if row[j] > 0]

    return result


def format_overview_terminal(overview: VaultOverview) -> str:
    """Format overview for terminal display."""
    lines = [
        f"🗺️  Vault Overview — {overview.vault_path.name}",
        f"   {overview.total_notes} notes · {overview.total_folders} folder(s)",
        "━" * 50,
        "",
    ]

    for fs in overview.folders:
        lines.append(f"📁 {fs.folder}  ({fs.note_count} note(s))")
        if fs.top_keywords:
            lines.append(f"   Keywords: {', '.join(fs.top_keywords)}")
        if fs.top_tags:
            tag_str = "  ".join(f"{tag}({count})" for tag, count in fs.top_tags[:5])
            lines.append(f"   Tags: {tag_str}")
        lines.append("")

    return "\n".join(lines)


def format_overview_markdown(overview: VaultOverview) -> str:
    """Format overview as markdown."""
    ts = overview.generated_at.strftime("%Y-%m-%d")
    lines = [
        "---",
        'title: "Vault Overview"',
        "tags:",
        "  - overview",
        f"created: {overview.generated_at.isoformat()}",
        "---",
        "",
        f"# Vault Overview — {overview.vault_path.name}",
        "",
        f"**{overview.total_notes} notes · {overview.total_folders} folder(s)** · generated {ts}",
        "",
    ]

    for fs in overview.folders:
        lines.append(f"## {fs.folder}  ({fs.note_count} notes)")
        lines.append("")
        if fs.top_keywords:
            lines.append(f"**Keywords**: {', '.join(fs.top_keywords)}")
            lines.append("")
        if fs.top_tags:
            lines.append("**Top tags**:")
            lines.append("")
            for tag, count in fs.top_tags[:8]:
                lines.append(f"- `{tag}` — {count} note(s)")
            lines.append("")

    return "\n".join(lines)


def format_overview_json(overview: VaultOverview) -> str:
    """Format overview as JSON."""
    data = {
        "vault_path": str(overview.vault_path),
        "total_notes": overview.total_notes,
        "total_folders": overview.total_folders,
        "generated_at": overview.generated_at.isoformat(),
        "folders": [
            {
                "folder": fs.folder,
                "note_count": fs.note_count,
                "top_keywords": fs.top_keywords,
                "top_tags": [[tag, count] for tag, count in fs.top_tags],
            }
            for fs in overview.folders
        ],
    }
    return json.dumps(data, indent=2)


def format_overview_compact(overview: VaultOverview) -> str:
    """Format overview as a dense single-line-per-folder block for agent injection."""
    lines = [
        f"vault: {overview.total_notes} notes, {overview.total_folders} folders",
        "---",
    ]

    for fs in overview.folders:
        folder_label = fs.folder if fs.folder == "(root)" else f"{fs.folder}/"
        parts = [f"{folder_label} ({fs.note_count} notes)"]
        if fs.top_keywords:
            parts.append(f"keywords: {' '.join(fs.top_keywords)}")
        if fs.top_tags:
            tags_str = " ".join(f"{tag}({count})" for tag, count in fs.top_tags[:5])
            parts.append(f"tags: {tags_str}")
        lines.append(" | ".join(parts))

    return "\n".join(lines)
