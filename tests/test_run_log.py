"""Tests for memory.run_log — tag inference, summary extraction, and RunLogger."""
import os
import pytest

from memory.vault import MemoryVault
from memory.moc import MOCManager
from memory.run_log import RunLogger, _infer_tags, _simple_summary


# ---------------------------------------------------------------------------
# _infer_tags
# ---------------------------------------------------------------------------

class TestInferTags:
    def test_python_lang(self):
        tags = _infer_tags("fix the bug in auth.py", "Fixed import error")
        assert "python" in tags

    def test_typescript_lang(self):
        tags = _infer_tags("fix TypeScript type errors in api.ts", "Resolved errors")
        assert "typescript" in tags

    def test_bugfix_task(self):
        tags = _infer_tags("fix the crash in main", "Fixed the crash")
        assert "bugfix" in tags

    def test_feature_task(self):
        tags = _infer_tags("add a new login endpoint", "Created endpoint")
        assert "feature" in tags

    def test_docs_task(self):
        tags = _infer_tags("write a readme for this project", "Created README.md")
        assert "docs" in tags

    def test_test_task(self):
        tags = _infer_tags("write pytest unit tests for the parser", "Added tests")
        assert "test" in tags

    def test_commit_task(self):
        tags = _infer_tags("commit all changes with a good message", "git commit done")
        assert "commit" in tags

    def test_auth_topic(self):
        tags = _infer_tags("fix the auth token validation", "Fixed JWT auth")
        assert "auth" in tags

    def test_api_topic(self):
        tags = _infer_tags("add a REST api endpoint for users", "Created endpoint")
        assert "api" in tags

    def test_no_duplicate_tags(self):
        tags = _infer_tags("fix python bug", "Fixed python error")
        assert len(tags) == len(set(tags))

    def test_max_tags(self):
        # Should never produce more than 5 tags
        tags = _infer_tags(
            "fix python typescript auth api database bug",
            "Fixed everything in auth.py and api.ts"
        )
        assert len(tags) <= 5

    def test_empty_input(self):
        tags = _infer_tags("", "")
        assert isinstance(tags, list)

    def test_returns_list(self):
        result = _infer_tags("write a readme", "done")
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)


# ---------------------------------------------------------------------------
# _simple_summary
# ---------------------------------------------------------------------------

class TestSimpleSummary:
    def test_extracts_last_substantive_line(self):
        output = "⟳ Read(main.py)\nFixed the login bug — passwords are now hashed correctly."
        summary = _simple_summary("fix login", output)
        assert "hashed" in summary

    def test_skips_tool_lines(self):
        output = "⟳ Bash(ls)\n⟳ Read(file.py)\nDone! Created the new endpoint."
        summary = _simple_summary("create endpoint", output)
        assert "⟳" not in summary

    def test_skips_code_fences(self):
        output = "⟳ Write(foo.py)\n```python\nprint('hi')\n```\nCreated foo.py successfully."
        summary = _simple_summary("create foo", output)
        assert "```" not in summary

    def test_fallback_to_prompt(self):
        # All lines are tool-use or too short
        output = "⟳ Bash(ls)\n⟳ Read(x)\nok"
        summary = _simple_summary("write a readme", output)
        assert len(summary) > 0

    def test_truncates_long_line(self):
        long_line = "a" * 500
        summary = _simple_summary("test", long_line)
        assert len(summary) <= 220

    def test_returns_string(self):
        result = _simple_summary("do something", "did it")
        assert isinstance(result, str)

    def test_empty_output(self):
        result = _simple_summary("fix bug", "")
        assert isinstance(result, str)
        assert len(result) > 0  # falls back to prompt


# ---------------------------------------------------------------------------
# RunLogger
# ---------------------------------------------------------------------------

@pytest.fixture
def logger(tmp_path):
    vault = MemoryVault(str(tmp_path / "vault"))
    moc = MOCManager(vault)
    return RunLogger(vault, moc), vault, moc


class TestRunLogger:
    def test_creates_note_file(self, logger):
        rl, vault, _ = logger
        note = rl.log("test_action", "test action", "myproject", "do a thing", "did it")
        assert os.path.isfile(note.path)

    def test_note_has_base_tags(self, logger):
        rl, _, _ = logger
        note = rl.log("test_action", "test action", "myproject", "do a thing", "did it")
        assert "run_log" in note.tags
        assert "myproject" in note.tags

    def test_note_has_inferred_tags(self, logger):
        rl, _, _ = logger
        note = rl.log(
            "fix_bug", "fix the python bug", "proj",
            "fix the python bug", "Fixed import error in main.py"
        )
        assert "python" in note.tags
        assert "bugfix" in note.tags

    def test_extra_tags_merged(self, logger):
        rl, _, _ = logger
        note = rl.log(
            "action", "do thing", "proj",
            "write tests", "done",
            extra_tags=["llm-tag-1", "llm-tag-2"],
        )
        assert "llm-tag-1" in note.tags
        assert "llm-tag-2" in note.tags

    def test_no_duplicate_tags(self, logger):
        rl, _, _ = logger
        note = rl.log(
            "action", "do thing", "proj",
            "write tests", "done",
            extra_tags=["run_log"],  # duplicate of base tag
        )
        assert note.tags.count("run_log") == 1

    def test_summary_in_body(self, logger):
        rl, _, _ = logger
        note = rl.log(
            "action", "do thing", "proj",
            "do a thing", "⟳ Read(x)\nCompleted the refactor successfully.",
        )
        body = note.body()
        assert "## Summary" in body
        assert len(body.split("## Summary")[1].strip()) > 0

    def test_llm_summary_takes_priority(self, logger):
        rl, _, _ = logger
        note = rl.log(
            "action", "do thing", "proj",
            "do a thing", "output text here",
            summary="LLM-provided summary sentence.",
        )
        assert "LLM-provided summary sentence." in note.body()

    def test_prompt_in_body(self, logger):
        rl, _, _ = logger
        note = rl.log("action", "my prompt", "proj", "my prompt", "output")
        assert "my prompt" in note.body()

    def test_output_in_body(self, logger):
        rl, _, _ = logger
        note = rl.log("action", "label", "proj", "prompt", "the output text")
        assert "the output text" in note.body()

    def test_creates_project_dir(self, logger):
        rl, vault, _ = logger
        rl.log("action", "label", "newproject", "prompt", "output")
        proj_dir = os.path.join(vault.root, "projects", "newproject")
        assert os.path.isdir(proj_dir)

    def test_creates_run_outputs_moc(self, logger):
        rl, vault, _ = logger
        rl.log("action", "label", "proj", "prompt", "output")
        moc_path = vault.moc_path("Run Outputs")
        assert os.path.isfile(moc_path)

    def test_creates_project_moc(self, logger):
        rl, vault, _ = logger
        rl.log("action", "label", "myproj", "prompt", "output")
        moc_path = vault.moc_path("myproj")
        assert os.path.isfile(moc_path)

    def test_note_linked_in_project_moc(self, logger):
        rl, vault, _ = logger
        note = rl.log("action", "label", "myproj", "prompt", "output")
        with open(vault.moc_path("myproj")) as f:
            content = f.read()
        assert note.title in content

    def test_note_linked_in_run_outputs_moc(self, logger):
        rl, vault, _ = logger
        note = rl.log("action", "label", "proj", "prompt", "output")
        with open(vault.moc_path("Run Outputs")) as f:
            content = f.read()
        assert note.title in content

    def test_files_modified_section(self, logger):
        rl, _, _ = logger
        note = rl.log(
            "action", "label", "proj", "prompt", "output",
            files_modified=["src/main.py", "tests/test_main.py"],
        )
        body = note.body()
        assert "src/main.py" in body
        assert "tests/test_main.py" in body

    def test_get_recent_outputs(self, logger):
        rl, _, _ = logger
        rl.log("a1", "first", "proj", "prompt1", "output1")
        rl.log("a2", "second", "proj", "prompt2", "output2")
        outputs = rl.get_recent_outputs("proj", n=5)
        assert len(outputs) >= 2

    def test_get_recent_outputs_empty_project(self, logger):
        rl, _, _ = logger
        outputs = rl.get_recent_outputs("nonexistent_project", n=5)
        assert outputs == []

    def test_multiple_runs_same_project(self, logger):
        rl, vault, _ = logger
        for i in range(3):
            rl.log(f"action_{i}", f"run {i}", "proj", f"prompt {i}", f"output {i}")
        notes = vault.get_project_notes("proj")
        run_logs = [n for n in notes if "run_log" in n.tags]
        assert len(run_logs) == 3
