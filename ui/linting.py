"""
ui/linting.py — File linting helpers and language detection for the editor panel.

Imported by ui/app.py:
    from ui.linting import LintIssue, lint_file as _lint_file
    from ui.linting import LINTABLE_EXTS as _LINTABLE_EXTS
    from ui.linting import language_for as _language_for
    from ui.linting import set_ta_language as _set_ta_language
"""
from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass as _dataclass
from pathlib import Path


@_dataclass
class LintIssue:
    line: int
    col: int
    severity: str   # "error" | "warning"
    message: str


def _lint_python(path: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    try:
        source = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues

    # Always-available: ast syntax check
    try:
        ast.parse(source, filename=path)
    except SyntaxError as e:
        issues.append(LintIssue(e.lineno or 0, e.offset or 0, "error", e.msg))
        return issues  # syntax errors make pyflakes output unreliable

    # Optional: pyflakes for undefined names / unused imports
    try:
        from pyflakes.checker import Checker as _PFChecker
        import pyflakes.messages as _pf_msg

        tree = ast.parse(source, filename=path)
        checker = _PFChecker(tree, filename=path)
        for msg in checker.messages:
            sev = "error" if isinstance(msg, _pf_msg.UndefinedName) else "warning"
            issues.append(LintIssue(msg.lineno, 0, sev, msg.message % msg.message_args))
    except ImportError:
        pass
    except Exception:
        pass

    return issues


def _lint_js(path: str) -> list[LintIssue]:
    """Syntax check via `node --check`; falls back silently if node absent."""
    issues: list[LintIssue] = []
    try:
        r = subprocess.run(
            ["node", "--check", path],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            for line in (r.stderr + r.stdout).splitlines():
                m = re.match(r".+:(\d+)$", line.strip())
                if m:
                    continue
                if line.strip().startswith("SyntaxError:"):
                    issues.append(LintIssue(
                        issues[-1].line if issues else 0, 0, "error",
                        line.strip(),
                    ))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return issues


def _lint_html(path: str) -> list[LintIssue]:
    """Detect unclosed tags and bare entities via stdlib html.parser."""
    import html.parser

    issues: list[LintIssue] = []

    class _Checker(html.parser.HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self._stack: list[tuple[str, int]] = []

        def handle_starttag(self, tag: str, attrs: list) -> None:
            void = {"area","base","br","col","embed","hr","img","input",
                    "link","meta","param","source","track","wbr"}
            if tag.lower() not in void:
                self._stack.append((tag, self.getpos()[0]))

        def handle_endtag(self, tag: str) -> None:
            for i in range(len(self._stack) - 1, -1, -1):
                if self._stack[i][0] == tag.lower():
                    self._stack.pop(i)
                    return

        def handle_entityref(self, name: str) -> None:
            import html.entities
            if name not in html.entities.html5:
                issues.append(LintIssue(
                    self.getpos()[0], self.getpos()[1], "warning",
                    f"Unknown entity &{name};",
                ))

        def unclosed(self) -> list[tuple[str, int]]:
            return list(self._stack)

    try:
        source = Path(path).read_text(encoding="utf-8", errors="replace")
        checker = _Checker()
        try:
            checker.feed(source)
        except html.parser.HTMLParseError as e:
            issues.append(LintIssue(e.lineno or 0, e.offset or 0, "error", str(e)))
        for tag, lineno in checker.unclosed():
            issues.append(LintIssue(lineno, 0, "warning", f"Unclosed <{tag}>"))
    except OSError:
        pass
    return issues


def _lint_css(path: str) -> list[LintIssue]:
    """Balance check for braces + detect empty rule-sets."""
    issues: list[LintIssue] = []
    try:
        source = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues

    depth = 0
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = re.sub(r"/\*.*?\*/", "", line)
        for ch in stripped:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    issues.append(LintIssue(lineno, 0, "error", "Unmatched closing brace }"))
                    depth = 0

    if depth > 0:
        issues.append(LintIssue(0, 0, "error", f"{depth} unclosed brace(s) {{"))

    depth = 0
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = re.sub(r"/\*.*?\*/", "", line).strip()
        if "{" in stripped:
            depth += stripped.count("{") - stripped.count("}")
        elif "}" in stripped:
            depth -= stripped.count("}")
        elif depth > 0 and ":" in stripped and stripped and not stripped.endswith((";", "{", "}", ",")):
            issues.append(LintIssue(lineno, 0, "warning", f"Missing semicolon: {stripped[:60]}"))

    return issues


def _lint_markdown(path: str) -> list[LintIssue]:
    """Check for unclosed fenced code blocks and heading hierarchy jumps."""
    issues: list[LintIssue] = []
    try:
        source = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues

    fence_open: int | None = None
    fence_char: str = ""
    prev_level = 0

    for lineno, line in enumerate(source.splitlines(), 1):
        if re.match(r"^(`{3,}|~{3,})", line):
            ch = line[0]
            if fence_open is None:
                fence_open = lineno
                fence_char = ch
            elif ch == fence_char:
                fence_open = None
                fence_char = ""
            continue

        if fence_open is not None:
            continue

        m = re.match(r"^(#{1,6})\s", line)
        if m:
            level = len(m.group(1))
            if prev_level and level > prev_level + 1:
                issues.append(LintIssue(
                    lineno, 0, "warning",
                    f"Heading jumps from H{prev_level} to H{level}",
                ))
            prev_level = level

    if fence_open is not None:
        issues.append(LintIssue(fence_open, 0, "error",
                                "Unclosed fenced code block"))

    return issues


LINTABLE_EXTS = frozenset({".py", ".js", ".jsx", ".ts", ".tsx",
                            ".html", ".css", ".md"})


def language_for(path: str) -> "str | None":
    ext = Path(path).suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescript", ".jsx": "javascript", ".go": "go",
        ".rs": "rust", ".md": "markdown", ".json": "json",
        ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
        ".html": "html", ".css": "css", ".sh": "bash",
        ".bash": "bash", ".c": "c", ".cpp": "cpp",
    }.get(ext)


def set_ta_language(ta: "object", language: "str | None") -> None:
    """Set TextArea.language, silently falling back to plain text if the
    installed tree-sitter is too old for this Python version (e.g. Python 3.9
    where tree-sitter 0.24+ — needed by Textual 8.x — cannot be installed).
    """
    try:
        ta.language = language  # type: ignore[attr-defined]
    except Exception:
        try:
            ta.language = None  # type: ignore[attr-defined]
        except Exception:
            pass


def lint_file(path: str) -> list[LintIssue]:
    ext = Path(path).suffix.lower()
    if ext == ".py":
        return _lint_python(path)
    if ext in (".js", ".jsx", ".ts", ".tsx"):
        return _lint_js(path)
    if ext == ".html":
        return _lint_html(path)
    if ext == ".css":
        return _lint_css(path)
    if ext == ".md":
        return _lint_markdown(path)
    return []
