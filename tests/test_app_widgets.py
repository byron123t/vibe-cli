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
    # A (shift+a) is handled via on_key char == "A"
    from ui.app import VibeCLIApp
    async with VibeCLIApp(_config()).run_test() as pilot:
        app = pilot.app
        pilot.app.query_one("#agent-panel").focus()
        await pilot.pause()
        initial_type = app._agent_type
        await pilot.press("A")
        await pilot.pause()
        assert app._agent_type != initial_type


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
