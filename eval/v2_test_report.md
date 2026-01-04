# Test Report — YouTube V2 Note Generation (Final)

## Scope
Evaluates 5 AI-generated notes using `youtube_v2` prompt after all bug fixes. Uses **Note Evaluation Rubric** (5 criteria × 1–3 points; max 15).

---

## Summary of Results

| # | Video | Accuracy | Completeness | Tag Quality | Structure | Usefulness | Total | Assessment |
|---|-------|----------|--------------|-------------|-----------|------------|-------|------------|
| 1 | [30-Minute Travel Approval System](https://www.youtube.com/watch?v=lOai20b1N0U) | 3 | 3 | 3 | 3 | 3 | **15/15** | Excellent |
| 2 | [Linus Torvalds on AI's Impact](https://www.youtube.com/watch?v=VHHT6W-N0ak) | 3 | 3 | 3 | 3 | 3 | **15/15** | Excellent |
| 3 | [AI Evolution: LLMs to Omnimodels](https://www.youtube.com/watch?v=YJE7RY_z6z8) | 3 | 3 | 3 | 3 | 3 | **15/15** | Excellent |
| 4 | [DORA 2024 AI Report](https://www.youtube.com/watch?v=CoGO6s7bS3A) | 3 | 3 | 3 | 3 | 3 | **15/15** | Excellent |
| 5 | [Social Contract: Post-Labor Economics](https://www.youtube.com/watch?v=nhrPDBDG7aA) | 3 | 3 | 2 | 3 | 3 | **14/15** | Excellent |

---

## Overall Statistics

- **Average score:** (15 + 15 + 15 + 15 + 14) / 5 = **14.8 / 15**
- **Pass rate (≥10):** 5/5 (**100%**)
- **Excellent (≥13):** 5/5 (**100%**)

### Comparison to Baseline (v1)

| Metric | Baseline (v1) | V2 Final | Δ |
|--------|---------------|----------|---|
| Average score | 9.4/15 | 14.8/15 | **+5.4** |
| Pass rate | 60% | 100% | **+40%** |
| Structure issues | 4/5 | 0/5 | **Fixed** |

---

## Bugs Fixed in This Session

1. **`youtube_v2.md` template** — Curly braces not escaped for `str.format()` → Added `{{` `}}`
2. **`Note.to_markdown()` Source section** — Missing f-string prefix → `{self.source_url}` now interpolates
3. **YAML special character escaping** — Titles with colons now quoted → `title: "Text: with colon"`

---

## Remaining Improvements (Future Work)

| Area | Status | Notes |
|------|--------|-------|
| Tag normalization | Needs data | Requires more vault notes to analyze tag consistency |
| Tag taxonomy enforcement | Planned | Controlled vocabulary for common topics |
| Non-English transcripts | Untested | May need Whisper fallback |

---

## Verdict

✅ **V2 note generation is vault-ready.**

All 5 test videos produce notes with:
- Accurate, complete content
- Proper YAML frontmatter
- Working source links
- Useful claims/implications sections

Tag normalization is the only area that may benefit from tuning once more vault data is available.
