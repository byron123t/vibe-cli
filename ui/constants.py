"""
ui/constants.py — Shared UI constants and small pure helpers.

Imported by ui/app.py with private aliases, e.g.:
    from ui.constants import AGENT_DISPLAY as _AGENT_DISPLAY
    from ui.constants import slash_hint_text as _slash_hint_text
    ...
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Agent display names
# ---------------------------------------------------------------------------

AGENT_DISPLAY: dict[str, str] = {
    "claude":   "Claude",
    "codex":    "Codex",
    "cursor":   "Cursor",
    "openclaw": "OpenClaw",
}

# ---------------------------------------------------------------------------
# Slash-command hint table — shown passively while the user types /…
# Each entry: (command, args_hint, short_description)
# ---------------------------------------------------------------------------

SLASH_HINTS: list[tuple[str, str, str]] = [
    ("/effort",       "[low|medium|high]",                    "set reasoning depth"),
    ("/agent",        "[claude|codex|cursor|openclaw]",       "switch agent"),
    ("/switch",       "[claude|codex|cursor|openclaw]",       "alias for /agent"),
    ("/perm",         "[plan|safe|accept_edits|bypass]",      "set permissions"),
    ("/permissions",  "[plan|safe|accept_edits|bypass]",      "alias for /perm"),
    ("/model",        "[provider/id]",                        "override model"),
    ("/budget",       "[amount]",                             "USD spending cap (Claude)"),
    ("/turns",        "[n]",                                  "max turns/attempts"),
    ("/max-turns",    "[n]",                                  "alias for /turns"),
    ("/system",       "[text]",                               "append to system prompt"),
    ("/tools",        "allow|deny|remove|clear <pat>",        "tool access lists"),
    ("/fork",         "[instruction]",                        "fork with previous context"),
    ("/clear",        "",                                     "clear agent panel"),
    ("/compact",      "",                                     "compact history"),
    ("/help",         "",                                     "show this list"),
    ("/obsidian",     "[path]",                               "connect Obsidian vault (opt-in)"),
]


def slash_hint_text(prefix: str) -> str:
    """Return a formatted hint string for commands matching *prefix* (e.g. '/mo')."""
    prefix_lower = prefix.lower()
    matches = [
        (cmd, args, desc)
        for cmd, args, desc in SLASH_HINTS
        if cmd.startswith(prefix_lower)
    ]
    if not matches:
        return ""
    parts = []
    for cmd, args, desc in matches[:6]:
        hint = f"{cmd}"
        if args:
            hint += f" {args}"
        hint += f"  — {desc}"
        parts.append(hint)
    return "  |  ".join(parts)


# ---------------------------------------------------------------------------
# Audio extensions
# ---------------------------------------------------------------------------

AUDIO_EXTS = frozenset({
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".wma",
})

# ---------------------------------------------------------------------------
# Permission mode constants
# ---------------------------------------------------------------------------

PERM_LABELS: dict[str, tuple[str, str]] = {
    "plan":         ("PLAN 📋",        "$accent"),
    "safe":         ("SAFE",          "$success"),
    "accept_edits": ("ACCEPT EDITS",  "$warning"),
    "bypass":       ("BYPASS ALL ⚡", "$error"),
}
PERM_CYCLE: list[str] = ["plan", "safe", "accept_edits", "bypass"]

PERM_INDICATOR_NAMES: dict[str, str] = {
    "plan":         "plan",
    "safe":         "safe",
    "accept_edits": "accept edits",
    "bypass":       "bypass all",
}


def perm_indicator_text(mode: str, project: str = "") -> str:
    """Return indicator line text, e.g. '⏵⏵ accept edits  ·  myproject'."""
    label = PERM_INDICATOR_NAMES.get(mode, mode)
    proj_part = f"  ·  {project}" if project else ""
    return f"⏵⏵ {label}{proj_part}"


# ---------------------------------------------------------------------------
# Agent / effort cycling constants
# ---------------------------------------------------------------------------

AGENT_LABELS: dict[str, tuple[str, str]] = {
    "claude":    ("Claude",    "$accent"),
    "codex":     ("Codex",     "$warning"),
    "cursor":    ("Cursor",    "$success"),
    "openclaw":  ("OpenClaw",  "$error"),
}
AGENT_CYCLE: list[str] = ["claude", "codex", "cursor", "openclaw"]

EFFORT_LABELS: dict[str, tuple[str, str]] = {
    "low":    ("LOW ⚡",   "$success"),
    "medium": ("MEDIUM",  "$text-muted"),
    "high":   ("HIGH 🧠", "$warning"),
}
EFFORT_CYCLE: list[str] = ["low", "medium", "high"]
