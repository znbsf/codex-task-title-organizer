# Install Codex Task Title Organizer

This repository is a complete one-plugin Codex marketplace.

## GitHub installation

```text
codex plugin marketplace add znbsf/codex-task-title-organizer --ref main
codex plugin add codex-task-title-organizer@codex-task-title-organizer
```

## Local checkout or extracted ZIP

From any terminal, keep the repository at a stable path and run:

```text
codex plugin marketplace add "<repository-root>"
codex plugin add codex-task-title-organizer@codex-task-title-organizer
```

If the marketplace is already configured, inspect `codex plugin marketplace
list` and reuse it instead of creating a duplicate.

## Required review

1. Verify Python 3 with its standard `sqlite3` module: use
   `py -3 -c "import sqlite3; print(sqlite3.sqlite_version)"` on Windows or
   `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"` on macOS/Linux.
2. Open `/hooks`, inspect the `UserPromptSubmit`, exact title-tool `PostToolUse`,
   and `Stop` commands, and trust them. Upgrading requires review of the new Stop
   handler and any changed matcher.
3. Refresh the configuration in the Codex instance running your tasks. For an
   existing task, follow the runtime refresh guidance below. Restart only when
   it will not interrupt active work.
4. The counter begins when the enabled and trusted plugin first observes a
   prompt in that task session.

Do not claim the automatic checkpoints are active before the Hook is trusted.

### Applying upgrades to existing tasks

Hook definitions and trust changes must reach the app-server hosting the task.
When using the native API, connect to that running server and confirm the task
appears in `thread/loaded/list`. After reviewing and trusting the upgraded Hook,
request `config/batchWrite` with these parameters to refresh loaded tasks without
changing configuration values:

```json
{"edits": [], "reloadUserConfig": true}
```

Sending this request to a separate verification app-server refreshes only that
process. A successful `hooks/list` result confirms the definitions and trust
records it reads; it does not verify that an existing task has applied them.

Validate an existing task through its next real title-tool success and the
following Stop. The plugin should record the successful result and let Stop
finish without a recovery prompt. At or after the final checkpoint, its own
SQLite row becomes `done = 1, state_json = NULL`. Earlier checkpoints retain
`title_write_confirmed = true` until the final checkpoint. Previously missed
receipts are not reconstructed, so a task may request one confirmation before
the refreshed observer receives its first successful call. Keep the validation
read-only; never insert a completion record to make it pass.

State is stored in the plugin's own `title-state.sqlite3`, separate
from Codex's databases. Existing per-task JSON state is imported as each task
resumes; the files remain unchanged for rollback. After the final check succeeds,
only a completion marker remains in SQLite and that task no longer triggers
automatic title checks. A rollback uses the older JSON snapshot and cannot
restore progress recorded only in SQLite.

## Migrating from codex-personal-skills

The package ID remains `codex-task-title-organizer`, so titles and skill prompts
do not need to change. Add this standalone marketplace and install the package
qualified by its new marketplace name:

```text
codex plugin add codex-task-title-organizer@codex-task-title-organizer
```

Do not delete the old repository. Remove or disable an older installed copy only
after confirming the standalone package appears in `codex plugin list` and the
new Hook has been reviewed. Existing Hook counters are marketplace-local and do
not need to be copied.

## Ask Codex to install a local ZIP

After extracting the ZIP and opening it as a Codex project, ask:

```text
Install the Codex plugin marketplace in this repository. Read INSTALL.md,
verify the package and Python 3, install codex-task-title-organizer, and stop for
me to review and trust its Hook. Do not edit Codex session or database files.
```

The plugin does not create a scheduled automation. Its Hook handles current-task
checkpoints; old-task batch cleanup remains an explicit skill action.

The default Hook checkpoints are `2,4,10`. Configure one repository with
`.codex/task-title-organizer.json`, or set the user-wide
`CODEX_TASK_TITLE_CHECKPOINTS` environment variable. Title-based sidebar
routing is disabled by default and needs an explicit bounded Domain allowlist
before it may create sections. See the configuration section in `README.md`
before applying a broader override.
