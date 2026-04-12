"""
ui/themes.py — Custom Textual app themes and Pygments style mappings.

Imported by ui/app.py:
    from ui.themes import CUSTOM_THEMES as _CUSTOM_THEMES
    from ui.themes import APP_TO_PYGMENTS_THEME as _APP_TO_PYGMENTS_THEME
"""
from __future__ import annotations

from textual.theme import Theme as _AppTheme

# ---------------------------------------------------------------------------
# Custom app themes — registered with App.register_theme() on startup
# ---------------------------------------------------------------------------

CUSTOM_THEMES: list[_AppTheme] = [
    # ── Dark – Vivid ────────────────────────────────────────────────────────
    _AppTheme(
        name="cyberpunk",
        primary="#00ffff", secondary="#ff00ff", accent="#ffff00",
        background="#0d0d1a", surface="#11112a", panel="#1a1a35",
        foreground="#e0e0ff",
        error="#ff3355", warning="#ffaa00", success="#00ff88",
        dark=True,
    ),
    _AppTheme(
        name="synthwave",
        primary="#ff6ec7", secondary="#7b2fff", accent="#00e5ff",
        background="#1a0533", surface="#2d1b4e", panel="#3d2560",
        foreground="#f4f4f4",
        error="#ff4455", warning="#ffaa00", success="#00ffaa",
        dark=True,
    ),
    _AppTheme(
        name="night-owl",
        primary="#c792ea", secondary="#7fdbca", accent="#ffbc42",
        background="#011627", surface="#0b2942", panel="#0e3556",
        foreground="#d6deeb",
        error="#ef5350", warning="#ffbc42", success="#22da6e",
        dark=True,
    ),
    _AppTheme(
        name="cobalt",
        primary="#ff9d00", secondary="#0088ff", accent="#ffc600",
        background="#002240", surface="#003055", panel="#003d6b",
        foreground="#e0e0e0",
        error="#ff4040", warning="#ff9d00", success="#3ad900",
        dark=True,
    ),
    _AppTheme(
        name="horizon",
        primary="#e95678", secondary="#21bfc2", accent="#f09383",
        background="#1c1e26", surface="#232530", panel="#2e303e",
        foreground="#d5d8da",
        error="#e95678", warning="#fab795", success="#29d398",
        dark=True,
    ),
    _AppTheme(
        name="panda",
        primary="#19f9d8", secondary="#ff75b5", accent="#6fc1ff",
        background="#292a2b", surface="#333435", panel="#3e3f40",
        foreground="#e6e6e6",
        error="#ff2c6d", warning="#ffb86c", success="#19f9d8",
        dark=True,
    ),
    # ── Dark – Muted / Natural ───────────────────────────────────────────────
    _AppTheme(
        name="everforest",
        primary="#a7c080", secondary="#83c092", accent="#dbbc7f",
        background="#2d353b", surface="#343f44", panel="#3d484d",
        foreground="#d3c6aa",
        error="#e67e80", warning="#dbbc7f", success="#a7c080",
        dark=True,
    ),
    _AppTheme(
        name="ayu-dark",
        primary="#e6b450", secondary="#39bae6", accent="#ff8f40",
        background="#0b0e14", surface="#0d1017", panel="#131721",
        foreground="#bfbdb6",
        error="#d95757", warning="#e6b450", success="#7fd962",
        dark=True,
    ),
    _AppTheme(
        name="ayu-mirage",
        primary="#ffcc66", secondary="#5ccfe6", accent="#ffa759",
        background="#1f2430", surface="#242936", panel="#2c3141",
        foreground="#cbccc6",
        error="#ff3333", warning="#ffcc66", success="#bae67e",
        dark=True,
    ),
    _AppTheme(
        name="material-ocean",
        primary="#82aaff", secondary="#89ddff", accent="#c3e88d",
        background="#0f111a", surface="#181a1f", panel="#1f2129",
        foreground="#eeffff",
        error="#f07178", warning="#ffcb6b", success="#c3e88d",
        dark=True,
    ),
    _AppTheme(
        name="palenight",
        primary="#c792ea", secondary="#89ddff", accent="#82aaff",
        background="#292d3e", surface="#2f3447", panel="#34394f",
        foreground="#d0d0d0",
        error="#f07178", warning="#ffcb6b", success="#c3e88d",
        dark=True,
    ),
    _AppTheme(
        name="kanagawa",
        primary="#7e9cd8", secondary="#6a9589", accent="#ff9e3b",
        background="#1f1f28", surface="#2a2a37", panel="#363646",
        foreground="#dcd7ba",
        error="#e82424", warning="#ff9e3b", success="#98bb6c",
        dark=True,
    ),
    _AppTheme(
        name="mellow",
        primary="#c9b8a8", secondary="#a8b8c9", accent="#f0c090",
        background="#1a1a1a", surface="#242424", panel="#2e2e2e",
        foreground="#d8c8b8",
        error="#c87070", warning="#c8a870", success="#88b888",
        dark=True,
    ),
    _AppTheme(
        name="midnight-blue",
        primary="#5b9bd5", secondary="#70b8d5", accent="#f0a040",
        background="#0d1b2a", surface="#152535", panel="#1c3040",
        foreground="#c8d8e8",
        error="#e05050", warning="#d0900a", success="#50c878",
        dark=True,
    ),
    _AppTheme(
        name="matrix",
        primary="#00cc44", secondary="#00aa33", accent="#00ff55",
        background="#000a00", surface="#001400", panel="#001e00",
        foreground="#00dd44",
        error="#ff2200", warning="#aacc00", success="#00ff44",
        dark=True,
    ),
    _AppTheme(
        name="hacker",
        primary="#33ff33", secondary="#00cc00", accent="#99ff99",
        background="#0a0a0a", surface="#111111", panel="#181818",
        foreground="#ccffcc",
        error="#ff3300", warning="#ffcc00", success="#33ff33",
        dark=True,
    ),
    # ── Light ────────────────────────────────────────────────────────────────
    _AppTheme(
        name="ayu-light",
        primary="#ff9940", secondary="#36a3d9", accent="#f07171",
        background="#fafafa", surface="#f3f4f5", panel="#e7e8e9",
        foreground="#5c6166",
        error="#f07171", warning="#ff9940", success="#86b300",
        dark=False,
    ),
    _AppTheme(
        name="paper",
        primary="#476582", secondary="#6b9e6e", accent="#bf5af2",
        background="#f7f7f7", surface="#efefef", panel="#e8e8e8",
        foreground="#333333",
        error="#d93025", warning="#e37400", success="#1e8e3e",
        dark=False,
    ),
    _AppTheme(
        name="everforest-light",
        primary="#5c6a72", secondary="#708089", accent="#dfa000",
        background="#fff9f0", surface="#f4eddb", panel="#eae0c4",
        foreground="#5c6a72",
        error="#f85552", warning="#dfa000", success="#8da101",
        dark=False,
    ),
    _AppTheme(
        name="warm-light",
        primary="#8a5a44", secondary="#6a8a44", accent="#4a6a8a",
        background="#fdf6e3", surface="#f5efd5", panel="#ede8c8",
        foreground="#584636",
        error="#cb4b16", warning="#b58900", success="#859900",
        dark=False,
    ),
]

# Map from Textual app theme name → Pygments style name.
# All styles listed here are confirmed available in Pygments ≥ 2.14.
APP_TO_PYGMENTS_THEME: dict[str, str] = {
    # Built-in Textual themes
    "textual-dark":          "monokai",
    "dracula":               "dracula",
    "monokai":               "monokai",
    "nord":                  "nord",
    "gruvbox":               "gruvbox-dark",
    "tokyo-night":           "one-dark",
    "atom-one-dark":         "one-dark",
    "catppuccin-mocha":      "monokai",
    "catppuccin-frappe":     "zenburn",
    "catppuccin-macchiato":  "native",
    "flexoki":               "inkpot",
    "rose-pine":             "inkpot",
    "rose-pine-moon":        "vim",
    "solarized-dark":        "solarized-dark",
    "textual-light":         "friendly",
    "textual-ansi":          "default",
    "atom-one-light":        "friendly",
    "catppuccin-latte":      "tango",
    "rose-pine-dawn":        "pastie",
    "solarized-light":       "solarized-light",
    # Custom dark – vivid
    "cyberpunk":             "fruity",
    "synthwave":             "monokai",
    "night-owl":             "one-dark",
    "cobalt":                "fruity",
    "horizon":               "monokai",
    "panda":                 "one-dark",
    # Custom dark – muted / natural
    "everforest":            "vim",
    "ayu-dark":              "native",
    "ayu-mirage":            "monokai",
    "material-ocean":        "one-dark",
    "palenight":             "one-dark",
    "kanagawa":              "vim",
    "mellow":                "zenburn",
    "midnight-blue":         "nord",
    "matrix":                "native",
    "hacker":                "native",
    # Custom light
    "ayu-light":             "friendly",
    "paper":                 "friendly",
    "everforest-light":      "tango",
    "warm-light":            "tango",
}
