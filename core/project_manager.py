"""ProjectManager — discovers and manages the set of open projects."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


PROJECTS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "vault", "user", "projects.json"
)


@dataclass
class Project:
    name: str
    path: str
    active_file: str = ""        # relative to project root
    pinned_files: list[str] = field(default_factory=list)
    ssh_info: dict | None = None  # set for remote SSH projects

    @property
    def is_remote(self) -> bool:
        return self.ssh_info is not None

    @property
    def display_name(self) -> str:
        """Tab label — shows remote indicator for SSH projects."""
        if self.ssh_info:
            host = self.ssh_info.get("host", "?")
            rpath = self.ssh_info.get("remote_path", "")
            return f"⟁ {self.name} ({host}:{rpath})"
        return self.name

    def resolve_active_file(self) -> str | None:
        """Return absolute path of active_file if it exists."""
        if not self.active_file:
            return self._guess_entry_file()
        abs_path = os.path.join(self.path, self.active_file)
        return abs_path if os.path.isfile(abs_path) else self._guess_entry_file()

    def _guess_entry_file(self) -> str | None:
        candidates = ["main.py", "app.py", "index.py", "src/main.py",
                       "index.ts", "index.js", "main.ts", "src/index.ts",
                       "README.md"]
        for c in candidates:
            p = os.path.join(self.path, c)
            if os.path.isfile(p):
                return p
        # Fall back to first .py or .ts file found
        for root, _, files in os.walk(self.path):
            for f in sorted(files):
                if f.endswith((".py", ".ts", ".js", ".go", ".rs")):
                    return os.path.join(root, f)
        return None

    def is_git_repo(self) -> bool:
        return os.path.isdir(os.path.join(self.path, ".git"))


class ProjectManager:
    """
    Manages the list of open projects, persistence, and project-level metadata.
    """

    def __init__(self) -> None:
        self._projects: list[Project] = []
        self._active_idx: int = 0
        self._load()

    # ------------------------------------------------------------------ CRUD

    def add_project(self, path: str) -> Project:
        path = os.path.abspath(path)
        for p in self._projects:
            if p.path == path:
                return p
        name = os.path.basename(path)
        proj = Project(name=name, path=path)
        self._projects.append(proj)
        self._save()
        return proj

    def add_ssh_project(self, local_mount: str, ssh_info: dict) -> Project:
        """Add a remote project backed by an sshfs mount at local_mount."""
        local_mount = os.path.abspath(local_mount)
        for p in self._projects:
            if p.path == local_mount:
                p.ssh_info = ssh_info
                self._save()
                return p
        # Use remote basename as name, fall back to host
        remote_path = ssh_info.get("remote_path", "")
        name = os.path.basename(remote_path.rstrip("/")) or ssh_info.get("host", "remote")
        proj = Project(name=name, path=local_mount, ssh_info=ssh_info)
        self._projects.append(proj)
        self._save()
        return proj

    def remove_project(self, idx: int) -> None:
        if 0 <= idx < len(self._projects):
            self._projects.pop(idx)
            self._active_idx = min(self._active_idx, len(self._projects) - 1)
            self._save()

    def set_active(self, idx: int) -> None:
        if 0 <= idx < len(self._projects):
            self._active_idx = idx

    def next_project(self) -> None:
        if self._projects:
            self._active_idx = (self._active_idx + 1) % len(self._projects)

    def prev_project(self) -> None:
        if self._projects:
            self._active_idx = (self._active_idx - 1) % len(self._projects)

    def set_active_file(self, rel_path: str) -> None:
        if self.active:
            self.active.active_file = rel_path
            self._save()

    # ------------------------------------------------------------------ access

    @property
    def projects(self) -> list[Project]:
        return list(self._projects)

    @property
    def active(self) -> Project | None:
        if not self._projects:
            return None
        return self._projects[self._active_idx]

    @property
    def active_idx(self) -> int:
        return self._active_idx

    # ------------------------------------------------------------------ persistence

    def _save(self) -> None:
        os.makedirs(os.path.dirname(PROJECTS_FILE), exist_ok=True)
        data = []
        for p in self._projects:
            entry: dict = {
                "name":         p.name,
                "path":         p.path,
                "active_file":  p.active_file,
                "pinned_files": p.pinned_files,
            }
            if p.ssh_info:
                entry["ssh_info"] = p.ssh_info
            data.append(entry)
        with open(PROJECTS_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self) -> None:
        if not os.path.isfile(PROJECTS_FILE):
            return
        try:
            with open(PROJECTS_FILE) as f:
                data = json.load(f)
            for item in data:
                path     = item.get("path", "")
                ssh_info = item.get("ssh_info")
                # Local projects must exist; SSH projects are re-mounted later
                if not ssh_info and not os.path.isdir(path):
                    continue
                proj = Project(
                    name=item.get("name", os.path.basename(path)),
                    path=path,
                    active_file=item.get("active_file", ""),
                    pinned_files=item.get("pinned_files", []),
                    ssh_info=ssh_info,
                )
                self._projects.append(proj)
        except Exception:
            pass
