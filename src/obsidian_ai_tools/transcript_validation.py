"""Utilities for validating transcript quality."""

import re


class TranscriptQualityIssue(Exception):
    """Raised when transcript quality is too low to process."""

    pass


def validate_transcript_quality(
    transcript: str,
    video_title: str,
    min_length: int = 100,
    min_avg_word_length: float = 2.5,
) -> str | None:
    """Validate transcript quality before processing.

    Args:
        transcript: Raw transcript text
        video_title: Video title for relevance checking
        min_length: Minimum transcript length in characters
        min_avg_word_length: Minimum average word length (detects fragmented text)

    Returns:
        None if valid, error message string if invalid

    Raises:
        TranscriptQualityIssue: If transcript fails quality checks
    """
    # Check minimum length
    if len(transcript.strip()) < min_length:
        return f"Transcript too short ({len(transcript)} chars, minimum {min_length})"

    # Split into words
    words = transcript.split()

    # Check if mostly single characters or very short words (indicates fragments)
    if words:
        avg_word_len = sum(len(w) for w in words) / len(words)
        if avg_word_len < min_avg_word_length:
            return f"Transcript appears fragmented (avg word length: {avg_word_len:.1f})"

    # Check for excessive repetition (same phrase repeated many times)
    # This catches corrupted transcripts that loop
    phrases = re.findall(r"\b\w+\s+\w+\s+\w+\b", transcript.lower())
    if phrases:
        from collections import Counter

        phrase_counts = Counter(phrases)
        most_common = phrase_counts.most_common(1)[0]
        if most_common[1] > len(phrases) * 0.1:  # More than 10% repetition
            return (
                f"Excessive repetition detected: '{most_common[0]}' appears {most_common[1]} times"
            )

    return None


def check_transcript_relevance(transcript: str, video_title: str, threshold: float = 0.3) -> bool:
    """Check if transcript seems relevant to video title.

    Args:
        transcript: Transcript text
        video_title: Video title
        threshold: Minimum word overlap ratio (0-1)

    Returns:
        True if transcript seems relevant, False otherwise
    """
    # Extract significant words from title (excluding common words)
    stop_words = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "vs",
        "big",
        "new",
    }

    title_words = set(
        word.lower()
        for word in re.findall(r"\b\w+\b", video_title)
        if len(word) > 2 and word.lower() not in stop_words
    )

    if not title_words:
        # Can't determine relevance without title words
        return True

    # Count how many title words appear in transcript
    transcript_lower = transcript.lower()
    matches = sum(1 for word in title_words if word in transcript_lower)

    overlap_ratio = matches / len(title_words)
    return overlap_ratio >= threshold
