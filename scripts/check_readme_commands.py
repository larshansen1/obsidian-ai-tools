#!/usr/bin/env python3
"""Verify that the README command reference matches the CLI help output."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from typer.main import get_command

from obsidian_ai_tools.cli import app

ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"


def readme_commands() -> set[str]:
    """Extract top-level command names from the README command table."""
    commands: set[str] = set()
    for line in README_PATH.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*`([^`]+)`\s*\|", line)
        if not match:
            continue

        cell = match.group(1).strip()
        if not cell.startswith("kai "):
            continue

        parts = cell.split()
        if len(parts) < 2:
            continue

        commands.add(parts[1])

    return commands


def cli_commands() -> set[str]:
    """Extract top-level command names from Typer's Click command graph."""
    command = get_command(app)
    commands = getattr(command, "commands", None)
    if not isinstance(commands, dict):
        raise RuntimeError("Expected the kai CLI to register a command group")
    return set(commands)


def main() -> int:
    readme = readme_commands()
    cli = cli_commands()

    missing = sorted(cli - readme)
    extra = sorted(readme - cli)

    if not missing and not extra:
        print("README command reference matches `kai --help`.")
        return 0

    print("README command reference is out of sync with `kai --help`.", file=sys.stderr)

    if missing:
        print("Missing from README:", file=sys.stderr)
        for command in missing:
            print(f"  - {command}", file=sys.stderr)

    if extra:
        print("Extra in README:", file=sys.stderr)
        for command in extra:
            print(f"  - {command}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
