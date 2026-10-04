"""Usage accounting regressions reproduced during the full code review."""
import json
import os
import sys

import pytest

import skill_monitor as sm


def _marker_log(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "MARKER_SOURCES", [("codex", str(tmp_path), ("*.jsonl",))])
    monkeypatch.setattr(sm, "CHUNK", 128)
    return tmp_path / "session.jsonl", {}, {"skills": {}}


def test_marker_survives_append_boundary(tmp_path, monkeypatch):
    log, state, data = _marker_log(tmp_path, monkeypatch)
    log.write_bytes(b"<!-- skill-marker:split")
    assert sm.scan_markers(state, data) == 0
    with log.open("ab") as f:
        f.write(b" -->\n")
    assert sm.scan_markers(state, data) == 1
    assert data["skills"]["split"]["marker"] == 1
    assert sm.scan_markers(state, data) == 0


def test_marker_survives_long_name_chunk_boundary(tmp_path, monkeypatch):
    log, state, data = _marker_log(tmp_path, monkeypatch)
    name = "long_skill_name_" * 4
    log.write_bytes(b"x" * 110 + ("<!-- skill-marker:" + name + " -->").encode())
    assert sm.scan_markers(state, data) == 1
    assert name in data["skills"]


def test_marker_keeps_all_session_dedup_entries(tmp_path, monkeypatch):
    log, state, data = _marker_log(tmp_path, monkeypatch)
    log.write_bytes("\n".join("<!-- skill-marker:skill%02d -->" % i for i in range(60)).encode())
    assert sm.scan_markers(state, data) == 60
    with log.open("ab") as f:
        f.write(b"\n<!-- skill-marker:skill00 -->")
    assert sm.scan_markers(state, data) == 0
    assert data["skills"]["skill00"]["marker"] == 1


def test_marker_exact_chunk_length_advances_offset(tmp_path, monkeypatch):
    log, state, data = _marker_log(tmp_path, monkeypatch)
    log.write_bytes(b"x" * sm.CHUNK)
    sm.scan_markers(state, data)
    assert state["marker_offsets"][str(log)] == sm.CHUNK


def test_atime_only_skill_counts_after_baseline(tmp_path, monkeypatch):
    skill = tmp_path / "atime-only"
    skill.mkdir()
    md = skill / "SKILL.md"
    md.write_text("# skill", encoding="utf-8")
    monkeypatch.setattr(sm, "AGENTS_SKILLS", str(tmp_path))
    state, data = {}, {"skills": {}}
    assert sm.scan_atime(state, data) == 0
    stat = md.stat()
    os.utime(md, (stat.st_atime + 20, stat.st_mtime))
    assert sm.scan_atime(state, data) == 1
    assert data["skills"]["atime-only"]["atime"] == 1


def test_claude_nested_subagent_log_is_counted(tmp_path, monkeypatch):
    log = tmp_path / "project" / "session" / "subagents" / "agent.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"message": {"content": [{"type": "tool_use", "name": "Skill",
                                                        "input": {"skill": "nested"}}]}}) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(sm, "CLAUDE_PROJECTS", str(tmp_path))
    data = {"skills": {}}
    assert sm.scan_claude({}, data) == 1
    assert data["skills"]["nested"]["claude"] == 1


def test_marker_default_zcode_pattern_covers_rollout_files(tmp_path, monkeypatch):
    # The exact layer and marker layer must scan the same root-level rollout files.
    source = next(src for src in sm.MARKER_SOURCES if src[0] == "zcode")
    monkeypatch.setattr(sm, "MARKER_SOURCES", [(source[0], str(tmp_path), source[2])])
    (tmp_path / "rollout.jsonl").write_text("<!-- skill-marker:zcode-demo -->\n", encoding="utf-8")
    assert sm.scan_markers({}, {"skills": {}}) == 1


def test_counter_checkpoint_recovers_interrupted_second_save(tmp_path, monkeypatch):
    logs = tmp_path / "zcode"
    logs.mkdir()
    (logs / "rollout.jsonl").write_text(json.dumps({"sessionId": "session", "response": {
        "toolCalls": [{"id": "call", "name": "Skill", "input": {"skill": "demo"}}]}}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(sm, "ZCODE_ROLLOUT", str(logs))
    monkeypatch.setattr(sm, "CLAUDE_PROJECTS", str(tmp_path / "no-claude"))
    monkeypatch.setattr(sm, "AGENTS_SKILLS", str(tmp_path / "no-skills"))
    monkeypatch.setattr(sm, "MARKER_SOURCES", [])
    monkeypatch.setattr(sm, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(sm, "DATA_FILE", str(tmp_path / "data.json"))
    monkeypatch.setattr(sys, "argv", ["skill_monitor.py"])
    save = sm._save
    def fail_data(path, data):
        if path == sm.DATA_FILE:
            raise OSError("counter export interrupted")
        save(path, data)
    monkeypatch.setattr(sm, "_save", fail_data)
    with pytest.raises(OSError, match="counter export interrupted"):
        sm.main()
    monkeypatch.setattr(sm, "_save", save)
    sm.main()
    with open(sm.DATA_FILE, encoding="utf-8") as f:
        assert json.load(f)["skills"]["demo"]["zcode"] == 1


def test_zcode_distinct_calls_without_ids_count_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "ZCODE_ROLLOUT", str(tmp_path))
    (tmp_path / "rollout.jsonl").write_text(json.dumps({"response": {"toolCalls": [
        {"name": "Skill", "input": {"skill": "first"}},
        {"name": "Skill", "input": {"skill": "second"}}]}}) + "\n", encoding="utf-8")
    data = {"skills": {}}
    assert sm.scan_zcode({}, data) == 2
    assert set(data["skills"]) == {"first", "second"}


def test_structured_layers_ignore_wrong_shape_records(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "ZCODE_ROLLOUT", str(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(sm, "CLAUDE_PROJECTS", str(tmp_path))
    records = ['["Skill"]', '{"message":"Skill"}', '{"response":{"toolCalls":"Skill"}}']
    valid = json.dumps({"response": {"toolCalls": [{"id": "call", "name": "Skill", "input": {"skill": "valid"}}]}})
    (tmp_path / "rollout.jsonl").write_text("\n".join(records + [valid]) + "\n", encoding="utf-8")
    (project / "session.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")
    assert sm.scan_zcode({}, {"skills": {}}) == 1
    assert sm.scan_claude({}, {"skills": {}}) == 0


def test_claude_replayed_tool_id_counts_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "CLAUDE_PROJECTS", str(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    log = project / "session.jsonl"
    record = json.dumps({"message": {"content": [{"id": "unique-call", "type": "tool_use", "name": "Skill",
                                                 "input": {"skill": "demo"}}]}}) + "\n"
    log.write_text(record, encoding="utf-8")
    state, data = {}, {"skills": {}}
    assert sm.scan_claude(state, data) == 1
    with log.open("a", encoding="utf-8") as f:
        f.write(record)
    assert sm.scan_claude(state, data) == 0
    assert data["skills"]["demo"]["claude"] == 1


def test_marker_parser_rejects_embedded_comment_or_multiline_names():
    assert sm._marker_hits(b"<!-- skill-marker:wrong <!-- hidden -->") == []
    assert sm._marker_hits(b"<!-- skill-marker:wrong\nname -->") == []
