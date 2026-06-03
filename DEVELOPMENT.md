# Development Process

`obsidian-ai-tools` was built with significant AI assistance using [Claude Code](https://claude.ai/code). This document explains the workflow, conventions, and lessons learned — both to set honest expectations about the codebase and to serve as a reference for contributors or anyone wanting to replicate the approach.

## How It Was Built

### Workflow overview

Each feature followed a roughly consistent loop:

1. **Define the problem** in a short natural-language prompt: what the command should do, what the inputs and outputs are, and any known edge cases.
2. **Collaborative design**: Claude Code proposed a module structure and API surface. Revisions happened in conversation before any code was written.
3. **Implementation**: Claude Code wrote the implementation. The human role was to review, redirect, and catch cases where the AI optimised for correctness-on-paper over usability.
4. **Tests**: Test cases were generated alongside the implementation, with the assistant identifying the important contract surfaces. Most test stubs were written by the AI; edge-case assertions were often added manually after running the suite.
5. **Quality gates**: Every commit passes ruff, mypy, bandit, gitleaks, radon complexity checks, and the test suite. These were enforced as pre-commit hooks from the start.

### Prompting strategy

- **Start narrow.** Prompts worked best when they described one command or one module at a time. Asking for "the whole ingestion pipeline" in one shot produced overly coupled code.
- **Describe outputs, not steps.** "Write a function that returns a list of notes sorted by backlink count" outperforms "first load the index, then sort, then..." as input.
- **Name the constraints upfront.** Mentioning Pydantic models, the existing vault path convention, or the Typer CLI surface at the start of a prompt saved several revision cycles.
- **Use the `--verbose` flag convention.** All commands accept `--verbose` for debug output; specifying this in prompts kept logging consistent.

### Architecture decisions made collaboratively

- **Typer for the CLI** — chosen over argparse/click for its first-class Pydantic integration and auto-generated `--help`.
- **Versioned prompt templates** (`prompts/`) — separating prompt content from code meant the AI could improve note quality without touching Python.
- **DuckDB for observability** — lightweight, no server, queryable from Python with zero setup. Suggested by the AI as an alternative to CSV logs.
- **Whoosh for search** — pure-Python, embeddable, no external process. Sufficient for personal-vault scale.

### What human review caught

- **Over-abstracting**: The AI frequently introduced helper functions and base classes that were not yet justified. Several abstractions were pruned back.
- **Silent failures**: Early versions of network calls sometimes caught broad exceptions and returned `None`. Explicit error propagation was enforced manually.
- **Confusing flag names**: `--confirm` vs `--yes` vs `--no-dry-run` conventions needed one pass of consistency review.
- **Test coverage gaps**: The AI reliably covered the happy path. Malformed vault paths, missing API keys, and provider-fallback chains required manually written tests.

## Tooling

| Tool | Role |
|------|------|
| [Claude Code](https://claude.ai/code) | Primary coding assistant (implementation, tests, refactoring) |
| [GitNexus](https://gitnexus.dev) | Code-intelligence MCP: impact analysis, call-graph navigation, safe renaming |
| `CLAUDE.md` | Project-level instructions baked into every Claude Code session |
| `AGENTS.md` | Conventions for AI agents operating in this repo |

### GitNexus integration

GitNexus indexes the codebase as a knowledge graph. Before modifying any symbol, running `gitnexus_impact` identifies the blast radius (direct callers, affected execution flows, risk level). This prevented several cases where renaming a utility function would have silently broken unrelated commands. See `CLAUDE.md` for the full protocol.

## Key Lessons

### 1. Guardrails prevent slop

AI-assisted development accelerates output dramatically — which means technical debt and low-quality code also accumulate faster without checks. Linting, type checking, complexity limits, security scans, and coverage thresholds all exist here because the first versions of several modules were functionally correct but structurally poor. Adding these gates as pre-commit hooks from the start, not retrofitting them later, was the single most important quality decision.

### 2. Fix root causes, not symptoms

When a check kept failing — a test, a hook, a CI step — the temptation was to patch it locally and move on. This consistently backfired. Updating `AGENTS.md` to record _why_ a check exists, or fixing the underlying convention rather than suppressing the warning, prevented the same failure from re-appearing three PRs later. If something keeps breaking, the answer is in the history and the process, not in the immediate error.

### 3. Avoid becoming a feature factory

AI coding assistants will happily implement whatever you ask, which makes it easy to accumulate features that nobody uses. Product consistency — every command following the same flag conventions, the same output format, the same error behaviour — is harder to maintain than adding new commands. Think hard about whether a new feature actually changes what you do with the tool. Prune commands that duplicate each other or that you have never run on real data.

### 4. Observability is not optional — it is feedback for the AI

Without `kai stats` and `kai quality`, there was no way to know whether the ingestion pipeline was actually working in practice: which providers were failing, which prompts produced low-quality output, what the real cost per note was. Observability here is not just operational hygiene — it is the feedback loop that tells you whether the AI-generated features are delivering value or just passing tests. Concretely:

- **Does it work?** Are ingestion success rates high? Are errors clustered around one provider or one content type?
- **Am I using it?** A command no one runs is a candidate for removal.
- **Does it drift?** Do cost-per-note or latency metrics change after a prompt update or model change?
- **Does it add value?** Are the generated notes actually useful, or are they verbose summaries of content you would have read anyway?

Build observability before you build the next feature.

**Keep development-time and runtime data strictly separated.** When the test suite invokes instrumented code — CLI commands, providers, the HTTP endpoint — it will write to the observability database unless explicitly isolated. This happened here: ~500 test invocations landed in the production DuckDB, making `kai usage` show 81 flashcard "calls" that never happened. The fix was a single autouse fixture in `conftest.py` that redirects `get_db()` to a per-test throwaway file. The lesson: any observability write path that activates during tests will corrupt production metrics unless isolation is built in from day one, not retrofitted after noticing the noise.

### 5. Documentation rots fast — prune it deliberately

AI assistants produce documentation readily and at volume. This is a trap: it is easy to end up with a README, an ARCHITECTURE doc, a DEVELOPMENT doc, a CHANGELOG, a QUICK_START, and several inline docstrings that all describe the same thing at slightly different points in time. The AI will not notice the drift. It will confidently generate a new section that contradicts an older one, or leave a "Week 1 MVP" heading in a document that describes a shipped tool.

Two rules that helped here:

- **Delete documentation that has become false rather than update it in place.** A stale document is worse than no document — it actively misleads. If a section no longer reflects reality, remove it unless there is a clear owner and timeline for fixing it.
- **Cross-reference, don't duplicate.** When `ARCHITECTURE.md` and `DEVELOPMENT.md` both try to explain the module structure, one of them will fall behind. Decide which document owns each fact and have the other point to it.

The architecture review document (`docs/architecture-review-20260601.html`) is an example of getting this right eventually: five improvement candidates identified, work tracked in issues, document removed once it had served its purpose rather than left to age.

### Smaller, evergreen lessons

- **Prompt templates as first-class artifacts.** Treating `prompts/` as versioned source (not generated output) made it possible to iterate on note quality independently of the Python code.
- **AI-generated tests need human adversarial review.** The test suite reached high coverage quickly, but coverage alone does not test the right things. Reviewing test assertions manually before merging a feature was worth the extra 15 minutes.
- **Conversational context degrades.** Long sessions with many files loaded produced less consistent code than shorter, focused sessions. Breaking work into discrete issues (one feature per branch) improved coherence.
- **The AI is good at boilerplate, not at taste.** Pydantic models, CLI scaffolding, and CI config came out clean on the first pass. Naming, error messages, and UX decisions needed more iteration.

## Running the Development Suite

```bash
# Run all quality gates in one shot
make quality

# Individual gates
make lint        # ruff
make typecheck   # mypy
make radon       # complexity
make bandit      # security
make coverage    # pytest + coverage threshold
```

Pre-commit hooks run a subset on every commit; the full suite runs on push. See `.pre-commit-config.yaml` and `.github/workflows/ci.yml` for the exact configuration.
