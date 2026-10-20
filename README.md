# Codex Task Title Organizer

A standalone Codex plugin for keeping task titles compact, searchable, visually
consistent, and tied to the right project or repository.

The plugin bundles one skill and three privacy-conscious Hooks:

- prompt 2 asks the active Codex turn to establish a useful title;
- prompt 4 refines it only when clearer task context is available;
- prompt 10 confirms the intended title once, even when its wording is unchanged;
- the Hook never reads or persists prompt text;
- `PostToolUse` verifies the current task's title-tool receipt in memory without
  retaining the requested or returned title;
- `Stop` requests at most one recovery continuation per checkpoint when a title
  write is due but has not been confirmed;
- old tasks can still be normalized manually in batches of at most 10.

## Title standard

`Marker Domain｜Project/repository key｜Work type｜Concrete object: purpose or symptom`

Color blocks remain the default, while semantic icons and explicit custom
markers are supported:

| Primary outcome | Color blocks (default) | Semantic icons |
| --- | --- | --- |
| Analysis or diagnosis | `🟥` | `🔎` |
| Fix or implementation | `🟦` | `🔧` |
| Validation or testing | `🟩` | `🧪` |
| Delivery or synchronization | `🟪` | `📦` |
| Review | `🟧` | `👀` |
| Documentation or reporting | `🟨` | `📝` |
| Environment, tooling, or automation | `🟫` | `⚙️` |

Use exactly one leading marker. A project may define one custom marker or a
complete category mapping in `AGENTS.md`; explicit instructions for the current
operation take precedence. Invalid or incomplete custom mappings fall back to
color blocks at automatic checkpoints.

## Sidebar examples

Color blocks (default):

![Color-block title examples](docs/assets/title-organizer-sidebar-examples-en-color-blocks.png)

Semantic icons:

![Semantic-icon title examples](docs/assets/title-organizer-sidebar-examples-en-semantic-icons.png)

For private projects without Jira or Gerrit, the second field uses the project
name, repository name, or only the `cwd` basename. It never generates `NO-ID`.
A compact local activity date is the final fallback only when no project context
exists.

## How the Hook works

The command Hook does not rename a task by reaching into Codex internals. It
counts unique prompts, then adds a narrowly scoped developer instruction to the
same foreground turn. The active Codex turn uses the public current-task title
tool, without a thread ID, after finishing the user's main request.

Automatic title maintenance applies only to the main agent of a persistent task.
Hook events carrying Codex's subagent identity fields (`agent_id` or `agent_type`)
or a null or missing `transcript_path` return before loading configuration or
opening the state database.
Codex supplies a null transcript path for ephemeral side chats, which cannot be
renamed. The Hook checks only whether this metadata is present; it never reads
the transcript or stores its path. Skipped events do not count prompts, record
title receipts, or request recovery, even if an older version already created
state. The plugin does not register a `SubagentStop` handler. The skill also tells
subagents and side chats to skip inherited title-maintenance instructions.

State is stored in `PLUGIN_DATA/title-state.sqlite3`, using Python's standard
library SQLite driver. One `sessions` row per task contains `session_key`, `done`,
and `state_json`. SQLite transactions serialize concurrent Hook updates; new
tasks no longer create individual JSON or lock files. Active state contains only:

- a SHA-256 hash of the session ID as the primary key;
- SHA-256 hashes of recently seen turn IDs and checkpoint turns awaiting the
  current foreground turn's title-tool completion;
- the prompt count, emitted checkpoints, and a bounded set of matching title-tool
  completion observations;
- a bounded list of turn hashes with each turn's last title-write result;
- a title-write confirmation flag and the hashed turn ID of the current
  checkpoint's Stop recovery, if used.

It stores no generated titles, prompt excerpts, transcripts, rollout data,
Codex database content, full user paths, or project taxonomy.

`fired_thresholds` means only that the `UserPromptSubmit` Hook emitted checkpoint
context. Completion observations remain separate from success. The observer
matches `codex_app__set_thread_title`, `codex_tui__set_thread_title`, and their
MCP-prefixed forms. It also accepts the flattened dynamic-tool Hook names
`codex_appset_thread_title` and `codex_tuiset_thread_title`: Codex can omit the
separator shown by code mode when emitting `PostToolUse`.
It confirms a write only when the call targets the current Codex task, the result
has no error, the returned `threadId` equals the Hook's `session_id`, and the
returned title equals the requested title. Native JSON results and MCP structured
or single-text JSON results are supported. An identical title counts as confirmed;
the Hook verifies the requested value was set, not that the text changed.

The observer saves the latest success or failure for each turn. `Stop` consumes
its own turn's result before deciding whether a recovery is needed, so a
successful write directly allows the turn to finish. This includes resumed turns
that have no new user prompt and therefore do not increment the checkpoint
counter. Another turn's delayed receipt cannot confirm the stopping turn's final
check. Before the final checkpoint, the confirmation flag is also updated when
the receipt arrives, so a new user message interrupting before Stop does not lose
an initial successful write. Results are capped at 64 turns, consumed by the
matching Stop, and cleared when a new user interaction begins. Existing schema
version 7 state remains readable.

At an emitted checkpoint, `Stop` can recover a missing first confirmation, a
failed title write, or a missing final confirmation. It does not force an
optional intermediate refinement after a title has been confirmed. Each
checkpoint allows one automatic recovery continuation, using the same current
task and the public title tool. The saved recovery turn ID remains set until
another checkpoint is emitted, so resumed turns cannot renew that allowance.
If the tool is unavailable or recovery fails, the turn ends normally and
automatic attempts wait for the next checkpoint. An interrupted checkpoint also
waits for the next checkpoint once a new user prompt begins. Manual title writes
can still be confirmed between checkpoints. Idle tasks are never scanned.

Codex displays Stop continuation feedback in the chat. The Hook emits one short
instruction linking to the installed title skill; detailed instructions stay in
the skill's Current-task checkpoint mode. The skill ends a successful recovery
without a separate title-update announcement. This reduces visible noise; it
does not hide Codex's hook-feedback message.

The last configured checkpoint (10 by default) always requests a confirming
write, even if the title is unchanged. Earlier success does not satisfy this
final check. After Stop confirms a successful current-turn receipt at or after
that checkpoint, the row becomes `done = 1, state_json = NULL`: counters and turn
records are removed. Later Hooks return without changing it or restarting checkpoints,
including after a configuration change. After the final checkpoint's recovery
is exhausted, a later manual write can complete confirmation. SQLite reuses
freed space; it does not run a full database compaction on every Hook.

The Hook imports the current task's legacy JSON state on its first event, keeping
its counters and emitted checkpoints. Old files remain untouched as rollback
data, and SQLite is authoritative after import. No idle tasks are scanned.
Older tasks already past the final checkpoint need one fresh manual
confirmation; an old success flag does not establish that the final check
completed. A suitable title can be submitted unchanged. Rolling back to the
JSON-based version uses the old snapshot (new tasks restart counting); it does
not undo written titles.

## Route renamed tasks to sidebar sections

Optional sidebar routing groups only successfully renamed **Codex tasks** by the
title's Domain field. It is disabled by default. When enabled, it reuses a
section named `🗂️ <Domain>` or, with an explicit allowlist, creates that section
and moves the same current task. It never routes ChatGPT conversations or turns
free-form project/object text into a section name.

Use a project-local configuration when one project needs this behavior:

```json
{
  "enabled": true,
  "checkpoints": [2, 4, 10],
  "sectionRouting": {
    "enabled": true,
    "sectionPrefix": "🗂️",
    "createMissingSections": true,
    "allowedDomains": ["Backend", "Mobile", "Infra"],
    "sectionDomainMap": {"Mobile": "Backend"},
    "maxSections": 12,
    "sectionCapacity": {
      "enabled": true,
      "maxItems": 10,
      "overflowDestination": "default"
    }
  }
}
```

The list is exact and bounded, so one malformed or overly specific title cannot
create unlimited sidebar sections. See the bundled
[`section-routing.md`](plugins/codex-task-title-organizer/skills/organize-codex-task-titles/references/section-routing.md)
for user-wide configuration, routing behavior, and batch limits.

`sectionDomainMap` is optional: it keeps a detailed Domain in the title while
routing it to one approved parent section.

`sectionCapacity` is separately opt-in. It keeps a routed section newest-first:
after a successful rename, the current task is moved to the top and the section
retains at most `maxItems` (10 by default). Confirmed Codex overflow tasks return
to the normal Tasks area through the safe `"default"` destination; they are not
deleted or archived. It never moves a ChatGPT conversation, re-reads the sidebar,
or stores sidebar IDs. See the bundled
[`section-routing.md`](plugins/codex-task-title-organizer/skills/organize-codex-task-titles/references/section-routing.md)
for the exact failure and ordering boundaries.

## Configure checkpoints

The default schedule is `2,4,10`. For one project, create
`.codex/task-title-organizer.json`:

    {
      "enabled": true,
      "checkpoints": [2, 4, 10]
    }

Set `"enabled": false` to disable the Hook for that project.

For a user-wide override, set `CODEX_TASK_TITLE_CHECKPOINTS` to a comma-separated
list such as `1,3,8`. The Hook also supports `PLUGIN_DATA/config.json` for managed
or advanced installs. Precedence is project file, environment variable,
plugin-data file, then defaults. Values must be 1-50, unique, with at most eight
checkpoints. Invalid configuration safely falls through to the next source.

Schedule changes do not replay checkpoint numbers that already passed in an
active task.

## Install from GitHub

```text
codex plugin marketplace add znbsf/codex-task-title-organizer --ref main
codex plugin add codex-task-title-organizer@codex-task-title-organizer
```

Restart or refresh Codex, open `/hooks`, inspect the exact `UserPromptSubmit`,
`PostToolUse`, and `Stop` commands, and trust them.
Automatic checkpoints are not active until that user review is complete.

## Install from a local checkout or ZIP

```text
codex plugin marketplace add "<repository-root>"
codex plugin add codex-task-title-organizer@codex-task-title-organizer
```

Keep the local marketplace directory at a stable path. See [INSTALL.md](INSTALL.md)
for the full AI-oriented procedure and migration guidance.

## Manual use

The bundled skill remains available for explicit work:

```text
Use $organize-codex-task-titles to standardize the current Codex task title.
```

```text
Use $organize-codex-task-titles to normalize recent Codex task titles, changing
at most 10 and skipping active and ChatGPT tasks.
```

```text
Use $organize-codex-task-titles to normalize and route recent Codex task titles
into sidebar sections.
```

```text
Use $organize-codex-task-titles to migrate recent compliant titles to semantic
icons, changing only the leading marker.
```

## Develop and verify

Python 3.10 or newer is recommended. The Hook itself has no third-party runtime
dependencies.

```text
python -m compileall -q plugins/codex-task-title-organizer/scripts
python -m unittest discover -s tests -v
```

CI exercises both Windows and Linux because the state lock is platform-specific.

## Safety boundaries

- Current-task checkpoints never list or read other task content. The sole
  routing exception may read sidebar section metadata once after a successful
  title update; it uses no task content and moves only that returned current
  Codex task.
- Batch operations modify only `kind=codex`, skip active tasks by default, and
  stop at 10 changes per run.
- The plugin never uses Codex databases, session/rollout files, or UI automation
  as a title-management fallback.
- Installing the plugin does not create, edit, or delete recurring automations.
