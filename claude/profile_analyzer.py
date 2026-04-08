"""ProfileAnalyzer — behavioral profiling, project summaries, and next-prompt prediction.

After each agent run this module:
  1. Generates a concise summary + semantic tags for the run note.
  2. Updates the global cross-project user profile (demographics, personality,
     interests, behavioral patterns, prompting style).
  3. Updates the project-specific profile (summary, tech stack, current focus).
  4. Predicts what the user will ask next, phrased in their own voice.
"""
from __future__ import annotations

import json as _json
from claude.sdk_client import ClaudeSDKClient


# ---------------------------------------------------------------------------
# Run summarization + tagging
# ---------------------------------------------------------------------------

_SUMMARIZE_SYSTEM = """\
You are summarizing a Claude coding agent run for a developer's memory vault.

Given the prompt and output tail, return a JSON object with exactly two keys:
  "summary": 1–2 sentence plain-English description of what was accomplished.
             Be specific and concrete. Start with an active verb.
  "tags": array of 3–6 lowercase tag strings.

Tag guidelines — pick from each category:
  Language (≤1): "python", "typescript", "javascript", "go", "rust", "bash", "sql", etc.
  Task type (exactly 1): "bugfix", "feature", "refactor", "test", "docs", "config",
                         "commit", "analysis", "setup"
  Topic (1–4): specific to what was touched — e.g. "auth", "database", "api", "ui",
               "cli", "performance", "security", "ci", "types", "logging", "deps"

Return ONLY valid JSON. No explanation, no markdown fences.\
"""

_SUMMARIZE_USER = """\
Project: {project}
Prompt: {prompt}
Output (tail):
{output}

Return the JSON object.\
"""


# ---------------------------------------------------------------------------
# Global user profile — demographics, personality, interests, behavior
# ---------------------------------------------------------------------------

_PROFILE_UPDATE_SYSTEM = """\
You are building a comprehensive developer profile for a user of vibe-cli,
a keyboard-first AI coding terminal. Analyze the full prompt history to infer
and update this profile. Ground every claim in quoted evidence from the prompts.

## Developer Identity
Infer experience level (junior / mid / senior / expert), likely role
(indie hacker, startup engineer, professional, student, researcher), and
primary domain. Use vocabulary, problem complexity, and tool choices as signals.
Quote exact fragments as evidence.

## Personality Traits
Map each trait to one of its poles with a quoted example:
- Perfectionist ↔ Pragmatic ("ship it" vs. "refactor first")
- Exploratory ↔ Systematic (experiments broadly vs. executes a plan)
- Detail-oriented ↔ High-level (granular diffs vs. architectural changes)
- Confident ↔ Hedging (direct imperatives vs. tentative phrasing)

## Technical Interests
Languages, frameworks, domains, and tools observed across all projects.
List by frequency (most common first). Note what they seem to enjoy vs. treat as chores.

## Behavioral Patterns
Do they finish tasks or drop them mid-thread? Do they test their own code?
Do they commit often or in batches? Do they iterate (many small prompts)
or sweep (few large ones)? Do they context-switch frequently?
Quote sequence evidence.

## Prompting Style
Phrasing style (imperative / interrogative / descriptive), verbosity (terse / detailed),
average length. Recurring vocabulary — quote exact fragments. Context they include or omit.

## Current Focus
Based on the 5–10 most recent prompts: what project / feature / file / bug
are they actively working on right now?

Rules:
- Total ≤ 600 words
- Quote exact prompt fragments (use "…" around quotes)
- Incremental: preserve established observations; add new; downweight stale ones
- Do NOT speculate beyond what the evidence shows
- Return ONLY the updated profile in markdown (same headings, no preamble)\
"""

_PROFILE_UPDATE_USER = """\
Current profile:
{profile}

Full prompt corpus across all projects (chronological, oldest first):
{all_prompts}

Latest completed run:
- Project: {project}
- Prompt: "{prompt}"

Update the profile based on the full corpus, paying special attention
to the latest run and the most recent prompts.\
"""


# ---------------------------------------------------------------------------
# Per-project profile
# ---------------------------------------------------------------------------

_PROJECT_PROFILE_SYSTEM = """\
You are maintaining a concise profile for one of a developer's coding projects.

Analyze the project's run history and update these sections:

## Summary
1–2 sentences: what this project is and what it does. Be concrete and specific.
No filler ("This is a project that..."). Start with the noun.

## Tech Stack
Languages, frameworks, libraries, tools observed in prompts and outputs. Comma-separated list.

## Current Focus
What the developer has been actively working on in recent sessions. 3–5 bullet points,
each one sentence.

## Recurring Tasks
What task types dominate this project (bugfix / feature / refactor / test / docs / etc.)
and what specific areas come up repeatedly.

Rules:
- Total ≤ 200 words
- Concrete and specific — no filler
- Incremental: preserve what's established, add new, remove outdated observations
- Return ONLY the updated profile in markdown (same headings, no preamble)\
"""

_PROJECT_PROFILE_USER = """\
Project: {project}

Current project profile:
{profile}

Recent prompts for this project (oldest first):
{recent_prompts}

Latest run:
- Prompt: "{prompt}"
- Output tail:
{output}

Update the project profile.\
"""


# ---------------------------------------------------------------------------
# Next-prompt prediction
# ---------------------------------------------------------------------------

_PREDICT_SYSTEM = """\
You are predicting what a developer will ask their AI coding assistant next.

You have their behavioral style profile and their recent prompt history.
Your predictions must:
  - Sound exactly like HOW they write prompts (match their phrasing, verbosity,
    vocabulary, and sentence structure from the profile)
  - Address what logically follows the most recent run
  - Cover distinct likely next steps — not near-duplicates
  - Be immediately usable as-is (no placeholders like [file])

Return ONLY a JSON array of {n} strings. No explanation.\
"""

_PREDICT_USER = """\
Behavioral profile:
{profile}

Current project: {project}

Recent prompts (oldest first):
{history}

Last prompt: "{last_prompt}"
Last run output (tail):
{output}

Return {n} next-prompt predictions as a JSON array.\
"""


# ---------------------------------------------------------------------------
# ProfileAnalyzer
# ---------------------------------------------------------------------------

class ProfileAnalyzer:
    """
    Uses the Anthropic SDK (via ClaudeSDKClient) to:
    - summarize_run(): generate a 1–2 sentence summary + semantic tags for a run
    - update_profile(): rebuild global behavioral/demographic profile from prompt corpus
    - update_project_profile(): rebuild per-project profile from recent project runs
    - predict_prompts(): generate personalized next-prompt predictions
    """

    def __init__(self, sdk: ClaudeSDKClient) -> None:
        self._sdk = sdk

    def is_available(self) -> bool:
        return self._sdk.is_available()

    def summarize_run(
        self,
        prompt: str,
        output_tail: list[str],
        project: str,
    ) -> tuple[str, list[str]]:
        """
        Return (summary, tags) for a completed agent run.
        summary: 1–2 sentence description of what was accomplished.
        tags: 3–6 lowercase semantic tag strings.
        Falls back to ("", []) on failure.
        """
        output_snippet = "\n".join(output_tail[-15:])[:600]
        user_msg = _SUMMARIZE_USER.format(
            project=project,
            prompt=prompt[:300],
            output=output_snippet,
        )
        raw = self._sdk.complete(_SUMMARIZE_SYSTEM, user_msg)
        if raw.startswith("[SDK"):
            return "", []
        try:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = _json.loads(raw[start:end])
                summary = str(data.get("summary", "")).strip()
                tags    = [str(t).lower().strip() for t in data.get("tags", [])
                           if isinstance(t, str)][:6]
                return summary, tags
        except Exception:
            pass
        return "", []

    def update_profile(
        self,
        prompt: str,
        output_tail: list[str],
        project: str,
        all_prompts: list[str],
        current_profile: str,
    ) -> str:
        """
        Analyze the full cross-project prompt corpus and return an updated
        global user profile. Returns empty string on failure.
        """
        corpus = "\n".join(f"- {p}" for p in all_prompts[-60:]) or "_no history yet_"
        user_msg = _PROFILE_UPDATE_USER.format(
            profile=current_profile[:2000],
            all_prompts=corpus,
            project=project,
            prompt=prompt[:300],
        )
        result = self._sdk.complete(_PROFILE_UPDATE_SYSTEM, user_msg)
        if result.startswith("[SDK"):
            return ""
        return result.strip()

    def update_project_profile(
        self,
        prompt: str,
        output_tail: list[str],
        project: str,
        recent_prompts: list[str],
        current_profile: str,
    ) -> str:
        """
        Update the per-project profile from recent project runs.
        Returns empty string on failure.
        """
        output_snippet = "\n".join(output_tail[-10:])[:400]
        history_text   = "\n".join(f"- {p}" for p in recent_prompts[-20:]) or "_none yet_"
        user_msg = _PROJECT_PROFILE_USER.format(
            project=project,
            profile=current_profile[:1500],
            recent_prompts=history_text,
            prompt=prompt[:300],
            output=output_snippet,
        )
        result = self._sdk.complete(_PROJECT_PROFILE_SYSTEM, user_msg)
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
        Return up to n next-prompt predictions phrased in the user's voice.
        Returns empty list on failure (caller falls back to graph/builtins).
        """
        output_snippet = "\n".join(output_tail[-10:])[:400]
        history_text   = "\n".join(f"- {p}" for p in recent_prompts[-12:]) or "_none yet_"

        system   = _PREDICT_SYSTEM.format(n=n)
        user_msg = _PREDICT_USER.format(
            profile=profile[:1500],
            project=project,
            last_prompt=last_prompt[:300],
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
