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
| `CLAUDE.md` | Project-level instructions baked into every Claude Code session |
| `AGENTS.md` | Conventions for AI agents operating in this repo |

## Key Lessons

### 1. Guardrails prevent slop

AI-assisted development accelerates output dramatically — which means technical debt and low-quality code also accumulate faster without checks. Linting, type checking, complexity limits, security scans, and coverage thresholds all exist here because the first versions of several modules were functionally correct but structurally poor. Adding these gates as pre-commit hooks from the start, not retrofitting them later, was the single most important quality decision.

### 2. Fix root causes, not symptoms

When a check kept failing — a test, a hook, a CI step — the temptation was to patch it locally and move on. This consistently backfired. Updating `AGENTS.md` to record _why_ a check exists, or fixing the underlying convention rather than suppressing the warning, prevented the same failure from re-appearing three PRs later. If something keeps breaking, the answer is in the history and the process, not in the immediate error.

### 3. Avoid becoming a feature factory

AI coding assistants will happily implement whatever you ask, which makes it easy to accumulate features that nobody uses. Product consistency — every command following the same flag conventions, the same output format, the same error behaviour — is harder to maintain than adding new commands. Think hard about whether a new feature actually changes what you do with the tool. Prune commands that duplicate each other or that you have never run on real data.

### 4. Observability is not optional — it is feedback for the AI

Without `kai usage`, there was no way to know whether the ingestion pipeline was actually working in practice: which providers were failing, which prompts produced low-quality output, what the real cost per note was. Observability here is not just operational hygiene — it is the feedback loop that tells you whether the AI-generated features are delivering value or just passing tests. Concretely:

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
make crap        # CRAP gate: radon complexity x coverage
```

### CRAP gate

`make crap` runs the CRAP (Change Risk Analysis and Prediction, Savoia/crap4j) gate: it merges the existing radon per-function complexity (`cc.json`) with line coverage (`coverage.json`, exported from the `.coverage` file the test run writes) into one per-function risk score

```
CRAP = complexity**3 * (1 - coverage)**2 + complexity
```

with `coverage` as a 0..1 fraction. The default threshold is 30 (crap4j convention; override with `CRAP_THRESHOLD` or `--threshold`). The output is a ranked hotspot list, worst first. Read it as change risk: a high-CC function at high coverage can be safer than a mid-CC function at low coverage — cover the uncovered lines of the top rows before simplifying.

CRAP complements, does not replace, the radon CC gates: at 100% coverage CRAP == complexity, so CRAP alone would tolerate complexity up to the threshold. The CC 10/5 gates stay the absolute complexity ceiling; CRAP adds the coverage dimension. Files omitted from coverage are treated as fully covered (missing evidence must not manufacture risk).

The gate landed on a codebase whose five worst hotspots already exceeded threshold 30 (worst: `process_inbox` at CRAP ~756). Rather than raise the threshold — a single high threshold that cleared 756 would tolerate CRAP up to 756 everywhere and gut the gate — those five are grandfathered through the `BASELINE` dict in `scripts/crap_report.py`: each keeps its recorded CRAP value as a cap, so it passes at or below that value but fails the moment it worsens. Grandfathering is explicitly not a threshold change. All five were fixed in #112 and `BASELINE` is now empty: fix a new hotspot rather than adding it there.

Pre-commit hooks run the quick checks (lint, format, bandit, gitleaks, README check, import-linter, radon) on every commit. Tests run on push only: the full suite in parallel with coverage, plus mypy and the CRAP gate. CI runs each check once per pull request. See `.pre-commit-config.yaml` and `.github/workflows/ci.yml` for the exact configuration.

## Vault Compass

Vault Compass is a second app in this repo (ADR 0005): `src/vault_compass/` is its FastAPI
backend, `web/` its Next.js + assistant-ui frontend. It has its own command, `compass serve`,
which binds to `127.0.0.1:8100` and refuses other hosts unless `--i-know-what-im-doing` is
passed (same rule as `kai serve`, ADR 0002).

```bash
uv run compass serve            # http://127.0.0.1:8100/status -> {"running": true}
uv run compass serve --reload   # development
```

Settings: the vault path is kai's `OBSIDIAN_VAULT_PATH` (same `.env` lookup). Compass writes only
to its own DuckDB file, `COMPASS_DB_PATH`, default `{vault}/.kai/compass.duckdb` (ADR 0006).

**AI and chat (#134).** The chat panel (`web/components/ChatDock.tsx`) talks to `POST /chat`, which
streams the AI SDK UI message stream (see `docs/vault-compass/spike-notes.md`). Settings, all
optional except the key, read from the same `.env`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | none | Without it the chat says how to set it |
| `LLM_MODEL` | `anthropic/claude-sonnet-4` | Any OpenRouter model id; no code change needed |
| `COMPASS_AI_ACTION_LIMIT_USD` | `0.25` | Ask before one chat action may cost more than this |
| `COMPASS_AI_MONTHLY_LIMIT_USD` | `10` | Ask before this month's AI spend would pass this |
| `COMPASS_AI_INPUT_USD_PER_MTOK` / `..._OUTPUT_...` | `3` / `15` | Prices used only to estimate a call before it runs |

- Every model call is logged as an `ai_call` row in `usage_events` with the cost OpenRouter reports.
- Notes in `ai_exclude_folders` (`.kai/topics.yaml`, default `notes/reflections`) never reach a model.
  `AiVault` in `vault_tools.py` is the only way note text gets to a model; new AI features must read
  notes through it.
- The assistant's tools (`search_notes`, `read_note`, `topic_stats`) also run from a script:
  `uv run python -m vault_compass.vault_tools search_notes '{"query": "agents"}'`.
- Claims (#135, T3/T4): `POST /topics/{id}/claims/run` reads a topic's unread notes (at most 30 per
  run; notes with a `## Key Claims` section cost no model call), then proposes questions, finds
  claims shared by 2+ notes and sorts claims by the chosen question. Everything is cached in
  `compass.duckdb` (`note_claims`, `claim_reads`, `topic_questions`, `claim_analysis`) and a step
  whose cache is current makes no model call. Each call is checked against the cost limits first.
  `GET /topics/{id}/claims` and the `topic_claims` chat tool only read the cache.
  Claims calls switch model thinking off (`COMPASS_AI_REASONING` stays on for chat) and use
  `COMPASS_AI_CLAIMS_MAX_OUTPUT_TOKENS` (default 4000), because a thinking model can spend its whole
  output budget thinking and return an empty answer. The view's `next_step` (read, analyse, sort,
  done) drives the four-step screen.
- Suggested questions (#166) are anchored in specific notes, not topic-wide. The questions step
  sends every claim of the topic grouped by note (thinned evenly only past
  `COMPASS_AI_QUESTIONS_MAX_PROMPT_CHARS`, default 600,000 characters, #177) and is the one claims call that thinks first, with `COMPASS_AI_QUESTIONS_MAX_OUTPUT_TOKENS` (default
  16000): without thinking, the questions came back as textbook debates. Each question has a kind
  (`tension`: notes disagree, a yes/no question the sort step can use; `recurring`: an idea many
  notes repeat; `carry_over`: does a principle hold in another kind of system, at most one), a
  one-line `why` and its anchor claims from at least two notes. Only `tension` questions get a
  "Use this question" button. Proposals saved in the old string format are suggested again.
  The cost check prices that call at the full 16000-token budget, so with the default
  (Sonnet-level) prices it asks for approval on most topics. Set `COMPASS_AI_INPUT_USD_PER_MTOK`
  and `COMPASS_AI_OUTPUT_USD_PER_MTOK` to your model's prices to get estimates that match.
- Sort and summary (#169): the sort first names the question's subject and counts a claim only if
  it is about that subject (background on the wider field is unrelated); sorts from before this
  (`stances_v2`) are redone. After the sort, one more call summarises the split (`split_summary_v1`
  in `claim_analysis`, keyed by question): bottom line, where the notes agree, where they split
  and why, what is missing, and 3 to 5 notes to read. A question no claim bears on gets a fixed
  summary with no model call. "Use this question" saves the question and runs sort and summary in
  one click; the claim lists sit folded under the summary.
- Agreement (#173): claims are matched to one evergreen per call (150 claims per chunk), because
  matching a chunk against all evergreens at once made the model misread claim numbers. Each call
  keeps at most 6 claims that make the evergreen's specific point. A second call per evergreen
  sees all its candidates together and keeps at most 10, closest first (`CONFIRM_SYSTEM`). On a
  2,406-claim topic with 5 evergreens that is about 90 calls, 4 to 5 minutes, and a few cents,
  run only when the claims change; matches went from 324 claims to 43. With the default prices
  the cost check asks for approval on topics with many evergreens. Older matches (`agreement`)
  are redone (`agreement_v2`). The list shows the 3 closest notes per shared point; the rest and
  the link button sit under "Show all".
- Before calling a claims change done, run it on real data: `uv run python scripts/replay_claims.py
  <topic>` copies your `compass.duckdb`, runs the whole flow with the real model and runs it again to
  check nothing repeats (`--dry` shows the cached state for free). Mocked tests cannot show how a
  real model answers: it has thought itself out of tokens and repeated one letter 2,700 times.
- Chat history is one thread per topic (plus one shared thread) in the `chat_history` table.

**Reading list from daily briefings (#197).** An agent on a server writes a briefing every morning.
`compass briefings pull` copies the new files to this machine over SSH, reads the links out of them
with plain text rules (no model call), drops what the vault has, checks the rest exist online (C7)
and shows them at `/briefings`: Preview (new tab), Ingest (the W3 send-to-kai job) and Dismiss.
One-way flow: this machine pulls, the server gets no access and nothing from the vault is sent to it.

| Setting | Default | Meaning |
| --- | --- | --- |
| `COMPASS_BRIEFINGS_REMOTE` | empty = off | rsync source, e.g. `user@host:/path/to/briefings/`. No host, address or key is ever in the repo |
| `COMPASS_BRIEFINGS_DIR` | `~/.compass/briefings` | Where pulled files live. Outside the vault on purpose, so Obsidian sync never carries them |

- Code: `briefing_parse.py` (plain text rules), `briefings.py` (pull, table, online check, list).
  State is in `compass.duckdb`: `briefing_links` (one row per page; status `new`, `ingested`,
  `dismissed`) and `briefing_runs`. Counts come from the files, so reading them again changes
  nothing, and a dismissed link stays dismissed after the next pull.
- Commands: `compass briefings pull` (rsync, then read and check) and `compass briefings parse`
  (read and check the files already in the folder; another source can drop `.md` files there).
  With the remote empty, `pull` says the feature is off and does nothing.
- When it runs: `compass serve` pulls in the background at start when the last pull is older than
  a day (and a remote is set). Run `compass briefings pull` yourself any time.
- Format: a briefing holds the job's prompt, then `## Response`, then the answer. Only the answer
  is read, so a file without that heading has no links. The date is in the file name
  (`2026-11-10-...md`, else the file's modified date). A link's title is the bold headline that opens
  its item (`- **Headline** (source) - text [InfoQ](url)`), also for link lines under it, because
  the link text is only the source name. Without a headline the link text is used. A short bold
  line on its own, a bold text ending in a colon, or a bold line above a list is a group label,
  not a headline. Google News redirect links are dropped.
- Real data (2026-11-11, 138 files, 2,004 links): the first version used the link text and put
  84% of links in "No topic"; with headlines it is 39% (785 links).
- Topics: a link joins the topic with the most tags found as whole words in its title; no match
  puts it in "No topic". Rough on purpose. Measure how many land in "No topic" on real data before
  asking for a model.
- Added links: `GET /briefings` also returns `added`, the list's links that kai finished
  ingesting (newest first, up to 100, from `ingest_jobs`), so the "Added to the vault" box with its
  "Open note" links survives a page refresh. The page merges this visit's links with these.
- Online check: a link is shown only after `url_exists` passes. Up to 500 links are checked per
  run, newest first; the rest wait for the next run. A failed check is repeated after 7 days.
- **What is untrusted and what leaves the machine.** Briefing text is untrusted: the agent reads
  the open web, so it can carry planted instructions. Only URL, title, date and section leave the
  parser; they are cleaned (control characters, emphasis marks, length) and shown as plain text,
  never as Markdown or HTML. Nothing here calls a model, and the chat does not see the list (if a
  title is ever sent to a model, that call has no tools). Loopback, private-network, local-name
  and credentialed URLs are refused when the briefing is read, and a name that resolves to a
  private address is refused again before the online check. Leaving the machine: ssh and rsync
  to your own server (read only; symlinks and files over 1 MB are not copied, ssh never prompts),
  and a HEAD or GET to each link's site, which reveals your IP to that site. No vault content.
- Server login: use a key that can only read the briefing folder, not an admin login. In the
  server's `~/.ssh/authorized_keys`:
  `restrict,command="rrsync -ro /path/to/briefings" ssh-ed25519 AAAA... compass-pull`
  and set `COMPASS_BRIEFINGS_REMOTE=user@host:/`. `rrsync` ships with rsync.
- Before calling a briefings change done, run `compass briefings pull` on a copy of your
  `compass.duckdb` with the real briefings: the format and the "No topic" share only show up there.

**Import rule.** `vault_compass` may import `obsidian_ai_tools`; `obsidian_ai_tools` must never
import `vault_compass`. `import-linter` enforces this from the `[tool.importlinter]` contract in
`pyproject.toml`, as a pre-commit hook and a CI step. Run it by hand with `uv run lint-imports`.

The Python side uses the same gates as kai (ruff, mypy, bandit, radon, CRAP, pytest, 80% coverage
floor; coverage counts both packages). `web/` has its own Node setup and checks, run by `.github/workflows/web.yml` only when `web/` changes:

```bash
cd web && npm install
npm run typecheck && npm run lint && npm test
npm run dev   # http://127.0.0.1:3117, proxies /api/* to compass serve on :8100
```

## Parallel Agent Worktrees

When dispatching multiple concurrent agents (one per issue), each worker gets its own branch and its own git worktree. Workers never share a checkout and never switch branches — the #50/#52 interference incident (mid-flight branch switch pulling the tree out from under a worker, one worker restoring a file another was editing, pre-commit's files-modified detector tripping on concurrent edits) only happened because multiple workers shared a single checkout.

The orchestrator creates the worktree and spawns each worker with its `cwd` set to it:

```bash
git worktree add .worktrees/<short-name> -b <branch> <base>
```

Each worktree needs its own environment before gates can run. Verified bootstrap (all commands run from inside the worktree):

```bash
uv venv
uv pip install -e . -r requirements-dev.txt
uv run pytest -q -m "not slow"
uv run pre-commit run ruff --all-files
```

Verification notes (the parts most likely to bite):

- The editable install records an **absolute path**: each worktree's `.venv` imports that worktree's own `src/`, never the main checkout's, so worktrees don't cross-contaminate.
- `uv run` picks up the worktree-local `.venv` automatically; no activation needed.
- The `language: system` hooks (pytest-coverage, crap-gate, radon-cc, README check) resolve `.venv/bin/python` and `./scripts/...` relative to the worktree CWD — the venv must live at `.worktrees/<name>/.venv`.
- Remote-repo hook environments (ruff, bandit, gitleaks, …) are cached under `~/.cache/pre-commit` and shared across worktrees, so the first `pre-commit run` inside a fresh worktree doesn't re-fetch.
- Worktrees share the main checkout's `.git/hooks`, so one `pre-commit install` in the main checkout covers every worktree.

After the PR merges, the orchestrator cleans up:

```bash
git worktree remove .worktrees/<short-name>
git branch -d <branch>
git worktree prune   # drop stale administrative entries
```

`.worktrees/` is git-ignored.

## Mutation Testing

Mutation testing measures whether the test suite actually *asserts* behavior,
not just executes lines. [mutmut](https://mutmut.readthedocs.io/) injects small
defects ("mutants") into the source and verifies that at least one test fails
for each one. A mutant that survives means no test notices the defect.

### Running it

```bash
uv pip install -r requirements-dev.txt   # includes mutmut

# Full run (mutates everything in src/, ~30 min on 8 cores)
uv run mutmut run --max-children 7

# Results summary
uv run mutmut results

# Inspect a specific surviving mutant (tab-complete names via `mutmut browse`)
uv run mutmut show "<mutant-name>"
```

The run copies the source tree into `mutants/` (git-ignored) and never touches
your working copy. Results are cached; subsequent runs only re-check mutants
affected by your changes.

### Score legend

| Symbol | Meaning                                                            |
|--------|--------------------------------------------------------------------|
| 🎉     | killed — a test failed, as it should                               |
| 🙁     | survived — no test detected the defect **(improve these)**         |
| 🫥     | no tests — no covering test was found (worse than survived)        |
| ⏰     | timeout — counted as killed (mutant caused a hang)                 |
| 🤔     | suspicious — test timings changed, worth a manual look             |

### Workflow when a mutant survives

1. `uv run mutmut show <name>` to see the injected defect.
2. If the defect is observable: add or strengthen a test assertion that fails
   under the mutant.
3. If the mutant is *equivalent* (the mutated code is observably identical,
   e.g. a `Literal` string comparison whose fall-through branch behaves the
   same, or a redundant keyword argument), mark the line with
   `# pragma: no mutate` and a short justification comment.

Do not chase a 100% score: real codebases contain equivalent mutants. The goal
is that every surviving mutant is either killed or explicitly justified.

### Writing mutation-resistant tests

The mutation-testing pass (PR #51) found that most surviving mutants traced to
a handful of systematic test-writing patterns, not to missing coverage: the
lines ran, but no assertion noticed the defect. Write every test so any
behavioural change is observable as a test failure.

- **Assert exact outcomes with `==`.** Compare full dicts, objects, error
  messages, and output streams. Never `assert "key" in dict` — the key can be
  present with the wrong value — and never substring-check error text (a mutant
  that returns `"success"` instead of `"unsuccessful"` survives an `in`
  check).
- **Assert every mock with `assert_called_once_with(...)` covering ALL
  arguments**: URLs, timeouts, headers, payloads, encodings, flags. A mock
  checked only with `assert_called()` lets a mutant that silently drops an
  argument survive.
- **Test every comparison at its exact boundary value, from both sides.** For
  `<` vs `<=` and threshold constants, assert both the value just below and the
  value at/just above the threshold so a boundary mutation cannot survive.
- **Exercise every default at least once by omitting the kwarg.** CLI
  frameworks (Typer) re-read the original function signature, so default
  mutations are invisible through CLI-level tests — call the function directly.
- **Assert log records exactly** (message, logger name, `exc_info`) whenever
  the behaviour is a log line, and assert persisted file content byte-for-byte
  (indent, encoding) — not just that the file exists.
- **Make time deterministic.** monkeypatch `time.monotonic` / `datetime`
  instead of sleeping or asserting within tolerances.
- **Use `SimpleNamespace`, not `MagicMock`, for fakes whose attribute
  *existence* is checked.** `MagicMock` auto-creates attributes, defeating
  `hasattr` guards. `SimpleNamespace` raises `AttributeError` for missing
  attributes, so the guard is exercised.
- **Verify kills with a batch harness**: copy `src` to a temp dir, apply each
  mutant diff, run the tests, and collect results across all mutants at
  once. Never hand-verify mutants one at a time.
- **`# pragma: no mutate` only for provably equivalent mutants, with a written
  justification** — prefer killing via tests. The pragma applies to the whole
  line, so never pragma a line that also hosts killable mutants.
