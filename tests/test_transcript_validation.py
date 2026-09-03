"""Tests for transcript quality and relevance checks."""

import pytest

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


def test_quality_accepts_exact_min_length_boundary() -> None:
    """A transcript exactly at min_length must pass the length check."""
    assert validate_transcript_quality("x" * 100, "Title") is None


def test_quality_uses_default_min_avg_word_length() -> None:
    """Average word length of 3.0 passes with the default 2.5 threshold."""
    short = [f"a{index}" for index in range(10)]
    short += [f"b{index}" for index in range(10)]
    short += [f"c{index}" for index in range(10)]
    long_words = [f"AA{index:02d}" for index in range(10)]
    long_words += [f"BB{index:02d}" for index in range(10)]
    long_words += [f"CC{index:02d}" for index in range(10)]
    transcript = " ".join(short + long_words)

    assert validate_transcript_quality(transcript, "Title") is None


def test_quality_accepts_exact_min_avg_word_length_boundary() -> None:
    """Average word length of exactly min_avg_word_length must pass."""
    short = [f"a{index}" for index in range(10)]
    short += [f"b{index}" for index in range(10)]
    short += [f"c{index}" for index in range(10)]
    medium = [f"d{index:02d}" for index in range(10)]
    medium += [f"e{index:02d}" for index in range(10)]
    medium += [f"f{index:02d}" for index in range(10)]
    transcript = " ".join(short + medium)

    result = validate_transcript_quality(transcript, "Title", min_avg_word_length=2.5)
    assert result is None


def test_quality_repetition_exact_threshold_boundary() -> None:
    """A repetition ratio of exactly 10% must not be flagged."""
    words = [f"w{index:03d}" for index in range(120)]
    for offset in (0, 30, 60, 90):
        words[offset : offset + 3] = ["aaa", "bbb", "ccc"]
    transcript = " ".join(words)

    assert validate_transcript_quality(transcript, "Title") is None


def test_quality_repetition_message_names_the_phrase() -> None:
    """Repetition message must contain the exact repeated phrase (lowercase)."""
    transcript = " ".join(["repeat this phrase"] * 30)
    message = str(validate_transcript_quality(transcript, "Title"))

    assert "repeat this phrase" in message


@pytest.mark.parametrize("stop_word", ["the", "and", "but", "for", "with", "big", "new"])
def test_relevance_filters_single_stop_word(stop_word: str) -> None:
    """Each longer stop word must be excluded from the significance count.

    "or" and the 2-character stop words are excluded by the length filter
    before the stop-word set is consulted, so their mutations are
    unobservable and not covered here.
    """
    title = f"alpha beta gamma delta epsilon zeta {stop_word}"

    assert check_transcript_relevance("alpha beta only", title) is True


def test_relevance_two_char_words_are_not_significant() -> None:
    """Title words of length 2 are filtered out before overlap counting."""
    assert check_transcript_relevance("alpha appears here", "Alpha Beta gamma AI") is True


def test_relevance_three_char_words_are_significant() -> None:
    """Title words of length 3 remain significant for the overlap ratio."""
    assert check_transcript_relevance("delta only words here", "Alpha Beta cat delta") is False


def test_relevance_counts_each_title_word_once() -> None:
    """Each matching title word contributes exactly one overlap."""
    assert check_transcript_relevance("only alpha here", "alpha beta gamma delta epsilon") is False


def test_relevance_threshold_is_inclusive() -> None:
    """A ratio of exactly 0.3 counts as relevant."""
    title = "alpha beta gamma delta epsilon zeta theta iota kappa omega"

    assert check_transcript_relevance("alpha beta gamma nothing else", title) is True
