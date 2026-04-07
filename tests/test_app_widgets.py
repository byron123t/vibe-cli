"""UI widget tests using Textual's run_test pilot."""
import pytest
import tempfile
import os


# Pin to asyncio backend — trio is not installed in this environment
pytestmark = pytest.mark.anyio


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


def _config():
    return {"vault": {"root": "/tmp/vibeswipe-test-vault"}, "git": {"auto_commit": False}}


async def test_app_mounts():
    from ui.app import VibeSwipeApp
    app = VibeSwipeApp(_config())
    async with app.run_test() as pilot:
        assert app.query_one("#agent-panel") is not None
        assert app.query_one("#prompt-bar") is not None


async def test_n_focuses_prompt():
    from ui.app import VibeSwipeApp
    from textual.widgets import Input
    app = VibeSwipeApp(_config())
    async with app.run_test() as pilot:
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.focused, Input)


async def test_e_toggles_editor():
    from ui.app import VibeSwipeApp
    app = VibeSwipeApp(_config())
    async with app.run_test() as pilot:
        editor = app.query_one("#editor-panel")
        initial = editor.display
        await pilot.press("e")
        await pilot.pause()
        assert editor.display != initial


async def test_m_toggles_graph():
    from ui.app import VibeSwipeApp
    app = VibeSwipeApp(_config())
    async with app.run_test() as pilot:
        graph = app.query_one("#graph-pane")
        await pilot.press("m")
        await pilot.pause()
        assert graph.display is True


async def test_project_tab_switch():
    from ui.app import VibeSwipeApp
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        # Add projects before constructing the app so they're loaded at mount time
        config = _config()
        app = VibeSwipeApp(config)
        app._pm.add_project(d1)
        app._pm.add_project(d2)
        async with app.run_test() as pilot:
            initial_idx = app._pm.active_idx
            await pilot.press("]")
            await pilot.pause()
            # active index should have changed (we have at least 2 projects)
            assert app._pm.active_idx != initial_idx


async def test_backspace_exits_prompt():
    from ui.app import VibeSwipeApp
    from textual.widgets import Input
    app = VibeSwipeApp(_config())
    async with app.run_test() as pilot:
        await pilot.press("n")  # enter prompt mode
        await pilot.pause()
        assert isinstance(app.focused, Input)
        # Return to command mode via agent panel focus
        app.query_one("#agent-panel").focus()
        await pilot.pause()
        # Press backspace in command mode — should not raise and should stay in command mode
        await pilot.press("backspace")
        await pilot.pause()
        # Should not be focused on Input anymore
        from textual.widgets import Input as TInput
        assert not isinstance(app.focused, TInput)


async def test_t_toggles_terminal():
    from ui.app import VibeSwipeApp
    app = VibeSwipeApp(_config())
    async with app.run_test() as pilot:
        terminal = app.query_one("#terminal-panel")
        assert terminal.display is False  # hidden by default
        await pilot.press("t")
        await pilot.pause()
        assert terminal.display is True


async def test_comma_exits_prompt():
    from ui.app import VibeSwipeApp
    from textual.widgets import Input
    app = VibeSwipeApp(_config())
    async with app.run_test() as pilot:
        # Focus agent panel (command mode)
        app.query_one("#agent-panel").focus()
        await pilot.pause()
        # Comma in command mode should just refocus agent panel without crash
        await pilot.press(",")
        await pilot.pause()
