"""Integration tests — multiple components working together end-to-end."""
import os
import json
import pytest

from memory.vault import MemoryVault
from memory.moc import MOCManager
from memory.run_log import RunLogger
from memory.user_profile import UserProfile
from memory.linker import Linker
from memory.linter import VaultLinter
from graph.personalization_graph import PersonalizationGraph
from graph.knowledge_graph import KnowledgeGraph
from claude.suggestion_engine import PromptSuggestionEngine
from core.session_store import SessionStore
from personalization.predictor import Predictor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_vault(tmp_path):
    return MemoryVault(str(tmp_path / "vault"))


@pytest.fixture
def graph_path(tmp_path):
    return str(tmp_path / "graph.json")


@pytest.fixture
def full_stack(tmp_path):
    """All memory components wired together the same way the app does it."""
    vault    = MemoryVault(str(tmp_path / "vault"))
    moc      = MOCManager(vault)
    logger   = RunLogger(vault, moc)
    profile  = UserProfile(vault)
    graph    = PersonalizationGraph(str(tmp_path / "graph.json"))
    sugg     = PromptSuggestionEngine(graph)
    pred     = Predictor(graph)
    kg       = KnowledgeGraph(vault)
    return vault, moc, logger, profile, graph, sugg, pred, kg


# ---------------------------------------------------------------------------
# RunLogger → Vault → MOC pipeline
# ---------------------------------------------------------------------------

class TestRunLoggerVaultMOC:
    def test_full_log_pipeline(self, full_stack):
        vault, moc, logger, *_ = full_stack
        note = logger.log(
            "fix_auth", "fix auth bug", "myproject",
            "Fix the auth bug in login.py",
            "⟳ Read(login.py)\nFixed the bug — passwords now hashed correctly."
        )
        # Note exists on disk
        assert os.path.isfile(note.path)
        # Tags inferred correctly
        assert "run_log" in note.tags
        assert "myproject" in note.tags
        assert "bugfix" in note.tags
        # Summary extracted from output
        body = note.body()
        assert "## Summary" in body
        assert "Fixed the bug" in body
        # MOCs updated
        assert os.path.isfile(vault.moc_path("Run Outputs"))
        assert os.path.isfile(vault.moc_path("myproject"))

    def test_multiple_projects_isolated_in_vault(self, full_stack):
        vault, moc, logger, *_ = full_stack
        logger.log("a1", "alpha run", "projA", "fix things in A", "Fixed A")
        logger.log("b1", "beta run", "projB", "build feature B", "Built B")
        proj_a_notes = vault.get_project_notes("projA")
        proj_b_notes = vault.get_project_notes("projB")
        a_titles = {n.title for n in proj_a_notes}
        b_titles = {n.title for n in proj_b_notes}
        assert not a_titles.intersection(b_titles)

    def test_lint_run_produces_report(self, full_stack):
        vault, moc, logger, *_ = full_stack
        logger.log("run", "label", "proj", "prompt", "output")
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        # LintReport is always returned (even if it has issues from [[proj]] wikilink)
        assert hasattr(report, "broken_links")
        assert hasattr(report, "has_issues")
        # Only broken links to actual non-project names should be absent
        non_proj_broken = [(s, t) for s, t in report.broken_links if t != "proj"]
        assert non_proj_broken == []


# ---------------------------------------------------------------------------
# Personalization graph → suggestion engine → predictor pipeline
# ---------------------------------------------------------------------------

class TestPersonalizationPipeline:
    def test_record_and_retrieve_suggestions(self, full_stack):
        *_, graph, sugg, pred, _ = full_stack
        sugg.record("myproject", "Fix the login bug")
        sugg.record("myproject", "Add unit tests")
        suggestions = sugg.get_suggestions("myproject", n=4)
        assert len(suggestions) > 0
        # Recently used prompts should appear
        assert any("login" in s.lower() or "unit test" in s.lower()
                   for s in suggestions)

    def test_graph_persistence_across_instances(self, full_stack, tmp_path):
        *_, graph, sugg, _, _ = full_stack
        graph_path = str(tmp_path / "graph.json")
        g1 = PersonalizationGraph(graph_path)
        for _ in range(3):
            g1.record_use("prompt:fix bug", "proj")
        g1.save()
        g2 = PersonalizationGraph(graph_path)
        stats = g2.action_stats()
        assert "prompt:fix bug" in stats
        assert stats["prompt:fix bug"]["total_uses"] == 3

    def test_predictor_ranks_frequent_action_higher(self, full_stack):
        *_, graph, sugg, pred, _ = full_stack
        for _ in range(10):
            graph.record_use("prompt:common action", "proj")
        graph.record_use("prompt:rare action", "proj")
        ranked = pred.rank_actions(
            ["prompt:common action", "prompt:rare action"], "proj"
        )
        assert ranked[0][0] == "prompt:common action"

    def test_get_all_prompts_cross_project(self, full_stack):
        *_, graph, sugg, _, _ = full_stack
        sugg.record("projA", "build feature X")
        sugg.record("projB", "write tests for Y")
        all_prompts = sugg.get_all_prompts(n=60)
        combined = " ".join(all_prompts)
        assert "feature X" in combined
        assert "tests for Y" in combined


# ---------------------------------------------------------------------------
# UserProfile + vault integration
# ---------------------------------------------------------------------------

class TestUserProfileVault:
    def test_global_profile_separate_from_project(self, full_stack):
        vault, *_, profile, _, _, _, _ = full_stack
        profile.write("# Global Profile\n\n## Developer Identity\nExpert.")
        profile.write_project("proj", "# Project Profile\n\n## Summary\nA web app.")
        assert "Expert" in profile.read()
        assert "web app" in profile.read_project("proj")
        assert "Expert" not in profile.read_project("proj")

    def test_project_profile_under_project_dir(self, full_stack, tmp_path):
        vault = full_stack[0]
        profile = UserProfile(vault)
        profile.write_project("myproj", "content")
        expected = os.path.join(vault.root, "projects", "myproj", "profile.md")
        assert os.path.isfile(expected)


# ---------------------------------------------------------------------------
# Knowledge graph + vault integration
# ---------------------------------------------------------------------------

class TestKnowledgeGraphVault:
    def test_run_logs_become_graph_nodes(self, full_stack):
        vault, moc, logger, *rest = full_stack
        kg = rest[-1]
        logger.log("r1", "run1", "proj", "fix login", "Fixed login")
        logger.log("r2", "run2", "proj", "add tests", "Added tests for [[run1]]")
        kg.build()
        assert kg.node_count() >= 2

    def test_wikilinks_in_notes_become_edges(self, full_stack):
        vault = full_stack[0]
        kg = full_stack[-1]
        vault.create_note("notes/a", "NoteA", "See [[NoteB]] for details")
        vault.create_note("notes/b", "NoteB", "See [[NoteA]] back")
        kg.build()
        assert kg.edge_count() == 2

    def test_central_nodes_after_logging(self, full_stack):
        vault, moc, logger, *rest = full_stack
        kg = rest[-1]
        # Create a hub note referenced by many run logs
        vault.create_note("notes/hub", "HubNote", "Central concept")
        for i in range(4):
            vault.create_note(
                f"notes/spoke{i}", f"Spoke{i}",
                f"relates to [[HubNote]]"
            )
        kg.build()
        central = kg.get_central_nodes(top_n=3)
        assert any(title == "HubNote" for title, _ in central)


# ---------------------------------------------------------------------------
# SessionStore + full pipeline
# ---------------------------------------------------------------------------

import core.session_store as _ss_module


class TestSessionStorePipeline:
    def test_save_and_restore_project_state(self, tmp_path, monkeypatch):
        path = str(tmp_path / "session.json")
        monkeypatch.setattr(_ss_module, "SESSION_FILE", path)
        store = SessionStore()
        state = {
            "version": 1,
            "global": {
                "active_project_idx": 1,
                "permission_mode": "accept_edits",
                "agent_type": "claude",
                "show_graph": False,
            },
            "projects": {
                "/some/project": {
                    "agents": [
                        {
                            "number": 1,
                            "prompt": "fix the bug",
                            "session_id": "abc123",
                            "project_path": "/some/project",
                            "permission_mode": "accept_edits",
                            "exit_code": 0,
                            "output": "Fixed it.",
                        }
                    ]
                }
            }
        }
        store.save(state)
        loaded = store.load()
        agent = loaded["projects"]["/some/project"]["agents"][0]
        assert agent["prompt"] == "fix the bug"
        assert agent["exit_code"] == 0
        assert loaded["global"]["permission_mode"] == "accept_edits"

    def test_cap_output_preserves_recent_lines(self):
        lines = [f"output line {i}" for i in range(600)]
        result = SessionStore.cap_output(lines)
        # The very last line should be there
        assert "output line 599" in result
        # Lines from the very beginning should be trimmed
        assert "output line 0" not in result


# ---------------------------------------------------------------------------
# End-to-end: simulate a full post-run-hook sequence
# ---------------------------------------------------------------------------

class TestEndToEndPostRunHook:
    def test_full_hook_sequence(self, full_stack, tmp_path):
        """
        Simulate what _post_run_hook does:
        1. Record in suggestion engine
        2. Log run (with inferred tags + summary)
        3. Update index MOC
        4. Run linter
        5. Check project profile is writable
        """
        vault, moc, logger, profile, graph, sugg, pred, kg = full_stack

        project = "testproject"
        prompt  = "fix the authentication bug in login.py"
        output  = ("⟳ Read(login.py)\n"
                   "Fixed the authentication bug — passwords now hashed with bcrypt.")

        # Step 1: record
        sugg.record(project, prompt)

        # Step 2: log run
        note = logger.log(
            action_id="fix_auth_bug",
            action_label=prompt[:60],
            project=project,
            prompt=prompt,
            output=output,
        )
        assert os.path.isfile(note.path)
        assert "bugfix" in note.tags
        assert "auth" in note.tags
        assert "Fixed the authentication bug" in note.body()

        # Step 3: index MOC
        moc.update_index_moc()
        assert os.path.isfile(vault.moc_path("Index"))

        # Step 4: linter — run logs wikilink back to [[project]] which isn't a note;
        # that's the only expected broken link. No other broken links should exist.
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        unexpected = [(s, t) for s, t in report.broken_links if t != project]
        assert unexpected == []

        # Step 5: project profile is writable
        profile.write_project(project, "## Summary\nAuth service.")
        assert "Auth service." in profile.read_project(project)

        # Step 6: suggestions populated after recording
        suggestions = sugg.get_suggestions(project, n=4)
        assert len(suggestions) > 0

    def test_multiple_runs_accumulate_in_moc(self, full_stack):
        vault, moc, logger, *_ = full_stack
        for i in range(5):
            logger.log(f"action_{i}", f"run {i}", "proj",
                       f"prompt {i}", f"output {i}")
        # All 5 should be linked in the project MOC
        with open(vault.moc_path("proj")) as f:
            content = f.read()
        assert content.count("[[") >= 5

    def test_knowledge_graph_reflects_logged_runs(self, full_stack):
        vault, moc, logger, _, _, _, _, kg = full_stack
        logger.log("r1", "run 1", "proj", "fix bug", "Fixed it")
        logger.log("r2", "run 2", "proj", "add feature", "Done [[2026-04-08 00:00 run 1]]")
        kg.build()
        assert kg.node_count() >= 2
