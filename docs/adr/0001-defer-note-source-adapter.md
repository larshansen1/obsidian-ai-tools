# 0001: Defer NoteSource adapter interface

Status: accepted (2026-08-17)

## Context

Issue [#14](https://github.com/larshansen1/obsidian-ai-tools/issues/14) proposed a `NoteSource` plugin/adapter interface so note storages other than Obsidian (Notion export, plain markdown folders, Logseq) could be supported without forking core logic. The issue was blocked on the usage review in [#29](https://github.com/larshansen1/obsidian-ai-tools/issues/29), which closed 2026-07-01 without pruning providers.

Evaluation against the codebase as of commit `d30cd15`:

- **Usage is a single pipeline against a single vault.** The #29 telemetry (28 days) shows the real product is `serve:ingest` → inbox → `process-inbox`. There is zero demand for a second note source — no user request, no contributor proposal.
- **Obsidian coupling is thin and localized.** Only three behaviors are genuinely Obsidian-specific: the `obsidian://open` URL built in `obsidian.py`, the `[[wikilink]]` regex in `wikilinks.py` (feeds search backlink boosting), and `.obsidian` directory skip-lists. Everything else — frontmatter schema, `inbox` folder, `.kai` metadata directory, `folder_rules.json` — is this tool's own markdown-on-disk convention, which any plain folder satisfies.
- **A "plain markdown folder" source already works today.** Nothing validates that `OBSIDIAN_VAULT_PATH` points at a real Obsidian vault; pointing it at any markdown folder works, with the Obsidian-specific extras degrading gracefully (the `obsidian://` link is simply less useful).
- **The testability motivation is already satisfied.** Every vault-touching test builds a throwaway directory under pytest's `tmp_path`; no test requires Obsidian.
- **The refactor would be large and speculative.** Vault I/O today is 19 direct `pathlib` call sites spread across 7 modules with no shared filesystem layer. Migrating all of them behind an interface designed against exactly one implementation risks producing the wrong abstraction (rule of three: we have one).

## Decision

Do not introduce a `NoteSource` plugin/adapter interface now.

## Consequences

- Vault I/O stays as direct `pathlib` calls against a `vault_path` root threaded in from settings, CLI flag, or HTTP request body.
- The known vault-side debts remain and are tracked separately: two independent frontmatter parsers (`indexer.py` via python-frontmatter vs. the hand-rolled scanner in `dedup.py`), frontmatter written via f-string instead of a YAML library, and the observability singleton reading `settings.obsidian_vault_path` directly (bypassing per-request vault overrides). A follow-up issue proposes consolidating vault I/O into one internal `VaultStore` seam — an internal refactor, not a plugin interface — which would also make a future adapter cheap.
- Issue #14 is closed with this record.

## Revisit triggers

Any one of these reopens the question:

1. A concrete second source is actually requested or attempted (e.g. a real Notion export or Logseq graph), not hypothetically.
2. A second Obsidian-specific behavior needs to spread beyond its current localized spot.
3. The vault I/O consolidation follow-up lands, making the adapter's remaining cost small.
4. An external contributor proposes an adapter PR.

If revisited, the interface should mirror the existing provider pattern (`providers/base.py`: small ABC surface, shared behavior in the base, ordered factory) rather than invent a second style.
