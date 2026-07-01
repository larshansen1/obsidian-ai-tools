"""Command-line interface for obsidian-ai-tools."""

import typer

from .commands import ingest as _ingest_cmd
from .commands import preview as _preview_cmd
from .commands import search as _search_cmd
from .commands import serve as _serve_cmd
from .commands import vault as _vault_cmd

app = typer.Typer(
    name="kai",
    help="Knowledge AI Tools - AI-powered tools for Obsidian knowledge management",
    add_completion=False,
)

_ingest_cmd.register(app)
_search_cmd.register(app)
_vault_cmd.register(app)
_preview_cmd.register(app)
_serve_cmd.register(app)

if __name__ == "__main__":
    app()
