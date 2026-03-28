# Terminology Inconsistencies Analysis

This document identifies inconsistencies in CLI option naming and terminology across the codebase.

## Summary of Issues

The following inconsistencies have been identified:

1. **Auto vs. Confirm vs. Yes** - Multiple ways to express automatic/confirmed execution
2. **Dry-run flag naming** - Inconsistent use of underscores in parameter names
3. **Batch operation flags** - Different patterns for batch operations

---

## Issue 1: Auto vs. Confirm vs. Yes

### Problem

Three different flag names are used to express similar concepts of "automatic execution without prompting":

| Command | Flag | Purpose |
|---------|------|---------|
| `connect` | `--auto-link` | Auto-insert wikilinks |
| `connect` | `--confirm` | Confirm before modifying files |
| `tags` | `--yes`, `-y` | Auto-accept all suggestions |
| `tags` | `--fix` | Interactively apply fixes |
| `refresh` | `--confirm` | Execute refresh (creates backups) |
| `reading-list clear` | (uses typer.confirm()) | Inline confirmation |

### Analysis

**Current Behavior:**
- `--auto-link` - Enables automatic linking (but still requires `--confirm` to execute)
- `--confirm` - Required to actually execute changes (used in `connect`, `refresh`)
- `--yes` / `-y` - Auto-accepts prompts during interactive mode (used in `tags`)

### Issues Identified

1. **`connect` command**: Uses both `--auto-link` AND `--confirm`
   - `--auto-link` enables the feature
   - `--confirm` actually executes it
   - This is confusing - why two flags?

2. **`tags` command**: Uses `--fix` for interactive mode, `--yes` to skip prompts
   - Different pattern than `connect`
   - `--yes` is only meaningful with `--fix`

3. **`refresh` command**: Uses `--confirm` to execute (opposite of `--dry-run`)
   - Without `--confirm`, just lists candidates
   - Clearer than `connect` pattern

### Recommendation

**Option A: Standardize on --yes pattern (safest)**
```bash
# Current (confusing)
kai connect --folder "AI" --auto-link --confirm

# Proposed (clearer)
kai connect --folder "AI" --auto-link --yes

# Current
kai tags --fix --yes

# Proposed (no change needed)
kai tags --fix --yes

# Current
kai refresh -p youtube_v2 --confirm

# Proposed
kai refresh -p youtube_v2 --yes
```

**Option B: Standardize on --confirm pattern**
```bash
# Current
kai connect --folder "AI" --auto-link --confirm

# Proposed (no change - already clear)
kai connect --folder "AI" --auto-link --confirm

# Current
kai tags --fix --yes

# Proposed
kai tags --fix --confirm

# Current
kai refresh -p youtube_v2 --confirm

# Proposed (no change)
kai refresh -p youtube_v2 --confirm
```

**Recommendation: Use Option B (--confirm)**

Rationale:
- `--confirm` is more explicit about what's happening
- `--yes` is too generic and commonly used in destructive operations
- `--confirm` pairs well with `--dry-run` (opposite concepts)
- Already used in 2/3 commands

**Implementation Plan:**

1. Keep `connect --confirm` as is
2. Change `tags --yes` to `tags --confirm` 
3. Keep `refresh --confirm` as is
4. Remove `--auto-link` requirement for `--confirm` in `connect` (if auto-link is enabled, confirm should execute it)

---

## Issue 2: Dry-run Implementation

### Problem

Inconsistent variable naming for dry-run flag:

**CLI Options (kebab-case - correct):**
```python
# cli.py line 467
dry_run: Annotated[bool, typer.Option("--dry-run", ...)]  # ✓ Correct

# cli.py line 1253  
dry_run: Annotated[bool, typer.Option("--dry-run", ...)]  # ✓ Correct

# cli.py line 1470
dry_run: Annotated[bool, typer.Option("--dry-run", ...)]  # ✓ Correct
```

**Function Parameters (snake_case - correct):**
```python
# folder_organizer.py line 317
def move_note(note: NoteToMove, vault_path: Path, dry_run: bool = False)  # ✓ Correct

# concept_linking.py line 408
def insert_wikilinks(..., dry_run: bool = True)  # ✓ Correct
```

### Analysis

**Good News:** The implementation is actually consistent!
- CLI flags use `--dry-run` (kebab-case) - this is the standard for CLI flags
- Python parameters use `dry_run` (snake_case) - this is PEP 8 compliant
- Typer automatically converts between the two

### Recommendation

✅ **No changes needed** - this is actually the correct pattern.

---

## Issue 3: Batch Operation Patterns

### Problem

Different patterns for enabling batch operations:

| Command | Flag | Purpose |
|---------|------|---------|
| `preview` | `--batch`, `-b` | Read URLs from stdin |
| `reading-list ingest` | `--all`, `-a` | Ingest all pending items |
| `connect` | `--folder`, `-f` | Scan entire folder |

### Analysis

These are actually different concepts:
- `--batch` - Process multiple inputs from stdin
- `--all` - Process all items in a collection
- `--folder` - Process all items in a folder

### Recommendation

✅ **No changes needed** - these serve different purposes and the naming is appropriate.

---

## Issue 4: Boolean Flag Naming Patterns

### Problem

Inconsistent naming for boolean toggles:

**Positive Flags (enable feature):**
- `--verbose` - Enable verbose logging
- `--interactive` - Enable interactive mode
- `--auto-link` - Enable auto-linking
- `--fix` - Enable fix mode
- `--orphans` - Find orphans
- `--by-folder` - Group by folder
- `--recent` - Show recent items
- `--confirm` - Confirm execution

**Negative Flags (disable feature):**
- `--dry-run` - Don't execute (preview only)
- `--no-backup` - Don't create backups

### Analysis

Most flags are positive (enable feature), which is good UX. The two negative flags are:
1. `--dry-run` - Common pattern, well understood
2. `--no-backup` - Follows common `--no-*` pattern for disabling defaults

### Recommendation

✅ **No changes needed** - this follows common CLI conventions.

---

## Issue 5: Confirmation Workflow Inconsistencies

### Current Confirmation Patterns

**Pattern A: Dry-run + Confirm flags (refresh command)**
```bash
kai refresh -p youtube_v2 --dry-run        # Preview
kai refresh -p youtube_v2 --confirm        # Execute
```
- `--dry-run` - List candidates only
- `--confirm` - Actually execute
- Without either: Lists candidates with a message to add --confirm

**Pattern B: Dry-run + interactive confirm (process-inbox command)**
```bash
kai process-inbox --dry-run    # Preview
kai process-inbox              # Prompts with typer.confirm()
```
- `--dry-run` - Preview only
- No flag - Shows plan then asks `typer.confirm("Move N notes?")`

**Pattern C: Auto-link + Confirm flags (connect command)**
```bash
kai connect --folder "AI" --auto-link --dry-run     # Preview
kai connect --folder "AI" --auto-link --confirm     # Execute
kai connect --folder "AI" --auto-link               # Also prompts!
```
- `--auto-link` enables the feature
- `--dry-run` - Preview what would be inserted
- `--confirm` - Skip the typer.confirm() prompt
- Without `--confirm` - Still prompts with typer.confirm()

**Pattern D: Fix + Yes flags (tags command)**
```bash
kai tags                  # Read-only analysis
kai tags --fix            # Interactive with prompts
kai tags --fix --yes      # Auto-apply all
```

### Issues Identified

1. **Inconsistent confirm behavior:**
   - `refresh`: Requires `--confirm` to execute
   - `process-inbox`: No flag needed, uses runtime prompt
   - `connect`: Uses both `--confirm` flag AND runtime prompt
   - `tags`: Uses `--yes` instead of `--confirm`

2. **Reading list clear**: Uses runtime `typer.confirm()` unless `--yes` is provided
   - BUT `--yes` is not exposed as a CLI option!
   - Lines 1721-1723 in cli.py check for `yes` variable, but it's only available in `tags` command

### Recommendation

**Standardize on this pattern for all destructive operations:**

```bash
# Safe: Preview changes without flag
kai <command> [query-options]              # Read-only analysis/preview

# Safer: Explicit dry-run preview  
kai <command> [query-options] --dry-run    # Explicit preview mode

# Execute: With confirmation prompt
kai <command> [query-options] --confirm    # Prompts before executing

# Execute: Skip prompt (dangerous)
kai <command> [query-options] --confirm --yes   # Execute without prompt
```

**Implementation:**

1. All destructive commands should support:
   - Default: Read-only or preview
   - `--dry-run`: Explicit preview (optional, for clarity)
   - `--confirm`: Execute with prompt (unless `--yes`)
   - `--yes`: Skip prompts (only valid with `--confirm`)

2. Update these commands:
   - `process-inbox`: Add `--confirm` flag (keep current behavior as default)
   - `connect`: Remove dual confirmation (flag + prompt)
   - `tags`: Rename `--yes` to use with `--confirm`
   - `reading-list clear`: Add `--confirm` and `--yes` flags

---

## Proposed Changes Summary

### High Priority (Breaking Changes - Do These Together)

1. **Standardize confirmation pattern:**
   ```python
   # Update all destructive commands to use:
   --confirm    # Execute the operation (with prompt)
   --yes        # Skip prompts (only with --confirm)
   --dry-run    # Preview only
   ```

2. **Specific command updates:**

   **`tags` command:**
   ```python
   # Current
   kai tags --fix --yes
   
   # Proposed  
   kai tags --fix --confirm       # Interactive with prompts
   kai tags --fix --confirm --yes # Auto-apply without prompts
   ```

   **`connect` command:**
   ```python
   # Current (confusing - both flag AND prompt)
   kai connect --folder "AI" --auto-link --confirm
   # Still prompts: "Insert N wikilinks? [y/n]"
   
   # Proposed (consistent)
   kai connect --folder "AI" --auto-link --confirm       # Prompts
   kai connect --folder "AI" --auto-link --confirm --yes # No prompt
   ```

   **`process-inbox` command:**
   ```python
   # Current (no confirm flag, always prompts)
   kai process-inbox
   
   # Proposed (consistent with others)
   kai process-inbox --confirm       # Prompts: "Move N notes?"
   kai process-inbox --confirm --yes # No prompt
   kai process-inbox                 # Read-only preview (NEW behavior)
   ```

   **`reading-list clear` command:**
   ```python
   # Current (hardcoded prompt, no --yes option)
   kai reading-list clear --status all
   # Always prompts
   
   # Proposed
   kai reading-list clear --status all --confirm       # Prompts
   kai reading-list clear --status all --confirm --yes # No prompt
   kai reading-list clear --status all                 # Read-only preview
   ```

### Medium Priority (Non-Breaking Improvements)

1. **Add `--yes` short flag consistently:**
   ```python
   # Make sure all commands that support --yes also support -y
   typer.Option("--yes", "-y", help="Skip confirmation prompts")
   ```

2. **Document the confirmation pattern in --help text:**
   ```python
   typer.Option(
       "--confirm", 
       help="Execute changes (prompts for confirmation unless --yes is used)"
   )
   typer.Option(
       "--yes", "-y",
       help="Skip confirmation prompts (must be used with --confirm)"
   )
   ```

### Low Priority (Documentation)

1. Update README.md to document the standard confirmation pattern
2. Add examples showing `--dry-run`, `--confirm`, and `--yes` workflows
3. Create a "Safety" section in docs explaining the confirmation levels

---

## Migration Path

To avoid breaking existing users:

### Phase 1: Additive Changes (v0.2.0)
1. Add `--confirm` to all commands (but keep current behavior as default)
2. Add `--yes` flag where missing
3. Deprecation warnings for old patterns
4. Update documentation

### Phase 2: Behavioral Changes (v0.3.0)
1. Change default behavior (current destructive defaults become read-only)
2. Require `--confirm` for execution
3. Remove deprecation warnings (old flags still work)

### Phase 3: Breaking Changes (v1.0.0)
1. Remove old flag patterns completely
2. Enforce new confirmation workflow

---

## Command Categories and Phase 2 Scope

### Commands by Modification Type

#### **DESTRUCTIVE Commands (Modify Existing Files)** 
*Phase 2 applies to these - will require `--confirm`*

1. **`process-inbox`** - Moves existing files from inbox to folders
2. **`connect --auto-link`** - Inserts wikilinks into existing note files
3. **`refresh`** - Re-processes and overwrites existing note files
4. **`tags --fix`** - Modifies tags in existing note files
5. **`reading-list clear`** - Deletes entries from reading list file

**Phase 2 Behavior:**
```bash
# Current (Phase 1) - Prompts by default
kai process-inbox
> ❓ Move 5 note(s)? [y/n]: 

# Phase 2 (v0.3.0) - Requires explicit --confirm
kai process-inbox
> ⚠️  Add --confirm to execute moves

kai process-inbox --confirm
> ❓ Move 5 note(s)? [y/n]:

kai process-inbox --confirm --yes
> 💾 Moving notes... ✅ Successfully moved 5 note(s)
```

---

#### **ADDITIVE Commands (Create New Files)**
*Phase 2 does NOT apply - these remain unchanged*

1. **`ingest`** - Creates NEW note in inbox (safe - can delete later)
2. **`digest --output`** - Creates NEW digest note (safe - can delete later)
3. **`reading-list ingest`** - Creates NEW notes from reading list (safe)

**Rationale:** Creating new content is inherently safer than modifying existing content. Users can always delete unwanted new files, but recovering modified content requires backups.

**Phase 2 Behavior:**
```bash
# No changes - works exactly as in Phase 1
kai ingest https://youtube.com/watch?v=...
> ✅ Ingestion complete!

kai digest --output weekly-review
> ✅ Digest saved to: inbox/weekly-review.md
```

---

#### **READ-ONLY Commands (No File Modifications)**
*Phase 2 does NOT apply - already safe*

- `search` - Queries vault index
- `list-tags` - Lists tags with counts
- `rebuild-index` - Updates search index (metadata only)
- `stats` - Shows cost statistics
- `quality` - Shows quality metrics
- `preview` - Previews URLs without ingesting
- `digest` - Terminal output (without `--output`)
- `reading-list list` - Lists reading list entries
- `connect` - Shows connections (without `--auto-link`)
- `tags` - Analysis only (without `--fix`)
- `version` - Shows version info

**Phase 2 Behavior:**
```bash
# No changes - already safe
kai search --keyword "AI"
kai list-tags
kai preview https://example.com
```

---

### Phase 2 Summary

**Scope:** Only commands that MODIFY existing files

**Change:** Default behavior becomes read-only/preview. Explicit `--confirm` required to execute.

**NOT Affected:** 
- Commands that create new files (additive)
- Commands that only read data (read-only)

**Backward Compatibility:** 
- Deprecation warnings in Phase 2
- Full breaking change in Phase 3 (v1.0.0)

---

## Testing Checklist

Before implementing changes:

- [x] Document current behavior of each destructive command (Phase 1 complete)
- [ ] Create integration tests for confirmation workflows  
- [ ] Add unit tests for new flag combinations
- [ ] Test backward compatibility with deprecated flags
- [ ] Update CLI help text
- [ ] Update README and CLI_COMMANDS.md
- [ ] Add migration guide for users

---

## Conclusion

**Main Inconsistency: Confirmation Workflow**

The primary inconsistency is in how commands handle destructive operations:
- Some require `--confirm` flag
- Some use runtime prompts
- Some use both
- Flag names vary (`--yes`, `--confirm`, `--auto-link`)

**Recommendation:**

Standardize on a clear, consistent pattern:
- No flags = Read-only/preview
- `--dry-run` = Explicit preview (optional)
- `--confirm` = Execute with prompt
- `--confirm --yes` = Execute without prompt

This provides a clear safety ladder from safest to most dangerous operations.
