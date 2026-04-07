"""ProjectRegistry — tracks known projects and persists project metadata."""
from __future__ import annotations

import json
import os
from datetime import datetime


class ProjectRegistry:
    """
    Persists project paths and metadata in vault/user/projects.json.
    Syncs with the vault's projects/ directory.
    """

    def __init__(self, vault_root: str) -> None:
        self._path = os.path.join(vault_root, "user", "projects.json")
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if os.path.isfile(self._path):
            with open(self._path) as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def register(self, name: str, path: str) -> None:
        self._data.setdefault(name, {}).update({
            "path": path,
            "last_active": datetime.utcnow().isoformat(),
        })
        self._save()

    def set_active(self, name: str) -> None:
        if name in self._data:
            self._data[name]["last_active"] = datetime.utcnow().isoformat()
            self._save()

    def all_projects(self) -> list[dict]:
        return [{"name": k, **v} for k, v in self._data.items()]

    def get(self, name: str) -> dict | None:
        return self._data.get(name)
