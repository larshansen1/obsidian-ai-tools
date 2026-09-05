"""Consolidated vault filesystem I/O and frontmatter parsing/writing.

VaultStore wraps pathlib operations for all vault file access. Every module
that reads or writes vault files goes through this class — no direct pathlib
calls on vault paths outside of this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import yaml


class PathTraversalError(ValueError):
    """Raised when a path escapes the vault."""


class VaultStore:
    """Thin wrapper over pathlib.Path for vault-scoped I/O.

    Constructor takes the vault root path. All methods resolve paths relative
    to this root and reject path traversal. No global state, no singleton.
    """

    def __init__(self, vault_path: Path) -> None:
        self._vault_path = vault_path.resolve()

    # ── properties ────────────────────────────────────────────────────────

    @property
    def vault_path(self) -> Path:
        return self._vault_path

    # ── path safety ─────────────────────────────────────────────────────────

    def validate_path(self, path: Path) -> Path:
        """Verify *path* is inside the vault and return its canonical form.

        Raises PathTraversalError if the path escapes the vault directory.
        """
        resolved = path.resolve()
        vault_resolved = self._vault_path
        if not str(resolved).startswith(str(vault_resolved)):
            raise PathTraversalError(f"Path escapes vault: {path}")
        return resolved

    def resolve(self, path: Path) -> Path:
        """Resolve a path inside the vault, raising on traversal."""
        return self.validate_path(self._vault_path / path)

    # ── markdown I/O ────────────────────────────────────────────────────────

    def read_markdown(self, path: Path) -> tuple[dict[str, Any], str]:
        """Read a markdown file and return (frontmatter_dict, body_text)."""
        resolved = self.validate_path(path)
        return self.parse_frontmatter(resolved)

    def write_markdown(
        self, path: Path, frontmatter: dict[str, Any], content: str
    ) -> None:
        """Write a markdown file with YAML frontmatter."""
        resolved = self.validate_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        fm_text = self.format_frontmatter(frontmatter)
        resolved.write_text(f"{fm_text}\n{content}", encoding="utf-8")

    # ── frontmatter helpers ─────────────────────────────────────────────────

    @staticmethod
    def parse_frontmatter(file_path: Path) -> tuple[dict[str, Any], str]:
        """Parse frontmatter from a markdown file.

        Returns (metadata_dict, body_text).
        """
        raw = file_path.read_text(encoding="utf-8")
        return VaultStore._parse_frontmatter_block(raw)

    @staticmethod
    def format_frontmatter(frontmatter: dict[str, Any]) -> str:
        """Format a dict as YAML frontmatter block.

        Returns something like::

            ---
            title: ...
            ---

        List items are indented 2 spaces (matching Obsidian convention).
        """
        body = yaml.dump(
            frontmatter,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        # Indent sequence items 2 spaces under their parent key.
        body = body.replace("\n- ", "\n  - ")
        return f"---\n{body}---\n"

    # ── file existence ──────────────────────────────────────────────────────

    def note_exists(self, path: Path) -> bool:
        """Check whether a note file exists inside the vault."""
        resolved = self.validate_path(path)
        return resolved.exists()

    # ── iteration ───────────────────────────────────────────────────────────

    def iter_notes(self, glob_pattern: str = "**/*.md") -> Iterator[Path]:
        """Yield note paths matching *glob_pattern*, sorted, skipping hidden dirs."""
        for path in sorted(self._vault_path.glob(glob_pattern)):
            rel = path.relative_to(self._vault_path)
            if any(part.startswith(".") for part in rel.parts):
                continue
            yield path

    # ── JSON I/O (index / metadata files) ────────────────────────────────────

    def read_json(self, path: Path) -> Any:
        """Read and deserialize a JSON file inside the vault."""
        resolved = self.validate_path(path)
        return json.loads(resolved.read_text(encoding="utf-8"))

    def write_json(self, path: Path, data: Any) -> None:
        """Serialize *data* as pretty-printed JSON and write inside the vault."""
        resolved = self.validate_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── plain-text I/O ──────────────────────────────────────────────────────

    def read_text(self, path: Path) -> str:
        """Read a plain text file inside the vault."""
        resolved = self.validate_path(path)
        return resolved.read_text(encoding="utf-8")

    def write_text(self, path: Path, text: str) -> None:
        """Write a plain text file inside the vault."""
        resolved = self.validate_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(text, encoding="utf-8")

    # ── file metadata ────────────────────────────────────────────────────────

    def latest_modification(self, glob_pattern: str) -> float:
        """Return the latest ``st_mtime`` among files matching *glob_pattern*."""
        return max(
            (f.stat().st_mtime for f in self._vault_path.glob(glob_pattern)),
            default=0.0,
        )

    # ── low-level file open (for appending etc.) ────────────────────────────

    def open(self, path: Path, mode: str) -> Any:  # noqa: A003  (shadow built-in ok here)
        """Open a file inside the vault for reading/writing/appending."""
        resolved = self.validate_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved.open(mode, encoding="utf-8")

    # ── internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _parse_frontmatter_block(raw: str) -> tuple[dict[str, Any], str]:
        """Split raw file text into (frontmatter_dict, body)."""
        if not raw.startswith("---"):
            return {}, raw
        end = raw.find("---", 3)
        if end == -1:
            return {}, raw
        fm_text = raw[3:end].strip()
        body = raw[end + 3 :].lstrip("\n")
        if not fm_text:
            return {}, body
        try:
            fm = yaml.safe_load(fm_text)
        except yaml.YAMLError:
            fm = {}
        if not isinstance(fm, dict):
            fm = {}
        return fm, body