"""Tests for VaultStore path containment (security)."""

from pathlib import Path

import pytest

from obsidian_ai_tools._vault_store import PathTraversalError, VaultStore


class TestValidatePathContainment:
    """Path containment checks on VaultStore.validate_path."""

    def test_accepts_note_inside_vault(self, tmp_path: Path) -> None:
        """Paths inside the vault resolve and are returned."""
        store = VaultStore(tmp_path / "vault")
        note = tmp_path / "vault" / "note.md"

        resolved = store.validate_path(note)

        assert resolved == note.resolve()

    def test_rejects_sibling_dir_sharing_vault_name_prefix(self, tmp_path: Path) -> None:
        """A sibling directory whose name starts with the vault name is
        rejected, not just directories with traversal sequences."""
        store = VaultStore(tmp_path / "vault")
        stolen = tmp_path / "vault-evil" / "stolen.md"

        with pytest.raises(PathTraversalError) as exc_info:
            store.validate_path(stolen)

        assert str(exc_info.value) == f"Path escapes vault: {stolen}"

    def test_rejects_dot_dot_into_sibling_vault(self, tmp_path: Path) -> None:
        """vault/../vault-evil/x.md is rejected after '..' resolution."""
        store = VaultStore(tmp_path / "vault")
        escaped = tmp_path / "vault" / ".." / "vault-evil" / "x.md"

        with pytest.raises(PathTraversalError) as exc_info:
            store.validate_path(escaped)

        assert str(exc_info.value) == f"Path escapes vault: {escaped}"

    def test_rejects_vault_named_after_vault_path_globally(self, tmp_path: Path) -> None:
        """Vault 'vault' must not accept '/vault-evil' at any depth."""
        store = VaultStore(tmp_path / "vault")
        nested = tmp_path / "vault-evil" / "deep" / "note.md"

        with pytest.raises(PathTraversalError) as exc_info:
            store.validate_path(nested)

        assert str(exc_info.value) == f"Path escapes vault: {nested}"
