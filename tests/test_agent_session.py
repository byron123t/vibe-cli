"""Tests for terminal.agent_session.AgentSession and RestoredSession."""
import asyncio
import time
import pytest

from terminal.agent_session import AgentSession, RestoredSession


# ---------------------------------------------------------------------------
# RestoredSession — the only concrete non-CLI subclass
# ---------------------------------------------------------------------------

class TestRestoredSession:
    def test_from_saved_basic(self):
        data = {
            "prompt": "write a readme",
            "project_path": "/tmp/myproject",
            "session_id": "abc12345",
            "permission_mode": "accept_edits",
            "exit_code": 0,
            "captured_session_id": "sess-xyz",
        }
        session = RestoredSession.from_saved(data)
        assert session.prompt == "write a readme"
        assert session.project_path == "/tmp/myproject"
        assert session.session_id == "abc12345"
        assert session.permission_mode == "accept_edits"
        assert session.exit_code == 0
        assert session.captured_session_id == "sess-xyz"

    def test_from_saved_null_exit_code_becomes_minus_one(self):
        data = {
            "prompt": "p",
            "project_path": "/tmp",
            "exit_code": None,
        }
        session = RestoredSession.from_saved(data)
        assert session.exit_code == -1

    def test_from_saved_missing_fields_use_defaults(self):
        data = {"prompt": "p", "project_path": "/tmp"}
        session = RestoredSession.from_saved(data)
        assert session.permission_mode == "accept_edits"
        assert session.exit_code == -1

    def test_is_done_true_when_exit_code_set(self):
        data = {"prompt": "p", "project_path": "/tmp", "exit_code": 0}
        session = RestoredSession.from_saved(data)
        assert session.is_done is True

    def test_is_available_always_true(self):
        assert RestoredSession.is_available() is True

    @pytest.mark.asyncio
    async def test_run_returns_exit_code(self):
        data = {"prompt": "p", "project_path": "/tmp", "exit_code": 0}
        session = RestoredSession.from_saved(data)
        code = await session.run()
        assert code == 0

    @pytest.mark.asyncio
    async def test_run_returns_minus_one_when_exit_code_was_null(self):
        # null exit_code → converted to -1 (interrupted); run() returns -1
        data = {"prompt": "p", "project_path": "/tmp", "exit_code": None}
        session = RestoredSession.from_saved(data)
        code = await session.run()
        assert code == -1

    def test_session_id_generated_when_missing(self):
        data = {"prompt": "p", "project_path": "/tmp"}
        session = RestoredSession.from_saved(data)
        assert session.session_id  # not empty

    def test_cancel_noop(self):
        data = {"prompt": "p", "project_path": "/tmp", "exit_code": 0}
        session = RestoredSession.from_saved(data)
        session.cancel()  # Should not raise

    @pytest.mark.asyncio
    async def test_approve_permission_noop(self):
        data = {"prompt": "p", "project_path": "/tmp", "exit_code": 0}
        session = RestoredSession.from_saved(data)
        await session.approve_permission("req-1", allow=True)  # Should not raise


# ---------------------------------------------------------------------------
# AgentSession shared properties (via RestoredSession)
# ---------------------------------------------------------------------------

class TestAgentSessionProperties:
    def _make(self, **kwargs):
        defaults = {"prompt": "test prompt", "project_path": "/tmp/proj", "exit_code": 0}
        defaults.update(kwargs)
        return RestoredSession.from_saved(defaults)

    def test_output_tail_initially_empty(self):
        session = self._make()
        assert session.output_tail == []

    def test_emit_appends_to_output_tail(self):
        session = self._make()
        session._emit("line one", None)
        session._emit("line two", None)
        assert "line one" in session.output_tail
        assert "line two" in session.output_tail

    def test_emit_calls_on_line(self):
        session = self._make()
        received = []
        session._emit("hello", received.append)
        assert "hello" in received

    def test_emit_caps_at_30_lines(self):
        session = self._make()
        for i in range(40):
            session._emit(f"line {i}", None)
        assert len(session.output_tail) == 30

    def test_emit_multiline_splits(self):
        session = self._make()
        session._emit("line a\nline b\nline c", None)
        assert len(session.output_tail) == 3

    def test_elapsed_is_positive(self):
        session = self._make()
        time.sleep(0.01)
        assert session.elapsed > 0

    def test_unique_session_ids(self):
        s1 = RestoredSession.from_saved({"prompt": "p", "project_path": "/tmp"})
        s2 = RestoredSession.from_saved({"prompt": "p", "project_path": "/tmp"})
        # Each gets its own generated ID when no session_id in data
        assert s1.session_id != s2.session_id

    def test_is_done_false_when_exit_code_none(self):
        session = RestoredSession(prompt="p", project_path="/tmp")
        session.exit_code = None
        assert session.is_done is False

    def test_is_done_true_when_exit_code_zero(self):
        session = RestoredSession(prompt="p", project_path="/tmp")
        session.exit_code = 0
        assert session.is_done is True
