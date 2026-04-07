"""PromptSuggestionEngine — generates ranked prompt suggestions."""
from __future__ import annotations

import os
import time

from graph.personalization_graph import PersonalizationGraph


# Built-in fallback prompts (shown when no personalization data yet)
_BUILTIN: list[str] = [
    "Fix the most obvious bug in this project",
    "Write unit tests for the main module",
    "Commit all changes with a descriptive message",
    "Add type annotations to all functions",
    "Review code for potential issues and suggest improvements",
    "Refactor for readability and clarity",
    "Add docstrings to all public functions",
    "Optimize the most performance-critical section",
]


class PromptSuggestionEngine:
    """
    Returns ranked prompt strings for the prompt bar suggestions.

    Sources (highest to lowest priority):
    1. Project-specific recently used prompts (from personalization graph)
    2. Built-in frequently applicable prompts
    3. Context-aware suggestions based on active file extension
    """

    def __init__(self, graph: PersonalizationGraph) -> None:
        self._graph = graph
        # In-memory log of (project, prompt, timestamp) for recent prompts
        self._recent: list[tuple[str, str, float]] = []

    def record(self, project_name: str, prompt: str) -> None:
        """Record a prompt that was used."""
        self._graph.record_use(f"prompt:{prompt[:60]}", project_name)
        self._recent.append((project_name, prompt, time.time()))
        if len(self._recent) > 200:
            self._recent = self._recent[-200:]

    def get_recent_prompts(self, project_name: str, n: int = 10) -> list[str]:
        """Return the n most recently used distinct prompts for a project."""
        seen:   set[str]  = set()
        result: list[str] = []
        for proj, prompt, _ in reversed(self._recent):
            if proj == project_name and prompt not in seen:
                seen.add(prompt)
                result.append(prompt)
            if len(result) >= n:
                break
        return list(reversed(result))  # oldest first

    def get_suggestions(
        self,
        project_name: str,
        active_file: str = "",
        last_prompt: str = "",
        n: int = 4,
    ) -> list[str]:
        """Return up to n ranked prompt strings."""
        candidates: list[tuple[str, float]] = []

        # 1. Personalization graph predictions
        last_action = f"prompt:{last_prompt[:60]}" if last_prompt else ""
        predicted = self._graph.get_likely_next(last_action, project_name, top_n=10)
        for action_id, score in predicted:
            if action_id.startswith("prompt:"):
                prompt_text = action_id[len("prompt:"):]
                candidates.append((prompt_text, score + 1.0))  # +1 boost

        # 2. Recent prompts for this project (last 10 minutes)
        recent_cutoff = time.time() - 600
        for proj, prompt, ts in reversed(self._recent):
            if proj == project_name and ts > recent_cutoff:
                # Boost recency
                recency_score = 0.5 + (ts - recent_cutoff) / 600
                exists = any(p == prompt for p, _ in candidates)
                if not exists:
                    candidates.append((prompt, recency_score))

        # 3. File-extension context hints
        ext_hints = _ext_hints(active_file)
        for i, hint in enumerate(ext_hints):
            if not any(p == hint for p, _ in candidates):
                candidates.append((hint, 0.3 - i * 0.05))

        # 4. Built-in fallbacks
        for i, builtin in enumerate(_BUILTIN):
            if not any(p == builtin for p, _ in candidates):
                candidates.append((builtin, 0.1 - i * 0.01))

        # Sort and deduplicate
        seen: set[str] = set()
        result: list[str] = []
        for prompt, _ in sorted(candidates, key=lambda x: x[1], reverse=True):
            if prompt not in seen:
                seen.add(prompt)
                result.append(prompt)
            if len(result) >= n:
                break

        return result


def _ext_hints(active_file: str) -> list[str]:
    """Return file-extension-specific hints."""
    ext = os.path.splitext(active_file)[1].lower()
    hints_map: dict[str, list[str]] = {
        ".py": [
            "Add type annotations and fix any type errors",
            "Write pytest unit tests for this module",
            "Fix any linting issues (flake8/ruff)",
        ],
        ".ts": [
            "Fix TypeScript type errors",
            "Write Jest unit tests for this file",
            "Convert to strict TypeScript types",
        ],
        ".js": [
            "Convert to TypeScript",
            "Add JSDoc comments",
            "Write Jest unit tests",
        ],
        ".go": [
            "Add error handling and write tests",
            "Optimize and add benchmarks",
        ],
        ".rs": [
            "Fix clippy warnings and add tests",
            "Add error handling with proper Result types",
        ],
        ".md": [
            "Improve clarity and fix grammar",
            "Add a table of contents",
        ],
    }
    return hints_map.get(ext, [])
