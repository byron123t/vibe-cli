"""ClaudeSDKClient — Anthropic SDK client for suggestions and memory queries."""
from __future__ import annotations

import os


def _get_client():
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        return None


class ClaudeSDKClient:
    """
    Wraps the Anthropic Python SDK for non-CLI tasks:
    - Generating dynamic menu suggestions
    - Answering memory queries
    - Generating commit messages / PR descriptions
    """

    def __init__(self, config: dict) -> None:
        self._cfg    = config.get("claude", {})
        self._model  = self._cfg.get("model", "claude-sonnet-4-6")
        self._max_t  = self._cfg.get("max_tokens", 2048)
        self._client = _get_client()

    def is_available(self) -> bool:
        return self._client is not None and bool(os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, system: str, user: str) -> str:
        if not self._client:
            return "[SDK] anthropic package not installed."
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_t,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text
        except Exception as e:
            return f"[SDK Error] {e}"

    def suggest_followup_prompts(
        self,
        project: str,
        last_prompt: str,
        output_tail: list[str],
        n: int = 4,
    ) -> list[str]:
        """
        Given what a Claude agent just did, suggest n follow-up prompts.
        Returns a list of plain prompt strings.
        """
        import json as _json

        system = (
            "You are a coding assistant. Given what a Claude agent just did on a project, "
            "suggest concise follow-up prompts the developer would most likely want next. "
            "Focus on fixing issues found, improving quality, running tests, or committing. "
            "Return ONLY a JSON array of strings (the prompts), no explanation."
        )
        output_snippet = "\n".join(output_tail[-20:])[:800]
        user = (
            f"Project: {project}\n"
            f"Prompt that just ran: {last_prompt}\n"
            f"Output tail:\n{output_snippet}"
        )
        raw = self.complete(system, user)
        try:
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start >= 0 and end > start:
                items = _json.loads(raw[start:end])
                return [str(s) for s in items if isinstance(s, str)][:n]
        except Exception:
            pass
        return []

    def suggest_menu_options(self, context: dict) -> list[dict]:
        """
        Ask Claude to suggest dynamic menu options.
        Returns list of {label, icon, action_prompt} dicts.
        """
        import json as _json

        system = (
            "You are a coding assistant. Given the user's current project context, "
            "recent run outputs, usage patterns, and any voice command context, "
            "suggest 3-4 useful next actions they might want to take. "
            "If voice_context is present, strongly prioritize actions related to it. "
            "Return ONLY a JSON array of objects with keys: "
            "label (short, ≤15 chars), icon (single emoji), action_prompt (what to ask claude)."
        )
        voice_ctx = context.get("voice_context", "")
        user = (
            f"Project: {context.get('project', 'unknown')}\n"
            f"Recent actions: {context.get('recent_actions', [])}\n"
            f"Recent output snippet: {context.get('recent_output', '')[:400]}\n"
            f"Current menu path: {context.get('menu_path', [])}\n"
            + (f"Voice command context: {voice_ctx}\n" if voice_ctx else "")
        )

        raw = self.complete(system, user)
        try:
            # Extract JSON array
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start >= 0 and end > start:
                return _json.loads(raw[start:end])
        except Exception:
            pass
        return []
