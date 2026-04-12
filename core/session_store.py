"""SessionStore — persist and restore the full UI session state."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any


def session_path_for_vault(vault_root: str) -> str:
    """Return session.json path under the configured vault (matches MemoryVault layout)."""
    return os.path.join(os.path.abspath(vault_root), "user", "session.json")


# Default when SessionStore() is constructed without a path (tests, scripts).
# The app uses session_path_for_vault(config vault root) so session lives with
# the same vault as notes and personalization data.
SESSION_FILE = os.path.join(
    os.path.dirname(__file__), "..", "vault", "user", "session.json"
)

SESSION_VERSION = 1
MAX_OUTPUT_LINES = 500
MAX_PROMPT_HISTORY = 500

DEFAULT_GLOBAL_STATE = {
    "active_project_idx": 0,
    "permission_mode": "accept_edits",
    "agent_type": "claude",
    "effort_mode": "medium",
    "show_files": False,
    "show_editor": False,
    "show_terminal": False,
    "show_graph": False,
    "show_obsidian": False,
    "ui_theme": "textual-dark",
}


class SessionStore:
    """Persistence wrapper for the app session file."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or SESSION_FILE

    @classmethod
    def default_state(cls) -> dict[str, Any]:
        return {
            "version": SESSION_VERSION,
            "global": copy.deepcopy(DEFAULT_GLOBAL_STATE),
            "projects": {},
            "detached": {},
            "prompt_history": [],
        }

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize(state)
        self._write(normalized)
        return normalized

    def patch_global(self, **kwargs: Any) -> dict[str, Any]:
        state = self.load() or self.default_state()
        state.setdefault("global", {}).update(kwargs)
        return self.save(state)

    def load(self) -> dict[str, Any]:
        raw = self._read_raw()
        if raw is None:
            return {}
        return self.normalize(raw)

    def normalize(self, state: Any) -> dict[str, Any]:
        base = self.default_state()
        if not isinstance(state, dict):
            return base

        # Distinguish "no global key in file" from "global object without ui_theme".
        # The palette writes ui.theme to config.json immediately; older session.json
        # files omitted ui_theme. Filling the default here made session override config
        # on every launch. Only omit ui_theme when global was explicitly saved without it.
        raw_global = state.get("global")
        if isinstance(raw_global, dict):
            global_state = raw_global
            global_explicit = True
        else:
            global_state = {}
            global_explicit = False

        if isinstance(global_state, dict):
            for key, default in DEFAULT_GLOBAL_STATE.items():
                value = global_state.get(key, default)
                if isinstance(default, bool):
                    base["global"][key] = value if isinstance(value, bool) else default
                elif isinstance(default, int):
                    base["global"][key] = value if isinstance(value, int) and value >= 0 else default
                elif isinstance(default, str):
                    base["global"][key] = value if isinstance(value, str) and value else default

            if global_explicit and "ui_theme" not in global_state:
                base["global"].pop("ui_theme", None)

        projects = state.get("projects", {})
        if isinstance(projects, dict):
            normalized_projects: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for project_path, project_state in projects.items():
                if not isinstance(project_path, str) or not project_path:
                    continue
                if not isinstance(project_state, dict):
                    continue
                agents = project_state.get("agents", [])
                if not isinstance(agents, list):
                    agents = []
                normalized_projects[project_path] = {
                    "agents": [self._normalize_agent(agent) for agent in agents if isinstance(agent, dict)],
                }
            base["projects"] = normalized_projects

        detached = state.get("detached", {})
        if isinstance(detached, dict):
            normalized_detached: dict[str, list[dict[str, Any]]] = {}
            for project_path, agent_states in detached.items():
                if not isinstance(project_path, str) or not project_path:
                    continue
                if not isinstance(agent_states, list):
                    continue
                normalized_detached[project_path] = [
                    self._normalize_agent(agent) for agent in agent_states if isinstance(agent, dict)
                ]
            base["detached"] = normalized_detached

        prompt_history = state.get("prompt_history", [])
        if isinstance(prompt_history, list):
            base["prompt_history"] = [
                item for item in prompt_history[-MAX_PROMPT_HISTORY:] if isinstance(item, str)
            ]

        return base

    def _normalize_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}

        int_fields = ("number", "exit_code")
        str_fields = (
            "prompt",
            "session_id",
            "captured_session_id",
            "project_path",
            "permission_mode",
            "agent_type",
            "model_override",
            "output",
        )

        for field in int_fields:
            value = agent.get(field)
            if isinstance(value, int):
                normalized[field] = value
            elif field == "exit_code" and value is None:
                normalized[field] = None

        for field in str_fields:
            value = agent.get(field)
            if isinstance(value, str):
                normalized[field] = value

        output = normalized.get("output", "")
        normalized["output"] = self.cap_output(output.splitlines())
        return normalized

    def _read_raw(self) -> dict[str, Any] | None:
        if not os.path.isfile(self.path):
            return None
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        version = data.get("version")
        if version not in (None, SESSION_VERSION):
            return {}
        return data

    def _write(self, state: dict[str, Any]) -> None:
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".session.", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def cap_output(lines: list[str]) -> str:
        """Keep only the last MAX_OUTPUT_LINES lines to bound file size."""
        return "\n".join(lines[-MAX_OUTPUT_LINES:])
