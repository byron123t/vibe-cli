"""ProfileAnalyzer — behavioral prompt-style profiling and next-prompt prediction.

After each agent run this module:
  1. Analyzes the FULL corpus of all prompts the user has ever issued (across
     all projects) to build a behavioral style profile.
  2. Uses that profile to predict what the user will ask next, phrased in
     their own voice.

The profile captures: prompting style, vocabulary, task-category distribution,
sequencing patterns, and current focus — all grounded in quoted evidence from
the actual prompt history rather than inferred psychology.
"""
from __future__ import annotations

import json as _json
from claude.sdk_client import ClaudeSDKClient


# ---------------------------------------------------------------------------
# Profile update — corpus-wide behavioral analysis
# ---------------------------------------------------------------------------

_PROFILE_UPDATE_SYSTEM = """\
You are building a behavioral prompt-style profile for a developer who uses an
AI coding assistant (vibe-cli).

Your goal: understand HOW this person writes prompts — their phrasing, vocab,
task categories, and patterns — so that future predictions sound exactly like
them and address what they actually need next.

Analyze the full prompt history and produce/update these sections:

## Prompting Style
How they phrase requests: imperative ("fix X"), interrogative ("why does X"),
descriptive ("X is broken when Y"). Verbosity: terse vs. detailed. Average
length. Whether they include context/files or not.

## Vocabulary & Phrases
Recurring words, abbreviations, project names, paths. Exact fragments that
appear multiple times (quote them). Terms they invent or shorten.

## Task Categories
Observed distribution across: bugfix / feature / refactor / test /
explanation / commit / config / tooling / docs. Which dominate?

## Sequencing Patterns
What follows what? Do they iterate (many small tweaks) or sweep (large changes)?
Do they ask follow-ups? Do they context-switch mid-task? Do they tend to finish
a thread before moving on?

## Current Focus
Based on the 5–10 most recent prompts: what project areas / files / features
are they actively working on right now?

Rules:
- Keep total profile under 500 words
- Quote exact prompt fragments as evidence (use "…" around quotes)
- Update INCREMENTALLY — preserve established observations; add new ones;
  downweight observations that haven't appeared recently
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

Update the profile based on the full corpus above, paying special attention
to the latest run and the most recent prompts.\
"""


# ---------------------------------------------------------------------------
# Next-prompt prediction — speak in the user's voice
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
    - update_profile(): rebuild behavioral profile from the full prompt corpus
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
        all_prompts: list[str],        # ALL prompts across all projects, oldest first
        current_profile: str,
    ) -> str:
        """
        Analyze the full prompt corpus and return an updated profile string.
        Returns empty string on failure (caller keeps the existing profile).

        `all_prompts` should be every prompt the user has ever issued, oldest
        first, formatted as "project: prompt text" strings.
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
