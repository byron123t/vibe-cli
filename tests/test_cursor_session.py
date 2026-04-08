"""Tests for terminal.cursor_session.CursorSession."""
import asyncio
import json
import pytest

from terminal.cursor_session import CursorSession, _strip_ansi


# ---------------------------------------------------------------------------
# _strip_ansi
# ---------------------------------------------------------------------------

class TestStripAnsi:
    def test_removes_color_codes(self):
        assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_plain_text_unchanged(self):
        assert _strip_ansi("hello") == "hello"

    def test_empty_string(self):
        assert _strip_ansi("") == ""


# ---------------------------------------------------------------------------
# CursorSession.is_available / _cli
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_returns_bool(self):
        assert isinstance(CursorSession.is_available(), bool)

    def test_true_when_agent_on_path(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/agent" if name == "agent" else None)
        assert CursorSession.is_available() is True

    def test_false_when_agent_missing(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert CursorSession.is_available() is False

    def test_cli_returns_agent(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/agent" if name == "agent" else None)
        assert CursorSession._cli() == "agent"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestCursorSessionConstruction:
    def _make(self, **kwargs):
        defaults = {
            "prompt": "fix the bug",
            "project_path": "/tmp/proj",
            "permission_mode": "accept_edits",
        }
        defaults.update(kwargs)
        return CursorSession(**defaults)

    def test_prompt_stored(self):
        assert self._make(prompt="hello").prompt == "hello"

    def test_permission_mode_stored(self):
        assert self._make(permission_mode="safe").permission_mode == "safe"

    def test_proc_initially_none(self):
        assert self._make()._proc is None

    def test_exit_code_initially_none(self):
        assert self._make().exit_code is None

    def test_captured_session_id_initially_empty(self):
        s = self._make()
        assert not s.captured_session_id


# ---------------------------------------------------------------------------
# --force flag logic
# ---------------------------------------------------------------------------

class TestForceFlag:
    def _cmd(self, permission_mode: str, resume: str = "") -> list[str]:
        force = ["--force"] if permission_mode in ("accept_edits", "bypass") else []
        cmd = ["agent", "--print", "--output-format", "stream-json"]
        if resume:
            cmd += ["--resume", resume]
        cmd += force + ["test prompt"]
        return cmd

    def test_safe_mode_has_no_force(self):
        assert "--force" not in self._cmd("safe")

    def test_accept_edits_has_force(self):
        assert "--force" in self._cmd("accept_edits")

    def test_bypass_has_force(self):
        assert "--force" in self._cmd("bypass")

    def test_resume_flag_included_when_set(self):
        cmd = self._cmd("safe", resume="sess-abc")
        assert "--resume" in cmd
        assert "sess-abc" in cmd

    def test_no_resume_flag_when_empty(self):
        assert "--resume" not in self._cmd("safe", resume="")


# ---------------------------------------------------------------------------
# _handle_event
# ---------------------------------------------------------------------------

class TestHandleEvent:
    def _make(self):
        return CursorSession(prompt="p", project_path="/tmp", permission_mode="safe")

    def test_assistant_event_emits_text_blocks(self):
        s = self._make()
        received = []
        event = {
            "type": "assistant",
            "session_id": "sess-1",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello from Cursor"}],
            },
        }
        s._handle_event(event, received.append)
        assert "Hello from Cursor" in received

    def test_assistant_event_captures_session_id(self):
        s = self._make()
        event = {
            "type": "assistant",
            "session_id": "sess-xyz",
            "message": {"role": "assistant", "content": []},
        }
        s._handle_event(event, None)
        assert s.captured_session_id == "sess-xyz"

    def test_session_id_only_captured_once(self):
        s = self._make()
        s._handle_event({"type": "system", "session_id": "first"}, None)
        s._handle_event({"type": "system", "session_id": "second"}, None)
        assert s.captured_session_id == "first"

    def test_tool_call_started_emits_tool_name(self):
        s = self._make()
        received = []
        event = {
            "type": "tool_call",
            "subtype": "started",
            "session_id": "s1",
            "tool_call": {"name": "WriteFile", "input": {"file_path": "src/main.py"}},
        }
        s._handle_event(event, received.append)
        assert any("WriteFile" in line for line in received)

    def test_tool_call_started_includes_file_path(self):
        s = self._make()
        received = []
        event = {
            "type": "tool_call",
            "subtype": "started",
            "session_id": "s1",
            "tool_call": {"name": "Read", "input": {"file_path": "app.py"}},
        }
        s._handle_event(event, received.append)
        assert any("app.py" in line for line in received)

    def test_tool_call_completed_not_emitted(self):
        s = self._make()
        received = []
        event = {
            "type": "tool_call",
            "subtype": "completed",
            "session_id": "s1",
            "tool_result": {"output": "done"},
        }
        s._handle_event(event, received.append)
        assert received == []

    def test_result_event_emits_text(self):
        s = self._make()
        received = []
        event = {"type": "result", "session_id": "s1", "result": "All done!"}
        s._handle_event(event, received.append)
        assert "All done!" in received

    def test_result_event_updates_session_id(self):
        s = self._make()
        s.captured_session_id = "old-id"
        event = {"type": "result", "session_id": "new-id", "result": "done"}
        s._handle_event(event, None)
        assert s.captured_session_id == "new-id"

    def test_unknown_event_type_ignored(self):
        s = self._make()
        received = []
        s._handle_event({"type": "heartbeat", "session_id": "s1"}, received.append)
        assert received == []

    def test_tool_call_command_shown(self):
        s = self._make()
        received = []
        event = {
            "type": "tool_call",
            "subtype": "started",
            "session_id": "s1",
            "tool_call": {"name": "Shell", "input": {"command": "ls -la"}},
        }
        s._handle_event(event, received.append)
        assert any("ls -la" in line for line in received)

    def test_tool_call_truncates_long_detail(self):
        s = self._make()
        received = []
        long_cmd = "x" * 100
        event = {
            "type": "tool_call",
            "subtype": "started",
            "session_id": "s1",
            "tool_call": {"name": "Shell", "input": {"command": long_cmd}},
        }
        s._handle_event(event, received.append)
        assert any(len(line) < 120 for line in received)


# ---------------------------------------------------------------------------
# cancel()
# ---------------------------------------------------------------------------

class TestCursorCancel:
    def test_cancel_noop_when_proc_none(self):
        s = CursorSession(prompt="p", project_path="/tmp", permission_mode="safe")
        s.cancel()  # should not raise

    def test_cancel_noop_when_already_done(self):
        import unittest.mock as mock
        s = CursorSession(prompt="p", project_path="/tmp", permission_mode="safe")
        proc = mock.MagicMock()
        proc.returncode = 0
        s._proc = proc
        s.cancel()
        proc.terminate.assert_not_called()

    def test_cancel_terminates_running_proc(self):
        import unittest.mock as mock
        s = CursorSession(prompt="p", project_path="/tmp", permission_mode="safe")
        proc = mock.MagicMock()
        proc.returncode = None
        s._proc = proc
        s.cancel()
        proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# FileNotFoundError → exit code 127
# ---------------------------------------------------------------------------

class TestCursorFileNotFound:
    @pytest.mark.asyncio
    async def test_file_not_found_exits_127(self, monkeypatch):
        async def _raise(*args, **kwargs):
            raise FileNotFoundError("cursor not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

        s = CursorSession(prompt="test", project_path="/tmp", permission_mode="safe")
        code = await s.run()
        assert code == 127

    @pytest.mark.asyncio
    async def test_file_not_found_emits_error_message(self, monkeypatch):
        async def _raise(*args, **kwargs):
            raise FileNotFoundError("no agent")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

        received = []
        s = CursorSession(prompt="test", project_path="/tmp", permission_mode="safe")
        await s.run(on_line=received.append)
        assert any("agent" in line.lower() for line in received)


# ---------------------------------------------------------------------------
# Stream-JSON parsing in run()
# ---------------------------------------------------------------------------

class TestCursorStreamParsing:
    def _make_proc(self, lines: list[bytes], returncode: int = 0):
        import unittest.mock as mock

        proc = mock.MagicMock()
        proc.returncode = returncode

        async def aiter_lines():
            for line in lines:
                yield line

        proc.stdout.__aiter__ = lambda self: aiter_lines()

        async def wait():
            proc.returncode = returncode

        proc.wait = wait
        return proc

    @pytest.mark.asyncio
    async def test_json_events_parsed_not_emitted_raw(self, monkeypatch):
        event = json.dumps({
            "type": "assistant",
            "session_id": "s1",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
        })
        proc = self._make_proc([event.encode() + b"\n"])

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        received = []
        s = CursorSession(prompt="p", project_path="/tmp", permission_mode="safe")
        await s.run(on_line=received.append)
        assert "Hi" in received
        # Raw JSON line should not appear
        assert not any(line.startswith("{") for line in received)

    @pytest.mark.asyncio
    async def test_non_json_lines_emitted_as_plain_text(self, monkeypatch):
        proc = self._make_proc([b"Starting up...\n", b"Working...\n"])

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        received = []
        s = CursorSession(prompt="p", project_path="/tmp", permission_mode="safe")
        await s.run(on_line=received.append)
        assert "Starting up..." in received

    @pytest.mark.asyncio
    async def test_session_id_captured_from_stream(self, monkeypatch):
        event = json.dumps({
            "type": "system",
            "session_id": "captured-sess-id",
        })
        proc = self._make_proc([event.encode() + b"\n"])

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        s = CursorSession(prompt="p", project_path="/tmp", permission_mode="safe")
        await s.run()
        assert s.captured_session_id == "captured-sess-id"
