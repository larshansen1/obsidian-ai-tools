# X (Twitter) Thread Analysis Prompt (v1)

You are analyzing an X (Twitter) thread to create a structured note for an Obsidian knowledge base. The thread is the primary source: each tweet was written by the author to build an argument across multiple posts. Your job is to capture the author's reasoning faithfully, not to improve upon it.

## Input

You will receive:
- Metadata (title, URL, author, site name)
- The full thread text (tweets in order, separated by ---)
- Existing tags from the vault (to encourage reuse)

**Thread Information:**
- Title: {title}
- URL: {url}
- Author: {author}
- Site: {site_name}

**Content:**
{content}

## Task

Generate a structured note with the following sections:

1. **Title**: A clear, descriptive title for the note (matching the thread or improved for clarity)
2. **Summary**: 2-3 sentences describing what this thread argues
3. **Key Claims**: The author's specific arguments or positions, in the order they appear
4. **Key Points**: Supporting facts, examples, or details that carry the argument forward
5. **Implications**: Why this matters, what changes, who should care
6. **Tags**: Relevant topic tags for categorization

## Output Format

Return ONLY a valid JSON object with this exact structure:

```json
{{
  "title": "Clear, descriptive title",
  "summary": "2-3 sentence summary of the thread content",
  "claims": [
    "Specific argument from the thread, attributed to the author",
    "Preserve the author's framing and any numbers, statistics, or names they cite"
  ],
  "key_points": [
    "Supporting fact or concrete example from the thread",
    "Detail that would help future retrieval"
  ],
  "implications": [
    "Why this matters or what changes as a result",
    "Who should care about this information"
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
- Mix broad topics (`ai`, `software`) with specific concepts (`prompt-engineering`, `devops`)
- Avoid overly generic tags (`technology`, `post`, `thread`)
- Ensure tags would help you find this note later

## Content Guidelines

### Faithful Transcription Over Summarization

- The thread is the source of truth. Quote the author's arguments as stated; do not paraphrase away nuance, hedging, or specificity.
- Preserve the order of the argument. A thread builds claim-by-claim; capture the chain.
- When the author cites a number, study, name, or example, keep it verbatim.
- Never add arguments the author did not make, and never soften positions you disagree with.

### Summary
- 2-3 sentences, approximately 50-75 words
- State what the thread argues, not just "This thread discusses..."
- Include the main topic and key context

### Claims
- Attribute to the author ("The author argues...", "The thread claims...")
- Only include claims actually made in the thread

### Key Points
- Facts, examples, and technical details that carry the argument forward
- Concrete references the author names

### Implications
- Why this matters (broader significance)
- What changes as a result (practical applications)
- Forward-looking consequences

## Quality Requirements

- Be accurate - don't contradict or distort the source thread
- Be comprehensive - cover every tweet; threads are short, so nothing should be dropped
- Be faithful - preserve the author's framing, numbers, and named references
- Be useful - write for future-you who needs to find and understand this thread

## Validation

Before returning, verify:
- ✓ Valid JSON format
- ✓ All required fields present
- ✓ Tags are an array of lowercase strings
- ✓ Claims match the thread's actual arguments in order
- ✓ Key points are concrete and traceable to specific tweets
