"""ProfileAnalyzer — LLM-driven behavioral psychometrics and prompt prediction.

After each agent run, this module:
  1. Analyzes the prompt + output to update the user's psychometric profile
  2. Uses the updated profile to generate personalized next-prompt predictions

The profile captures: prompting style, technical stack, task patterns,
cognitive tendencies, current focus, and behavioral notes.
"""
from __future__ import annotations

import json as _json
from claude.sdk_client import ClaudeSDKClient


_PROFILE_UPDATE_SYSTEM = """\
You are a behavioral analyst maintaining a developer's psychometric profile \
for an AI coding assistant (VibeSwipe).

Your job: observe how this developer prompts AI and update their profile so \
future prompt predictions are highly accurate and personalized.

Profile sections to maintain:
- **Prompting Style** — sentence structure, verbosity, specificity, tone \
(imperative/interrogative/descriptive)
- **Technical Stack** — languages, frameworks, tools observed in prompts/output
- **Task Patterns** — categories of work: bugfix / feature / refactor / test \
/ commit / review / explain
- **Cognitive Tendencies** — iterative vs. sweeping changes; detail-focused vs. \
high-level; proactive vs. reactive; how they handle errors
- **Current Focus** — what project areas they are actively working on right now
- **Behavioral Notes** — notable patterns (e.g. often skips tests, prefers small \
commits, context-switches frequently, ADHD-style multi-project juggling, etc.)

Rules:
- Keep the profile under 450 words total
- Update INCREMENTALLY — preserve established observations, add new ones
- Be specific and evidence-based (reference actual prompt wording or output)
- Downweight old observations that seem no longer relevant
- Return the FULL updated profile in markdown (same headings, updated content)\
"""

_PROFILE_UPDATE_USER = """\
Current profile:
{profile}

Just-completed run:
- Project: {project}
- Prompt used: "{prompt}"
- Output tail: {output}

Recent prompt history (oldest first):
{history}

Update the profile with new observations from this run.\
"""

_PREDICT_SYSTEM = """\
You are predicting what a developer will ask their AI coding assistant next \
in VibeSwipe, a keyboard-first multi-project TUI.

Use their psychometric profile and current context to return exactly {n} \
high-confidence next-prompt predictions.

Rules:
- Match their EXACT prompting style (brief if they prompt briefly; specific if \
they are specific; use their vocabulary)
- Prioritise what logically follows the most recent run's output
- Consider their current focus and cognitive tendencies from the profile
- Make each prediction distinct (don't suggest near-duplicates)
- Return ONLY a JSON array of {n} strings — no explanation\
"""

_PREDICT_USER = """\
User profile:
{profile}

Current project: {project}

Last prompt: "{last_prompt}"
Last run output (tail):
{output}

Recent prompts (oldest first):
{history}

Return {n} next-prompt predictions as a JSON array.\
"""


class ProfileAnalyzer:
    """
    Uses the Anthropic SDK (via ClaudeSDKClient) to:
    - update_profile(): incrementally update the user psychometric profile
    - predict_prompts(): generate personalized next-prompt predictions
    """

    def __init__(self, sdk: ClaudeSDKClient) -> None:
        self._sdk = sdk

    def is_available(self) -> bool:
        return self._sdk.is_available()

    def update_profile(
        self,
        prompt: str,
        output_tail: list[str],
        project: str,
        recent_prompts: list[str],
        current_profile: str,
    ) -> str:
        """
        Analyze the completed run and return an updated profile markdown string.
        Returns empty string on failure (caller keeps the existing profile).
        """
        output_snippet = "\n".join(output_tail[-15:])[:600]
        history_text   = "\n".join(f"- {p}" for p in recent_prompts[-10:]) or "_none yet_"

        user_msg = _PROFILE_UPDATE_USER.format(
            profile=current_profile[:1500],
            project=project,
            prompt=prompt[:200],
            output=output_snippet,
            history=history_text,
        )
        result = self._sdk.complete(_PROFILE_UPDATE_SYSTEM, user_msg)
        if result.startswith("[SDK"):
            return ""
        return result.strip()

    def predict_prompts(
        self,
        profile: str,
        project: str,
        last_prompt: str,
        output_tail: list[str],
        recent_prompts: list[str],
        n: int = 4,
    ) -> list[str]:
        """
        Return up to n personalized next-prompt predictions.
        Returns empty list on failure (caller falls back to graph/builtins).
        """
        output_snippet = "\n".join(output_tail[-10:])[:400]
        history_text   = "\n".join(f"- {p}" for p in recent_prompts[-8:]) or "_none yet_"

        system  = _PREDICT_SYSTEM.format(n=n)
        user_msg = _PREDICT_USER.format(
            profile=profile[:1200],
            project=project,
            last_prompt=last_prompt[:200],
            output=output_snippet,
            history=history_text,
            n=n,
        )
        raw = self._sdk.complete(system, user_msg)
        if raw.startswith("[SDK"):
            return []
        try:
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start >= 0 and end > start:
                items = _json.loads(raw[start:end])
                return [str(s) for s in items if isinstance(s, str)][:n]
        except Exception:
            pass
        return []
