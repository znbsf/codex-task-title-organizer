---
name: organize-codex-task-titles
description: Standardize and safely rename Codex task titles with color-block, semantic-icon, or explicit custom markers plus a domain, project or repository key, work type, and concrete object-plus-purpose summary. Use for current-task title checkpoints, Stop recovery notices, manual title cleanup, cross-repository batch normalization, marker migration, NO-ID migration, or recurring title maintenance. Private projects without Jira or Gerrit should use the project name, repository name, or cwd basename.
---

# Organize Codex task titles

Run title maintenance only from the main agent of a persistent task. If you are
a subagent or are in a side or ephemeral conversation, skip this skill, including
inherited checkpoints and Stop recovery notices. Do not rename your own thread
or its parent task.

## Build the title

Use this structure:

`Marker Domain｜Project/repository key｜Work type｜Concrete object: purpose or symptom`

Match the language already used in the task. Select the marker style in this
order:

1. an explicit instruction for the current operation;
2. the active project's instructions, such as `AGENTS.md`;
3. an already valid color-block, semantic-icon, or custom style;
4. color blocks for a new or noncompliant title.

Put exactly one leading marker grapheme followed by one ordinary space. Never
combine styles. Color blocks are the default:

- `🟥`: analysis or diagnosis
- `🟦`: fix or implementation
- `🟩`: validation or testing
- `🟪`: delivery or synchronization
- `🟧`: review
- `🟨`: documentation or reporting
- `🟫`: environment, tooling, or automation

Choose the marker from the primary outcome. For mixed work, use at most two work
types, such as `Fix + Validate`. Do not use markers for severity, success, or
urgency. For the semantic-icon mapping and custom marker constraints, read
[references/title-styles.md](references/title-styles.md).

Fill the fields as follows:

- **Domain:** use a stable, short product, component, or workstream label such as
  `Android`, `Runtime`, `Infra`, `Docs`, or `Codex`. Prefer terminology already
  established by the user or project.
- **Project/repository key:** always identify where the work belongs. Prefer a
  known project or repository name. A Jira, Gerrit, issue, pull request, change,
  or release key may be appended when it is already established, for example
  `service-api#APP-123` or `player#PR-456`. For private projects without such a
  key, use the project name, repository name, or only the basename of `cwd`.
  Never invent an identifier and never emit placeholders such as `NO-ID`. Use a
  local `MMDD` activity date only when no stable project or repository context
  exists at all.
- **Work type:** use a concise action label in the task's language, for example
  Analyze, Diagnose, Fix, Build, Review, Validate, Deliver, Sync, Document,
  Report, Environment, Tooling, or Automate.
- **Concrete object and purpose:** answer both “what is being handled?” and “why
  or what symptom?”. Preserve the concrete feature, component, failure, or
  intended behavior.

Shape the last field around the work:

- analysis/diagnosis: `Object: symptom or unknown cause`
- fix/build: `Change point: intended behavior`
- validation: `Changed behavior: validation target`
- review: `Change: risk or requirement`
- docs/tooling/automation: `Artifact or trigger: intended use`

Avoid vague endings such as “implementation”, “behavior”, “content”, “update”,
“optimization”, “test”, or “issue” without a concrete object and purpose. Keep
the title to one readable sidebar line. Do not claim success or validation that
the task has not established.

## Respect local customization

Treat user instructions and the active project's `AGENTS.md` as a local overlay.
It may define domain aliases, repository abbreviations, issue-key formats,
preferred work-type wording, a marker style, a custom marker mapping, or a
timezone. Do not copy private taxonomies into public files unless the user
explicitly asks.

Never infer a client identity, project name, or issue key from unrelated tasks.

## Current-task checkpoint mode

When Hook context requests a plugin checkpoint or a title-confirmation recovery,
finish the user's main request first, then run this section before ending the
same foreground turn. Do not enter the batch workflow:

1. Generate the title only from the current task context. Do not call
   `list_threads`, `read_thread`, or an automation tool, except for the narrow
   post-rename sidebar routing flow explicitly enabled by Hook context.
   Do not inspect persisted prompts, transcripts, rollouts, Codex databases, or
   session files, and do not use UI automation.
2. The current task may be active. Call `set_thread_title` without a `threadId`
   so only the calling task is targeted.
3. Prefer the current project/repository identifier or `cwd` basename over an
   activity date when no Jira/Gerrit key exists.
4. At the first checkpoint, establish a useful title. At an intermediate
   checkpoint, refine only when the title is missing, inaccurate, or materially
   clearer context is now available. At the final checkpoint, review the title
   and write the intended value once even if it is unchanged. The successful
   receipt completes automatic title maintenance for this task.
5. Act only on a confirmed Codex task. If task kind or tool availability cannot
   be confirmed, skip the write. At a normal checkpoint, skip quietly; during a
   Stop recovery, briefly report the limitation. Do not create a task, send a
   follow-up, or inspect internal files as a workaround.
6. Treat title maintenance as secondary. Do not add a maintenance report unless
   a failure affects the user's decision.

When Hook context identifies **Stop recovery**, including a brief notice linking
to this skill, make one current-task title write even if
the intended title already matches the existing wording. This confirms the
requested value and overrides the optional refinement skip above.
Verify the tool returned the current task ID and intended title. If the call
fails, report the failure briefly and finish; do not retry or restart the main
request in that continuation. After success, end without a separate
title-maintenance announcement. The Hook stores counters and confirmation flags
in its own SQLite database, never title text. After the final required write
succeeds, it clears the detailed state and retains only a completion marker.

## Sidebar section routing

When the user explicitly requests title-based sidebar grouping, or Hook context
explicitly says that routing is enabled, read
[references/section-routing.md](references/section-routing.md). Route by the
final title's **Domain** field only, then apply an enabled exact parent-section
mapping when configured. Never derive a section from a full project path,
prompt text, or object description.

For a current-task checkpoint, routing is allowed only after a successful
`set_thread_title` call returns the current `threadId` and new title. Use that
same returned task ID; do not discover a task ID through transcripts, databases,
or a general task search. Call `list_threads` at most once and use only custom
section metadata (`sectionId`, name, and `itemKeys`) to resolve the destination.
When enabled `sectionCapacity` needs to verify an exact overflow candidate, it
may additionally use only task ID and `kind` metadata from that same result;
never inspect, summarize, or infer other task content. Reuse exactly one
matching section; create one only when the enabled configuration permits the
specific Domain. With enabled capacity retention, use the returned current task
ID first, map every remaining target `itemKey` deterministically to its returned
task or conversation ID, then pass those mapped IDs exactly once to
`reorder_section`, including any retained ChatGPT conversation. Return only
confirmed Codex overflow tasks to the configured safe destination. If any
mapping or routing step fails, keep the title result and skip the remaining move
or reorder quietly.

## Configure Hook checkpoints

The default schedule is `2, 4, 10`. When the user explicitly asks to change or
disable checkpoints, read [references/configuration.md](references/configuration.md)
and use the narrowest requested scope. Prefer a project-level configuration for
one repository. Do not change a user environment variable or plugin-global data
unless the user explicitly requests that broader scope.

## Batch workflow

1. Locate the Codex task tools `list_threads`, `read_thread`, and
   `set_thread_title`; when title-based routing is explicitly requested, also
   locate `create_sidebar_section` and `move_thread_to_sidebar_section`. Use the
   automation tool only when the user explicitly requests recurring maintenance.
2. List recent tasks and inspect both pinned and ordinary results within the
   tool's request limit. Follow supported pagination when more results exist.
3. Modify only `kind=codex`. Count but do not rename `kind=chatgpt` entries.
4. Skip active tasks unless the user explicitly includes the current task or a
   named active task.
5. Infer a title from the existing title, summary, project context, and only the
   basename of `cwd`. Read a few recent turns only when the object or purpose is
   still unclear.
6. Treat a title as needing work when it lacks the selected leading marker, has
   incomplete fields, uses a placeholder key, distinguishes duplicates with a
   numeric suffix, or omits the concrete object or purpose.
7. Do not rewrite already compliant titles. If the user asks only for an icon
   or marker migration, change only the leading marker and preserve all fields.
8. Modify at most 10 tasks per batch. Split larger requests into batches and
   re-list after each batch.
9. Count success only when the title tool returns the target and new title. On
   `No Codex thread found`, `no rollout found`, timeout, or empty response, stop
   writes for that target and report the failure. Never bypass task tools with
   databases, session/rollout files, or UI automation.
10. Re-list the affected scope and report scanned, changed, failed, and skipped
    counts plus representative before/after examples.
11. In an explicitly requested "normalize and route" batch, route at most the
    same 10 eligible Codex tasks. A title already compliant but in the wrong
    allowed section may be moved without rewriting its title. Skip active,
    pinned, and ChatGPT tasks by default. Count renamed and moved separately.

## Privacy and safety

- Use only metadata appropriate for display in the user's task list.
- Never place credentials, prompt excerpts, personal names, email addresses,
  full local paths, private hostnames, or private URLs in a title.
- Use a neutral component alias or activity date when project visibility is
  uncertain.
- Preserve clear existing meaning and do not guess identifiers, intent, or
  validation results.
- Routing may inspect sidebar section metadata once only when the user or
  enabled Hook configuration authorizes it. Never persist section IDs, title
  domains, or other sidebar metadata in Hook state.
- Do not archive, delete, pin/unpin, create, or follow up on tasks unless the
  user explicitly asks. The only exception is explicitly enabled bounded
  `sectionCapacity`: it may reorder one resolved target section and return
  confirmed Codex overflow tasks to the default Tasks area. Creating a sidebar
  section is allowed only through the narrow, allowlisted routing configuration.
- Do not edit workspace files or inspect Codex SQLite, session indexes,
  transcripts, or rollout files as substitutes for task tools.
- If a required task tool is unavailable, stop title writes and name the missing
  capability.

## Recurring maintenance

The plugin Hook covers the current active task at its checkpoints and provides
one automatic Stop recovery per checkpoint when a title write remains
unconfirmed. If the tool is unavailable or recovery fails, finish normally and
wait for a later checkpoint or an explicit manual request. Resuming a task does
not renew an exhausted checkpoint's allowance. Idle old tasks are never scanned.

When the user explicitly asks for recurring batch cleanup, prefer updating an
existing automation instead of creating a duplicate. Preserve its schedule,
status, notification policy, and target unless the user requests a change.
Each scheduled batch must still obey the 10-task limit and all task-tool safety
rules above.
