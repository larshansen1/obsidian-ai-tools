# PDF Document Analysis Prompt (v1)

You are analyzing a PDF document to create a structured note for an Obsidian knowledge base.

## Input

You will receive:
- Metadata (title, URL/path, author)
- Extracted text content from the PDF
- Existing tags from the vault (to encourage reuse)

**PDF Information:**
- Title: {title}
- Source: {url}
- Author: {author}

**Content:**
{content}

## Task

Generate a structured note with the following sections:

1. **Title**: A clear, descriptive title for the note (matching the PDF title or improved for clarity)
2. **Summary**: 2-3 sentences describing what this document is about
3. **Key Claims**: Specific arguments, opinions, or positions stated in the text
4. **Key Points**: Actionable insights, factual details, or important takeaways
5. **Implications**: Why this matters, what changes, who should care
6. **Tags**: Relevant topic tags for categorization

## Output Format

Return ONLY a valid JSON object with this exact structure:

```json
{{
  "title": "Clear, descriptive title",
  "summary": "2-3 sentence summary of the document content",
  "claims": [
    "Specific argument, opinion, or position from the text",
    "Include numbers, statistics, or quotes when relevant"
  ],
  "key_points": [
    "Actionable insight or factual detail",
    "Concrete example mentioned in the text",
    "Important takeaway that would help future retrieval"
  ],
  "implications": [
    "Why this matters or what changes as a result",
    "Who should care about this information",
    "Future-facing consequences or applications"
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
- Consider document type tags when appropriate (`research-paper`, `technical-report`, `whitepaper`)
- Avoid overly generic tags (`document`, `pdf`, `interesting`)
- Ensure tags would help you find this note later

## Content Guidelines

### Summary
- 2-3 sentences, approximately 50-75 words
- Explain what the document is about, not just "The author discusses..."
- Include the main topic and key context
- For academic papers, note the research question or objective

### Claims
- Extract specific arguments or positions
- Attribute to the author(s) when appropriate
- Include research findings or hypotheses
- Only include claims actually made in the text

### Key Points
- Factual information and actionable insights
- Technical details that matter
- Concrete examples, definitions, or methodologies
- Results, metrics, or empirical findings

### Implications
- Why this matters (broader significance)
- What changes as a result (practical applications)
- Forward-looking consequences
- Future research directions or open questions

## Quality Requirements

- Be accurate - don't contradict the source material
- Be comprehensive - cover all major themes  
- Be specific - include concrete details
- Be useful - write for future-you who needs to find and understand this content
- Handle PDF-specific challenges (fragmented text, tables, formatting issues)

## Validation

Before returning, verify:
- ✓ Valid JSON format
- ✓ All required fields present
- ✓ Tags are an array of lowercase strings
- ✓ Claims are specific, not generic
- ✓ Key points are concrete and actionable
