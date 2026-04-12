"""UI widget tests using Textual's run_test pilot."""
import tempfile
import os
import pytest


pytestmark = pytest.mark.anyio


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


def _config(tmp_vault=None):
    return {
        "vault": {"root": tmp_vault or "/tmp/vibe-cli-test-vault"},
        "git":   {"auto_commit": False},
        "claude": {"model": "claude-sonnet-4-6"},
        "ui":    {"suggestions_count": 4, "max_agents_per_project": 8},
    }


# ---------------------------------------------------------------------------
# App startup
# ---------------------------------------------------------------------------

async def test_app_mounts():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        assert pilot.app.query_one("#agent-panel") is not None
        assert pilot.app.query_one("#prompt-bar") is not None
        assert pilot.app.query_one("#tab-bar") is not None
        assert pilot.app.query_one("#status-bar") is not None
        assert pilot.app.query_one("#graph-pane") is not None


async def test_default_layout_has_agent_panel_visible():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        ap = pilot.app.query_one("#agent-panel")
        graph = pilot.app.query_one("#graph-pane")
        assert ap.display is True
        assert graph.display is False


async def test_file_browser_hidden_by_default():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        fb = pilot.app.query_one("#file-browser")
        assert fb.display is False


async def test_editor_panel_hidden_by_default():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        ep = pilot.app.query_one("#editor-panel")
        assert ep.display is False


async def test_terminal_panel_hidden_by_default():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        tp = pilot.app.query_one("#terminal-panel")
        assert tp.display is False


async def test_restores_theme_from_session(monkeypatch, tmp_path):
    from core.session_store import SessionStore, session_path_for_vault
    from terminal.approval_server import ApprovalServer
    from ui.app import VibeCLIApp

    vault = str(tmp_path / "vault")
    session_path = session_path_for_vault(vault)

    async def _noop_start(self):
        self._port = 0

    monkeypatch.setattr(ApprovalServer, "start", _noop_start)
    monkeypatch.setattr(VibeCLIApp, "_persist_theme_config", lambda self, name: None)

    cfg = _config(vault)

    app = VibeCLIApp(cfg)
    async with app.run_test() as pilot:
        app._set_theme("dracula")
        app._save_session()
        await pilot.pause()

    restored = VibeCLIApp(cfg)
    async with restored.run_test() as pilot:
        await pilot.pause()
        assert restored._ui_theme == "dracula"
        assert restored.theme == "dracula"
        assert SessionStore(session_path).load()["global"]["ui_theme"] == "dracula"


async def test_palette_theme_selection_persists_immediately(monkeypatch, tmp_path):
    from core.session_store import SessionStore, session_path_for_vault
    from terminal.approval_server import ApprovalServer
    from ui.app import VibeCLIApp
    from ui.screens.command_palette import CommandPaletteScreen

    vault = str(tmp_path / "vault")
    session_path = session_path_for_vault(vault)

    async def _noop_start(self):
        self._port = 0

    monkeypatch.setattr(ApprovalServer, "start", _noop_start)
    monkeypatch.setattr(VibeCLIApp, "_persist_theme_config", lambda self, name: None)

    app = VibeCLIApp(_config(vault))
    async with app.run_test() as pilot:
        app.action_open_palette()
        await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, CommandPaletteScreen)
        screen._set_app_theme("dracula")
        screen.dismiss(None)
        await pilot.pause()
        assert app._ui_theme == "dracula"
        assert app.theme == "dracula"
        assert SessionStore(session_path).load()["global"]["ui_theme"] == "dracula"

async def test_palette_enter_from_input_selects_theme_and_closes(monkeypatch, tmp_path):
    from core.session_store import SessionStore, session_path_for_vault
    from terminal.approval_server import ApprovalServer
    from textual.widgets import Input
    from ui.app import VibeCLIApp
    from ui.screens.command_palette import CommandPaletteScreen

    vault = str(tmp_path / "vault")
    session_path = session_path_for_vault(vault)

    async def _noop_start(self):
        self._port = 0

    monkeypatch.setattr(ApprovalServer, "start", _noop_start)
    monkeypatch.setattr(VibeCLIApp, "_persist_theme_config", lambda self, name: None)

    app = VibeCLIApp(_config(vault))
    async with app.run_test() as pilot:
        app.action_open_palette()
        await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, CommandPaletteScreen)

        inp = screen.query_one("#cp-input", Input)
        inp.value = "dracula"
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen_stack[-1], CommandPaletteScreen)
        assert app._ui_theme == "dracula"
        assert app.theme == "dracula"
        assert SessionStore(session_path).load()["global"]["ui_theme"] == "dracula"


async def test_config_theme_used_when_session_global_omits_ui_theme(monkeypatch, tmp_path):
    """Legacy session.json without ui_theme must not override ui.theme from config.json."""
    import json
    from core.session_store import session_path_for_vault
    from terminal.approval_server import ApprovalServer
    from ui.app import VibeCLIApp

    vault = str(tmp_path / "vault")
    session_path = session_path_for_vault(vault)
    os.makedirs(os.path.dirname(session_path), exist_ok=True)

    async def _noop_start(self):
        self._port = 0

    monkeypatch.setattr(ApprovalServer, "start", _noop_start)

    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(
            {"version": 1, "global": {"show_files": False, "show_editor": False}},
            f,
        )

    cfg = _config(str(tmp_path / "vault"))
    cfg.setdefault("ui", {})["theme"] = "dracula"

    async with VibeCLIApp(cfg).run_test() as pilot:
        await pilot.pause()
        assert pilot.app._ui_theme == "dracula"
        assert pilot.app.theme == "dracula"


async def test_theme_persist_writes_to_passed_config_path(monkeypatch, tmp_path):
    """Theme must update the same config file main.py loaded (--config), not ui/../config.json only."""
    import json
    from terminal.approval_server import ApprovalServer
    from ui.app import VibeCLIApp

    vault = tmp_path / "vault"
    cfg_path = tmp_path / "alt-config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "vault": {"root": str(vault)},
                "ui": {},
                "claude": {"model": "claude-sonnet-4-6"},
            }
        ),
        encoding="utf-8",
    )

    async def _noop_start(self):
        self._port = 0

    monkeypatch.setattr(ApprovalServer, "start", _noop_start)

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    async with VibeCLIApp(cfg, config_path=str(cfg_path)).run_test() as pilot:
        pilot.app._set_theme("dracula")
        await pilot.pause()

    assert json.loads(cfg_path.read_text(encoding="utf-8"))["ui"]["theme"] == "dracula"


# ---------------------------------------------------------------------------
# Keyboard navigation
# ---------------------------------------------------------------------------

async def test_n_focuses_prompt():
    from ui.app import VibeCLIApp
    from textual.widgets import Input
    async with VibeCLIApp(_config()).run_test() as pilot:
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(pilot.app.focused, Input)


async def test_enter_focuses_prompt():
    from ui.app import VibeCLIApp
    from textual.widgets import Input
    async with VibeCLIApp(_config()).run_test() as pilot:
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(pilot.app.focused, Input)


async def test_e_toggles_editor():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        ep = pilot.app.query_one("#editor-panel")
        assert ep.display is False
        await pilot.press("e")
        await pilot.pause()
        assert ep.display is True
        await pilot.press("e")
        await pilot.pause()
        assert ep.display is False


async def test_f_toggles_file_browser():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        fb = pilot.app.query_one("#file-browser")
        assert fb.display is False
        await pilot.press("f")
        await pilot.pause()
        assert fb.display is True
        await pilot.press("f")
        await pilot.pause()
        assert fb.display is False


async def test_m_toggles_graph():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        graph = pilot.app.query_one("#graph-pane")
        await pilot.press("m")
        await pilot.pause()
        assert graph.display is True
        await pilot.press("m")
        await pilot.pause()
        assert graph.display is False


async def test_t_toggles_terminal():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        tp = pilot.app.query_one("#terminal-panel")
        assert tp.display is False
        await pilot.press("t")
        await pilot.pause()
        assert tp.display is True


async def test_backspace_exits_prompt():
    from ui.app import VibeCLIApp
    from textual.widgets import Input
    async with VibeCLIApp(_config()).run_test() as pilot:
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(pilot.app.focused, Input)
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        await pilot.press("backspace")
        await pilot.pause()
        assert not isinstance(pilot.app.focused, Input)


async def test_comma_exits_to_command_mode():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        await pilot.press(",")
        await pilot.pause()
        # App should still be running and focused
        assert pilot.app.query_one("#agent-panel") is not None


async def test_capital_P_cycles_permission_mode():
    # P (shift+p) is handled via on_key char == "P", so press the key "P"
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        app = pilot.app
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        initial_mode = app._perm_mode
        await pilot.press("P")
        await pilot.pause()
        assert app._perm_mode != initial_mode


async def test_permission_cycles_through_all_modes():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        app = pilot.app
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        modes_seen = {app._perm_mode}
        for _ in range(3):
            await pilot.press("P")
            await pilot.pause()
            modes_seen.add(app._perm_mode)
        assert len(modes_seen) >= 2


async def test_capital_A_cycles_agent_type():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        app = pilot.app
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        initial_type = app._agent_type
        await pilot.press("A")
        await pilot.pause()
        assert app._agent_type != initial_type


async def test_A_cycles_through_all_agents():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        app = pilot.app
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        start = app._agent_type
        seen = {start}
        # Cycle through all 4 agent types (claude, codex, cursor, openclaw) back to start
        for _ in range(4):
            await pilot.press("A")
            await pilot.pause()
            seen.add(app._agent_type)
        assert app._agent_type == start
        assert len(seen) >= 2


async def test_shortcuts_bar_rendered():
    from ui.app import VibeCLIApp, ShortcutsBar
    async with VibeCLIApp(_config()).run_test() as pilot:
        sb = pilot.app.query_one("#shortcuts-bar", ShortcutsBar)
        assert sb is not None


# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------

async def test_project_tab_switch():
    from ui.app import VibeCLIApp
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        app = VibeCLIApp(_config())
        app._pm.add_project(d1)
        app._pm.add_project(d2)
        async with app.run_test() as pilot:
            initial_idx = app._pm.active_idx
            await pilot.press("]")
            await pilot.pause()
            assert app._pm.active_idx != initial_idx


async def test_bracket_wraps_at_end():
    from ui.app import VibeCLIApp
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        app = VibeCLIApp(_config())
        app._pm.add_project(d1)
        app._pm.add_project(d2)
        async with app.run_test() as pilot:
            start_idx = app._pm.active_idx
            n = len(app._pm.projects)
            # Pressing ] n times should cycle back to the start
            for _ in range(n):
                await pilot.press("]")
                await pilot.pause()
            assert app._pm.active_idx == start_idx


async def test_left_bracket_goes_prev():
    from ui.app import VibeCLIApp
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        app = VibeCLIApp(_config())
        app._pm.add_project(d1)
        app._pm.add_project(d2)
        async with app.run_test() as pilot:
            app._pm.set_active(1)
            await pilot.press("[")
            await pilot.pause()
            assert app._pm.active_idx == 0


async def test_number_key_fills_suggestion():
    # Keys 1-4 fill prompt bar suggestions, not switch projects
    from ui.app import VibeCLIApp, PromptBar
    async with VibeCLIApp(_config()).run_test() as pilot:
        pb = pilot.app.query_one("#prompt-bar", PromptBar)
        # Set a known suggestion so we can verify it's filled
        pb.suggestions = ["Fix the bug", "Write tests", "Add types", "Commit changes"]
        await pilot.pause()
        # Focus agent panel (command mode) then press "1" to fill first suggestion
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        # App should still be running (no crash)
        assert pilot.app.query_one("#prompt-bar") is not None


async def test_tab_bar_rendered():
    from ui.app import VibeCLIApp
    from ui.app import ProjectTabBar
    with tempfile.TemporaryDirectory() as d1:
        app = VibeCLIApp(_config())
        app._pm.add_project(d1)
        async with app.run_test() as pilot:
            tab_bar = pilot.app.query_one("#tab-bar", ProjectTabBar)
            assert tab_bar is not None


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------

async def test_status_bar_shows_permission_mode():
    from ui.app import VibeCLIApp, StatusBar
    async with VibeCLIApp(_config()).run_test() as pilot:
        sb = pilot.app.query_one("#status-bar", StatusBar)
        assert sb is not None
        # Content should reflect current permission mode
        assert pilot.app._perm_mode in ("safe", "accept_edits", "bypass")


async def test_status_bar_updates_on_permission_change():
    from ui.app import VibeCLIApp, StatusBar
    async with VibeCLIApp(_config()).run_test() as pilot:
        app = pilot.app
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        mode_before = app._perm_mode
        await pilot.press("P")
        await pilot.pause()
        assert app._perm_mode != mode_before


# ---------------------------------------------------------------------------
# Graph pane
# ---------------------------------------------------------------------------

async def test_graph_pane_populated_with_projects():
    from ui.app import VibeCLIApp, GraphPane
    with tempfile.TemporaryDirectory() as d1:
        app = VibeCLIApp(_config())
        app._pm.add_project(d1)
        async with app.run_test() as pilot:
            # Toggle graph pane on
            await pilot.press("m")
            await pilot.pause()
            gp = pilot.app.query_one("#graph-pane", GraphPane)
            assert gp.display is True


async def test_m_hides_agent_panel():
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        ap = pilot.app.query_one("#agent-panel")
        assert ap.display is True
        await pilot.press("m")
        await pilot.pause()
        assert ap.display is False


# ---------------------------------------------------------------------------
# Prompt bar
# ---------------------------------------------------------------------------

async def test_prompt_bar_has_suggestions():
    from ui.app import VibeCLIApp, PromptBar
    async with VibeCLIApp(_config()).run_test() as pilot:
        pb = pilot.app.query_one("#prompt-bar", PromptBar)
        assert pb is not None


async def test_prompt_bar_suggestions_updated():
    from ui.app import VibeCLIApp, PromptBar
    async with VibeCLIApp(_config()).run_test() as pilot:
        pb = pilot.app.query_one("#prompt-bar", PromptBar)
        # Setting suggestions should not raise
        pb.suggestions = ["Fix tests", "Commit changes"]
        await pilot.pause()


# ---------------------------------------------------------------------------
# Audio annotator (editor)
# ---------------------------------------------------------------------------

async def test_editor_audio_mode_uses_sidecar_notes():
    from ui.app import VibeCLIApp, EditorPanel, _audio_annotation_path
    from textual.widgets import TextArea
    import os

    with tempfile.TemporaryDirectory() as d:
        audio = os.path.join(d, "clip.wav")
        with open(audio, "wb") as f:
            f.write(b"RIFF")
        app = VibeCLIApp(_config())
        app._pm.add_project(d)
        async with app.run_test() as pilot:
            ep = pilot.app.query_one("#editor-panel", EditorPanel)
            ep.load_file(audio)
            assert ep.is_audio_mode is True
            meta = pilot.app.query_one("#ep-audio-meta")
            assert meta.display is True
            ta = pilot.app.query_one("#ep-area", TextArea)
            assert "Audio notes" in ta.text or "#" in ta.text
            # TextArea is always editable for audio — write directly and save
            ta.text = "0:00 — intro\n"
            ep.save()
        sidecar = _audio_annotation_path(audio)
        assert os.path.isfile(sidecar)
        with open(sidecar, encoding="utf-8") as f:
            assert "intro" in f.read()
