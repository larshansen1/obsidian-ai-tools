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

Generate a structured note that helps future-me decide whether this repository
is relevant, trustworthy, and worth using or extending.

Focus on synthesis, not inventory. Do not enumerate every documented feature.
Group related facts into judgments about architecture, security posture, and
maturity. Focus only on what is supported by the selected documentation, and
surface uncertainty when the documentation is thin.

Cover these areas where the documentation provides evidence:

1. **Purpose**: What the project does and who or what it is for
2. **Architecture Read**: System shape, major components, integration points,
   data/control boundaries, and external dependencies
3. **Design Principles and Tradeoffs**: Design philosophy, constraints,
   assumptions, explicit tradeoffs, or project values
4. **Technology and Runtime**: Languages, frameworks, dependencies,
   integrations, runtime needs, and deployment substrate
5. **Usage Surface**: CLI commands, APIs, app behavior, workflows, or
   user-facing tools
6. **Security Posture**: Auth/authz model, trust boundaries, secrets handling,
   network exposure, fail-open/fail-closed behavior, and documented gaps
7. **Operational Maturity**: Setup/deployment story, tests, CI, observability,
   release/versioning, license, maintenance signals, and maturity classification
8. **Caveats and Unknowns**: Limitations, missing evidence, maintenance risks,
   security unknowns, or contribution constraints

## Output Format

Return ONLY a valid JSON object with this exact structure:

```json
{{
  "title": "Repository name or clear project title",
  "summary": "2-3 sentences explaining the repository purpose and context",
  "claims": [
    "High-signal documented evidence supporting the architecture/security/maturity read",
    "Only include claims supported by the provided documentation"
  ],
  "key_points": [
    "Purpose: ...",
    "Architecture Read: ...",
    "Design Principles and Tradeoffs: ...",
    "Technology and Runtime: ...",
    "Usage Surface: ...",
    "Security Posture: ...",
    "Operational Maturity: ...",
    "Caveats and Unknowns: ..."
  ],
  "implications": [
    "Adoption fit, including who should use it and who should be cautious",
    "What future-me should verify before relying on or extending it"
  ],
  "tags": ["tag1", "tag2", "tag3"]
}}
```

## Section Guidance

- Write 1 item for `Purpose`.
- Write 1-3 items for each other `key_points` section.
- Each item should be a dense synthesis, not a copied feature list.
- `claims` should contain 3-7 evidence highlights total. Do not include every
  documented fact.
- `implications` should contain 2-4 adoption-fit bullets total.
- Include a maturity classification in `Operational Maturity`, using one of:
  `prototype`, `early/private`, `usable personal tool`,
  `production-oriented`, or `mature/reusable`. State the evidence behind it.
- If security evidence is absent or shallow, say that explicitly in
  `Security Posture` or `Caveats and Unknowns`; do not invent a security model.

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
- Prefer fewer, denser bullets over many discrete points
- Judge maturity from evidence, not polish or ambition
- Security posture should separate documented protections from unknowns

## Validation

Before returning, verify:
- Valid JSON format
- All required fields present
- Tags are an array of lowercase strings
- Key points use the section prefixes exactly as shown when evidence exists:
  `Purpose:`, `Architecture Read:`, `Design Principles and Tradeoffs:`,
  `Technology and Runtime:`, `Usage Surface:`, `Security Posture:`,
  `Operational Maturity:`, `Caveats and Unknowns:`
- No unsupported claims about the source code
