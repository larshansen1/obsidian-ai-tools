# arXiv Paper Analysis Prompt (v1)

You are analyzing an arXiv research paper to create a structured note for an
Obsidian knowledge base. The source is a scientific paper, not a blog post:
surface the research question, methodology, and findings, and attribute
claims to the authors.

## Input

You will receive:
- Metadata (title, URL, authors)
- Abstract (or extracted full-text) of the paper
- Existing tags from the vault (to encourage reuse)

**Paper Information:**
- Title: {title}
- Source: {url}
- Author: {author}

**Content:**
{content}

## Task

Generate a structured note with the following sections:

1. **Title**: A clear, descriptive title for the note (matching the paper title or improved for clarity)
2. **Summary**: 2-3 sentences describing the paper's research question and main contribution
3. **Key Claims**: Specific findings, hypotheses, or positions stated by the authors
4. **Key Points**: Methodology details, results, metrics, or important takeaways
5. **Implications**: Why this matters, what changes, who should care
6. **Tags**: Relevant topic tags for categorization

## Output Format

Return ONLY a valid JSON object with this exact structure:

```json
{{
  "title": "Clear, descriptive title",
  "summary": "2-3 sentence summary of the paper's research question and contribution",
  "claims": [
    "Specific finding or argument attributed to the author(s)",
    "Include numbers, statistics, or quotes when relevant"
  ],
  "key_points": [
    "Methodology or experimental setup detail",
    "Concrete result or metric from the paper",
    "Important takeaway that would help future retrieval"
  ],
  "implications": [
    "Why this matters or what changes as a result",
    "Who should care about this research",
    "Future research directions or open questions"
  ],
  "tags": ["tag1", "tag2", "tag3"]
}}
```

## Tag Guidelines

### Existing Tags (Prefer Reuse)

{EXISTING_TAGS}

**Important**: Prefer reusing existing tags when relevant. Only create new tags when the topic is not adequately covered by existing tags.

### Tag Format Rules

1. **All lowercase** - Use `ai` not `AI`
2. **Hyphens for compound words** - Use `machine-learning` not `machinelearning`
3. **Singular form** - Use `system` not `systems` (unless plural is standard)
4. **Short forms preferred**:
   - `ai` not `artificial-intelligence`
   - `llm` not `large-language-model`
   - `ml` not `machine-learning`

### Tag Quality

- Choose 3-7 specific, relevant tags
- Mix broad topics (`ai`, `research`) with specific concepts (`neural-networks`, `methodology`)
- Include subject categories (e.g., `cs.AI`, `stat.ML`) as tags when they aid retrieval
- Consider document type tags when appropriate (`research-paper`, `survey`, `position-paper`)
- Avoid overly generic tags (`document`, `paper`, `interesting`)
- Ensure tags would help you find this note later

## Content Guidelines

### Summary
- 2-3 sentences, approximately 50-75 words
- State the research question or problem the paper addresses
- Include the main contribution and key context, not just "The authors present..."

### Claims
- Extract specific findings, hypotheses, or arguments
- Attribute claims to the author(s) (e.g., "The authors find that...")
- Include experimental results, metrics, or comparisons with baselines
- Distinguish claimed results from speculation
- Only include claims actually made in the paper

### Key Points
- Methodology details: approach, models, datasets, experimental setup
- Concrete results, metrics, or empirical findings
- Technical details that matter for understanding the contribution
- Limitations or assumptions the authors state

### Implications
- Why this matters (broader significance of the research)
- What changes as a result (practical applications)
- Who should care (practitioners, researchers, downstream fields)
- Future research directions or open questions the paper raises

## Quality Requirements

- Be accurate - don't contradict the source material
- Be comprehensive - cover all major themes
- Be specific - include concrete details
- Be useful - write for future-you who needs to find and understand this paper
- Handle paper-specific challenges (dense prose, technical jargon, math notation)

## Validation

Before returning, verify:
- ✓ Valid JSON format
- ✓ All required fields present
- ✓ Tags are an array of lowercase strings
- ✓ Claims are specific and attributed to the authors
- ✓ Key points are concrete and actionable