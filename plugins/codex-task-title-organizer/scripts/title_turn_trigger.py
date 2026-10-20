#!/usr/bin/env python3
"""Request title maintenance and verify receipts without persisting title text."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CHECKPOINTS = (2, 4, 10)
CHECKPOINT_ENV = "CODEX_TASK_TITLE_CHECKPOINTS"
SECTION_ROUTING_ENV = "CODEX_TASK_TITLE_SECTION_ROUTING"
PROJECT_CONFIG_RELATIVE = Path(".codex") / "task-title-organizer.json"
PLUGIN_DATA_CONFIG_NAME = "config.json"
MAX_CONFIG_BYTES = 16 * 1024
MAX_CHECKPOINTS = 8
MAX_CHECKPOINT_VALUE = 50
DEFAULT_SECTION_PREFIX = "🗂️"
DEFAULT_MAX_SECTIONS = 12
DEFAULT_MAX_SECTION_ITEMS = 10
DEFAULT_SECTION_OVERFLOW_DESTINATION = "default"
DISABLED_SECTION_CAPACITY = (
    False,
    DEFAULT_MAX_SECTION_ITEMS,
    DEFAULT_SECTION_OVERFLOW_DESTINATION,
)
MAX_SECTION_PREFIX_LENGTH = 16
MAX_SECTION_COUNT = 20
MAX_SECTION_ITEMS = 50
MAX_ROUTED_DOMAINS = 16
MAX_ROUTED_DOMAIN_LENGTH = 40
MAX_SECTION_DOMAIN_MAPPINGS = 16
MAX_SEEN_TURNS = 64
MAX_TITLE_TOOL_COMPLETION_OBSERVATIONS = MAX_CHECKPOINTS
SCHEMA_VERSION = 7
STATE_DATABASE_NAME = "title-state.sqlite3"
TITLE_TOOL_NAMES = (
    "mcp__codex_app__set_thread_title",
    "codex_app__set_thread_title",
    "mcp__codex_tui__set_thread_title",
    "codex_tui__set_thread_title",
    # Dynamic-tool Hook names concatenate namespace and name without __.
    "codex_appset_thread_title",
    "codex_tuiset_thread_title",
)

SectionDomainMap = tuple[tuple[str, str], ...]
SectionCapacity = tuple[bool, int, str]
SectionRoutingConfig = tuple[
    bool,
    str,
    bool,
    tuple[str, ...],
    SectionDomainMap,
    int,
    SectionCapacity,
    str,
]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _normalize_checkpoints(value: Any) -> tuple[int, ...] | None:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        raw_items: Any = [item.strip() for item in text.split(",")]
    elif isinstance(value, (list, tuple)):
        raw_items = value
    else:
        return None

    parsed: list[int] = []
    for item in raw_items:
        if isinstance(item, bool):
            return None
        if isinstance(item, int):
            checkpoint = item
        elif isinstance(item, str) and item.isdigit():
            checkpoint = int(item)
        else:
            return None
        if not 1 <= checkpoint <= MAX_CHECKPOINT_VALUE:
            return None
        parsed.append(checkpoint)

    if not parsed or len(parsed) > MAX_CHECKPOINTS or len(set(parsed)) != len(parsed):
        return None
    return tuple(sorted(parsed))


def _parse_config(payload: Any, source: str) -> tuple[bool, tuple[int, ...], str] | None:
    if not isinstance(payload, dict):
        return None
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        return None
    if not enabled:
        return False, (), source
    checkpoints = _normalize_checkpoints(payload.get("checkpoints"))
    if checkpoints is None:
        return None
    return True, checkpoints, source


def _read_json_file(path: Path) -> Any | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _read_config_file(path: Path, source: str) -> tuple[bool, tuple[int, ...], str] | None:
    payload = _read_json_file(path)
    if payload is None:
        return None
    return _parse_config(payload, source)


def _project_config_path(payload: dict[str, Any]) -> Path | None:
    cwd_text = payload.get("cwd")
    if not isinstance(cwd_text, str) or not cwd_text:
        return None
    cwd = Path(cwd_text)
    if not cwd.is_absolute():
        return None
    return cwd / PROJECT_CONFIG_RELATIVE


def _load_config(
    payload: dict[str, Any], data_root: Path
) -> tuple[bool, tuple[int, ...], str]:
    project_path = _project_config_path(payload)
    if project_path is not None:
        project = _read_config_file(project_path, "project")
        if project is not None:
            return project

    env_value = os.environ.get(CHECKPOINT_ENV)
    if env_value is not None:
        if env_value.strip().lower() in {"off", "false", "disabled", "none"}:
            return False, (), "environment"
        checkpoints = _normalize_checkpoints(env_value)
        if checkpoints is not None:
            return True, checkpoints, "environment"

    plugin_data = _read_config_file(
        data_root / PLUGIN_DATA_CONFIG_NAME, "plugin-data"
    )
    if plugin_data is not None:
        return plugin_data

    return True, DEFAULT_CHECKPOINTS, "default"


def _normalize_section_prefix(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    prefix = value.strip()
    if (
        not prefix
        or len(prefix) > MAX_SECTION_PREFIX_LENGTH
        or "｜" in prefix
        or "\r" in prefix
        or "\n" in prefix
    ):
        return None
    return prefix


def _normalize_max_sections(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= MAX_SECTION_COUNT:
        return None
    return value


def _normalize_max_section_items(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= MAX_SECTION_ITEMS:
        return None
    return value


def _normalize_routed_domain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    domain = value.strip()
    if (
        not domain
        or len(domain) > MAX_ROUTED_DOMAIN_LENGTH
        or "｜" in domain
        or "/" in domain
        or "\\" in domain
        or "://" in domain
        or "@" in domain
        or "\r" in domain
        or "\n" in domain
    ):
        return None
    return domain


def _normalize_routed_domains(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    domains: list[str] = []
    for item in value:
        domain = _normalize_routed_domain(item)
        if domain is None:
            return None
        if domain not in domains:
            domains.append(domain)
    if len(domains) > MAX_ROUTED_DOMAINS:
        return None
    return tuple(domains)


def _normalize_section_domain_map(value: Any) -> SectionDomainMap | None:
    if value is None:
        return ()
    if not isinstance(value, dict) or len(value) > MAX_SECTION_DOMAIN_MAPPINGS:
        return None
    mappings: list[tuple[str, str]] = []
    normalized_sources: set[str] = set()
    for source, target in value.items():
        normalized_source = _normalize_routed_domain(source)
        normalized_target = _normalize_routed_domain(target)
        if normalized_source is None or normalized_target is None:
            return None
        if normalized_source in normalized_sources:
            return None
        normalized_sources.add(normalized_source)
        mappings.append((normalized_source, normalized_target))
    if any(target in normalized_sources for _, target in mappings):
        return None
    return tuple(mappings)


def _parse_section_capacity(value: Any) -> SectionCapacity | None:
    if value is None:
        return DISABLED_SECTION_CAPACITY
    if not isinstance(value, dict):
        return None

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        return None
    if not enabled:
        return DISABLED_SECTION_CAPACITY

    max_items = _normalize_max_section_items(
        value.get("maxItems", DEFAULT_MAX_SECTION_ITEMS)
    )
    overflow_destination = value.get(
        "overflowDestination", DEFAULT_SECTION_OVERFLOW_DESTINATION
    )
    if (
        max_items is None
        or overflow_destination != DEFAULT_SECTION_OVERFLOW_DESTINATION
    ):
        return None
    return True, max_items, overflow_destination


def _parse_section_routing_value(
    routing: Any, source: str
) -> SectionRoutingConfig | None:
    if not isinstance(routing, dict):
        return None
    enabled = routing.get("enabled", False)
    if not isinstance(enabled, bool):
        return None
    if not enabled:
        return (
            False,
            DEFAULT_SECTION_PREFIX,
            False,
            (),
            (),
            DEFAULT_MAX_SECTIONS,
            DISABLED_SECTION_CAPACITY,
            source,
        )

    prefix = _normalize_section_prefix(
        routing.get("sectionPrefix", DEFAULT_SECTION_PREFIX)
    )
    max_sections = _normalize_max_sections(
        routing.get("maxSections", DEFAULT_MAX_SECTIONS)
    )
    create_missing = routing.get("createMissingSections", False)
    if not isinstance(create_missing, bool):
        return None
    raw_domains = routing.get("allowedDomains", [])
    domains = _normalize_routed_domains(raw_domains)
    domain_map = _normalize_section_domain_map(routing.get("sectionDomainMap"))
    section_capacity = _parse_section_capacity(routing.get("sectionCapacity"))
    if (
        prefix is None
        or max_sections is None
        or domains is None
        or domain_map is None
        or section_capacity is None
    ):
        return None
    # Creating a new section from arbitrary title text would be surprising and
    # vulnerable to untrusted title data. It is permitted only for an explicit,
    # bounded domain allowlist.
    if create_missing and not domains:
        return None
    if domain_map and (
        not domains
        or any(
            source_domain not in domains or target_domain not in domains
            for source_domain, target_domain in domain_map
        )
    ):
        return None
    return (
        True,
        prefix,
        create_missing,
        domains,
        domain_map,
        max_sections,
        section_capacity,
        source,
    )


def _parse_section_routing(
    payload: Any, source: str
) -> SectionRoutingConfig | None:
    if not isinstance(payload, dict) or "sectionRouting" not in payload:
        return None
    return _parse_section_routing_value(payload["sectionRouting"], source)


def _parse_section_routing_environment(
    value: str | None,
) -> SectionRoutingConfig | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"off", "false", "disabled", "none"}:
        return (
            False,
            DEFAULT_SECTION_PREFIX,
            False,
            (),
            (),
            DEFAULT_MAX_SECTIONS,
            DISABLED_SECTION_CAPACITY,
            "environment",
        )
    if normalized in {"domain", "on", "true", "enabled"}:
        return (
            True,
            DEFAULT_SECTION_PREFIX,
            False,
            (),
            (),
            DEFAULT_MAX_SECTIONS,
            DISABLED_SECTION_CAPACITY,
            "environment",
        )
    try:
        return _parse_section_routing_value(json.loads(value), "environment")
    except json.JSONDecodeError:
        return None


def _read_section_routing_file(
    path: Path, source: str
) -> SectionRoutingConfig | None:
    payload = _read_json_file(path)
    if payload is None:
        return None
    return _parse_section_routing(payload, source)


def _load_section_routing(
    payload: dict[str, Any], data_root: Path
) -> SectionRoutingConfig:
    project_path = _project_config_path(payload)
    if project_path is not None:
        project = _read_section_routing_file(project_path, "project")
        if project is not None:
            return project

    environment = _parse_section_routing_environment(
        os.environ.get(SECTION_ROUTING_ENV)
    )
    if environment is not None:
        return environment

    plugin_data = _read_section_routing_file(
        data_root / PLUGIN_DATA_CONFIG_NAME, "plugin-data"
    )
    if plugin_data is not None:
        return plugin_data

    return (
        False,
        DEFAULT_SECTION_PREFIX,
        False,
        (),
        (),
        DEFAULT_MAX_SECTIONS,
        DISABLED_SECTION_CAPACITY,
        "default",
    )


def _default_state(checkpoints: tuple[int, ...]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_count": 0,
        "seen_turns": [],
        "fired_thresholds": [],
        "checkpoint_signature": list(checkpoints),
        "pending_title_tool_completions": [],
        "title_tool_completion_observations": [],
        "title_tool_results": [],
        "title_write_confirmed": False,
        "stop_recovery_turn_token": None,
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _threshold_set(value: Any) -> set[int]:
    if not isinstance(value, list):
        return set()
    parsed: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            threshold = item
        elif isinstance(item, str) and item.isdigit():
            threshold = int(item)
        else:
            continue
        if 1 <= threshold <= MAX_CHECKPOINT_VALUE:
            parsed.add(threshold)
    return parsed


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _checkpoint_turn_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        turn_token = item.get("turn_token")
        checkpoint = item.get("checkpoint")
        if (
            not _is_digest(turn_token)
            or isinstance(checkpoint, bool)
            or not isinstance(checkpoint, int)
            or not 1 <= checkpoint <= MAX_CHECKPOINT_VALUE
        ):
            continue
        record = {"turn_token": turn_token, "checkpoint": checkpoint}
        if record not in records:
            records.append(record)
    return records[-MAX_TITLE_TOOL_COMPLETION_OBSERVATIONS:]


def _load_state(path: Path, checkpoints: tuple[int, ...]) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeError):
        return _default_state(checkpoints)
    return _normalize_state(loaded, checkpoints)


def _title_tool_results(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {"turn_token": item["turn_token"], "success": item["success"]}
        for item in value
        if isinstance(item, dict)
        and _is_digest(item.get("turn_token"))
        and isinstance(item.get("success"), bool)
    ][-MAX_SEEN_TURNS:]


def _normalize_state(loaded: Any, checkpoints: tuple[int, ...]) -> dict[str, Any]:
    if not isinstance(loaded, dict) or loaded.get("schema_version") not in (2, 3, 4, 5, 6, 7):
        return _default_state(checkpoints)

    state = _default_state(checkpoints)
    state["prompt_count"] = _nonnegative_int(loaded.get("prompt_count"))
    state["seen_turns"] = _string_list(loaded.get("seen_turns"))[-MAX_SEEN_TURNS:]
    fired = _threshold_set(loaded.get("fired_thresholds"))

    previous_signature = (
        _normalize_checkpoints(loaded.get("checkpoint_signature"))
        if loaded.get("schema_version") in (5, 6, 7)
        else None
    )
    if previous_signature != checkpoints:
        # A newly introduced checkpoint must not replay after its prompt has
        # already passed. Future checkpoints in the new schedule remain eligible.
        fired.update(
            checkpoint
            for checkpoint in checkpoints
            if checkpoint <= state["prompt_count"]
        )

    state["fired_thresholds"] = sorted(fired)
    if loaded.get("schema_version") in (5, 6, 7):
        state["pending_title_tool_completions"] = _checkpoint_turn_records(
            loaded.get("pending_title_tool_completions")
        )
        state["title_tool_completion_observations"] = _checkpoint_turn_records(
            loaded.get("title_tool_completion_observations")
        )
    if loaded.get("schema_version") in (6, 7):
        state["title_tool_results"] = _title_tool_results(loaded.get("title_tool_results"))
        state["title_write_confirmed"] = loaded.get("title_write_confirmed") is True
        recovery_turn = loaded.get("stop_recovery_turn_token")
        if _is_digest(recovery_turn):
            state["stop_recovery_turn_token"] = recovery_turn
    if state["prompt_count"] >= checkpoints[-1] and (
        loaded.get("schema_version") != SCHEMA_VERSION
        or previous_signature != checkpoints
    ):
        # Earlier versions did not require a receipt at the final checkpoint.
        # A success from prompt 2 cannot retire a task already past prompt 10.
        state["title_write_confirmed"] = False
    return state


def _section_routing_context(
    enabled: bool,
    section_prefix: str,
    create_missing_sections: bool,
    allowed_domains: tuple[str, ...],
    section_domain_map: SectionDomainMap,
    max_sections: int,
    section_capacity: SectionCapacity,
    source: str,
) -> str:
    if not enabled:
        return ""

    prefix_with_space = f"{section_prefix} "
    if allowed_domains:
        allowed_domain_text = ", ".join(f"`{domain}`" for domain in allowed_domains)
        domain_rule = (
            "Route only when the parsed Domain exactly matches this allowlist: "
            f"{allowed_domain_text}."
        )
    else:
        domain_rule = (
            "No creation allowlist is configured, so reuse an existing exact section "
            "only; do not create a new section."
        )

    if section_domain_map:
        mapping_text = ", ".join(
            f"`{source_domain}` -> `{target_domain}`"
            for source_domain, target_domain in section_domain_map
        )
        mapping_rule = (
            "Before resolving the section, apply only this exact, configuration-owned "
            f"Domain-to-section mapping: {mapping_text}. All other allowlisted Domains "
            "map to themselves. Apply at most one mapping; never chain or remap its target. "
        )
    else:
        mapping_rule = "Use the parsed Domain itself as the target section Domain. "

    if create_missing_sections:
        creation_rule = (
            "If that exact custom section is absent, create it only when the mapped "
            "target Domain matched the allowlist and fewer than "
            f"{max_sections} custom sections start with `{prefix_with_space}`."
        )
    else:
        creation_rule = "Do not create a missing sidebar section."

    capacity_enabled, max_section_items, _ = section_capacity
    if capacity_enabled:
        capacity_rule = (
            " Section capacity retention is enabled. Treat target `itemKeys` as opaque "
            "sidebar-order keys, not `threadIds`. Build a planned order from the returned "
            "current `threadId` first, followed by each other target item once in its "
            "existing order. Determine whether the current task is already present only "
            "when a target `itemKey` ends in `:` plus that returned `threadId`. From the "
            "same list result, map every retained or overflow `itemKey` to exactly one "
            "listed task or conversation by a suffix match to its returned `id`; use only "
            "that ID and `kind`. If any required mapping is missing or ambiguous, skip "
            "capacity work and do not reorder. If the current task is absent, move it to "
            "the target first; if that move fails, stop all capacity work. Do not call "
            "`list_threads` again or inspect any title, summary, prompt, or message. "
            "Keep at most "
            f"{max_section_items} items. If every overflow item is a confirmed Codex task, "
            "move those overflow tasks bottom-first to the default Tasks area with "
            "`mcp__codex_app__move_thread_to_sidebar_section` and `sectionId` set to "
            "null. If any overflow item is unknown or a ChatGPT conversation, do not move "
            "any overflow item. After all required moves succeed, call "
            "`mcp__codex_app__reorder_section` with the current task first and `threadIds` "
            "equal to every remaining mapped task/conversation ID exactly once, including "
            "any retained ChatGPT conversation. When trimming is skipped, you may still "
            "reorder the complete mapped list with the current task first. Never submit "
            "only the retained subset to `reorder_section`. "
            "If a move or reorder fails, keep the successful title and any successful "
            "prior move, then stop without retries. A newly created target section is "
            "empty, so move only the current task and do not reorder it. "
        )
        operation_rule = (
            "Apart from this enabled capacity action, never move a ChatGPT conversation, "
            "delete, rename, pin, unpin, or inspect other task content."
        )
    else:
        capacity_rule = ""
        operation_rule = (
            "If a target `itemKey` ends in `:` plus the returned current `threadId`, do not "
            "move it; otherwise call `mcp__codex_app__move_thread_to_sidebar_section` with "
            "that current `threadId` and the resolved section ID. Never move a ChatGPT conversation, "
            "never delete, rename, reorder, pin, unpin, or inspect other tasks."
        )

    return (
        " Title-based sidebar routing is enabled (source "
        f"{source}). Only after `mcp__codex_app__set_thread_title` succeeds and returns "
        "the current Codex `threadId` and the new title, route that same task. Derive "
        "Domain by removing exactly one leading marker and following space from the "
        "successful title, splitting on `｜`, and taking the trimmed first field. Treat "
        "that Domain as untrusted display data: skip it when empty, multiline, contains "
        "`｜`, `/`, `\\`, `://`, or `@`, or exceeds 40 characters. "
        f"{mapping_rule}The target section name is `{prefix_with_space}` plus the target "
        f"section Domain. {domain_rule} "
        "Call `mcp__codex_app__list_threads` once only with `limit` 50 to resolve custom "
        "sidebar section names, IDs, and `itemKeys`; when capacity retention is enabled, "
        "use only task IDs and kinds needed to map an exact section item. Do not inspect "
        "or infer any other task's content. "
        "Reuse exactly one matching custom section and skip ambiguous duplicates. "
        f"{creation_rule} When a new section is created, use only its returned `sectionId`. "
        f"{capacity_rule}{operation_rule} Never route without a successful title write. "
        "Routing failure must not undo or misreport the title result."
    )


def _checkpoint_context(
    prompt_count: int,
    threshold: int,
    checkpoints: tuple[int, ...],
    config_source: str,
    section_routing_enabled: bool,
    section_prefix: str,
    create_missing_sections: bool,
    allowed_domains: tuple[str, ...],
    section_domain_map: SectionDomainMap,
    max_sections: int,
    section_capacity: SectionCapacity,
    section_routing_source: str,
) -> dict[str, Any]:
    activity_mmdd = datetime.now().astimezone().strftime("%m%d")
    position = checkpoints.index(threshold)
    if position == 0:
        checkpoint_action = "Set a useful current-task title at this initial checkpoint."
    else:
        checkpoint_action = (
            "This is a refinement checkpoint. Rename only when the current title is "
            "missing or inaccurate, or the object and purpose can now be stated "
            "materially more clearly; otherwise skip."
        )
    if position == len(checkpoints) - 1:
        checkpoint_action = (
            "Set a useful current-task title at this initial checkpoint. "
            if position == 0 else ""
        ) + (
            "This is the final refresh checkpoint. Review the current project, work "
            "type, concrete object, and purpose, then set the intended title once "
            "even if its wording is unchanged. A successful receipt completes title "
            "maintenance for this task; do not skip this confirmation."
        )

    schedule = ",".join(str(item) for item in checkpoints)
    routing_context = _section_routing_context(
        section_routing_enabled,
        section_prefix,
        create_missing_sections,
        allowed_domains,
        section_domain_map,
        max_sections,
        section_capacity,
        section_routing_source,
    )
    context = (
        "Codex Task Title Organizer checkpoint: this is user prompt "
        f"{prompt_count} observed after the plugin was enabled (checkpoint "
        f"{threshold}; configured schedule {schedule}; source {config_source}). "
        f"{checkpoint_action} Finish the user's main request first, then explicitly "
        "use $organize-codex-task-titles in Current-task checkpoint mode before "
        "ending this same foreground turn. Generate the title only from current-task "
        "context using `Marker Domain｜Project/repository key｜Work type｜Concrete "
        "object: purpose or symptom`, matching the task language. Honor an explicit "
        "marker style or active project instruction; otherwise preserve an already valid "
        "existing marker style, using color blocks only for a new or noncompliant title. When no "
        "Jira, Gerrit, issue, PR, or change key exists, prefer the project name, "
        "repository name, or cwd basename; never emit `NO-ID`. Only use local "
        f"activity date `{activity_mmdd}` when no stable project or repository context "
        "exists. Call `mcp__codex_app__set_thread_title` without threadId only when this is "
        "a confirmed Codex task and the tool is available. Do not list or read other "
        "tasks, create a task, send a follow-up, inspect persisted prompts, transcripts, "
        "rollouts, databases, or session files, or use UI automation. Skip quietly when "
        "the task kind or required tool is unsuitable."
        f"{routing_context}"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def _handle_user_prompt(
    payload: dict[str, Any],
    state: dict[str, Any],
    checkpoints: tuple[int, ...],
    config_source: str,
    section_routing_enabled: bool,
    section_prefix: str,
    create_missing_sections: bool,
    allowed_domains: tuple[str, ...],
    section_domain_map: SectionDomainMap,
    max_sections: int,
    section_capacity: SectionCapacity,
    section_routing_source: str,
) -> dict[str, Any] | None:
    turn_id = payload.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        return None
    turn_token = _digest(turn_id)
    seen = _string_list(state.get("seen_turns"))
    if turn_token in seen:
        return None

    seen.append(turn_token)
    state["seen_turns"] = seen[-MAX_SEEN_TURNS:]
    # User prompts count checkpoints; resumed turns do not add to that count.
    # Results not consumed by Stop belong to the previous interaction.
    state["pending_title_tool_completions"] = []
    state["title_tool_results"] = []
    state["prompt_count"] = _nonnegative_int(state.get("prompt_count")) + 1

    fired = _threshold_set(state.get("fired_thresholds"))
    eligible = [
        item
        for item in checkpoints
        if state["prompt_count"] >= item and item not in fired
    ]
    if not eligible:
        state["fired_thresholds"] = sorted(fired)
        return None

    threshold = eligible[0]
    # Only an emitted checkpoint grants another automatic recovery attempt.
    state["stop_recovery_turn_token"] = None
    if threshold == checkpoints[-1]:
        state["title_write_confirmed"] = False
    fired.add(threshold)
    state["fired_thresholds"] = sorted(fired)
    pending = _checkpoint_turn_records(
        state.get("pending_title_tool_completions")
    )
    pending.append({"turn_token": turn_token, "checkpoint": threshold})
    state["pending_title_tool_completions"] = pending[
        -MAX_TITLE_TOOL_COMPLETION_OBSERVATIONS:
    ]
    return _checkpoint_context(
        state["prompt_count"],
        threshold,
        checkpoints,
        config_source,
        section_routing_enabled,
        section_prefix,
        create_missing_sections,
        allowed_domains,
        section_domain_map,
        max_sections,
        section_capacity,
        section_routing_source,
    )


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def _title_receipt(value: Any) -> dict[str, Any] | None:
    """Accept the native result or the documented MCP structured/text envelope."""
    result = _json_object(value)
    if result is None or result.get("isError") is True or result.get("error") is not None:
        return None
    if "structuredContent" in result:
        result = _json_object(result["structuredContent"])
    elif "content" in result:
        content = result["content"]
        if (
            not isinstance(content, list)
            or len(content) != 1
            or not isinstance(content[0], dict)
            or content[0].get("type") != "text"
        ):
            return None
        result = _json_object(content[0].get("text"))
    if result is None or result.get("isError") is True or result.get("error") is not None:
        return None
    return result


def _handle_title_tool_completion(payload: dict[str, Any], state: dict[str, Any]) -> bool:
    """Verify the current task's write and save its result for that turn's Stop."""
    if payload.get("tool_name") not in TITLE_TOOL_NAMES:
        return False

    arguments = _json_object(payload.get("tool_input"))
    session_id = payload.get("session_id")
    if (
        arguments is None
        or not isinstance(session_id, str)
        or not session_id
        or arguments.get("source", "codex") != "codex"
        or arguments.get("threadId") not in (None, session_id)
        or not isinstance(arguments.get("title"), str)
        or not arguments["title"].strip()
    ):
        return False
    turn_id = payload.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        return False
    turn_token = _digest(turn_id)

    receipt = _title_receipt(payload.get("tool_response"))
    success = (
        receipt is not None
        and receipt.get("threadId") == session_id
        and receipt.get("title") == arguments["title"]
    )
    results = [
        record for record in state["title_tool_results"]
        if record["turn_token"] != turn_token
    ]
    results.append({"turn_token": turn_token, "success": success})
    state["title_tool_results"] = results[-MAX_SEEN_TURNS:]
    # Preserve early confirmation if a new user message interrupts before Stop.
    # Final confirmation still belongs to the actual stopping turn.
    if state["prompt_count"] < state["checkpoint_signature"][-1]:
        state["title_write_confirmed"] = success

    pending = _checkpoint_turn_records(state.get("pending_title_tool_completions"))
    matched = [record for record in pending if record["turn_token"] == turn_token]
    observations = _checkpoint_turn_records(
        state.get("title_tool_completion_observations")
    )
    for record in matched:
        if record not in observations:
            observations.append(record)
    state["title_tool_completion_observations"] = observations[
        -MAX_TITLE_TOOL_COMPLETION_OBSERVATIONS:
    ]
    state["pending_title_tool_completions"] = [
        record for record in pending if record["turn_token"] != turn_token
    ]
    return True


def _handle_stop(
    payload: dict[str, Any], state: dict[str, Any], checkpoints: tuple[int, ...]
) -> dict[str, Any] | None:
    turn_id = payload.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        return None
    turn_token = _digest(turn_id)
    # Stop identifies the actual foreground turn, including a continuation
    # without UserPromptSubmit. Other turns' late receipts cannot confirm it.
    results = state["title_tool_results"]
    for record in results:
        if record["turn_token"] == turn_token:
            state["title_write_confirmed"] = record["success"]
    state["title_tool_results"] = [
        record for record in results if record["turn_token"] != turn_token
    ]
    checkpoint_records = (
        state["pending_title_tool_completions"]
        + state["title_tool_completion_observations"]
    )
    if (
        state["title_write_confirmed"]
        or state["prompt_count"] not in checkpoints
        # Schedule changes mark past thresholds as fired without emitting them.
        or not any(
            record["checkpoint"] == state["prompt_count"]
            for record in checkpoint_records
        )
    ):
        return None
    if payload.get("stop_hook_active") is True:
        if state["stop_recovery_turn_token"] == turn_token:
            return {
                "systemMessage": (
                    "Title confirmation is incomplete; retry at a later checkpoint or manually."
                )
            }
        return None
    if (
        payload.get("stop_hook_active") is not False
        or state["stop_recovery_turn_token"] is not None
    ):
        return None

    state["stop_recovery_turn_token"] = turn_token
    skill_path = (
        Path(__file__).resolve().parents[1]
        / "skills" / "organize-codex-task-titles" / "SKILL.md"
    )
    return {
        "decision": "block",
        "reason": (
            "Use the current context to run "
            "[$codex-task-title-organizer:organize-codex-task-titles]"
            f"({skill_path.as_posix()})"
        ),
    }


def main() -> int:
    data_root_text = os.environ.get("PLUGIN_DATA")
    if not data_root_text:
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    # Codex includes these fields only for hooks running inside a subagent.
    # Skip before loading configuration or opening the state database.
    if payload.get("agent_id") or payload.get("agent_type"):
        return 0
    # Ephemeral side chats have no transcript path and cannot be renamed.
    # Check only the metadata; never open or persist the transcript path.
    if payload.get("transcript_path") is None:
        return 0
    event_name = payload.get("hook_event_name")
    if event_name not in ("UserPromptSubmit", "PostToolUse", "Stop"):
        return 0

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0

    data_root = Path(data_root_text)
    try:
        enabled, checkpoints, config_source = _load_config(payload, data_root)
        if not enabled:
            return 0
        session_key = _digest(session_id)
        legacy_path = data_root / f"{session_key}.json"
        database_path = data_root / STATE_DATABASE_NAME
        if event_name == "PostToolUse" and payload.get("tool_name") not in TITLE_TOOL_NAMES:
            return 0
        if event_name != "UserPromptSubmit" and not (
            database_path.is_file() or legacy_path.is_file()
        ):
            if event_name == "Stop":
                print("{}")
            return 0

        data_root.mkdir(parents=True, exist_ok=True)
        output: dict[str, Any] | None = None
        with closing(sqlite3.connect(database_path, timeout=1.0)) as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "session_key TEXT PRIMARY KEY, done INTEGER NOT NULL, state_json TEXT, "
                "CHECK ((done = 0 AND state_json IS NOT NULL) "
                "OR (done = 1 AND state_json IS NULL)))"
            )
            with database:
                # Serialize the complete read/modify/write, including first import.
                database.execute("BEGIN IMMEDIATE")
                row = database.execute(
                    "SELECT done, state_json FROM sessions WHERE session_key = ?",
                    (session_key,),
                ).fetchone()
                if row is not None and row[0]:
                    if event_name == "Stop":
                        print("{}")
                    return 0
                if row is None:
                    if event_name != "UserPromptSubmit" and not legacy_path.is_file():
                        if event_name == "Stop":
                            print("{}")
                        return 0
                    # Legacy files remain untouched for rollback. Once imported,
                    # SQLite is authoritative even if an old Hook writes JSON.
                    state = _load_state(legacy_path, checkpoints)
                else:
                    state = _normalize_state(json.loads(row[1]), checkpoints)

                done = False
                if event_name == "PostToolUse":
                    if not _handle_title_tool_completion(payload, state):
                        return 0
                elif event_name == "Stop":
                    output = _handle_stop(payload, state, checkpoints)
                    done = (
                        state["title_write_confirmed"]
                        and state["prompt_count"] >= checkpoints[-1]
                    )
                else:
                    output = _handle_user_prompt(
                        payload, state, checkpoints, config_source,
                        *_load_section_routing(payload, data_root),
                    )

                state_text = None if done else json.dumps(
                    state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                if row is None or row != (int(done), state_text):
                    database.execute(
                        "INSERT INTO sessions (session_key, done, state_json) VALUES (?, ?, ?) "
                        "ON CONFLICT(session_key) DO UPDATE SET "
                        "done = excluded.done, state_json = excluded.state_json",
                        (session_key, int(done), state_text),
                    )
        if output is not None or event_name == "Stop":
            # ASCII output is portable on Windows legacy consoles; JSON parsing
            # restores the original Unicode title instructions.
            print(json.dumps(output or {}, ensure_ascii=True, separators=(",", ":")))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        # Hooks must fail open: title maintenance is secondary to the user task.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
