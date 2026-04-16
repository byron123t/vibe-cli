"""SessionStore — persist and restore the full UI session state.

Storage backends (in priority order):
  1. Redis  — low-latency, survives crashes, no fsync overhead.
             Enabled when redis-py is installed and the server is reachable.
             Key: ``vibe:session``  (no TTL — persists indefinitely).
  2. File   — JSON file under the vault directory (always written as a backup
             even when Redis is active, so the vault remains portable).
"""
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
SESSION_FILE = os.path.join(
    os.path.dirname(__file__), "..", "vault", "user", "session.json"
)

SESSION_VERSION = 1
MAX_OUTPUT_LINES = 500
MAX_PROMPT_HISTORY = 500

DEFAULT_THEME = "textual-dark"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
REDIS_KEY = "vibe:session"

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
    # ui_theme is intentionally absent — it is handled separately in normalize()
    # so the loop never injects "textual-dark" as a default that overwrites a saved choice.
}


def _try_connect_redis(url: str) -> "Any | None":
    """Attempt to connect to Redis; return a client or None on any failure."""
    try:
        import redis  # type: ignore
        client = redis.from_url(url, socket_connect_timeout=1, socket_timeout=2)
        client.ping()
        return client
    except Exception:
        return None


class SessionStore:
    """Persistence wrapper for the app session file.

    Parameters
    ----------
    path:
        Path to the JSON file.  Falls back to ``SESSION_FILE`` when omitted.
    redis_url:
        Redis connection URL (default ``redis://localhost:6379/0``).
        Pass ``None`` to disable Redis entirely.
    redis_key:
        Redis key used to store the session (default ``"vibe:session"``).
        Override to isolate from other vibe installations on the same server.
    """

    def __init__(
        self,
        path: str | None = None,
        redis_url: str | None = DEFAULT_REDIS_URL,
        redis_key: str = REDIS_KEY,
    ) -> None:
        self.path = path or SESSION_FILE
        self._redis = None
        self._redis_key = redis_key or REDIS_KEY
        if redis_url:
            self._redis = _try_connect_redis(redis_url)

    @property
    def backend(self) -> str:
        """Return ``'redis'`` or ``'file'`` — the active primary backend."""
        return "redis" if self._redis is not None else "file"

    @classmethod
    def default_state(cls) -> dict[str, Any]:
        return {
            "version": SESSION_VERSION,
            "global": copy.deepcopy(DEFAULT_GLOBAL_STATE),
            "projects": {},
            "detached": {},
            "closed_projects": {},
            "prompt_history": [],
        }

    # ---------------------------------------------------------------------- public API

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize(state)
        self._write(normalized)          # always write file (portable backup)
        self._redis_set(normalized)      # no-op when Redis is unavailable
        return normalized

    def patch_global(self, **kwargs: Any) -> dict[str, Any]:
        state = self.load() or self.default_state()
        state.setdefault("global", {}).update(kwargs)
        return self.save(state)

    def load(self) -> dict[str, Any]:
        raw = self._redis_get() or self._read_raw()
        if raw is None:
            return {}
        return self.normalize(raw)

    # ---------------------------------------------------------------------- normalisation

    def normalize(self, state: Any) -> dict[str, Any]:
        base = self.default_state()
        if not isinstance(state, dict):
            return base

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

            # ui_theme is NOT in DEFAULT_GLOBAL_STATE so the loop never touches it.
            # Copy it only when the file explicitly stored it; otherwise leave it
            # absent so config.json (or the app default) wins at startup.
            if global_explicit:
                theme_val = global_state.get("ui_theme")
                if isinstance(theme_val, str) and theme_val:
                    base["global"]["ui_theme"] = theme_val

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

        closed_projects = state.get("closed_projects", {})
        if isinstance(closed_projects, dict):
            normalized_closed: dict[str, list[dict[str, Any]]] = {}
            for project_path, agent_states in closed_projects.items():
                if not isinstance(project_path, str) or not project_path:
                    continue
                if not isinstance(agent_states, list):
                    continue
                normalized_closed[project_path] = [
                    self._normalize_agent(agent) for agent in agent_states if isinstance(agent, dict)
                ]
            base["closed_projects"] = normalized_closed

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

    # ---------------------------------------------------------------------- Redis helpers

    def _redis_set(self, state: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            self._redis.set(self._redis_key, json.dumps(state))
        except Exception:
            pass

    def _redis_get(self) -> dict[str, Any] | None:
        if self._redis is None:
            return None
        try:
            data = self._redis.get(self._redis_key)
            if data is None:
                return None
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                return None
            version = parsed.get("version")
            if version not in (None, SESSION_VERSION):
                return None
            return parsed
        except Exception:
            return None

    # ---------------------------------------------------------------------- file helpers

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

    # ---------------------------------------------------------------------- utilities

    @staticmethod
    def cap_output(lines: list[str]) -> str:
        """Keep only the last MAX_OUTPUT_LINES lines to bound file size."""
        return "\n".join(lines[-MAX_OUTPUT_LINES:])
