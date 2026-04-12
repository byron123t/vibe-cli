"""
tests/test_refactoring.py — Verify that the refactored module structure exposes
the same public names that ui/app.py relied on before extraction.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# ui/themes.py
# ---------------------------------------------------------------------------

def test_themes_custom_themes():
    from ui.themes import CUSTOM_THEMES
    assert isinstance(CUSTOM_THEMES, list)
    assert len(CUSTOM_THEMES) > 0
    names = [t.name for t in CUSTOM_THEMES]
    assert "cyberpunk" in names
    assert "kanagawa" in names
    assert "ayu-light" in names


def test_themes_pygments_map():
    from ui.themes import APP_TO_PYGMENTS_THEME
    assert isinstance(APP_TO_PYGMENTS_THEME, dict)
    assert APP_TO_PYGMENTS_THEME["textual-dark"] == "monokai"
    assert APP_TO_PYGMENTS_THEME["cyberpunk"] == "fruity"
    assert APP_TO_PYGMENTS_THEME["warm-light"] == "tango"


def test_themes_imported_as_private_aliases():
    from ui.themes import CUSTOM_THEMES as _CUSTOM_THEMES
    from ui.themes import APP_TO_PYGMENTS_THEME as _APP_TO_PYGMENTS_THEME
    assert _CUSTOM_THEMES is not None
    assert _APP_TO_PYGMENTS_THEME is not None


# ---------------------------------------------------------------------------
# ui/linting.py
# ---------------------------------------------------------------------------

def test_linting_lint_issue_dataclass():
    from ui.linting import LintIssue
    issue = LintIssue(line=1, col=0, severity="error", message="test")
    assert issue.line == 1
    assert issue.severity == "error"


def test_linting_lintable_exts():
    from ui.linting import LINTABLE_EXTS
    assert ".py" in LINTABLE_EXTS
    assert ".js" in LINTABLE_EXTS
    assert ".md" in LINTABLE_EXTS


def test_linting_lint_file_on_valid_python(tmp_path):
    from ui.linting import lint_file
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n")
    issues = lint_file(str(f))
    assert isinstance(issues, list)
    assert all(hasattr(i, "line") for i in issues)


def test_linting_lint_file_on_bad_python(tmp_path):
    from ui.linting import lint_file
    f = tmp_path / "bad.py"
    f.write_text("def foo(\n")  # syntax error
    issues = lint_file(str(f))
    assert len(issues) >= 1
    assert issues[0].severity == "error"


def test_linting_language_for():
    from ui.linting import language_for
    assert language_for("foo.py") == "python"
    assert language_for("bar.ts") == "typescript"
    assert language_for("index.html") == "html"
    assert language_for("readme.txt") is None


# ---------------------------------------------------------------------------
# ui/constants.py
# ---------------------------------------------------------------------------

def test_constants_agent_display():
    from ui.constants import AGENT_DISPLAY
    assert AGENT_DISPLAY["claude"] == "Claude"
    assert AGENT_DISPLAY["openclaw"] == "OpenClaw"


def test_constants_slash_hints():
    from ui.constants import SLASH_HINTS
    cmds = [cmd for cmd, _, _ in SLASH_HINTS]
    assert "/effort" in cmds
    assert "/model" in cmds
    assert "/clear" in cmds


def test_constants_slash_hint_text():
    from ui.constants import slash_hint_text
    result = slash_hint_text("/mo")
    assert "/model" in result

    empty = slash_hint_text("/zzz")
    assert empty == ""


def test_constants_audio_exts():
    from ui.constants import AUDIO_EXTS
    assert ".mp3" in AUDIO_EXTS
    assert ".wav" in AUDIO_EXTS


def test_constants_perm_constants():
    from ui.constants import PERM_LABELS, PERM_CYCLE, perm_indicator_text
    assert "safe" in PERM_LABELS
    assert "bypass" in PERM_CYCLE
    text = perm_indicator_text("safe", "myproject")
    assert "safe" in text
    assert "myproject" in text


def test_constants_agent_effort_cycles():
    from ui.constants import AGENT_CYCLE, EFFORT_CYCLE
    assert "claude" in AGENT_CYCLE
    assert "high" in EFFORT_CYCLE


# ---------------------------------------------------------------------------
# ui/screens/__init__.py re-exports
# ---------------------------------------------------------------------------

def test_screens_package_exports():
    from ui.screens import (
        DirectoryPickerScreen,
        BrainImportScreen,
        DetachMenuScreen,
        _ObsidianPathScreen,
        CommandPaletteScreen,
    )
    from textual.screen import ModalScreen
    for cls in (DirectoryPickerScreen, BrainImportScreen,
                DetachMenuScreen, _ObsidianPathScreen, CommandPaletteScreen):
        assert issubclass(cls, ModalScreen), f"{cls.__name__} not a ModalScreen"


def test_screens_importable_from_app():
    """app.py must still expose these names at the ui.app module level."""
    from ui.app import (
        VibeCLIApp,
        ShortcutsBar,
        EditorPanel,
        ProjectTabBar,
        PromptBar,
        StatusBar,
        GraphPane,
        _audio_annotation_path,
    )
    assert callable(VibeCLIApp)
    assert callable(_audio_annotation_path)
