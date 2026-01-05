"""Digest generation for knowledge vault summaries."""

import re
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from .indexer import NoteMetadata, VaultIndex, build_index


class NoteSummary(BaseModel):
    """Summary of a single note for digest listing."""

    title: str = Field(..., description="Note title")
    summary: str = Field(..., description="First sentence or truncated from content")
    source_type: str = Field(..., description="Source type: youtube, web, pdf, or manual")
    file_path: Path = Field(..., description="Path to the note file")


class DigestReport(BaseModel):
    """Weekly/daily knowledge digest report."""

    period_start: datetime = Field(..., description="Start of the digest period")
    period_end: datetime = Field(..., description="End of the digest period")
    total_notes: int = Field(..., description="Total notes in vault")
    new_notes: int = Field(..., description="Notes created in the period")
    new_notes_details: list[NoteSummary] = Field(
        default_factory=list, description="Details of each new note"
    )
    by_source_type: dict[str, int] = Field(
        default_factory=dict, description="Count by source type"
    )
    top_tags: list[tuple[str, int]] = Field(
        default_factory=list, description="Top tags with counts"
    )
    most_referenced: list[tuple[str, int]] = Field(
        default_factory=list, description="Most backlinked notes"
    )
    inbox_count: int = Field(0, description="Notes still in inbox")


# Regex pattern for [[wikilinks]] including [[Link|Alias]] format
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# Characters not allowed in Obsidian wikilinks
WIKILINK_INVALID_CHARS = re.compile(r"[/\\:\[\]#^|]")


def sanitize_wikilink(title: str) -> str:
    """Sanitize a title for use in Obsidian wikilinks.

    Obsidian doesn't allow certain characters in wikilink targets:
    / \\ : [ ] # ^ |

    Args:
        title: The note title to sanitize

    Returns:
        Sanitized title safe for wikilinks
    """
    # Replace invalid characters with safe alternatives
    sanitized = WIKILINK_INVALID_CHARS.sub("-", title)
    # Collapse multiple dashes
    sanitized = re.sub(r"-+", "-", sanitized)
    # Remove leading/trailing dashes
    sanitized = sanitized.strip("-")
    return sanitized


def extract_summary(note: NoteMetadata, max_length: int = 150) -> str:
    """Extract summary from note content.

    Looks for ## Summary section or takes first paragraph.
    Truncates to first sentence if over max_length.

    Args:
        note: Note metadata with content
        max_length: Maximum length of summary

    Returns:
        Extracted summary string
    """
    content = note.content

    # Try to find ## Summary section
    summary_match = re.search(
        r"##\s*Summary\s*\n+(.+?)(?=\n##|\n\n\n|\Z)",
        content,
        re.IGNORECASE | re.DOTALL,
    )

    if summary_match:
        summary_text = summary_match.group(1).strip()
    else:
        # Fallback: first non-empty paragraph after frontmatter
        paragraphs = content.strip().split("\n\n")
        summary_text = ""
        for para in paragraphs:
            para = para.strip()
            # Skip headers and empty lines
            if para and not para.startswith("#"):
                summary_text = para
                break

    if not summary_text:
        return ""

    # Clean up: remove markdown formatting
    summary_text = re.sub(r"\*\*|__", "", summary_text)  # Bold
    summary_text = re.sub(r"\*|_", "", summary_text)  # Italic
    summary_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary_text)  # Links
    summary_text = " ".join(summary_text.split())  # Normalize whitespace

    # Truncate to first sentence if too long
    if len(summary_text) > max_length:
        # Try to find first sentence end
        sentence_end = re.search(r"[.!?]\s", summary_text[:max_length])
        if sentence_end:
            summary_text = summary_text[: sentence_end.end()].strip()
        else:
            # Hard truncate with ellipsis
            summary_text = summary_text[: max_length - 3].rsplit(" ", 1)[0] + "..."

    return summary_text


def count_backlinks(vault_index: VaultIndex) -> dict[str, int]:
    """Count [[wikilink]] references across all notes.

    Args:
        vault_index: VaultIndex with all notes

    Returns:
        Dictionary mapping note title to backlink count
    """
    backlink_counts: dict[str, int] = {}

    for note in vault_index.notes:
        # Find all wikilinks in content
        matches = WIKILINK_PATTERN.findall(note.content)

        for link_target in matches:
            link_target = link_target.strip()
            if link_target:
                backlink_counts[link_target] = backlink_counts.get(link_target, 0) + 1

    return backlink_counts


def generate_digest(
    vault_path: Path,
    since_days: int = 7,
    inbox_folder: str = "inbox",
) -> DigestReport:
    """Generate a digest report for the specified period.

    Args:
        vault_path: Path to the Obsidian vault
        since_days: Number of days to include
        inbox_folder: Name of inbox folder

    Returns:
        DigestReport with vault statistics
    """
    # Calculate date range
    period_end = datetime.now()
    period_start = period_end - timedelta(days=since_days)

    # Build vault index (entire vault)
    vault_index = build_index(vault_path, folder=None, force_rebuild=True)

    # Filter notes by creation date
    new_notes: list[NoteMetadata] = []
    for note in vault_index.notes:
        note_date = note.created or datetime.fromtimestamp(note.modified_time)
        if note_date >= period_start:
            new_notes.append(note)

    # Build new_notes_details
    new_notes_details: list[NoteSummary] = []
    for note in new_notes:
        source_type = note.source_type or "manual"
        summary = extract_summary(note)
        new_notes_details.append(
            NoteSummary(
                title=note.title,
                summary=summary,
                source_type=source_type,
                file_path=note.file_path,
            )
        )

    # Count by source type
    by_source_type: dict[str, int] = {}
    for note in new_notes:
        source_type = note.source_type or "manual"
        by_source_type[source_type] = by_source_type.get(source_type, 0) + 1

    # Top tags from new notes
    tag_counts: dict[str, int] = {}
    for note in new_notes:
        for tag in note.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Most referenced (backlinks from entire vault)
    backlinks = count_backlinks(vault_index)
    most_referenced = sorted(backlinks.items(), key=lambda x: x[1], reverse=True)[:5]

    # Count inbox notes
    inbox_path = vault_path / inbox_folder
    inbox_count = 0
    if inbox_path.exists():
        inbox_count = len(list(inbox_path.glob("*.md")))

    return DigestReport(
        period_start=period_start,
        period_end=period_end,
        total_notes=len(vault_index.notes),
        new_notes=len(new_notes),
        new_notes_details=new_notes_details,
        by_source_type=by_source_type,
        top_tags=list(top_tags),
        most_referenced=list(most_referenced),
        inbox_count=inbox_count,
    )


def format_digest_terminal(report: DigestReport) -> str:
    """Format digest for terminal display.

    Args:
        report: DigestReport to format

    Returns:
        Terminal-friendly string with emoji
    """
    lines = []

    # Header
    start_str = report.period_start.strftime("%b %d")
    end_str = report.period_end.strftime("%b %d, %Y")
    lines.append(f"📊 Knowledge Digest ({start_str} - {end_str})")
    lines.append("━" * 50)
    lines.append("")

    # Notes summary
    lines.append("📝 Notes Summary")
    lines.append(f"   Total in vault: {report.total_notes}")
    lines.append(f"   New this period: {report.new_notes}")
    lines.append("")

    # By source type
    if report.by_source_type:
        lines.append("📦 By Source Type")
        for source_type, count in sorted(
            report.by_source_type.items(), key=lambda x: x[1], reverse=True
        ):
            lines.append(f"   {source_type}: {count} note(s)")
        lines.append("")

    # New notes details (compact for terminal)
    if report.new_notes_details:
        lines.append("📄 New Notes")
        for note in report.new_notes_details[:10]:  # Limit to 10 for terminal
            summary_preview = note.summary[:60] + "..." if len(note.summary) > 60 else note.summary
            lines.append(f"   • {note.title}")
            if summary_preview:
                lines.append(f"     {summary_preview}")
        if len(report.new_notes_details) > 10:
            lines.append(f"   ... and {len(report.new_notes_details) - 10} more")
        lines.append("")

    # Top tags
    if report.top_tags:
        lines.append("🏷️  Top Tags")
        for tag, count in report.top_tags[:5]:
            lines.append(f"   {tag}: {count} note(s)")
        lines.append("")

    # Most referenced
    if report.most_referenced:
        lines.append("🔗 Most Referenced")
        for title, count in report.most_referenced:
            safe_title = sanitize_wikilink(title)
            lines.append(f"   [[{safe_title}]]: {count} backlink(s)")
        lines.append("")

    # Inbox status
    lines.append("📥 Inbox Status")
    lines.append(f"   {report.inbox_count} note(s) awaiting organization")

    return "\n".join(lines)


def format_digest_markdown(report: DigestReport) -> str:
    """Format digest as markdown for vault storage.

    Args:
        report: DigestReport to format

    Returns:
        Markdown string with frontmatter
    """
    start_str = report.period_start.strftime("%b %d")
    end_str = report.period_end.strftime("%b %d, %Y")
    period_title = f"{start_str} - {end_str}"

    # Frontmatter
    lines = [
        "---",
        'title: "Knowledge Digest"',
        "tags:",
        "  - digest",
        "  - weekly-review",
        f"created: {report.period_end.isoformat()}",
        "type: digest",
        f"period_start: {report.period_start.strftime('%Y-%m-%d')}",
        f"period_end: {report.period_end.strftime('%Y-%m-%d')}",
        "---",
        "",
        "# Knowledge Digest",
        "",
        f"**Period**: {period_title}",
        "",
    ]

    # Notes summary
    lines.extend([
        "## 📝 Notes Summary",
        "",
        f"- **Total notes in vault**: {report.total_notes}",
        f"- **New notes this period**: {report.new_notes}",
        "",
    ])

    # By source type
    if report.by_source_type:
        lines.extend([
            "## 📦 By Source Type",
            "",
            "| Source | Count |",
            "|--------|-------|",
        ])
        for source_type, count in sorted(
            report.by_source_type.items(), key=lambda x: x[1], reverse=True
        ):
            lines.append(f"| {source_type} | {count} |")
        lines.append("")

    # New notes details (grouped by source type)
    if report.new_notes_details:
        lines.extend([
            "## 📄 New Notes This Period",
            "",
        ])

        # Group by source type
        by_type: dict[str, list[NoteSummary]] = {}
        for note in report.new_notes_details:
            if note.source_type not in by_type:
                by_type[note.source_type] = []
            by_type[note.source_type].append(note)

        for source_type in sorted(by_type.keys()):
            notes = by_type[source_type]
            lines.append(f"### {source_type.title()} ({len(notes)})")
            lines.append("")
            lines.append("| Note | Summary |")
            lines.append("|------|---------|")
            for note in notes:
                # Escape pipe characters in summary
                # Use filename stem for wikilink (Obsidian matches by filename, not title)
                safe_summary = note.summary.replace("|", "\\|")
                file_stem = note.file_path.stem
                lines.append(f"| [[{file_stem}]] | {safe_summary} |")
            lines.append("")

    # Top tags
    if report.top_tags:
        lines.extend([
            "## 🏷️ Top Tags",
            "",
        ])
        for i, (tag, count) in enumerate(report.top_tags, 1):
            lines.append(f"{i}. `{tag}` — {count} note(s)")
        lines.append("")

    # Most referenced
    if report.most_referenced:
        lines.extend([
            "## 🔗 Most Referenced Notes",
            "",
            "These notes were linked to most often from other notes:",
            "",
        ])
        for i, (title, count) in enumerate(report.most_referenced, 1):
            safe_title = sanitize_wikilink(title)
            lines.append(f"{i}. [[{safe_title}]] — {count} backlink(s)")
        lines.append("")

    # Inbox status
    lines.extend([
        "## 📥 Inbox Status",
        "",
        f"**{report.inbox_count} note(s)** are still in the inbox awaiting organization.",
        "",
        "---",
        "*Generated by kai digest*",
    ])

    return "\n".join(lines)


def format_digest_json(report: DigestReport) -> str:
    """Format digest as JSON.

    Args:
        report: DigestReport to format

    Returns:
        JSON string
    """
    import json

    data = {
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "total_notes": report.total_notes,
        "new_notes": report.new_notes,
        "new_notes_details": [
            {
                "title": n.title,
                "summary": n.summary,
                "source_type": n.source_type,
                "file_path": str(n.file_path),
            }
            for n in report.new_notes_details
        ],
        "by_source_type": report.by_source_type,
        "top_tags": report.top_tags,
        "most_referenced": report.most_referenced,
        "inbox_count": report.inbox_count,
    }

    return json.dumps(data, indent=2)
