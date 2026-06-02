"""ingest command."""

from pathlib import Path
from typing import Annotated

import typer

from ..config import get_settings
from ..ingestion import (
    ContentFetchError,
    IngestionProgress,
    IngestionRequest,
    NoteGenerationStageError,
    ProviderSelectionError,
    VaultWriteError,
    ingest_content,
)
from ..logging import setup_logging
from ..models import ArticleMetadata, VideoMetadata
from ..observability import track_command
from ..youtube import (
    InvalidYouTubeURLError,
    TranscriptUnavailableError,
)


def register(app: typer.Typer) -> None:
    app.command()(ingest)


@track_command("ingest")
def ingest(
    url: Annotated[str, typer.Argument(help="URL or file path to ingest")],
    vault: Annotated[
        str | None,
        typer.Option(
            "--vault",
            "-v",
            help="Override vault path (default: from .env OBSIDIAN_VAULT_PATH)",
        ),
    ] = None,
    prompt_version: Annotated[
        str | None,
        typer.Option(
            "--prompt-version",
            "-p",
            help="Prompt version (default: auto-detected based on source)",
        ),
    ] = None,
    max_pages: Annotated[
        int | None,
        typer.Option(
            "--max-pages",
            help="Maximum pages to extract from PDF (default: 50)",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Enable verbose logging",
        ),
    ] = False,
    transcript_providers: Annotated[
        str | None,
        typer.Option(
            "--transcript-providers",
            help="Override provider order (comma-separated: direct,supadata,decodo)",
        ),
    ] = None,
) -> None:
    """Ingest content into your Obsidian vault.

    Supports:
    - YouTube videos (fetches transcript)
    - Web articles (fetches text content)
    - PDF documents (extracts text, local or remote)
    - Local Markdown files (reads content)

    Fetches content, generates a structured note using LLM,
    and saves it to your vault's inbox folder.

    Examples:
        kai ingest https://www.youtube.com/watch?v=dQw4w9WgXcQ
        kai ingest https://example.com/blog/article
        kai ingest https://example.com/research-paper.pdf
        kai ingest ./documents/paper.pdf --max-pages 30
        kai ingest ./notes/draft.md
    """
    setup_logging(verbose)

    try:
        settings = get_settings()
    except Exception as e:
        typer.echo(f"❌ Configuration error: {e}", err=True)
        typer.echo("💡 Make sure you have a .env file with required settings.", err=True)
        raise typer.Exit(1) from e

    def show_progress(progress: IngestionProgress) -> None:
        if progress.stage == "provider_selected":
            typer.echo(f"🌐 Ingesting {progress.provider_name} content...")
            typer.echo(f"   Source: {url}")
        elif progress.stage == "fetching":
            typer.echo(f"📥 Fetching content using {progress.provider_name} provider...")
            if progress.provider_name == "youtube" and progress.transcript_providers:
                providers_list = progress.transcript_providers.replace(",", ", ")
                typer.echo(f"   🔍 Trying transcript providers: {providers_list}")
        elif progress.stage == "content_fetched":
            metadata = progress.metadata
            if isinstance(metadata, VideoMetadata):
                if progress.provider_name == "youtube" and metadata.provider_used:
                    typer.echo(
                        f"   ✓ Transcript via {metadata.provider_used} "
                        f"({len(metadata.transcript)} chars)"
                    )
                else:
                    typer.echo(f"   ✓ Transcript fetched ({len(metadata.transcript)} chars)")
            elif isinstance(metadata, ArticleMetadata):
                typer.echo(
                    f"   ✓ Content fetched: '{metadata.title}' ({len(metadata.content)} chars)"
                )
                if progress.provider_name == "pdf" and "Only the first" in metadata.content:
                    typer.echo("   ⚠️  PDF truncated due to page limit", err=False)
        elif progress.stage == "generating":
            typer.echo(
                f"🤖 Generating note with {settings.llm_model} ({progress.prompt_version})..."
            )
        elif progress.stage == "note_generated" and progress.note is not None:
            typer.echo(f"   ✓ Note generated: '{progress.note.title}'")
            typer.echo(f"   ✓ Tags: {', '.join(progress.note.tags)}")
        elif progress.stage == "writing":
            typer.echo("💾 Writing note to vault...")
        elif progress.stage == "note_written":
            typer.echo(f"   ✓ Note saved to: {progress.file_path}")

    request = IngestionRequest(
        url=url,
        vault_path=Path(vault) if vault else None,
        prompt_version=prompt_version,
        transcript_providers=transcript_providers,
        max_pages=max_pages,
    )
    try:
        ingest_content(request, settings, on_progress=show_progress)
    except ProviderSelectionError:
        typer.echo("❌ Unknown source type. Please provide a valid URL or file path.", err=True)
        raise typer.Exit(1) from None
    except ContentFetchError as e:
        cause = e.__cause__
        if isinstance(cause, InvalidYouTubeURLError):
            typer.echo(f"❌ Invalid URL: {cause}", err=True)
        elif isinstance(cause, TranscriptUnavailableError):
            typer.echo(f"❌ Transcript unavailable: {cause}", err=True)
            typer.echo(
                "💡 This video may not have English captions or may be private.",
                err=True,
            )
        elif isinstance(cause, FileNotFoundError):
            typer.echo(f"❌ File not found: {cause}", err=True)
        else:
            typer.echo(f"❌ Failed to fetch content: {cause or e}", err=True)
        raise typer.Exit(1) from e
    except NoteGenerationStageError as e:
        typer.echo(f"❌ Failed to generate note: {e.__cause__ or e}", err=True)
        typer.echo("💡 Check your OpenRouter API key and model configuration.", err=True)
        raise typer.Exit(1) from e
    except VaultWriteError as e:
        typer.echo(f"❌ Failed to write note: {e.__cause__ or e}", err=True)
        raise typer.Exit(1) from e

    typer.echo("✅ Ingestion complete!")
