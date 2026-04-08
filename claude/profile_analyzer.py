"""ProfileAnalyzer — LLM-powered forensic behavioral profiling and prompt personalization.

After each agent run this module:
  1. Generates a concise summary + semantic tags for the run note.
  2. Runs forensic analysis on the full prompt corpus to build a structured
     JSON profile: demographics, personality, interests, behavioral patterns.
  3. Updates the per-project profile (summary, tech stack, current focus).
  4. Predicts what the user will ask next, phrased in their own voice.

The global profile is stored as structured JSON — each category has typed
attributes and specific qualifier arrays, enabling downstream personalization.
"""
from __future__ import annotations

import json as _json
import re
from collections import Counter
from claude.sdk_client import ClaudeSDKClient


# ---------------------------------------------------------------------------
# Non-LLM keyword tables (same patterns as run_log._infer_tags)
# ---------------------------------------------------------------------------

_LANG_PATTERNS: list[tuple[str, str]] = [
    ("python",     r"\bpython\b|\.py\b|import |def |pytest"),
    ("typescript", r"\btypescript\b|\.ts\b|interface |type "),
    ("javascript", r"\bjavascript\b|\.js\b|const |let |npm "),
    ("go",         r"\bgolang\b|\bgo\b|\.go\b|func |goroutine"),
    ("rust",       r"\brust\b|\.rs\b|cargo |fn |impl "),
    ("bash",       r"\bbash\b|#!/|shell |\.sh\b"),
    ("sql",        r"\bsql\b|SELECT |INSERT |CREATE TABLE"),
]

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "auth":        ["auth", "login", "password", "token", "jwt", "oauth", "session"],
    "database":    ["database", " db ", "sql", "postgres", "sqlite", "redis", "orm"],
    "api":         ["api", "endpoint", "rest", "graphql", "request", "response", "http"],
    "ui":          ["widget", "component", "button", "layout", "css", "style", "render"],
    "cli":         ["cli", "command", "argument", "flag", "terminal", "shell", "argparse"],
    "testing":     ["test", "pytest", "unittest", "assert", "coverage", "mock"],
    "devops":      ["docker", "deploy", "ci/cd", "github action", "pipeline", "workflow"],
    "ai":          ["llm", "claude", "gpt", "openai", "anthropic", "agent", "prompt"],
}

_TASK_PATTERNS: list[tuple[str, list[str]]] = [
    ("bugfix",   ["fix", "bug", "error", "broken", "crash", "debug"]),
    ("test",     ["test", "pytest", "unittest", "spec", "assert", "coverage"]),
    ("docs",     ["readme", "docstring", "documentation", "comment", "document"]),
    ("refactor", ["refactor", "clean", "restructure", "rename", "simplify"]),
    ("feature",  ["add", "implement", "create", "build", "new", "write"]),
]


def _basic_profile_from_prompts(prompts: list[str]) -> dict:
    """Build a basic forensic profile from prompt text alone — no LLM required."""
    if not prompts:
        return {}

    corpus = " ".join(prompts).lower()
    n = len(prompts)

    # --- languages ---
    langs = []
    for lang, pat in _LANG_PATTERNS:
        if re.search(pat, corpus, re.IGNORECASE):
            langs.append(lang)

    # --- domains ---
    domains = [d for d, kws in _DOMAIN_KEYWORDS.items()
               if any(kw in corpus for kw in kws)]

    # --- task distribution ---
    task_counts: Counter = Counter()
    for prompt in prompts:
        pt = prompt.lower()
        for task_type, kws in _TASK_PATTERNS:
            if any(kw in pt for kw in kws):
                task_counts[task_type] += 1
                break

    dominant_task = task_counts.most_common(1)[0][0] if task_counts else "feature"
    test_count = sum(1 for p in prompts if any(k in p.lower() for k in ["test", "pytest", "assert"]))

    # --- prompting style ---
    avg_len = sum(len(p.split()) for p in prompts) / n
    verbosity = "terse" if avg_len < 8 else ("detailed" if avg_len > 20 else "moderate")

    question_count = sum(1 for p in prompts if p.strip().endswith("?") or p.lower().startswith(("why", "how", "what", "explain")))
    phrasing = "interrogative" if question_count > n * 0.4 else "imperative"

    # --- recurring vocabulary (top non-trivial words) ---
    stopwords = {"the", "a", "an", "and", "or", "in", "on", "at", "to", "for",
                 "of", "with", "it", "is", "this", "that", "be", "as", "from",
                 "i", "my", "me", "we", "you", "he", "she", "they", "all",
                 "add", "fix", "make", "get", "set", "use", "run", "update"}
    word_freq: Counter = Counter()
    for p in prompts:
        for w in re.findall(r"\b[a-z]{4,}\b", p.lower()):
            if w not in stopwords:
                word_freq[w] += 1
    vocab = [w for w, _ in word_freq.most_common(6) if word_freq[w] > 1]

    # --- iteration style ---
    iteration = "many small prompts" if avg_len < 10 else ("few sweeping prompts" if avg_len > 25 else "mixed")

    return {
        "demographics": {
            "estimated_age_range": "unknown",
            "likely_occupation": "software engineer",
            "likely_location": "unknown",
            "experience_level": "unknown",
            "role_type": "unknown",
            "education_signal": "unknown",
        },
        "personality": {
            "work_style": "unknown",
            "approach": "unknown",
            "focus_granularity": "unknown",
            "confidence": "unknown",
            "traits": [],
        },
        "technical_interests": {
            "primary_languages": langs,
            "frameworks": [],
            "domains": domains,
            "tools": [],
            "enjoys": [],
            "avoids_or_delegates": [],
        },
        "behavioral_patterns": {
            "completion_tendency": "unknown",
            "testing_behavior": "writes tests" if test_count > n * 0.2 else "delegates testing",
            "commit_pattern": "unknown",
            "iteration_style": iteration,
            "context_switching": "unknown",
            "prompting_cadence": f"avg {avg_len:.0f} words/prompt, {n} total runs",
        },
        "prompting_style": {
            "phrasing": phrasing,
            "verbosity": verbosity,
            "recurring_vocabulary": vocab,
            "context_inclusion": "unknown",
        },
        "inferences": {
            "likely_motivations": [],
            "current_focus": "unknown",
            "project_maturity": "unknown",
            "career_signal": f"primarily {dominant_task} tasks",
        },
    }


# ---------------------------------------------------------------------------
# Run summarization + tagging
# ---------------------------------------------------------------------------

_SUMMARIZE_SYSTEM = """\
You are summarizing a coding agent run for a developer's memory vault.

Given the prompt and output tail, return a JSON object with exactly two keys:
  "summary": 1–2 sentence plain-English description of what was accomplished.
             Be specific and concrete. Start with an active verb.
  "tags": array of 3–6 lowercase tag strings.

Tag guidelines:
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
# Forensic global user profile — structured JSON
# ---------------------------------------------------------------------------

_FORENSIC_PROFILE_SYSTEM = """\
You are a forensic behavioral analyst specializing in developer psychology.

The user will provide a list of prompts they have given to an AI coding assistant,
plus their current profile (may be empty on first run).

Analyze these prompts to build or update a comprehensive forensic profile of the person.
Use clues in vocabulary, problem complexity, tools chosen, naming conventions, phrasing
style, and topic patterns to make specific inferences — including demographics.

Return ONLY a JSON object with this exact structure (all fields required):

{
  "demographics": {
    "estimated_age_range": "e.g. 25-35",
    "likely_occupation": "specific job title inferred",
    "likely_location": "country/region inferred from tooling, conventions, or references",
    "experience_level": "junior | mid | senior | expert",
    "role_type": "indie hacker | startup engineer | enterprise dev | student | researcher | freelancer | other",
    "education_signal": "self-taught | CS degree | bootcamp | unclear"
  },
  "personality": {
    "work_style": "perfectionist | pragmatic",
    "approach": "exploratory | systematic",
    "focus_granularity": "detail-oriented | high-level",
    "confidence": "confident | hedging",
    "traits": ["specific trait 1", "specific trait 2", "..."]
  },
  "technical_interests": {
    "primary_languages": ["lang1", "lang2"],
    "frameworks": ["fw1", "fw2"],
    "domains": ["domain1", "domain2"],
    "tools": ["tool1", "tool2"],
    "enjoys": ["things they seem to enjoy working on"],
    "avoids_or_delegates": ["things they treat as chores or hand off to the AI"]
  },
  "behavioral_patterns": {
    "completion_tendency": "finishes tasks | abandons mid-thread | mixed",
    "testing_behavior": "writes tests | asks AI to test | skips testing",
    "commit_pattern": "frequent small commits | batched large commits | unclear",
    "iteration_style": "many small prompts | few sweeping prompts | mixed",
    "context_switching": "frequent across projects | stays focused | unclear",
    "prompting_cadence": "short description of how/when they prompt"
  },
  "prompting_style": {
    "phrasing": "imperative | interrogative | descriptive | mixed",
    "verbosity": "terse | moderate | detailed",
    "recurring_vocabulary": ["word or phrase 1", "word or phrase 2"],
    "context_inclusion": "minimal | moderate | heavy"
  },
  "inferences": {
    "likely_motivations": ["specific motivation 1", "specific motivation 2"],
    "current_focus": "one sentence on what they are actively building right now",
    "project_maturity": "prototyping | building | refining | maintaining",
    "career_signal": "specific inference about career stage or direction"
  }
}

Rules:
- Be specific and confident — do not hedge with "possibly" or "maybe"
- Make new inferences beyond just what is explicitly stated
- Preserve established observations from the current profile; add/update with new evidence
- Ground key inferences in exact quoted fragments from prompts where possible
- Return ONLY valid JSON. No explanation, no markdown, no code fences.\
"""

_FORENSIC_PROFILE_USER = """\
Current profile (empty if first run):
{current_profile}

Full prompt corpus (chronological, oldest first):
{all_prompts}

Latest run:
- Project: {project}
- Prompt: "{prompt}"

Update the forensic profile JSON.\
"""


# ---------------------------------------------------------------------------
# Per-project profile (markdown — concise project summary)
# ---------------------------------------------------------------------------

_PROJECT_PROFILE_SYSTEM = """\
You are maintaining a concise profile for one of a developer's coding projects.

Analyze the project's run history and update these sections:

## Summary
1–2 sentences: what this project is and what it does. Concrete and specific.
No filler. Start with the noun.

## Tech Stack
Languages, frameworks, libraries, tools observed. Comma-separated list.

## Current Focus
What the developer has been actively working on recently. 3–5 bullet points, one sentence each.

## Recurring Tasks
What task types dominate (bugfix / feature / refactor / test / docs / etc.)
and what specific areas come up repeatedly.

Rules:
- Total ≤ 200 words
- Concrete and specific — no filler
- Incremental: preserve what's established, add new, remove outdated
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

You have their forensic behavioral profile (JSON) and recent prompt history.
Your predictions must:
  - Sound exactly like HOW they write prompts — match their phrasing style,
    verbosity, vocabulary, and sentence structure from the profile
  - Address what logically follows the most recent run
  - Cover distinct likely next steps — not near-duplicates
  - Be immediately usable as-is (no placeholders like [file] or [name])

Return ONLY a JSON array of {n} strings. No explanation.\
"""

_PREDICT_USER = """\
Forensic behavioral profile:
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
    LLM-powered forensic behavioral profiler and prompt personalizer.

    Always writes a profile after every run (using keyword-based analysis as
    baseline). When the Anthropic SDK is available, enriches it with a full
    forensic LLM analysis covering demographics, personality, and inferences.

    - build_basic_profile():    fast keyword-based profile — always works
    - build_forensic_profile(): structured JSON profile via LLM
    - summarize_run():          1–2 sentence summary + semantic tags
    - update_project_profile(): per-project markdown profile via LLM
    - predict_prompts():        next-prompt predictions in the user's own voice
    """

    def __init__(self, sdk: ClaudeSDKClient) -> None:
        self._sdk = sdk

    def is_available(self) -> bool:
        return self._sdk.is_available()

    def build_basic_profile(self, all_prompts: list[str]) -> dict:
        """
        Build a keyword-based profile from the prompt corpus. No LLM required.
        Returns a populated profile dict that can be written immediately.
        """
        return _basic_profile_from_prompts(all_prompts)

    # ------------------------------------------------------------------ run summary

    def summarize_run(
        self,
        prompt: str,
        output_tail: list[str],
        project: str,
    ) -> tuple[str, list[str]]:
        """
        Return (summary, tags) for a completed agent run.
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
                data    = _json.loads(raw[start:end])
                summary = str(data.get("summary", "")).strip()
                tags    = [str(t).lower().strip() for t in data.get("tags", [])
                           if isinstance(t, str)][:6]
                return summary, tags
        except Exception:
            pass
        return "", []

    # ------------------------------------------------------------------ forensic profile

    def build_forensic_profile(
        self,
        prompt: str,
        project: str,
        all_prompts: list[str],
        current_profile: dict,
    ) -> dict:
        """
        Analyze the full cross-project prompt corpus and return an updated
        forensic profile dict. Returns empty dict on failure.

        The returned dict has the structure defined in _FORENSIC_PROFILE_SYSTEM:
        demographics, personality, technical_interests, behavioral_patterns,
        prompting_style, inferences.
        """
        corpus = "\n".join(f"- {p}" for p in all_prompts[-80:]) or "_no history yet_"
        current_json = _json.dumps(current_profile, indent=2) if current_profile else "{}"

        user_msg = _FORENSIC_PROFILE_USER.format(
            current_profile=current_json[:2000],
            all_prompts=corpus,
            project=project,
            prompt=prompt[:300],
        )
        raw = self._sdk.complete(_FORENSIC_PROFILE_SYSTEM, user_msg)
        if raw.startswith("[SDK"):
            return {}
        try:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start >= 0 and end > start:
                return _json.loads(raw[start:end])
        except Exception:
            pass
        return {}

    # ------------------------------------------------------------------ project profile

    def update_project_profile(
        self,
        prompt: str,
        output_tail: list[str],
        project: str,
        recent_prompts: list[str],
        current_profile: str,
    ) -> str:
        """
        Update the per-project markdown profile from recent project runs.
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

    # ------------------------------------------------------------------ prediction

    def predict_prompts(
        self,
        profile: dict,
        project: str,
        last_prompt: str,
        output_tail: list[str],
        recent_prompts: list[str],
        n: int = 4,
    ) -> list[str]:
        """
        Return up to n next-prompt predictions phrased in the user's voice.
        profile is the forensic JSON dict (or empty dict if unavailable).
        Returns empty list on failure.
        """
        output_snippet = "\n".join(output_tail[-10:])[:400]
        history_text   = "\n".join(f"- {p}" for p in recent_prompts[-12:]) or "_none yet_"
        profile_str    = _json.dumps(profile, indent=2)[:1500] if profile else "{}"

        system   = _PREDICT_SYSTEM.format(n=n)
        user_msg = _PREDICT_USER.format(
            profile=profile_str,
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
