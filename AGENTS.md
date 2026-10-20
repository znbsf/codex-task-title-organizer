# Repository instructions

This repository contains one distributable Codex plugin and its local
marketplace entry.

## Development

- Keep the public package domain-neutral and privacy-conscious.
- Private projects without Jira or Gerrit must fall back to a project name,
  repository name, or `cwd` basename; never generate a placeholder identifier.
- The Hook may persist only hashed session/turn identifiers, small counters, and
  completion flags in its own SQLite database under `PLUGIN_DATA`. It may import
  its legacy JSON state. It must not read or persist prompt text, transcripts,
  rollouts, Codex databases, or full paths.
- Keep current-task Hook behavior separate from cross-task batch maintenance.
- Sidebar routing is opt-in and must use only a successful current title result
  plus sidebar section metadata. It may create a section only for an explicit,
  bounded allowlist; never persist section IDs or route arbitrary title text.
- Preserve the package ID `codex-task-title-organizer` for upgrade compatibility.
- Keep the default checkpoints at `2,4,10`; configuration must remain bounded,
  fail-open, and non-replaying when a schedule changes mid-session.
- Keep color blocks as the default marker style. Semantic icons or custom
  markers require an explicit request or project instruction, and titles use
  exactly one leading marker.
- Run the unit tests on every Hook, manifest, marketplace, or skill change.

## Installation

Do not install anything merely because this repository was opened. When the user
explicitly asks to install it, read `INSTALL.md`, inspect both manifests and the
Hook command, verify Python 3, then use the supported plugin CLI.

Never bypass the required user review and trust step for a command Hook. Do not
edit Codex databases, session indexes, transcripts, or rollout files as an
installation shortcut.
