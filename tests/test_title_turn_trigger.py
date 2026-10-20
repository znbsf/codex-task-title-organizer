from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "codex-task-title-organizer"
SCRIPT = PLUGIN_ROOT / "scripts" / "title_turn_trigger.py"
HOOKS_CONFIG = PLUGIN_ROOT / "hooks" / "hooks.json"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
SKILL = PLUGIN_ROOT / "skills" / "organize-codex-task-titles" / "SKILL.md"
CONFIG_REFERENCE = SKILL.parent / "references" / "configuration.md"
SECTION_ROUTING_REFERENCE = SKILL.parent / "references" / "section-routing.md"
STYLE_REFERENCE = SKILL.parent / "references" / "title-styles.md"
CHECKPOINT_ENV = "CODEX_TASK_TITLE_CHECKPOINTS"
SECTION_ROUTING_ENV = "CODEX_TASK_TITLE_SECTION_ROUTING"
TITLE_TOOL_NAME = "mcp__codex_app__set_thread_title"


def run_hook(
    data_dir: str,
    payload: dict[str, object],
    extra_env: dict[str, str] | None = None,
) -> str:
    env = os.environ.copy()
    env.pop(CHECKPOINT_ENV, None)
    env.pop(SECTION_ROUTING_ENV, None)
    env["PLUGIN_DATA"] = data_dir
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    return result.stdout.strip()


def prompt_payload(
    number: int,
    prompt: str = "secret prompt",
    *,
    session_id: str = "thr-test",
    cwd: str | None = None,
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "transcript_path": "/unavailable/synthetic-transcript.jsonl",
        "turn_id": f"{session_id}-turn-{number}",
        "cwd": cwd or str(REPO_ROOT),
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
    }


def post_tool_payload(
    number: int,
    *,
    session_id: str = "thr-test",
    tool_name: str = TITLE_TOOL_NAME,
    title: str = "secret generated title",
    cwd: str | None = None,
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "transcript_path": "/unavailable/synthetic-transcript.jsonl",
        "turn_id": f"{session_id}-turn-{number}",
        "cwd": cwd or str(REPO_ROOT),
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_use_id": "secret-tool-use-id",
        "tool_input": {"title": title},
        "tool_response": {"threadId": session_id, "title": title},
    }


def state_path_for(data_dir: str, session_id: str = "thr-test") -> Path:
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(data_dir) / f"{session_key}.json"


def state_row_for(data_dir: str, session_id: str = "thr-test") -> tuple[int, str | None] | None:
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    database = sqlite3.connect(Path(data_dir) / "title-state.sqlite3")
    try:
        return database.execute(
            "SELECT done, state_json FROM sessions WHERE session_key = ?", (session_key,)
        ).fetchone()
    finally:
        database.close()


def state_text_for(data_dir: str, session_id: str = "thr-test") -> str:
    row = state_row_for(data_dir, session_id)
    if row is None or row[0] or row[1] is None:
        raise AssertionError("expected an active SQLite session")
    return row[1]


def stop_payload(number: int, *, active: bool = False) -> dict[str, object]:
    return {
        "session_id": "thr-test",
        "transcript_path": "/unavailable/synthetic-transcript.jsonl",
        "turn_id": f"thr-test-turn-{number}",
        "cwd": str(REPO_ROOT),
        "hook_event_name": "Stop",
        "stop_hook_active": active,
        "last_assistant_message": "secret assistant message",
    }


def write_project_config(project_root: str, payload: dict[str, object]) -> None:
    config_dir = Path(project_root) / ".codex"
    config_dir.mkdir()
    (config_dir / "task-title-organizer.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


class TitleTurnTriggerTests(unittest.TestCase):
    def test_ephemeral_events_do_not_create_state(self) -> None:
        for payload in (prompt_payload(2), post_tool_payload(2), stop_payload(2)):
            with self.subTest(event=payload["hook_event_name"]), tempfile.TemporaryDirectory() as data_dir:
                payload["transcript_path"] = None
                self.assertEqual(run_hook(data_dir, payload), "")
                self.assertEqual(list(Path(data_dir).iterdir()), [])

    def test_ephemeral_events_leave_preexisting_state_unchanged(self) -> None:
        for payload in (prompt_payload(3), post_tool_payload(2), stop_payload(2)):
            with self.subTest(event=payload["hook_event_name"]), tempfile.TemporaryDirectory() as data_dir:
                # Earlier versions could start title maintenance in side chats.
                run_hook(data_dir, prompt_payload(1))
                run_hook(data_dir, prompt_payload(2))
                before = state_row_for(data_dir)
                payload["transcript_path"] = None
                self.assertEqual(run_hook(data_dir, payload), "")
                self.assertEqual(state_row_for(data_dir), before)

    def test_events_without_transcript_metadata_skip_title_maintenance(self) -> None:
        for payload in (prompt_payload(2), post_tool_payload(2), stop_payload(2)):
            with self.subTest(event=payload["hook_event_name"]), tempfile.TemporaryDirectory() as data_dir:
                del payload["transcript_path"]
                self.assertEqual(run_hook(data_dir, payload), "")
                self.assertEqual(list(Path(data_dir).iterdir()), [])

    def test_subagent_events_do_not_create_state(self) -> None:
        for identity in (
            {"agent_id": "child-agent"},
            {"agent_type": "explorer"},
            {"agent_id": "child-agent", "agent_type": "default"},
        ):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as data_dir:
                events = [
                    prompt_payload(1),
                    prompt_payload(2),
                    post_tool_payload(2),
                    stop_payload(2),
                    {**stop_payload(2), "hook_event_name": "SubagentStop"},
                ]
                for payload in events:
                    payload.update(identity)
                    self.assertEqual(run_hook(data_dir, payload), "")
                    self.assertEqual(list(Path(data_dir).iterdir()), [])

    def test_subagent_events_leave_preexisting_state_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            # Older versions could count a child thread as an ordinary task.
            run_hook(data_dir, prompt_payload(1))
            run_hook(data_dir, prompt_payload(2))
            before = state_row_for(data_dir)
            events = [
                prompt_payload(3),
                post_tool_payload(2),
                stop_payload(2),
                {**stop_payload(2), "hook_event_name": "SubagentStop"},
            ]
            for payload in events:
                payload.update(agent_id="child-agent", agent_type="default")
                self.assertEqual(run_hook(data_dir, payload), "")
                self.assertEqual(state_row_for(data_dir), before)

    def test_packaged_hook_command_runs(self) -> None:
        config = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))
        handler = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        command = handler["commandWindows"] if os.name == "nt" else handler["command"]
        post_handler = config["hooks"]["PostToolUse"][0]["hooks"][0]
        post_command = (
            post_handler["commandWindows"]
            if os.name == "nt"
            else post_handler["command"]
        )
        with tempfile.TemporaryDirectory() as data_dir:
            env = os.environ.copy()
            env.pop(CHECKPOINT_ENV, None)
            env.pop(SECTION_ROUTING_ENV, None)
            env["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
            env["PLUGIN_DATA"] = data_dir
            output = ""
            for number in range(1, 3):
                result = subprocess.run(
                    command,
                    input=json.dumps(prompt_payload(number)),
                    text=True,
                    capture_output=True,
                    env=env,
                    shell=True,
                    check=True,
                )
                output = result.stdout.strip()
            parsed = json.loads(output)
            self.assertIn(
                "user prompt 2",
                parsed["hookSpecificOutput"]["additionalContext"],
            )
            result = subprocess.run(
                post_command,
                input=json.dumps(post_tool_payload(2)),
                text=True,
                capture_output=True,
                env=env,
                shell=True,
                check=True,
            )
            self.assertEqual(result.stdout.strip(), "")
            state = json.loads(state_text_for(data_dir))
            self.assertEqual(
                state["title_tool_completion_observations"],
                [
                    {
                        "checkpoint": 2,
                        "turn_token": hashlib.sha256(
                            b"thr-test-turn-2"
                        ).hexdigest(),
                    }
                ],
            )

    def test_default_thresholds_deduplication_and_content_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertEqual(run_hook(data_dir, prompt_payload(1)), "")

            first = json.loads(run_hook(data_dir, prompt_payload(2)))
            first_context = first["hookSpecificOutput"]["additionalContext"]
            self.assertIn("user prompt 2", first_context)
            self.assertIn("initial checkpoint", first_context)
            self.assertIn("configured schedule 2,4,10", first_context)
            self.assertIn(
                "preserve an already valid existing marker style",
                first_context,
            )
            self.assertNotIn("Title-based sidebar routing is enabled", first_context)
            self.assertEqual(run_hook(data_dir, prompt_payload(2)), "")

            self.assertEqual(run_hook(data_dir, prompt_payload(3)), "")
            second = json.loads(run_hook(data_dir, prompt_payload(4)))
            second_context = second["hookSpecificOutput"]["additionalContext"]
            self.assertIn("refinement checkpoint", second_context)
            self.assertIn("materially more clearly", second_context)

            for number in range(5, 10):
                self.assertEqual(run_hook(data_dir, prompt_payload(number)), "")

            third = json.loads(run_hook(data_dir, prompt_payload(10)))
            third_context = third["hookSpecificOutput"]["additionalContext"]
            self.assertIn("final refresh checkpoint", third_context)
            self.assertIn("even if its wording is unchanged", third_context)
            self.assertEqual(run_hook(data_dir, prompt_payload(11)), "")

            state_text = state_text_for(data_dir)
            state = json.loads(state_text)
            self.assertEqual(
                set(state),
                {
                    "schema_version",
                    "prompt_count",
                    "seen_turns",
                    "fired_thresholds",
                    "checkpoint_signature",
                    "pending_title_tool_completions",
                    "title_tool_completion_observations",
                    "title_tool_results",
                    "title_write_confirmed",
                    "stop_recovery_turn_token",
                },
            )
            self.assertEqual(state["schema_version"], 7)
            self.assertEqual(state["checkpoint_signature"], [2, 4, 10])
            self.assertEqual(state["fired_thresholds"], [2, 4, 10])
            self.assertEqual(state["pending_title_tool_completions"], [])
            self.assertEqual(state["title_tool_completion_observations"], [])
            self.assertNotIn("secret prompt", state_text)
            self.assertNotIn("thr-test", state_text)
            self.assertNotIn("turn-2", state_text)
            self.assertNotIn(str(REPO_ROOT), state_text)
            self.assertNotIn("section", state_text)

    def test_matching_post_tool_use_observes_completion_without_title_data(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertEqual(run_hook(data_dir, prompt_payload(1)), "")
            self.assertTrue(run_hook(data_dir, prompt_payload(2)))

            state = json.loads(state_text_for(data_dir))
            expected = {
                "checkpoint": 2,
                "turn_token": hashlib.sha256(b"thr-test-turn-2").hexdigest(),
            }
            self.assertEqual(state["pending_title_tool_completions"], [expected])
            self.assertEqual(state["title_tool_completion_observations"], [])

            payload = post_tool_payload(2, title="secret title from tool payload")
            self.assertEqual(run_hook(data_dir, payload), "")
            self.assertEqual(run_hook(data_dir, payload), "")
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2))), {})

            state_text = state_text_for(data_dir)
            state = json.loads(state_text)
            self.assertEqual(state["prompt_count"], 2)
            self.assertEqual(state["fired_thresholds"], [2])
            self.assertEqual(state["pending_title_tool_completions"], [])
            self.assertEqual(state["title_tool_completion_observations"], [expected])
            self.assertTrue(state["title_write_confirmed"])
            self.assertNotIn("secret title from tool payload", state_text)
            self.assertNotIn("secret-tool-use-id", state_text)
            self.assertNotIn("thr-test", state_text)

    def test_missing_post_tool_completion_is_not_recorded_as_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertEqual(run_hook(data_dir, prompt_payload(1)), "")
            self.assertTrue(run_hook(data_dir, prompt_payload(2)))
            self.assertEqual(run_hook(data_dir, prompt_payload(3)), "")

            state = json.loads(state_text_for(data_dir))
            self.assertEqual(state["pending_title_tool_completions"], [])
            self.assertEqual(state["title_tool_completion_observations"], [])

    def test_nonmatching_post_tool_use_does_not_create_or_change_state(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertEqual(
                run_hook(data_dir, post_tool_payload(2, tool_name="Bash")), ""
            )
            self.assertEqual(list(Path(data_dir).iterdir()), [])

            self.assertEqual(run_hook(data_dir, prompt_payload(1)), "")
            self.assertTrue(run_hook(data_dir, prompt_payload(2)))
            before = state_text_for(data_dir)
            self.assertEqual(
                run_hook(data_dir, post_tool_payload(2, tool_name="Bash")), ""
            )
            self.assertEqual(state_text_for(data_dir), before)

    def test_stop_recovers_missed_write_once_and_preserves_prompt_count(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            run_hook(data_dir, prompt_payload(1))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(1))), {})
            run_hook(data_dir, prompt_payload(2))
            recovery = json.loads(run_hook(data_dir, stop_payload(2)))
            self.assertEqual(recovery["decision"], "block")
            self.assertEqual(
                recovery["reason"],
                "Use the current context to run "
                "[$codex-task-title-organizer:organize-codex-task-titles]"
                f"({SKILL.resolve().as_posix()})",
            )
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2))), {})
            self.assertEqual(run_hook(data_dir, prompt_payload(2)), "")
            exhausted = json.loads(run_hook(data_dir, stop_payload(2, active=True)))
            self.assertNotIn("decision", exhausted)
            self.assertIn("Title confirmation is incomplete", exhausted["systemMessage"])

            run_hook(data_dir, post_tool_payload(2))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2, active=True))), {})
            state_text = state_text_for(data_dir)
            state = json.loads(state_text)
            self.assertEqual(state["prompt_count"], 2)
            self.assertTrue(state["title_write_confirmed"])
            for private_value in ("secret", "thr-test", "turn-2", str(REPO_ROOT)):
                self.assertNotIn(private_value, state_text)

    def test_title_receipts_support_native_and_mcp_envelopes(self) -> None:
        receipt = {"threadId": "thr-test", "title": "secret generated title"}
        responses = [
            receipt,
            json.dumps(receipt),
            {"structuredContent": receipt, "isError": False},
            {"content": [{"type": "text", "text": json.dumps(receipt)}]},
        ]
        for tool_name in (
            TITLE_TOOL_NAME,
            "codex_app__set_thread_title",
            "mcp__codex_tui__set_thread_title",
            "codex_tui__set_thread_title",
            "codex_appset_thread_title",
            "codex_tuiset_thread_title",
        ):
            for response in responses:
                with self.subTest(tool_name=tool_name, response=response):
                    with tempfile.TemporaryDirectory() as data_dir:
                        run_hook(data_dir, prompt_payload(1))
                        run_hook(data_dir, prompt_payload(2))
                        payload = post_tool_payload(2, tool_name=tool_name)
                        payload["tool_response"] = response
                        run_hook(data_dir, payload)
                        self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2))), {})
                        state = json.loads(state_text_for(data_dir))
                        self.assertTrue(state["title_write_confirmed"])

    def test_flattened_dynamic_title_tool_receipt_prevents_stop_recovery(self) -> None:
        # Codex app-server 0.162.1 emits namespace + name in PostToolUse,
        # without the __ separator shown by code mode. Its native result is JSON text.
        matcher = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))[
            "hooks"
        ]["PostToolUse"][0]["matcher"]
        for tool_name in ("codex_appset_thread_title", "codex_tuiset_thread_title"):
            for checkpoint in (2, 10):
                with self.subTest(tool=tool_name, checkpoint=checkpoint):
                    self.assertIsNotNone(re.fullmatch(matcher, tool_name))
                    with tempfile.TemporaryDirectory() as data_dir:
                        for number in range(1, checkpoint + 1):
                            run_hook(data_dir, prompt_payload(number))
                        payload = post_tool_payload(checkpoint, tool_name=tool_name)
                        payload["tool_response"] = json.dumps(payload["tool_response"])
                        run_hook(data_dir, payload)
                        self.assertEqual(
                            json.loads(run_hook(data_dir, stop_payload(checkpoint))), {}
                        )
                        if checkpoint == 10:
                            self.assertEqual(state_row_for(data_dir), (1, None))
                        else:
                            self.assertTrue(json.loads(state_text_for(data_dir))["title_write_confirmed"])
                        run_hook(data_dir, prompt_payload(checkpoint + 1))
                        self.assertEqual(
                            json.loads(run_hook(data_dir, stop_payload(checkpoint + 1))), {}
                        )

    def test_tui_confirmation_prevents_repeated_stop_recovery(self) -> None:
        matcher = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))[
            "hooks"
        ]["PostToolUse"][0]["matcher"]
        for tool_name in ("mcp__codex_tui__set_thread_title", "codex_tui__set_thread_title"):
            with self.subTest(tool_name=tool_name):
                self.assertIsNotNone(re.search(matcher, tool_name))
                with tempfile.TemporaryDirectory() as data_dir:
                    run_hook(data_dir, prompt_payload(1))
                    run_hook(data_dir, prompt_payload(2))
                    self.assertEqual(
                        json.loads(run_hook(data_dir, stop_payload(2)))["decision"], "block"
                    )
                    receipt = post_tool_payload(2, tool_name=tool_name)
                    receipt["tool_response"] = {
                        "content": [{"type": "text", "text": json.dumps(receipt["tool_response"])}],
                        "isError": False,
                    }
                    run_hook(data_dir, receipt)
                    self.assertEqual(
                        json.loads(run_hook(data_dir, stop_payload(2, active=True))), {}
                    )
                    for number in range(3, 10):
                        run_hook(data_dir, prompt_payload(number))
                        self.assertEqual(json.loads(run_hook(data_dir, stop_payload(number))), {})

                    run_hook(data_dir, prompt_payload(10))
                    self.assertEqual(
                        json.loads(run_hook(data_dir, stop_payload(10)))["decision"], "block"
                    )
                    run_hook(data_dir, post_tool_payload(10, tool_name=tool_name))
                    self.assertEqual(
                        json.loads(run_hook(data_dir, stop_payload(10, active=True))), {}
                    )
                    self.assertEqual(state_row_for(data_dir), (1, None))
                    self.assertEqual(run_hook(data_dir, prompt_payload(11)), "")
                    self.assertEqual(json.loads(run_hook(data_dir, stop_payload(11))), {})

    def test_failed_wrong_or_missing_receipts_do_not_confirm_title(self) -> None:
        receipt = {"threadId": "thr-test", "title": "secret generated title"}
        responses = [
            None,
            "No Codex thread found",
            {},
            {**receipt, "isError": True},
            {**receipt, "error": "write failed"},
            {"threadId": "another-thread", "title": receipt["title"]},
            {"threadId": "thr-test", "title": "a different title"},
            {"isError": True, "structuredContent": receipt},
            {"structuredContent": {**receipt, "isError": True}},
            {"content": [{"type": "text", "text": "write failed"}]},
        ]
        for response in responses:
            with self.subTest(response=response):
                with tempfile.TemporaryDirectory() as data_dir:
                    run_hook(data_dir, prompt_payload(1))
                    run_hook(data_dir, prompt_payload(2))
                    payload = post_tool_payload(2)
                    payload["tool_response"] = response
                    run_hook(data_dir, payload)
                    state = json.loads(state_text_for(data_dir))
                    self.assertFalse(state["title_write_confirmed"])
                    self.assertEqual(len(state["title_tool_completion_observations"]), 1)
                    self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2)))["decision"], "block")

    def test_other_task_or_chatgpt_write_cannot_confirm_current_task(self) -> None:
        for overrides in ({"threadId": "another-thread"}, {"source": "chatgpt"}):
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as data_dir:
                    run_hook(data_dir, prompt_payload(1))
                    run_hook(data_dir, prompt_payload(2))
                    payload = post_tool_payload(2)
                    payload["tool_input"].update(overrides)
                    before = state_text_for(data_dir)
                    run_hook(data_dir, payload)
                    self.assertEqual(state_text_for(data_dir), before)
                    self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2)))["decision"], "block")

    def test_successful_title_allows_refinement_skip_but_failed_update_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            run_hook(data_dir, prompt_payload(1))
            run_hook(data_dir, prompt_payload(2))
            run_hook(data_dir, post_tool_payload(2))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2))), {})
            run_hook(data_dir, prompt_payload(3))
            run_hook(data_dir, prompt_payload(4))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(4))), {})
            failed = post_tool_payload(4)
            failed["tool_response"] = {"isError": True}
            run_hook(data_dir, failed)
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(4)))["decision"], "block")
            run_hook(data_dir, post_tool_payload(4))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(4, active=True))), {})

    def test_missing_or_failed_title_tool_recovers_once_per_checkpoint(self) -> None:
        for checkpoints in ((2, 4, 10), (3, 6)):
            for failed_write in (False, True):
                with self.subTest(checkpoints=checkpoints, failed_write=failed_write):
                    with tempfile.TemporaryDirectory() as data_dir:
                        env = {CHECKPOINT_ENV: ",".join(map(str, checkpoints))}
                        for number in range(1, checkpoints[-1] + 4):
                            run_hook(data_dir, prompt_payload(number), env)
                            if failed_write:
                                failed = post_tool_payload(number)
                                failed["tool_response"] = {"isError": True}
                                run_hook(data_dir, failed, env)
                            output = json.loads(run_hook(data_dir, stop_payload(number), env))
                            if number in checkpoints:
                                self.assertEqual(output["decision"], "block")
                                # A recovery can have a new turn ID without a new prompt.
                                resumed = number + 100
                                continuation = json.loads(
                                    run_hook(data_dir, stop_payload(resumed, active=True), env)
                                )
                                self.assertNotIn("decision", continuation)
                                self.assertEqual(
                                    json.loads(run_hook(data_dir, stop_payload(resumed), env)), {}
                                )
                                self.assertEqual(
                                    json.loads(run_hook(data_dir, stop_payload(number), env)), {}
                                )
                            else:
                                self.assertEqual(output, {})
                        state = json.loads(state_text_for(data_dir))
                        self.assertFalse(state["title_write_confirmed"])
                        self.assertEqual(state["prompt_count"], checkpoints[-1] + 3)

    def test_interrupted_checkpoint_waits_for_next_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            for number in range(1, 4):
                run_hook(data_dir, prompt_payload(number))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(3))), {})
            run_hook(data_dir, prompt_payload(4))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(4)))["decision"], "block")
            run_hook(data_dir, post_tool_payload(4))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(4, active=True))), {})

    def test_manual_write_after_exhausted_recovery_still_confirms(self) -> None:
        for checkpoint in (2, 10):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as data_dir:
                for number in range(1, checkpoint + 1):
                    run_hook(data_dir, prompt_payload(number))
                self.assertEqual(
                    json.loads(run_hook(data_dir, stop_payload(checkpoint)))["decision"], "block"
                )
                run_hook(data_dir, prompt_payload(checkpoint + 1))
                self.assertEqual(json.loads(run_hook(data_dir, stop_payload(checkpoint + 1))), {})
                run_hook(data_dir, post_tool_payload(checkpoint + 1))
                self.assertEqual(json.loads(run_hook(data_dir, stop_payload(checkpoint + 1))), {})
                if checkpoint == 10:
                    self.assertEqual(state_row_for(data_dir), (1, None))
                else:
                    self.assertTrue(json.loads(state_text_for(data_dir))["title_write_confirmed"])

    def test_stop_does_not_replay_newly_added_past_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            for number in range(1, 4):
                run_hook(data_dir, prompt_payload(number))
            env = {CHECKPOINT_ENV: "3,6"}
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(3), env)), {})
            for number in range(4, 7):
                run_hook(data_dir, prompt_payload(number), env)
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(6), env))["decision"], "block")

    def test_resumed_turn_success_allows_stop_without_new_user_prompt(self) -> None:
        for checkpoint in (2, 10):
            for tool_name in (TITLE_TOOL_NAME, "codex_tui__set_thread_title"):
                for recovery in (False, True):
                    with self.subTest(checkpoint=checkpoint, tool=tool_name, recovery=recovery):
                        with tempfile.TemporaryDirectory() as data_dir:
                            for number in range(1, checkpoint + 1):
                                run_hook(data_dir, prompt_payload(number))
                            if recovery:
                                self.assertEqual(
                                    json.loads(run_hook(data_dir, stop_payload(checkpoint)))["decision"],
                                    "block",
                                )
                            run_hook(data_dir, post_tool_payload(100, tool_name=tool_name))
                            self.assertEqual(state_row_for(data_dir)[0], 0)
                            self.assertEqual(
                                json.loads(run_hook(data_dir, stop_payload(100, active=recovery))),
                                {},
                            )
                            if checkpoint == 10:
                                self.assertEqual(state_row_for(data_dir), (1, None))
                            else:
                                state = json.loads(state_text_for(data_dir))
                                self.assertEqual(state["prompt_count"], checkpoint)
                                self.assertTrue(state["title_write_confirmed"])
                                run_hook(data_dir, prompt_payload(3))
                                self.assertEqual(json.loads(run_hook(data_dir, stop_payload(3))), {})

    def test_interleaved_receipts_are_confirmed_by_the_stopping_turn(self) -> None:
        for current_success in (False, True):
            for current_first in (False, True):
                with self.subTest(success=current_success, first=current_first):
                    with tempfile.TemporaryDirectory() as data_dir:
                        for number in range(1, 11):
                            run_hook(data_dir, prompt_payload(number))
                        current = post_tool_payload(100)
                        previous = post_tool_payload(10)
                        failed = previous if current_success else current
                        failed["tool_response"] = {"isError": True}
                        events = (current, previous) if current_first else (previous, current)
                        for event in events:
                            run_hook(data_dir, event)
                            self.assertEqual(state_row_for(data_dir)[0], 0)
                        output = json.loads(run_hook(data_dir, stop_payload(100)))
                        if current_success:
                            self.assertEqual(output, {})
                            self.assertEqual(state_row_for(data_dir), (1, None))
                        else:
                            self.assertEqual(output["decision"], "block")
                            self.assertEqual(state_row_for(data_dir)[0], 0)

    def test_early_success_survives_a_user_prompt_before_stop(self) -> None:
        for receipt_turn in (2, 100):
            with self.subTest(receipt_turn=receipt_turn), tempfile.TemporaryDirectory() as data_dir:
                run_hook(data_dir, prompt_payload(1))
                run_hook(data_dir, prompt_payload(2))
                run_hook(data_dir, post_tool_payload(receipt_turn))
                run_hook(data_dir, prompt_payload(3))
                self.assertEqual(json.loads(run_hook(data_dir, stop_payload(3))), {})
                self.assertTrue(json.loads(state_text_for(data_dir))["title_write_confirmed"])

    def test_last_result_in_same_turn_controls_stop(self) -> None:
        for checkpoint in (4, 10):
            for last_success in (False, True):
                with self.subTest(checkpoint=checkpoint, success=last_success):
                    with tempfile.TemporaryDirectory() as data_dir:
                        for number in range(1, checkpoint + 1):
                            run_hook(data_dir, prompt_payload(number))
                            if number == 2:
                                run_hook(data_dir, post_tool_payload(2))
                                self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2))), {})
                        success = post_tool_payload(100)
                        failed = post_tool_payload(100)
                        failed["tool_response"] = {"isError": True}
                        events = (failed, success) if last_success else (success, failed)
                        for event in events:
                            run_hook(data_dir, event)
                        output = json.loads(run_hook(data_dir, stop_payload(100)))
                        if last_success:
                            self.assertEqual(output, {})
                            if checkpoint == 10:
                                self.assertEqual(state_row_for(data_dir), (1, None))
                        else:
                            self.assertEqual(output["decision"], "block")

    def test_old_completion_observations_are_not_success_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            state_path_for(data_dir).write_text(json.dumps({
                "schema_version": 5,
                "prompt_count": 30,
                "seen_turns": [],
                "fired_thresholds": [2, 4, 10],
                "checkpoint_signature": [2, 4, 10],
                "title_tool_completion_observations": [{
                    "checkpoint": 2,
                    "turn_token": hashlib.sha256(b"old-turn").hexdigest(),
                }],
            }), encoding="utf-8")
            self.assertEqual(run_hook(data_dir, prompt_payload(31)), "")
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(31))), {})
            self.assertFalse(json.loads(state_text_for(data_dir))["title_write_confirmed"])
            run_hook(data_dir, post_tool_payload(31))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(31))), {})
            self.assertEqual(state_row_for(data_dir), (1, None))

    def test_existing_v7_state_preserves_confirmation_without_pending_results(self) -> None:
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed), tempfile.TemporaryDirectory() as data_dir:
                state_path_for(data_dir).write_text(json.dumps({
                    "schema_version": 7,
                    "prompt_count": 3,
                    "seen_turns": [hashlib.sha256(b"thr-test-turn-3").hexdigest()],
                    "fired_thresholds": [2],
                    "checkpoint_signature": [2, 4, 10],
                    "title_write_confirmed": confirmed,
                }), encoding="utf-8")
                output = json.loads(run_hook(data_dir, stop_payload(3)))
                self.assertEqual(output, {})
                state = json.loads(state_text_for(data_dir))
                self.assertEqual(state["prompt_count"], 3)
                self.assertEqual(state["title_write_confirmed"], confirmed)
                self.assertEqual(state["title_tool_results"], [])

    def test_pending_results_are_bounded_private_and_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            run_hook(data_dir, prompt_payload(1))
            run_hook(data_dir, prompt_payload(2))
            for number in range(100, 170):
                run_hook(data_dir, post_tool_payload(number))
            state_text = state_text_for(data_dir)
            self.assertEqual(len(json.loads(state_text)["title_tool_results"]), 64)
            for private_value in ("secret", "thr-test", "turn-", str(REPO_ROOT)):
                self.assertNotIn(private_value, state_text)
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(169))), {})
            state = json.loads(state_text_for(data_dir))
            self.assertEqual(len(state["title_tool_results"]), 63)
            self.assertTrue(state["title_write_confirmed"])
            run_hook(data_dir, prompt_payload(3))
            self.assertEqual(json.loads(state_text_for(data_dir))["title_tool_results"], [])

    def test_stop_respects_disabled_and_custom_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {CHECKPOINT_ENV: "5,9"}
            for number in range(1, 5):
                run_hook(data_dir, prompt_payload(number), env)
                self.assertEqual(json.loads(run_hook(data_dir, stop_payload(number), env)), {})
            run_hook(data_dir, prompt_payload(5), env)
            self.assertEqual(run_hook(data_dir, stop_payload(5), {CHECKPOINT_ENV: "off"}), "")
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(5), env))["decision"], "block")

    def test_packaged_stop_command_runs_and_does_not_create_missing_state(self) -> None:
        handler = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))["hooks"]["Stop"][0]["hooks"][0]
        command = handler["commandWindows"] if os.name == "nt" else handler["command"]
        with tempfile.TemporaryDirectory() as data_dir:
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(1))), {})
            self.assertEqual(list(Path(data_dir).iterdir()), [])
            run_hook(data_dir, prompt_payload(1))
            run_hook(data_dir, prompt_payload(2))
            env = os.environ.copy()
            env.pop(CHECKPOINT_ENV, None)
            env.pop(SECTION_ROUTING_ENV, None)
            env.update(PLUGIN_ROOT=str(PLUGIN_ROOT), PLUGIN_DATA=data_dir)
            result = subprocess.run(command, input=json.dumps(stop_payload(2)), text=True,
                                    capture_output=True, env=env, shell=True, check=True)
            self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_section_routing_project_configuration_and_state_privacy(self) -> None:
        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(
                project_root,
                {
                    "enabled": True,
                    "checkpoints": [1],
                    "sectionRouting": {
                        "enabled": True,
                        "sectionPrefix": "🗂️",
                        "createMissingSections": True,
                        "allowedDomains": ["Backend", "Mobile"],
                        "sectionDomainMap": {"Mobile": "Backend"},
                        "maxSections": 3,
                    },
                },
            )
            context = json.loads(
                run_hook(data_dir, prompt_payload(1, cwd=project_root))
            )["hookSpecificOutput"]["additionalContext"]

            self.assertIn(
                "Title-based sidebar routing is enabled (source project)",
                context,
            )
            self.assertIn("`Backend`, `Mobile`", context)
            self.assertIn("`Mobile` -> `Backend`", context)
            self.assertIn("target section Domain", context)
            self.assertIn("fewer than 3 custom sections start with `🗂️ `", context)
            self.assertIn("current Codex `threadId`", context)
            self.assertIn("Never move a ChatGPT conversation", context)
            self.assertNotIn("Section capacity retention is enabled", context)
            self.assertNotIn("`mcp__codex_app__reorder_section`", context)

            state_text = state_text_for(data_dir)
            self.assertNotIn("Backend", state_text)
            self.assertNotIn("Mobile", state_text)
            self.assertNotIn("sectionPrefix", state_text)

    def test_section_capacity_retention_is_bounded_and_private(self) -> None:
        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(
                project_root,
                {
                    "enabled": True,
                    "checkpoints": [1],
                    "sectionRouting": {
                        "enabled": True,
                        "allowedDomains": ["Backend"],
                        "sectionCapacity": {
                            "enabled": True,
                            "maxItems": 10,
                            "overflowDestination": "default",
                        },
                    },
                },
            )
            context = json.loads(
                run_hook(data_dir, prompt_payload(1, cwd=project_root))
            )["hookSpecificOutput"]["additionalContext"]

            self.assertIn("Section capacity retention is enabled", context)
            self.assertIn("Keep at most 10 items", context)
            self.assertIn("`mcp__codex_app__reorder_section`", context)
            self.assertIn("`sectionId` set to null", context)
            self.assertIn("Never submit only the retained subset", context)
            self.assertIn("Do not call `list_threads` again or inspect any title", context)
            self.assertIn("never move a ChatGPT conversation", context)
            self.assertIn("Treat target `itemKeys` as opaque sidebar-order keys", context)
            self.assertIn("map every retained or overflow `itemKey`", context)
            self.assertIn("`limit` 50", context)

            state_text = state_text_for(data_dir)
            self.assertNotIn("Backend", state_text)
            self.assertNotIn("sectionCapacity", state_text)
            self.assertNotIn("overflowDestination", state_text)

    def test_section_domain_map_requires_allowlisted_safe_values(self) -> None:
        environment = {
            CHECKPOINT_ENV: "1",
            SECTION_ROUTING_ENV: "domain",
        }
        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(
                project_root,
                {
                    "enabled": True,
                    "checkpoints": [1],
                    "sectionRouting": {
                        "enabled": True,
                        "allowedDomains": ["Backend"],
                        "sectionDomainMap": {"Mobile": "Backend"},
                    },
                },
            )
            context = json.loads(
                run_hook(
                    data_dir,
                    prompt_payload(1, cwd=project_root),
                    environment,
                )
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn(
                "Title-based sidebar routing is enabled (source environment)",
                context,
            )

        for invalid_map in (
            {" Mobile": "Backend", "Mobile": "Infra"},
            {"Mobile": "Backend", "Backend": "Infra"},
        ):
            with (
                tempfile.TemporaryDirectory() as data_dir,
                tempfile.TemporaryDirectory() as project_root,
            ):
                write_project_config(
                    project_root,
                    {
                        "enabled": True,
                        "checkpoints": [1],
                        "sectionRouting": {
                            "enabled": True,
                            "allowedDomains": ["Backend", "Mobile", "Infra"],
                            "sectionDomainMap": invalid_map,
                        },
                    },
                )
                context = json.loads(
                    run_hook(
                        data_dir,
                        prompt_payload(1, cwd=project_root),
                        environment,
                    )
                )["hookSpecificOutput"]["additionalContext"]
                self.assertIn(
                    "Title-based sidebar routing is enabled (source environment)",
                    context,
                )
                self.assertNotIn("Domain-to-section mapping", context)
            self.assertNotIn("`Mobile` -> `Backend`", context)

        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(
                project_root,
                {
                    "enabled": True,
                    "checkpoints": [1],
                    "sectionRouting": {
                        "enabled": True,
                        "allowedDomains": ["Backend", "Mobile"],
                        "sectionDomainMap": {"Mobile": "unsafe/domain"},
                    },
                },
            )
            context = json.loads(
                run_hook(
                    data_dir,
                    prompt_payload(1, cwd=project_root),
                    environment,
                )
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn(
                "Title-based sidebar routing is enabled (source environment)",
                context,
            )

    def test_invalid_section_capacity_falls_through_to_environment(self) -> None:
        environment = {
            CHECKPOINT_ENV: "1",
            SECTION_ROUTING_ENV: "domain",
        }
        for capacity in (
            {"enabled": True, "maxItems": 0, "overflowDestination": "default"},
            {"enabled": True, "maxItems": 51, "overflowDestination": "default"},
            {"enabled": True, "maxItems": True, "overflowDestination": "default"},
            {"enabled": True, "maxItems": 10, "overflowDestination": "archive"},
            [],
        ):
            with (
                tempfile.TemporaryDirectory() as data_dir,
                tempfile.TemporaryDirectory() as project_root,
            ):
                write_project_config(
                    project_root,
                    {
                        "enabled": True,
                        "checkpoints": [1],
                        "sectionRouting": {
                            "enabled": True,
                            "allowedDomains": ["Backend"],
                            "sectionCapacity": capacity,
                        },
                    },
                )
                context = json.loads(
                    run_hook(
                        data_dir,
                        prompt_payload(1, cwd=project_root),
                        environment,
                    )
                )["hookSpecificOutput"]["additionalContext"]
                self.assertIn(
                    "Title-based sidebar routing is enabled (source environment)",
                    context,
                )
                self.assertNotIn("Section capacity retention is enabled", context)

    def test_section_routing_environment_and_invalid_project_fallback(self) -> None:
        environment = {
            CHECKPOINT_ENV: "1",
            SECTION_ROUTING_ENV: "domain",
        }
        with tempfile.TemporaryDirectory() as data_dir:
            context = json.loads(
                run_hook(data_dir, prompt_payload(1), environment)
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn(
                "Title-based sidebar routing is enabled (source environment)",
                context,
            )
            self.assertIn("No creation allowlist is configured", context)
            self.assertIn("Do not create a missing sidebar section", context)

        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(
                project_root,
                {
                    "enabled": True,
                    "checkpoints": [1],
                    "sectionRouting": {
                        "enabled": True,
                        "createMissingSections": True,
                        "allowedDomains": ["unsafe/domain"],
                    },
                },
            )
            context = json.loads(
                run_hook(
                    data_dir,
                    prompt_payload(1, cwd=project_root),
                    environment,
                )
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn(
                "Title-based sidebar routing is enabled (source environment)",
                context,
            )
            self.assertIn("Do not create a missing sidebar section", context)

    def test_section_routing_project_disable_overrides_environment(self) -> None:
        environment = {
            CHECKPOINT_ENV: "1",
            SECTION_ROUTING_ENV: "domain",
        }
        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(
                project_root,
                {
                    "enabled": True,
                    "checkpoints": [1],
                    "sectionRouting": {"enabled": False},
                },
            )
            context = json.loads(
                run_hook(
                    data_dir,
                    prompt_payload(1, cwd=project_root),
                    environment,
                )
            )["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("Title-based sidebar routing is enabled", context)

    def test_environment_configuration(self) -> None:
        custom_env = {CHECKPOINT_ENV: "1,3,5"}
        with tempfile.TemporaryDirectory() as data_dir:
            first = json.loads(
                run_hook(data_dir, prompt_payload(1), custom_env)
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("initial checkpoint", first)
            self.assertIn("configured schedule 1,3,5", first)
            self.assertIn("source environment", first)
            self.assertEqual(run_hook(data_dir, prompt_payload(2), custom_env), "")
            middle = json.loads(
                run_hook(data_dir, prompt_payload(3), custom_env)
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("refinement checkpoint", middle)
            self.assertEqual(run_hook(data_dir, prompt_payload(4), custom_env), "")
            final = json.loads(
                run_hook(data_dir, prompt_payload(5), custom_env)
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("final refresh checkpoint", final)

    def test_project_configuration_overrides_environment(self) -> None:
        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(
                project_root, {"enabled": True, "checkpoints": [2, 6]}
            )
            custom_env = {CHECKPOINT_ENV: "1,3"}
            self.assertEqual(
                run_hook(
                    data_dir,
                    prompt_payload(1, cwd=project_root),
                    custom_env,
                ),
                "",
            )
            first = json.loads(
                run_hook(
                    data_dir,
                    prompt_payload(2, cwd=project_root),
                    custom_env,
                )
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("configured schedule 2,6", first)
            self.assertIn("source project", first)
            for number in range(3, 6):
                self.assertEqual(
                    run_hook(
                        data_dir,
                        prompt_payload(number, cwd=project_root),
                        custom_env,
                    ),
                    "",
                )
            final = json.loads(
                run_hook(
                    data_dir,
                    prompt_payload(6, cwd=project_root),
                    custom_env,
                )
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("final refresh checkpoint", final)

    def test_plugin_data_configuration_and_project_disable(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            (Path(data_dir) / "config.json").write_text(
                json.dumps({"enabled": True, "checkpoints": [1, 2]}),
                encoding="utf-8",
            )
            first = json.loads(run_hook(data_dir, prompt_payload(1)))[
                "hookSpecificOutput"
            ]["additionalContext"]
            self.assertIn("source plugin-data", first)
            final = json.loads(run_hook(data_dir, prompt_payload(2)))[
                "hookSpecificOutput"
            ]["additionalContext"]
            self.assertIn("final refresh checkpoint", final)

        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(project_root, {"enabled": False})
            self.assertEqual(
                run_hook(data_dir, prompt_payload(1, cwd=project_root)),
                "",
            )
            self.assertEqual(list(Path(data_dir).iterdir()), [])

    def test_invalid_project_config_falls_through_to_environment(self) -> None:
        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            write_project_config(
                project_root, {"enabled": True, "checkpoints": [2, 2]}
            )
            custom_env = {CHECKPOINT_ENV: "3,7"}
            for number in range(1, 3):
                self.assertEqual(
                    run_hook(
                        data_dir,
                        prompt_payload(number, cwd=project_root),
                        custom_env,
                    ),
                    "",
                )
            output = json.loads(
                run_hook(
                    data_dir,
                    prompt_payload(3, cwd=project_root),
                    custom_env,
                )
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("source environment", output)
            self.assertIn("configured schedule 3,7", output)

    def test_migrates_v2_v3_and_v4_state_without_replaying_past_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            state_path = state_path_for(data_dir)
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "prompt_count": 6,
                        "seen_turns": [
                            hashlib.sha256(b"thr-test-turn-6").hexdigest()
                        ],
                        "fired_thresholds": [4],
                        "successful_updates": 0,
                        "pending_threshold": 4,
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(run_hook(data_dir, prompt_payload(7)), "")
            migrated = json.loads(state_text_for(data_dir))
            self.assertEqual(migrated["schema_version"], 7)
            self.assertEqual(migrated["prompt_count"], 7)
            self.assertEqual(migrated["checkpoint_signature"], [2, 4, 10])
            self.assertEqual(migrated["fired_thresholds"], [2, 4])
            self.assertNotIn("successful_updates", migrated)
            self.assertEqual(migrated["pending_title_tool_completions"], [])
            self.assertEqual(migrated["title_tool_completion_observations"], [])

            self.assertEqual(run_hook(data_dir, prompt_payload(8)), "")
            self.assertEqual(run_hook(data_dir, prompt_payload(9)), "")
            refresh = json.loads(run_hook(data_dir, prompt_payload(10)))
            self.assertIn(
                "final refresh checkpoint",
                refresh["hookSpecificOutput"]["additionalContext"],
            )

        with tempfile.TemporaryDirectory() as data_dir:
            state_path = state_path_for(data_dir)
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "prompt_count": 3,
                        "seen_turns": [],
                        "fired_thresholds": [],
                    }
                ),
                encoding="utf-8",
            )
            refinement = json.loads(run_hook(data_dir, prompt_payload(4)))
            self.assertIn(
                "refinement checkpoint",
                refinement["hookSpecificOutput"]["additionalContext"],
            )
            migrated = json.loads(state_text_for(data_dir))
            self.assertEqual(migrated["fired_thresholds"], [2, 4])

        with tempfile.TemporaryDirectory() as data_dir:
            state_path = state_path_for(data_dir)
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "prompt_count": 6,
                        "seen_turns": [],
                        "fired_thresholds": [2, 4],
                        "checkpoint_signature": [2, 4, 10],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(run_hook(data_dir, prompt_payload(7)), "")
            migrated = json.loads(state_text_for(data_dir))
            self.assertEqual(migrated["schema_version"], 7)
            self.assertEqual(migrated["fired_thresholds"], [2, 4])
            self.assertEqual(migrated["pending_title_tool_completions"], [])
            self.assertEqual(migrated["title_tool_completion_observations"], [])

    def test_schedule_change_does_not_replay_past_checkpoints(self) -> None:
        with (
            tempfile.TemporaryDirectory() as data_dir,
            tempfile.TemporaryDirectory() as project_root,
        ):
            for number in range(1, 6):
                run_hook(data_dir, prompt_payload(number, cwd=project_root))

            write_project_config(
                project_root, {"enabled": True, "checkpoints": [1, 3, 8]}
            )
            self.assertEqual(
                run_hook(data_dir, prompt_payload(6, cwd=project_root)),
                "",
            )
            self.assertEqual(
                run_hook(data_dir, prompt_payload(7, cwd=project_root)),
                "",
            )
            output = json.loads(
                run_hook(data_dir, prompt_payload(8, cwd=project_root))
            )["hookSpecificOutput"]["additionalContext"]
            self.assertIn("configured schedule 1,3,8", output)
            self.assertIn("final refresh checkpoint", output)

            state = json.loads(state_text_for(data_dir))
            self.assertEqual(state["checkpoint_signature"], [1, 3, 8])
            self.assertEqual(state["fired_thresholds"], [1, 2, 3, 4, 8])

    def test_corrupt_and_wrong_shaped_legacy_state_fail_open(self) -> None:
        for legacy_text in (
            "{broken",
            json.dumps({
                "schema_version": 4,
                "prompt_count": "bad",
                "seen_turns": "not-a-list",
                "fired_thresholds": {"bad": True},
                "checkpoint_signature": "bad",
            }),
        ):
            with self.subTest(legacy_text=legacy_text), tempfile.TemporaryDirectory() as data_dir:
                state_path_for(data_dir).write_text(legacy_text, encoding="utf-8")
                self.assertEqual(run_hook(data_dir, prompt_payload(1)), "")
                output = json.loads(run_hook(data_dir, prompt_payload(2)))
                self.assertIn(
                    "initial checkpoint",
                    output["hookSpecificOutput"]["additionalContext"],
                )

    def test_final_receipt_retires_state_and_later_events_cannot_restart_it(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            for number in range(1, 10):
                run_hook(data_dir, prompt_payload(number))
                if number == 2:
                    run_hook(data_dir, post_tool_payload(number))
                    self.assertEqual(json.loads(run_hook(data_dir, stop_payload(number))), {})
            self.assertTrue(json.loads(state_text_for(data_dir))["title_write_confirmed"])
            context = json.loads(run_hook(data_dir, prompt_payload(10)))
            self.assertIn("do not skip this confirmation", str(context))
            self.assertFalse(json.loads(state_text_for(data_dir))["title_write_confirmed"])
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(10)))["decision"], "block")
            run_hook(data_dir, post_tool_payload(10))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(10, active=True))), {})
            self.assertEqual(state_row_for(data_dir), (1, None))

            for number in range(11, 15):
                self.assertEqual(run_hook(data_dir, prompt_payload(number)), "")
                self.assertEqual(json.loads(run_hook(data_dir, stop_payload(number))), {})
            self.assertEqual(run_hook(data_dir, prompt_payload(15), {CHECKPOINT_ENV: "1,20"}), "")
            failed = post_tool_payload(15)
            failed["tool_response"] = {"isError": True}
            run_hook(data_dir, failed)
            self.assertEqual(state_row_for(data_dir), (1, None))
            self.assertEqual([path.name for path in Path(data_dir).iterdir()], ["title-state.sqlite3"])

    def test_failed_final_check_waits_for_manual_write(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            for number in range(1, 11):
                run_hook(data_dir, prompt_payload(number))
            failed = post_tool_payload(10)
            failed["tool_response"] = {"isError": True}
            run_hook(data_dir, failed)
            self.assertEqual(state_row_for(data_dir)[0], 0)
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(10)))["decision"], "block")
            run_hook(data_dir, failed)
            self.assertNotIn("decision", json.loads(run_hook(data_dir, stop_payload(10, active=True))))
            run_hook(data_dir, prompt_payload(11))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(11))), {})
            run_hook(data_dir, post_tool_payload(11))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(11))), {})
            self.assertEqual(state_row_for(data_dir), (1, None))

    def test_delayed_receipt_cannot_satisfy_final_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            for number in range(1, 11):
                run_hook(data_dir, prompt_payload(number))
            run_hook(data_dir, post_tool_payload(2))
            self.assertFalse(json.loads(state_text_for(data_dir))["title_write_confirmed"])
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(10)))["decision"], "block")
            run_hook(data_dir, post_tool_payload(10))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(10, active=True))), {})
            self.assertEqual(state_row_for(data_dir), (1, None))

    def test_sessions_share_one_database_but_have_independent_lifecycles(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            run_hook(data_dir, prompt_payload(1, session_id="thr-other"))
            single_checkpoint = {CHECKPOINT_ENV: "1"}
            context = json.loads(run_hook(data_dir, prompt_payload(1), single_checkpoint))
            self.assertIn("initial checkpoint", str(context))
            self.assertIn("final refresh checkpoint", str(context))
            run_hook(data_dir, post_tool_payload(1), single_checkpoint)
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(1), single_checkpoint)), {})
            self.assertEqual(state_row_for(data_dir), (1, None))
            self.assertEqual(json.loads(state_text_for(data_dir, "thr-other"))["prompt_count"], 1)
            self.assertTrue(run_hook(data_dir, prompt_payload(2, session_id="thr-other")))
            self.assertEqual(state_row_for(data_dir)[0], 1)
            database_bytes = (Path(data_dir) / "title-state.sqlite3").read_bytes()
            for private_value in (b"secret", b"thr-test", b"thr-other", str(REPO_ROOT).encode()):
                self.assertNotIn(private_value, database_bytes)

    def test_v6_import_preserves_counters_but_needs_final_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            legacy = {
                "schema_version": 6,
                "prompt_count": 30,
                "seen_turns": [],
                "fired_thresholds": [2, 4, 10],
                "checkpoint_signature": [2, 4, 10],
                "title_write_confirmed": True,
            }
            legacy_text = json.dumps(legacy)
            state_path_for(data_dir).write_text(legacy_text, encoding="utf-8")
            self.assertEqual(run_hook(data_dir, prompt_payload(31)), "")
            imported = json.loads(state_text_for(data_dir))
            self.assertEqual(imported["prompt_count"], 31)
            self.assertEqual(imported["fired_thresholds"], [2, 4, 10])
            self.assertFalse(imported["title_write_confirmed"])
            self.assertEqual(state_path_for(data_dir).read_text(encoding="utf-8"), legacy_text)
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(31))), {})
            run_hook(data_dir, post_tool_payload(31))
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(31))), {})
            self.assertEqual(state_row_for(data_dir), (1, None))
            # An old installed Hook must not revive a session already in SQLite.
            state_path_for(data_dir).write_text("{}", encoding="utf-8")
            self.assertEqual(run_hook(data_dir, prompt_payload(32)), "")
            self.assertEqual(state_row_for(data_dir), (1, None))

    def test_schedule_shortened_past_last_checkpoint_requires_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            for number in range(1, 6):
                run_hook(data_dir, prompt_payload(number))
                if number == 2:
                    run_hook(data_dir, post_tool_payload(2))
                    self.assertEqual(json.loads(run_hook(data_dir, stop_payload(2))), {})
            shortened = {CHECKPOINT_ENV: "1,3"}
            self.assertEqual(run_hook(data_dir, prompt_payload(6), shortened), "")
            self.assertEqual(json.loads(run_hook(data_dir, stop_payload(6), shortened)), {})
            self.assertFalse(json.loads(state_text_for(data_dir))["title_write_confirmed"])
            run_hook(data_dir, post_tool_payload(6), shortened)
            self.assertEqual(
                json.loads(run_hook(data_dir, stop_payload(6), shortened)), {}
            )
            self.assertEqual(state_row_for(data_dir), (1, None))

    def test_concurrent_duplicate_turns_count_once_across_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            session_ids = [f"thr-concurrent-{number % 3}" for number in range(24)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                outputs = list(pool.map(
                    lambda session_id: run_hook(data_dir, prompt_payload(1, session_id=session_id)),
                    session_ids,
                ))
            self.assertEqual(outputs, [""] * len(session_ids))
            for session_id in set(session_ids):
                self.assertEqual(json.loads(state_text_for(data_dir, session_id))["prompt_count"], 1)

    def test_locked_database_skips_event_without_resetting_state(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            run_hook(data_dir, prompt_payload(1))
            database = sqlite3.connect(Path(data_dir) / "title-state.sqlite3")
            try:
                database.execute("BEGIN IMMEDIATE")
                self.assertEqual(run_hook(data_dir, prompt_payload(2)), "")
            finally:
                database.rollback()
                database.close()
            self.assertEqual(json.loads(state_text_for(data_dir))["prompt_count"], 1)
            self.assertTrue(run_hook(data_dir, prompt_payload(2)))

    def test_corrupt_database_is_not_overwritten_or_reset(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            database_path = Path(data_dir) / "title-state.sqlite3"
            database_path.write_bytes(b"not a SQLite database")
            self.assertEqual(run_hook(data_dir, prompt_payload(1)), "")
            self.assertEqual(database_path.read_bytes(), b"not a SQLite database")

    def test_non_prompt_events_do_not_create_state(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            payload = prompt_payload(1)
            payload["hook_event_name"] = "SessionEnd"
            self.assertEqual(run_hook(data_dir, payload), "")
            self.assertEqual(list(Path(data_dir).iterdir()), [])

    def test_concurrent_unique_turns_are_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                outputs = list(
                    pool.map(
                        lambda number: run_hook(data_dir, prompt_payload(number)),
                        range(1, 17),
                    )
                )
            contexts = [
                json.loads(output)["hookSpecificOutput"]["additionalContext"]
                for output in outputs
                if output
            ]
            self.assertEqual(len(contexts), 3)
            state = json.loads(
                state_text_for(data_dir)
            )
            self.assertEqual(state["prompt_count"], 16)
            self.assertEqual(state["fired_thresholds"], [2, 4, 10])

    def test_package_manifests_and_hook_layout(self) -> None:
        plugin = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        hooks = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))

        self.assertEqual(plugin["name"], "codex-task-title-organizer")
        self.assertEqual(plugin["version"].split("+", 1)[0], "0.5.0")
        self.assertLessEqual(len(plugin["interface"]["defaultPrompt"]), 3)
        self.assertNotIn("hooks", plugin)
        self.assertEqual(set(hooks["hooks"]), {"UserPromptSubmit", "PostToolUse", "Stop"})
        self.assertIn("2, 4, and 10", hooks["description"])
        post_hook = hooks["hooks"]["PostToolUse"]
        self.assertEqual(len(post_hook), 1)
        self.assertEqual(
            post_hook[0]["matcher"],
            "^(?:(mcp__)?codex_(app|tui)__|codex_(app|tui))set_thread_title$",
        )
        for tool_name in (
            "mcp__other__set_thread_title",
            "mcp__codex_tui__read_thread",
            "mcp__codex_tui__set_thread_title_backup",
            "prefix_codex_app__set_thread_title",
            "codex_appset_thread_title_backup",
            "prefix_codex_tuiset_thread_title",
            "mcp__codex_appset_thread_title",
            "codex_app_set_thread_title",
        ):
            self.assertIsNone(re.search(post_hook[0]["matcher"], tool_name))
        self.assertEqual(len(post_hook[0]["hooks"]), 1)
        self.assertEqual(post_hook[0]["hooks"][0]["type"], "command")
        self.assertEqual(marketplace["name"], "codex-task-title-organizer")
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], plugin["name"])
        source = (REPO_ROOT / entry["source"]["path"]).resolve()
        self.assertEqual(source, PLUGIN_ROOT.resolve())
        self.assertTrue((source / ".codex-plugin" / "plugin.json").is_file())

    def test_skill_and_configuration_reference(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        config_text = CONFIG_REFERENCE.read_text(encoding="utf-8")
        section_routing_text = SECTION_ROUTING_REFERENCE.read_text(encoding="utf-8")
        style_text = STYLE_REFERENCE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: organize-codex-task-titles\n"))
        self.assertIn(
            "project name, repository name, or only the basename",
            text,
        )
        self.assertIn("Modify at most 10 tasks per batch", text)
        self.assertIn("references/configuration.md", text)
        self.assertIn("references/section-routing.md", text)
        self.assertIn("references/title-styles.md", text)
        self.assertIn("task-title-organizer.json", config_text)
        self.assertIn("default", config_text)
        self.assertIn("2,4,10", config_text)
        self.assertIn("CODEX_TASK_TITLE_SECTION_ROUTING", config_text)
        self.assertIn("allowedDomains", section_routing_text)
        self.assertIn("sectionCapacity", section_routing_text)
        self.assertIn("overflowDestination", section_routing_text)
        self.assertIn("reorder_section", section_routing_text)
        self.assertIn("never creates one", section_routing_text)
        self.assertIn("Color blocks (default)", style_text)
        self.assertIn("Semantic icons", style_text)
        self.assertIn("Custom styles", style_text)
        self.assertIn("exactly one leading marker", style_text)

    def test_public_files_avoid_obvious_sensitive_data(self) -> None:
        patterns = {
            "absolute user home": re.compile(
                r"(?:[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+|/(?:Users|home)/[^/\s]+)",
                re.IGNORECASE,
            ),
            "email address": re.compile(
                r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
                re.IGNORECASE,
            ),
            "credential-like token": re.compile(
                r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})",
                re.IGNORECASE,
            ),
            "private key": re.compile(
                r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
            ),
        }
        findings: list[str] = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for label, pattern in patterns.items():
                if pattern.search(content):
                    findings.append(f"{path.relative_to(REPO_ROOT)}: {label}")
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
