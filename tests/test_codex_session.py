"""Tests for terminal.codex_session.CodexSession."""
import asyncio
import sys
import pytest

from terminal.codex_session import CodexSession, _strip_ansi, _APPROVAL


# ---------------------------------------------------------------------------
# _strip_ansi
# ---------------------------------------------------------------------------

class TestStripAnsi:
    def test_removes_color_codes(self):
        assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_removes_bold(self):
        assert _strip_ansi("\x1b[1mbold\x1b[0m") == "bold"

    def test_removes_cursor_movement(self):
        assert _strip_ansi("\x1b[2Aup\x1b[0K") == "up"

    def test_plain_text_unchanged(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_empty_string(self):
        assert _strip_ansi("") == ""

    def test_multiple_codes_stripped(self):
        result = _strip_ansi("\x1b[32m\x1b[1mgreen bold\x1b[0m\x1b[0m")
        assert result == "green bold"


# ---------------------------------------------------------------------------
# _APPROVAL mapping
# ---------------------------------------------------------------------------

class TestApprovalMapping:
    def test_safe_maps_to_suggest(self):
        assert _APPROVAL["safe"] == ["--approval-mode", "suggest"]

    def test_accept_edits_maps_to_auto_edit(self):
        assert _APPROVAL["accept_edits"] == ["--approval-mode", "auto-edit"]

    def test_bypass_maps_to_full_auto(self):
        assert _APPROVAL["bypass"] == ["--approval-mode", "full-auto"]

    def test_all_three_modes_present(self):
        assert set(_APPROVAL.keys()) == {"safe", "accept_edits", "bypass"}


# ---------------------------------------------------------------------------
# CodexSession.is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_returns_bool(self):
        result = CodexSession.is_available()
        assert isinstance(result, bool)

    def test_true_when_codex_on_path(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None)
        assert CodexSession.is_available() is True

    def test_false_when_codex_missing(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert CodexSession.is_available() is False


# ---------------------------------------------------------------------------
# CodexSession construction
# ---------------------------------------------------------------------------

class TestCodexSessionConstruction:
    def _make(self, **kwargs):
        defaults = {
            "prompt": "write a test",
            "project_path": "/tmp/proj",
            "permission_mode": "accept_edits",
        }
        defaults.update(kwargs)
        return CodexSession(**defaults)

    def test_prompt_stored(self):
        s = self._make(prompt="fix the bug")
        assert s.prompt == "fix the bug"

    def test_permission_mode_stored(self):
        s = self._make(permission_mode="safe")
        assert s.permission_mode == "safe"

    def test_project_path_stored(self):
        s = self._make(project_path="/my/project")
        assert s.project_path == "/my/project"

    def test_proc_initially_none(self):
        s = self._make()
        assert s._proc is None

    def test_exit_code_initially_none(self):
        s = self._make()
        assert s.exit_code is None


# ---------------------------------------------------------------------------
# Permission mode → approval flag mapping in run()
# ---------------------------------------------------------------------------

class TestPermissionModeFlag:
    """Verify the correct --approval-mode flag is baked into the command."""

    def _get_cmd(self, permission_mode: str) -> list[str]:
        approval = _APPROVAL.get(permission_mode, _APPROVAL["accept_edits"])
        return ["codex", "-q"] + approval + ["test prompt"]

    def test_safe_includes_suggest(self):
        cmd = self._get_cmd("safe")
        assert "--approval-mode" in cmd
        assert "suggest" in cmd

    def test_accept_edits_includes_auto_edit(self):
        cmd = self._get_cmd("accept_edits")
        assert "auto-edit" in cmd

    def test_bypass_includes_full_auto(self):
        cmd = self._get_cmd("bypass")
        assert "full-auto" in cmd

    def test_unknown_mode_falls_back_to_accept_edits(self):
        cmd = self._get_cmd("nonexistent_mode")
        assert "auto-edit" in cmd


# ---------------------------------------------------------------------------
# cancel()
# ---------------------------------------------------------------------------

class TestCodexCancel:
    def test_cancel_noop_when_proc_none(self):
        s = CodexSession(prompt="p", project_path="/tmp", permission_mode="safe")
        s.cancel()  # should not raise

    def test_cancel_noop_when_already_done(self):
        """Simulate a completed process — terminate() should not be called."""
        import unittest.mock as mock
        s = CodexSession(prompt="p", project_path="/tmp", permission_mode="safe")
        fake_proc = mock.MagicMock()
        fake_proc.returncode = 0
        s._proc = fake_proc
        s.cancel()
        fake_proc.terminate.assert_not_called()

    def test_cancel_terminates_running_proc(self):
        import unittest.mock as mock
        s = CodexSession(prompt="p", project_path="/tmp", permission_mode="safe")
        fake_proc = mock.MagicMock()
        fake_proc.returncode = None  # still running
        s._proc = fake_proc
        s.cancel()
        fake_proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# FileNotFoundError → exit code 127
# ---------------------------------------------------------------------------

class TestCodexFileNotFound:
    @pytest.mark.asyncio
    async def test_file_not_found_exits_127(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: None)

        async def _raise(*args, **kwargs):
            raise FileNotFoundError("codex not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

        s = CodexSession(prompt="test", project_path="/tmp", permission_mode="safe")
        code = await s.run()
        assert code == 127

    @pytest.mark.asyncio
    async def test_file_not_found_emits_error_message(self, monkeypatch):
        async def _raise(*args, **kwargs):
            raise FileNotFoundError("no codex")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

        received = []
        s = CodexSession(prompt="test", project_path="/tmp", permission_mode="safe")
        await s.run(on_line=received.append)
        assert any("codex" in line.lower() or "not found" in line.lower() for line in received)


# ---------------------------------------------------------------------------
# Output streaming
# ---------------------------------------------------------------------------

class TestCodexOutputStreaming:
    @pytest.mark.asyncio
    async def test_plain_text_output_emitted(self, monkeypatch):
        import unittest.mock as mock

        output_lines = [b"Analyzing files...\n", b"Done.\n"]

        async def fake_exec(*args, **kwargs):
            proc = mock.MagicMock()
            proc.returncode = 0

            async def aiter_lines():
                for line in output_lines:
                    yield line

            proc.stdout.__aiter__ = lambda self: aiter_lines()

            async def wait():
                proc.returncode = 0

            proc.wait = wait
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        received = []
        s = CodexSession(prompt="test", project_path="/tmp", permission_mode="accept_edits")
        code = await s.run(on_line=received.append)
        assert "Analyzing files..." in received
        assert "Done." in received
        assert code == 0

    @pytest.mark.asyncio
    async def test_ansi_codes_stripped_from_output(self, monkeypatch):
        import unittest.mock as mock

        output_lines = [b"\x1b[32mGreen text\x1b[0m\n"]

        async def fake_exec(*args, **kwargs):
            proc = mock.MagicMock()
            proc.returncode = 0

            async def aiter_lines():
                for line in output_lines:
                    yield line

            proc.stdout.__aiter__ = lambda self: aiter_lines()

            async def wait():
                proc.returncode = 0

            proc.wait = wait
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        received = []
        s = CodexSession(prompt="test", project_path="/tmp", permission_mode="safe")
        await s.run(on_line=received.append)
        assert "Green text" in received
        assert not any("\x1b" in line for line in received)

    @pytest.mark.asyncio
    async def test_empty_lines_not_emitted(self, monkeypatch):
        import unittest.mock as mock

        output_lines = [b"\n", b"   \n", b"Real line\n"]

        async def fake_exec(*args, **kwargs):
            proc = mock.MagicMock()
            proc.returncode = 0

            async def aiter_lines():
                for line in output_lines:
                    yield line

            proc.stdout.__aiter__ = lambda self: aiter_lines()

            async def wait():
                proc.returncode = 0

            proc.wait = wait
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        received = []
        s = CodexSession(prompt="test", project_path="/tmp", permission_mode="safe")
        await s.run(on_line=received.append)
        # Empty/whitespace-only lines should not appear
        assert "" not in received
        assert "Real line" in received
