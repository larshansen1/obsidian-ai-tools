"""Tests for the flashcard_extraction module."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from obsidian_ai_tools.flashcard_extraction import (
    FlashcardCandidate,
    FlashcardError,
    _parse_frontmatter,
    _strip_frontmatter,
    compute_deck,
    estimate_flashcard_cost,
    find_flashcard_candidates,
    generate_flashcards,
    note_tags,
    write_flashcard_file,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_note(
    path: Path,
    title: str = "Test Note",
    tags: list[str] | None = None,
    created: str | None = None,
    body: str = "## Summary\n\nSome content.\n\n## Key Points\n\n- Point one\n",
) -> Path:
    """Write a minimal markdown note and return its path."""
    tags_yaml = "\n".join(f"  - {t}" for t in (tags or []))
    created_line = f"created: {created}\n" if created else ""
    content = (
        f"---\ntitle: {title}\ntags:\n{tags_yaml}\n{created_line}---\n\n{body}"
        if tags_yaml
        else f"---\ntitle: {title}\n{created_line}---\n\n{body}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── find_flashcard_candidates ─────────────────────────────────────────────────


class TestFindFlashcardCandidates:
    def test_returns_note_without_existing_flashcard(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _make_note(vault / "note.md")

        candidates = find_flashcard_candidates(vault)

        assert len(candidates) == 1
        assert candidates[0].file_path == vault / "note.md"
        assert candidates[0].title == "Test Note"

    def test_excludes_note_with_existing_flashcard(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _make_note(vault / "note.md")
        flashcard = vault / "Flashcards" / "note.md"
        flashcard.parent.mkdir(parents=True)
        flashcard.write_text("existing flashcard", encoding="utf-8")

        candidates = find_flashcard_candidates(vault)

        assert candidates == []

    def test_includes_existing_flashcard_when_force(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _make_note(vault / "note.md")
        flashcard = vault / "Flashcards" / "note.md"
        flashcard.parent.mkdir(parents=True)
        flashcard.write_text("existing flashcard", encoding="utf-8")

        candidates = find_flashcard_candidates(vault, force=True)

        assert len(candidates) == 1

    def test_excludes_files_inside_flashcards_folder(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        fc_dir = vault / "Flashcards"
        fc_dir.mkdir(parents=True)
        (fc_dir / "some_card.md").write_text("---\ntitle: Card\n---\n", encoding="utf-8")

        candidates = find_flashcard_candidates(vault)

        assert candidates == []

    def test_excludes_hidden_directories(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        hidden = vault / ".obsidian"
        hidden.mkdir(parents=True)
        (hidden / "note.md").write_text("---\ntitle: Hidden\n---\n", encoding="utf-8")

        candidates = find_flashcard_candidates(vault)

        assert candidates == []

    def test_filters_by_tag(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _make_note(vault / "ai_note.md", title="AI Note", tags=["ai", "llm"])
        _make_note(vault / "other.md", title="Other Note", tags=["productivity"])

        candidates = find_flashcard_candidates(vault, tag="ai")

        assert len(candidates) == 1
        assert candidates[0].title == "AI Note"

    def test_filters_by_folder(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _make_note(vault / "AI" / "deep.md", title="AI Note")
        _make_note(vault / "cooking.md", title="Cooking Note")

        candidates = find_flashcard_candidates(vault, folder="AI")

        assert len(candidates) == 1
        assert candidates[0].title == "AI Note"

    def test_filters_by_since_days_includes_recent(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        recent = datetime.now().isoformat()
        _make_note(vault / "recent.md", title="Recent Note", created=recent)

        candidates = find_flashcard_candidates(vault, since_days=7)

        assert len(candidates) == 1
        assert candidates[0].title == "Recent Note"

    def test_filters_by_since_days_excludes_old(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        old = (datetime.now() - timedelta(days=30)).isoformat()
        _make_note(vault / "old.md", title="Old Note", created=old)

        candidates = find_flashcard_candidates(vault, since_days=7)

        assert candidates == []

    def test_custom_flashcards_folder_name(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _make_note(vault / "note.md")
        # Flashcard exists under custom folder name
        custom = vault / "Cards" / "note.md"
        custom.parent.mkdir(parents=True)
        custom.write_text("card content", encoding="utf-8")

        candidates = find_flashcard_candidates(vault, flashcards_folder="Cards")

        assert candidates == []


# ── write_flashcard_file ──────────────────────────────────────────────────────


class TestWriteFlashcardFile:
    _CARDS = [
        {"question": "What is attention?", "answer": "A mechanism to weigh inputs."},
        {"question": "Who introduced transformers?", "answer": "Vaswani et al. in 2017."},
    ]

    def test_writes_to_correct_mirror_path(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "AI" / "attention.md")

        result = write_flashcard_file(note, self._CARDS, vault)

        assert result == vault / "Flashcards" / "AI" / "attention.md"
        assert result is not None
        assert result.exists()

    def test_frontmatter_contains_flashcard_tag(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "note.md")

        result = write_flashcard_file(note, self._CARDS, vault)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "- flashcards" in content

    def test_custom_deck_written_to_frontmatter(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "note.md")

        result = write_flashcard_file(note, self._CARDS, vault, deck="flashcards/ai")

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "- flashcards/ai" in content

    def test_frontmatter_contains_source_wikilink(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "AI" / "attention.md")

        result = write_flashcard_file(note, self._CARDS, vault)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "[[AI/attention]]" in content

    def test_frontmatter_contains_generated_date(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "note.md")

        result = write_flashcard_file(note, self._CARDS, vault)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        today = datetime.now().date().isoformat()
        assert f"generated: {today}" in content

    def test_body_contains_backlink(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "attention.md")

        result = write_flashcard_file(note, self._CARDS, vault)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "> Source: [[attention]]" in content

    def test_cards_written_in_qa_format(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "note.md")

        result = write_flashcard_file(note, self._CARDS, vault)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "What is attention? :: A mechanism to weigh inputs." in content
        assert "Who introduced transformers? :: Vaswani et al. in 2017." in content
        # Cards separated by a blank line for readability
        first_card = "What is attention? :: A mechanism to weigh inputs."
        second_card = "Who introduced transformers?"
        assert f"{first_card}\n\n{second_card}" in content

    def test_skips_when_file_exists_without_force(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "note.md")
        existing = vault / "Flashcards" / "note.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("old content", encoding="utf-8")

        result = write_flashcard_file(note, self._CARDS, vault, force=False)

        assert result is None
        assert existing.read_text(encoding="utf-8") == "old content"

    def test_overwrites_when_force_is_true(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "note.md")
        existing = vault / "Flashcards" / "note.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("old content", encoding="utf-8")

        result = write_flashcard_file(note, self._CARDS, vault, force=True)

        assert result is not None
        assert "What is attention?" in result.read_text(encoding="utf-8")

    def test_creates_intermediate_directories(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "a" / "b" / "c" / "note.md")

        result = write_flashcard_file(note, self._CARDS, vault)

        assert result is not None
        assert result.exists()
        assert result.parent == vault / "Flashcards" / "a" / "b" / "c"

    def test_custom_flashcards_folder(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "note.md")

        result = write_flashcard_file(note, self._CARDS, vault, flashcards_folder="Cards")

        assert result is not None
        assert result == vault / "Cards" / "note.md"

    def test_skips_cards_with_empty_question_or_answer(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        note = _make_note(vault / "note.md")
        cards = [
            {"question": "Valid question?", "answer": "Valid answer."},
            {"question": "", "answer": "No question."},
            {"question": "No answer?", "answer": ""},
        ]

        result = write_flashcard_file(note, cards, vault)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "Valid question? :: Valid answer." in content
        assert "No question." not in content
        assert "No answer?" not in content


# ── compute_deck / note_tags ──────────────────────────────────────────────────


class TestComputeDeck:
    def test_tag_filter_overrides_note_tags(self) -> None:
        assert compute_deck(["python", "ai"], tag_filter="ai") == "flashcards/ai"

    def test_uses_first_note_tag_when_no_filter(self) -> None:
        assert compute_deck(["python", "ai"]) == "flashcards/python"

    def test_returns_root_deck_when_no_tags(self) -> None:
        assert compute_deck([]) == "flashcards"

    def test_returns_root_deck_when_filter_absent_and_no_tags(self) -> None:
        assert compute_deck([], tag_filter=None) == "flashcards"


class TestNoteTags:
    def test_reads_block_list_tags(self, tmp_path: Path) -> None:
        note = _make_note(tmp_path / "note.md", tags=["ai", "llm"])
        assert note_tags(note) == ["ai", "llm"]

    def test_returns_empty_for_note_without_tags(self, tmp_path: Path) -> None:
        note = _make_note(tmp_path / "note.md")
        assert note_tags(note) == []

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        assert note_tags(tmp_path / "missing.md") == []


# ── estimate_flashcard_cost ───────────────────────────────────────────────────


class TestEstimateFlashcardCost:
    def test_returns_float(self, tmp_path: Path) -> None:
        candidates = [FlashcardCandidate(file_path=tmp_path / "a.md", title="A")]
        cost = estimate_flashcard_cost(candidates, count=5)
        assert isinstance(cost, float)

    def test_scales_with_candidate_count(self, tmp_path: Path) -> None:
        one = [FlashcardCandidate(file_path=tmp_path / "a.md", title="A")]
        ten = [FlashcardCandidate(file_path=tmp_path / f"{i}.md", title=str(i)) for i in range(10)]
        assert estimate_flashcard_cost(ten, count=5) == pytest.approx(
            estimate_flashcard_cost(one, count=5) * 10
        )

    def test_scales_with_card_count(self, tmp_path: Path) -> None:
        candidates = [FlashcardCandidate(file_path=tmp_path / "a.md", title="A")]
        cost_5 = estimate_flashcard_cost(candidates, count=5)
        cost_10 = estimate_flashcard_cost(candidates, count=10)
        assert cost_10 == pytest.approx(cost_5 * 2)

    def test_zero_candidates_returns_zero(self) -> None:
        assert estimate_flashcard_cost([]) == 0.0

    def test_positive_for_nonempty_input(self, tmp_path: Path) -> None:
        candidates = [FlashcardCandidate(file_path=tmp_path / "a.md", title="A")]
        assert estimate_flashcard_cost(candidates, count=5) > 0


# ── _parse_frontmatter / _strip_frontmatter helpers ───────────────────────────


class TestParseFrontmatterHelpers:
    def test_no_frontmatter_returns_empty(self) -> None:
        assert _parse_frontmatter("# Just a heading\n\nBody.") == {}

    def test_unclosed_frontmatter_returns_empty(self) -> None:
        assert _parse_frontmatter("---\ntitle: No closing\n") == {}

    def test_inline_list_tags(self) -> None:
        content = "---\ntags: [ai, llm]\n---\n"
        result = _parse_frontmatter(content)
        assert result["tags"] == ["ai", "llm"]

    def test_comment_and_blank_lines_ignored(self) -> None:
        content = "---\n# comment\n\ntitle: Test\n---\n"
        result = _parse_frontmatter(content)
        assert result["title"] == "Test"
        assert "#" not in result

    def test_strip_frontmatter_no_frontmatter(self) -> None:
        body = "# Heading\n\nContent."
        assert _strip_frontmatter(body) == body

    def test_strip_frontmatter_unclosed(self) -> None:
        content = "---\ntitle: Unclosed\n"
        assert _strip_frontmatter(content) == content

    def test_strip_frontmatter_returns_body_only(self) -> None:
        content = "---\ntitle: T\n---\n\n# Body\n"
        assert _strip_frontmatter(content) == "# Body\n"


# ── generate_flashcards ───────────────────────────────────────────────────────


def _make_openai_response(content: str, cost: float = 0.005) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = content
    usage = MagicMock()
    usage.cost = cost
    response.usage = usage
    return response


class TestGenerateFlashcards:
    _CARDS_JSON = '[{"question": "What is X?", "answer": "X is Y."}]'

    def _note(self, tmp_path: Path) -> Path:
        note = tmp_path / "note.md"
        note.write_text("---\ntitle: Test\n---\n\nBody content.", encoding="utf-8")
        return note

    def test_returns_cards_and_cost(self, tmp_path: Path) -> None:
        with (
            patch("obsidian_ai_tools.flashcard_extraction.OpenAI") as mock_openai,
            patch(
                "obsidian_ai_tools.llm.load_prompt_template",
                return_value="prompt {title} {content} {count}",
            ),
        ):
            mock_openai.return_value.chat.completions.create.return_value = _make_openai_response(
                self._CARDS_JSON, cost=0.01
            )
            cards, cost = generate_flashcards(self._note(tmp_path), count=5, model="m", api_key="k")

        assert cards == [{"question": "What is X?", "answer": "X is Y."}]
        assert cost == pytest.approx(0.01)

    def test_extracts_json_from_json_code_block(self, tmp_path: Path) -> None:
        wrapped = f"```json\n{self._CARDS_JSON}\n```"
        with (
            patch("obsidian_ai_tools.flashcard_extraction.OpenAI") as mock_openai,
            patch(
                "obsidian_ai_tools.llm.load_prompt_template",
                return_value="p {title} {content} {count}",
            ),
        ):
            mock_openai.return_value.chat.completions.create.return_value = _make_openai_response(
                wrapped
            )
            cards, _ = generate_flashcards(self._note(tmp_path), count=5, model="m", api_key="k")

        assert cards[0]["question"] == "What is X?"

    def test_extracts_json_from_plain_code_block(self, tmp_path: Path) -> None:
        wrapped = f"```\n{self._CARDS_JSON}\n```"
        with (
            patch("obsidian_ai_tools.flashcard_extraction.OpenAI") as mock_openai,
            patch(
                "obsidian_ai_tools.llm.load_prompt_template",
                return_value="p {title} {content} {count}",
            ),
        ):
            mock_openai.return_value.chat.completions.create.return_value = _make_openai_response(
                wrapped
            )
            cards, _ = generate_flashcards(self._note(tmp_path), count=5, model="m", api_key="k")

        assert len(cards) == 1

    def test_llm_call_failure_raises_flashcard_error(self, tmp_path: Path) -> None:
        with (
            patch("obsidian_ai_tools.flashcard_extraction.OpenAI") as mock_openai,
            patch(
                "obsidian_ai_tools.llm.load_prompt_template",
                return_value="p {title} {content} {count}",
            ),
        ):
            mock_openai.return_value.chat.completions.create.side_effect = RuntimeError("timeout")
            with pytest.raises(FlashcardError, match="LLM call failed"):
                generate_flashcards(self._note(tmp_path), count=5, model="m", api_key="k")

    def test_bad_json_raises_flashcard_error(self, tmp_path: Path) -> None:
        with (
            patch("obsidian_ai_tools.flashcard_extraction.OpenAI") as mock_openai,
            patch(
                "obsidian_ai_tools.llm.load_prompt_template",
                return_value="p {title} {content} {count}",
            ),
        ):
            mock_openai.return_value.chat.completions.create.return_value = _make_openai_response(
                "not valid json"
            )
            with pytest.raises(FlashcardError, match="Failed to parse"):
                generate_flashcards(self._note(tmp_path), count=5, model="m", api_key="k")

    def test_non_list_json_raises_flashcard_error(self, tmp_path: Path) -> None:
        with (
            patch("obsidian_ai_tools.flashcard_extraction.OpenAI") as mock_openai,
            patch(
                "obsidian_ai_tools.llm.load_prompt_template",
                return_value="p {title} {content} {count}",
            ),
        ):
            mock_openai.return_value.chat.completions.create.return_value = _make_openai_response(
                '{"question": "Q", "answer": "A"}'
            )
            with pytest.raises(FlashcardError, match="Expected JSON array"):
                generate_flashcards(self._note(tmp_path), count=5, model="m", api_key="k")
