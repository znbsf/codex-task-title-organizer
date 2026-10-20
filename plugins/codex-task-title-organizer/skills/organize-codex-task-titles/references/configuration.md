# Configure title checkpoints

The Hook defaults to checkpoints `2, 4, 10`:

- `2`: establish a useful title early;
- `4`: refine only when the title is missing, inaccurate, or materially clearer;
- `10`: review and confirm the intended title, writing the same value if unchanged.

The Stop Hook can request one recovery continuation at each emitted checkpoint.
If the title tool is unavailable or the write fails, the turn ends normally;
automatic recovery waits for the next checkpoint. Resumed turns share the same
allowance. Between checkpoints, title writes can be triggered manually and their
receipts are still confirmed. A successful title receipt allows intermediate
refinements to be skipped; the final checkpoint requires its own receipt.
Disabling the plugin's Hook configuration also disables Stop recovery.

State lives in `PLUGIN_DATA/title-state.sqlite3`, with one row per hashed session
ID. When Stop confirms a successful current-turn write at or after the last
configured checkpoint, it replaces the detailed state with `done = 1` and
`state_json = NULL`. Later events return without
counting, writing state, or requesting another title check, even if the schedule
changes. Deleting the completion marker would restart counting, so it is retained.
Failed final writes keep their state for a later manual write once that
checkpoint's automatic recovery has been used.

Existing JSON state is imported once when that task next runs a Hook. Counters
are preserved, but tasks already past the final checkpoint need one fresh
manual confirmation. The legacy files remain unchanged for rollback; SQLite
takes precedence after import. New tasks create no per-session JSON or lock files.

## Project-level configuration

Use this for one repository. Create `.codex/task-title-organizer.json` under the
project root:

    {
      "enabled": true,
      "checkpoints": [2, 4, 10]
    }

To disable the Hook for that project:

    {
      "enabled": false
    }

Write this file only when the user explicitly asks to configure the project.
Do not add it merely because the skill was loaded.

## User-wide environment override

Set `CODEX_TASK_TITLE_CHECKPOINTS` to a comma-separated list such as `2,4,10`.
Use `off`, `false`, `disabled`, or `none` to disable the Hook. Restart Codex
so the desktop process inherits a changed environment variable.

Change the user environment only when the user explicitly asks for a user-wide
setting. Do not set it as a side effect of project-level configuration.

## Plugin-data configuration

Managed or advanced installations may place this JSON at
`PLUGIN_DATA/config.json`, using the same schema as the project file. Codex
provides `PLUGIN_DATA` to the Hook process as its writable plugin data directory.
Do not guess or derive that path from Codex databases or session files.

## Precedence and validation

Configuration precedence is:

1. project `.codex/task-title-organizer.json`;
2. `CODEX_TASK_TITLE_CHECKPOINTS`;
3. `PLUGIN_DATA/config.json`;
4. default `2,4,10`.

Checkpoint values must be unique integers from 1 through 50. At most eight are
accepted. The Hook sorts valid values. Invalid, oversized, or malformed config
is ignored and the next valid source is used.

Changing a schedule during an unfinished task never replays newly added
checkpoints whose prompt numbers already passed. Future checkpoints remain
eligible. Shortening the schedule past its last checkpoint requires a fresh
confirmation before retiring the state. Completed tasks stay completed.

## Sidebar routing

Sidebar routing uses the same project file and plugin-data file, but its
user-wide override is `CODEX_TASK_TITLE_SECTION_ROUTING`. It is disabled by
default and has its own per-field precedence so a project routing policy can
override a user-wide one without changing checkpoints. Read
[section-routing.md](section-routing.md) before enabling it.
