import json

import pytest

from aegaeon_lite.session import Session


def task(**values):
    return {"request": "Build the requested files", "checks": [],
            "allowed_paths": ["**"], "max_actions": 30, **values}


def test_three_reads_survive_the_two_exchange_window(tmp_path):
    for name in ("requirements.md", "contract.py", "checks.py"):
        (tmp_path / name).write_text("Important requirement in " + name)

    class Provider:
        calls = 0

        def complete(self, messages):
            self.calls += 1
            if self.calls <= 3:
                return json.dumps({"action": "read", "path":
                                   ["requirements.md", "contract.py", "checks.py"][self.calls-1]})
            context = json.loads(messages[1]["content"])
            files = context["working_memory"]["inspected_files"]
            assert len(files) == 3
            assert any("Important requirement in requirements.md" in f["content"] for f in files)
            return json.dumps({"action": "finish", "summary": "Inspected", "unresolved": []})

    Session(tmp_path, task(mode="ask"), Provider()).run()


def test_missing_file_returns_creation_guidance_without_mutating(tmp_path):
    result = Session(tmp_path, task(), None).tool({"action": "read", "path": "index.html"})
    assert result["exists"] is False and "expected_sha256:null" in result["next_step"]
    assert not (tmp_path / "index.html").exists()


def test_unproductive_cycle_is_bounded_and_not_marked_success(tmp_path):
    class Provider:
        def complete(self, messages):
            return json.dumps({"action": "read", "path": "index.html"})

    session = Session(tmp_path, task(), Provider())
    result = session.run()
    assert result["status"] == "Partial"
    assert "without progress" in result["summary"]
    assert len([e for e in session.history if e["type"] == "recovery"]) == 3
    assert len([e for e in session.history if e["type"] == "tool"]) == 2


def test_read_memory_is_bounded_and_eviction_is_explicit(tmp_path):
    session = Session(tmp_path, task(), None)
    for i in range(10):
        name = f"file{i}.py"
        (tmp_path / name).write_text("X" * 5000)
        observation = session.tool({"action": "read", "path": name})
        session.event({"type": "tool", "action": "read", "path": name,
                       "observation": observation})
    files = session.working_memory()["inspected_files"]
    assert sum(len(f.get("content", "")) for f in files) <= 9000
    assert any(f.get("memory_truncated") for f in files)
    assert files[0]["path"] == "file9.py"


def test_edit_invalidates_old_source_in_memory(tmp_path):
    session = Session(tmp_path, task(), None)
    (tmp_path / "code.py").write_text("OLD = 1\n")
    observation = session.tool({"action": "read", "path": "code.py"})
    session.event({"type": "tool", "action": "read", "path": "code.py",
                   "observation": observation})
    edited = session.tool({"action": "edit", "path": "code.py",
                           "expected_sha256": observation["sha256"],
                           "old": "OLD = 1", "new": "NEW = 2"})
    session.event({"type": "tool", "action": "edit", "path": "code.py",
                   "observation": edited})
    memory = session.working_memory()["inspected_files"][0]
    assert memory["needs_read"] is True
    assert "content" not in memory
    assert memory["sha256"] == edited["sha256"]


def test_create_is_compatible_but_cannot_escape_scope_or_overwrite(tmp_path):
    session = Session(tmp_path, task(allowed_paths=["code.py"]), None)
    session.tool({"action": "create", "path": "code.py", "content": "VALUE = 1\n"})
    with pytest.raises(ValueError, match="overwrite"):
        session.tool({"action": "create", "path": "code.py", "content": "VALUE = 2\n"})
    with pytest.raises(ValueError, match="scope"):
        session.tool({"action": "create", "path": "other.py", "content": "VALUE = 2\n"})
    assert (tmp_path/"code.py").read_text() == "VALUE = 1\n"


def test_noop_edit_does_not_claim_a_changed_file(tmp_path):
    (tmp_path/"code.py").write_text("VALUE = 1\n")
    session = Session(tmp_path, task(), None)
    read = session.tool({"action": "read", "path": "code.py"})
    result = session.tool({"action": "edit", "path": "code.py",
                           "expected_sha256": read["sha256"],
                           "old": "VALUE = 1", "new": "VALUE = 1"})
    assert result["unchanged"] == "code.py" and not session.changed


def test_question_synthesizes_from_complete_evidence_without_extra_tools(tmp_path):
    (tmp_path / "config.py").write_text("LIMIT = 4\n")

    class Provider:
        calls = 0

        def complete(self, messages):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"action": "read", "path": "config.py"})
            assert messages[0]["content"].startswith("Answer the code question")
            assert "LIMIT = 4" in messages[1]["content"]
            return json.dumps({"summary": "LIMIT is 4", "unresolved": []})

    result = Session(tmp_path, task(mode="ask"), Provider()).run()
    assert result["status"] == "Answer" and result["summary"] == "LIMIT is 4"
