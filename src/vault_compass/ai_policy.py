"""Which notes may reach an LLM (N4, ADR 0003).

The one place that decides. Every path from the vault to a model goes through
`AiVault` (vault_tools.py), which asks `is_excluded` before it returns text,
so a feature cannot send an excluded note by forgetting to check.
"""

from collections.abc import Iterable


def is_excluded(path: str, folders: Iterable[str]) -> bool:
    """True when `path` (vault-relative) is inside one of the excluded folders."""
    norm = path.replace("\\", "/").lstrip("/")
    return any(norm == f or norm.startswith(f + "/") for f in folders)
