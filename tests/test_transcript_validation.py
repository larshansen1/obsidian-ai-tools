"""Tests for transcript quality and relevance checks."""

from obsidian_ai_tools.transcript_validation import (
    check_transcript_relevance,
    validate_transcript_quality,
)


def test_quality_rejects_short_transcript() -> None:
    """Short transcripts should be rejected before downstream processing."""
    assert "too short" in str(validate_transcript_quality("brief", "Title"))


def test_quality_rejects_fragmented_transcript() -> None:
    """Mostly single-character fragments should be rejected."""
    transcript = " ".join(["a", "b", "c", "d"] * 30)

    assert "fragmented" in str(validate_transcript_quality(transcript, "Title"))


def test_quality_rejects_repeated_phrases() -> None:
    """Looping subtitle phrases should be rejected."""
    transcript = " ".join(["repeat this phrase"] * 30)

    assert "Excessive repetition" in str(validate_transcript_quality(transcript, "Title"))


def test_quality_accepts_normal_transcript() -> None:
    """Natural varied text should pass quality checks."""
    transcript = " ".join(
        f"section{index} explains detailed concept{index} clearly" for index in range(30)
    )

    assert validate_transcript_quality(transcript, "Detailed concepts") is None


def test_relevance_handles_titles_without_significant_words() -> None:
    """Stop-word-only titles should not reject a transcript."""
    assert check_transcript_relevance("anything", "The and for") is True


def test_relevance_compares_title_overlap() -> None:
    """Relevant titles should pass while unrelated titles fail."""
    transcript = "Python testing patterns improve software reliability."

    assert check_transcript_relevance(transcript, "Python testing guide") is True
    assert check_transcript_relevance(transcript, "Cycling nutrition guide") is False
