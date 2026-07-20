# GitHub Repository Documentation Analysis Prompt (v1)

You are analyzing documentation from a GitHub repository to create a structured
note for an Obsidian knowledge base.

## Input

You will receive:
- Repository metadata
- A bounded set of documentation files selected from the repository
- Existing tags from the vault (to encourage reuse)

**Repository Information:**
- Title: {title}
- URL: {url}
- Owner: {author}
- Source: {site_name}

**Selected Documentation Content:**
{content}

## Task

Generate a structured note that explains the repository at a high level.
Focus only on what is supported by the selected documentation.

Cover these areas where the documentation provides evidence:

1. **Purpose**: What the project does and who or what it is for
2. **Principles**: Design philosophy, constraints, assumptions, or project values
3. **Technology**: Languages, frameworks, dependencies, integrations, runtime needs
4. **Usage Surface**: CLI commands, APIs, app behavior, workflows, or user-facing tools
5. **Setup and Operations**: Installation, configuration, authentication, deployment, local dev
6. **Caveats**: Maintenance status, limitations, security notes, contribution guidance

## Output Format

Return ONLY a valid JSON object with this exact structure:

```json
{{
  "title": "Repository name or clear project title",
  "summary": "2-3 sentences explaining the repository purpose and context",
  "claims": [
    "Specific documented purpose, principle, or project claim",
    "Only include claims supported by the provided documentation"
  ],
  "key_points": [
    "Purpose: ...",
    "Principles: ...",
    "Technology: ...",
    "Usage Surface: ...",
    "Setup and Operations: ...",
    "Caveats: ..."
  ],
  "implications": [
    "Why this repository matters or how it should be used",
    "Operational or adoption implications from the docs"
  ],
  "tags": ["tag1", "tag2", "tag3"]
}}
```

## Tag Guidelines

### Existing Tags (Prefer Reuse)

{EXISTING_TAGS}

**Important**: Prefer reusing existing tags when relevant. Only create new tags
when the repository topic is not adequately covered by existing tags.

### Tag Format Rules

1. **All lowercase** - Use `ai` not `AI`
2. **Hyphens for compound words** - Use `github-repo` not `githubrepo`
3. **Singular form** - Use `tool` not `tools` unless plural is standard
4. **Short forms preferred**:
   - `ai` not `artificial-intelligence`
   - `llm` not `large-language-model`
   - `cli` not `command-line-interface`

## Quality Requirements

- Do not infer implementation details from file names alone
- Do not claim the repository uses technology unless the docs or package metadata support it
- Preserve uncertainty when documentation is incomplete
- Write for future-you who needs to quickly understand whether this repo is relevant

## Validation

Before returning, verify:
- Valid JSON format
- All required fields present
- Tags are an array of lowercase strings
- Key points use the section prefixes exactly as shown when evidence exists:
  `Purpose:`, `Principles:`, `Technology:`, `Usage Surface:`,
  `Setup and Operations:`, `Caveats:`
- No unsupported claims about the source code
