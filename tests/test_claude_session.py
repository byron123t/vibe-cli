"""Tests for terminal.claude_session.ClaudeSession."""
import time
import pytest

from terminal.claude_session import ClaudeSession, _strip_ansi


def test_is_available():
    result = ClaudeSession.is_available()
    assert isinstance(result, bool)


def test_session_id_unique():
    s1 = ClaudeSession(prompt="hello", project_path="/tmp")
    s2 = ClaudeSession(prompt="hello", project_path="/tmp")
    assert s1.session_id != s2.session_id


def test_elapsed_increases():
    session = ClaudeSession(prompt="test", project_path="/tmp")
    time.sleep(0.01)
    assert session.elapsed > 0


def test_cancel_noop_when_not_running():
    session = ClaudeSession(prompt="test", project_path="/tmp")
    # Should not raise even though no process is running
    session.cancel()


def test_strip_ansi():
    assert _strip_ansi("\x1b[32mhello\x1b[0m") == "hello"
    assert _strip_ansi("no ansi here") == "no ansi here"
    assert _strip_ansi("\x1b[1;31mERROR\x1b[0m: something") == "ERROR: something"
    assert _strip_ansi("") == ""
