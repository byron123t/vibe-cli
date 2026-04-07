"""SessionStore — persist and restore the full UI session state.

Saved to vault/user/session.json alongside projects.json.

Schema (version 1)
------------------
{
  "version": 1,
  "global": {
    "active_project_idx": 0,
    "permission_mode": "accept_edits",
    "agent_type": "claude",
    "show_files": false,
    "show_editor": false,
    "show_terminal": false,
    "show_graph": false
  },
  "projects": {
    "/abs/path/to/project": {
      "agents": [
        {
          "number": 1,
          "prompt": "fix the null pointer",
          "session_id": "abc12345",
          "captured_session_id": "def67890",   // for --resume
          "project_path": "/abs/path/to/project",
          "permission_mode": "accept_edits",
          "exit_code": 0,                       // null = was still running
          "output": "line1\\nline2\\n..."        // capped at 500 lines
        }
      ]
    }
  }
}
"""
from __future__ import annotations

import json
import os

SESSION_FILE = os.path.join(
    os.path.dirname(__file__), "..", "vault", "user", "session.json"
)

MAX_OUTPUT_LINES = 500


class SessionStore:

    def save(self, state: dict) -> None:
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        with open(SESSION_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def load(self) -> dict:
        if not os.path.isfile(SESSION_FILE):
            return {}
        try:
            with open(SESSION_FILE) as f:
                data = json.load(f)
            if data.get("version") != 1:
                return {}
            return data
        except Exception:
            return {}

    @staticmethod
    def cap_output(lines: list[str]) -> str:
        """Keep only the last MAX_OUTPUT_LINES lines to bound file size."""
        return "\n".join(lines[-MAX_OUTPUT_LINES:])
