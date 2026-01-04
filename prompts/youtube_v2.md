# YouTube Video Analysis Prompt (v2)

You are analyzing a YouTube video transcript to create a structured note for an Obsidian knowledge base.

## Input

You will receive:
- Video metadata (title, URL, author)
- Full transcript text (may be in any language)
- Existing tags from the vault (to encourage reuse)

**Video Information:**
- Title: {title}
- URL: {url}

**Transcript:**
{transcript}

## Task

Generate a structured note with the following sections:

1. **Title**: A clear, descriptive title for the note
2. **Summary**: 2-3 sentences describing what this video is about
3. **Key Claims**: Specific arguments, predictions, or positions stated in the video
4. **Key Points**: Actionable insights, technical details, or important takeaways
5. **Implications**: Why this matters, what changes, who should care
6. **Tags**: Relevant topic tags for categorization

## Output Format

Return ONLY a valid JSON object with this exact structure:

```json
{{
  "title": "Clear, descriptive title",
  "summary": "2-3 sentence summary of the video content",
  "claims": [
    "Specific argument, prediction, or position from the video",
    "Include numbers, timelines, or comparisons when mentioned",
    "Attribute to speaker if relevant (e.g., 'Linus argues that...')"
  ],
  "key_points": [
    "Actionable insight or technical detail",
    "Concrete example mentioned in the video",
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
- Mix broad topics (`ai`, `software`) with specific concepts (`prompt-engineering`, `devops`)
- Avoid overly generic tags (`technology`, `video`, `interesting`)
- Ensure tags would help you find this note later

## Content Guidelines

### Summary
- 2-3 sentences, approximately 50-75 words
- Explain what the video is about, not what you think of it
- Include the main topic and key context

### Claims
- Extract specific arguments, predictions, or positions stated in the video
- Include concrete details: numbers, timelines, comparisons
- Attribute to speaker when relevant ("Linus argues...", "The presenter claims...")
- Only include claims actually made in the video (no hallucinations)
- Aim for 3-5 claims if available

### Key Points
- Actionable insights or memorable takeaways
- Technical details that matter
- Concrete examples or case studies mentioned
- Things you'd want to remember 6 months from now
- Aim for 3-7 points

### Implications
- Why this matters (broader significance)
- What changes as a result (practical applications)
- Who should care (target audience)
- Forward-looking consequences
- Aim for 2-4 implications

## Language Handling

**Important**: The transcript may be in any language. Always generate your response in **English**, regardless of the transcript language. If the transcript is in a language other than English, translate key concepts and summarize in English.

## Quality Requirements

- Be accurate - don't contradict the source material
- Be comprehensive - cover all major themes  
- Be specific - include concrete details, not just generalities
- Be useful - write for future-you who needs to find and understand this content

## Example Output

```json
{{
  "title": "Docker Container Fundamentals and Best Practices",
  "summary": "A 15-minute technical tutorial covering Docker container basics, including image layers, container lifecycle, and common commands. Focuses on practical examples for developers new to containerization.",
  "claims": [
    "Docker images are immutable - once built, they never change",
    "Containers share the host OS kernel, making them lighter than VMs",
    "Multi-stage builds can reduce image size by 70-90% in production"
  ],
  "key_points": [
    "Use `docker run -d` to run containers in background (detached mode)",
    "Layer caching speeds up builds - put frequently changing files last in Dockerfile",
    "Always use specific image tags in production, never `latest`",
    "The .dockerignore file prevents bloating images with unnecessary files"
  ],
  "implications": [
    "Understanding image layers is critical for optimizing build times and image sizes",
    "Container orchestration (Kubernetes, Docker Swarm) builds on these fundamentals",
    "Developers using Docker can ship consistent environments from dev to production"
  ],
  "tags": ["docker", "containers", "devops", "infrastructure", "best-practices"]
}}
```

## Validation

Before returning, verify:
- ✓ Valid JSON format
- ✓ All required fields present
- ✓ Tags are an array of lowercase strings
- ✓ Claims are specific, not generic
- ✓ Key points are concrete and actionable
- ✓ Output is in English
