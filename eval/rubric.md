# Note Evaluation Rubric

## Overview

This rubric evaluates the quality of AI-generated notes from YouTube videos. Each note is scored on 5 criteria using a 1-3 scale.

**Total Score**: 15 points maximum (5 criteria × 3 points each)

---

## Evaluation Criteria

### 1. Accuracy

How well does the summary reflect the actual video content?

| Score | Description |
|-------|-------------|
| **1 (Poor)** | Summary contradicts source material or contains major factual errors |
| **2 (Acceptable)** | Minor omissions or imprecisions, but generally correct |
| **3 (Good)** | Faithful to source content, no significant errors |

**What to check**:
- Does the summary capture the main topic correctly?
- Are there any factual errors or misrepresentations?
- Does it contradict what was actually said in the video?

---

### 2. Completeness

Does the note cover all major themes and key points from the video?

| Score | Description |
|-------|-------------|
| **1 (Poor)** | Missing major themes or critical information |
| **2 (Acceptable)** | Covers main points but misses some secondary themes |
| **3 (Good)** | Comprehensive coverage of all major and most minor themes |

**What to check**:
- Are all main topics from the video mentioned?
- Are important details or examples included?
- Is the depth appropriate for the video length?

---

### 3. Tag Quality

Are the tags relevant, accurate, and consistent with your taxonomy?

| Score | Description |
|-------|-------------|
| **1 (Poor)** | Tags are missing, wrong, or irrelevant |
| **2 (Acceptable)** | Tags present but some are too generic or have minor drift |
| **3 (Good)** | Tags are accurate, specific, and consistent |

**What to check**:
- Do tags match the actual topics discussed?
- Are tags at the right level of specificity?
- Would these tags help you find this note later?

---

### 4. Structure

Does the note follow the expected schema with proper formatting?

| Score | Description |
|-------|-------------|
| **1 (Poor)** | Frontmatter broken, incomplete, or body is messy/unstructured |
| **2 (Acceptable)** | Frontmatter correct but body formatting could be better |
| **3 (Good)** | Clean structure, follows schema perfectly |

**What to check**:
- Is all required frontmatter present and correctly formatted?
- Are sections (Summary, Key Points) clearly organized?
- Is markdown formatting clean and readable?

---

### 5. Usefulness

Would this note actually help you retrieve and understand the content later?

| Score | Description |
|-------|-------------|
| **1 (Poor)** | Wouldn't help future retrieval or understanding |
| **2 (Acceptable)** | Findable but not particularly insightful |
| **3 (Good)** | Would genuinely help future-you find and understand this content |

**What to check**:
- If you search for this topic in 6 months, would you find this note?
- Does the summary give you enough context to decide if you should re-watch?
- Are the key points actionable or memorable?

---

## Evaluation Process

For each note:

1. **Watch/review the source video** (or at least sample it)
2. **Read the generated note** carefully
3. **Score each criterion** using the 1-3 scale
4. **Calculate total score** (out of 15)
5. **Write one sentence** on what would improve it most
6. **Log results** in `baseline_results.md`

---

## Score Interpretation

| Total Score | Assessment |
|-------------|------------|
| **13-15** | Excellent - Ready for vault |
| **10-12** | Good - Minor improvements needed |
| **7-9** | Acceptable - Needs refinement |
| **4-6** | Poor - Major issues |
| **1-3** | Failed - Unusable |

---

## Example Evaluation

**Video**: "Introduction to Docker Containers" (15 min technical tutorial)

| Criterion | Score | Notes |
|-----------|-------|-------|
| Accuracy | 3 | Perfect summary of Docker basics |
| Completeness | 2 | Missed the networking section |
| Tag Quality | 3 | `docker`, `containers`, `devops` are spot-on |
| Structure | 3 | Clean frontmatter and formatting |
| Usefulness | 2 | Good but key points are too generic |

**Total**: 13/15

**Improvement**: Key points should be more specific (e.g., "Use `docker run -d` for background containers" vs "Docker has useful commands")
