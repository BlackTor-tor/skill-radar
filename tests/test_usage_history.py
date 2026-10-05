"""Observed skill loads from real agent log schemas, without parsing catalogs."""
import json
from pathlib import Path
import skill_monitor as sm


def append(path, record):
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")


def codex_call(call_id, command, timestamp="2026-10-04T09:20:01Z"):
    return {"type": "response_item", "timestamp": timestamp,
            "payload": {"type": "function_call", "name": "exec_command",
                        "call_id": call_id, "arguments": json.dumps({"cmd": command})}}


def test_codex_structured_read_load_and_replay_dedup(tmp_path):
    log = tmp_path / "rollout.jsonl"
    append(log, {"type": "session_meta", "payload": {"id": "session"}})
    append(log, codex_call("load", "Get-Content -LiteralPath 'C:/skills/debugging/SKILL.md'"))
    state, data = {}, {"skills": {}}
    assert getattr(sm, "scan_codex", lambda *a, **k: 0)(state, data, roots=[str(tmp_path)]) == 1
    assert data["skills"]["debugging"]["codex"] == 1
    append(log, codex_call("load", "Get-Content -LiteralPath 'C:/skills/debugging/SKILL.md'"))
    assert sm.scan_codex(state, data, roots=[str(tmp_path)]) == 0
    assert data["skills"]["debugging"]["last_tool_use"] == "2026-10-04T09:20:01Z"


def test_codex_exec_wrapper_reads_multiple_skills_but_skips_catalog_and_search(tmp_path):
    log = tmp_path / "rollout.jsonl"
    command = 'Get-Content -LiteralPath "C:/skills/one/SKILL.md"; Get-Content -LiteralPath "C:/skills/two/SKILL.md"'
    script = "const r = await tools.exec_command({cmd: " + json.dumps(command) + "}); text(r.output)"
    append(log, {"type": "response_item", "timestamp": "2026-10-04T10:00:00Z", "payload": {
        "type": "custom_tool_call", "name": "exec", "call_id": "wrapped", "input": script}})
    append(log, {"type": "response_item", "payload": {"type": "message", "role": "developer",
        "content": [{"text": "Get-Content C:/skills/catalog/SKILL.md"}]}})
    append(log, codex_call("search", "rg SKILL.md C:/skills/unused/SKILL.md"))
    append(log, codex_call("echo", "echo \"Get-Content C:/skills/fake/SKILL.md\""))
    data = {"skills": {}}
    assert getattr(sm, "scan_codex", lambda *a, **k: 0)({}, data, roots=[str(tmp_path)]) == 2
    assert set(data["skills"]) == {"one", "two"}


def test_codex_partial_log_line_is_read_on_next_refresh(tmp_path):
    log = tmp_path / "rollout.jsonl"
    raw = json.dumps(codex_call("load", "cat /skills/linux/SKILL.md"))
    log.write_text(raw[:50], encoding="utf-8")
    state, data = {}, {"skills": {}}
    assert getattr(sm, "scan_codex", lambda *a, **k: 0)(state, data, roots=[str(tmp_path)]) == 0
    with log.open("a", encoding="utf-8") as out:
        out.write(raw[50:] + "\n")
    assert getattr(sm, "scan_codex", lambda *a, **k: 0)(state, data, roots=[str(tmp_path)]) == 1


def test_zcode_skill_read_fallback_is_counted_once_with_explicit_skill_call(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "ZCODE_ROLLOUT", str(tmp_path))
    log = tmp_path / "model-io.jsonl"
    append(log, {"sessionId": "s", "completedAt": "2026-10-04T12:10:00Z", "response": {
        "toolCalls": [{"id": "invoke", "name": "Skill", "input": {"skill": "debugging"}},
                      {"id": "read", "name": "Read", "input": {"file_path": "C:/skills/debugging/SKILL.md"}},
                      {"id": "other", "name": "Read", "input": {"file_path": "C:/skills/other/SKILL.md"}}]}})
    data = {"skills": {}}
    assert sm.scan_zcode({}, data) == 2
    assert data["skills"]["debugging"]["zcode"] == 1
    assert data["skills"]["other"]["zcode"] == 1


def test_upgrade_reuses_existing_exact_offsets_without_recounting(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "ZCODE_ROLLOUT", str(tmp_path))
    log = tmp_path / "model-io.jsonl"
    record = {"sessionId": "old", "response": {"toolCalls": [{"id": "call", "name": "Skill", "input": {"skill": "old"}}]}}
    append(log, record)
    state = {"offsets": {str(log): log.stat().st_size}, "recent_ids": ["z|old|call"]}
    data = {"skills": {"old": {"zcode": 1}}}
    assert sm.scan_zcode(state, data) == 0
    assert data["skills"]["old"]["zcode"] == 1
    append(log, record)
    assert sm.scan_zcode(state, data) == 0


def test_zcode_agent_event_schema_and_rollout_copy_dedup(tmp_path):
    log = tmp_path / "agent.jsonl"
    append(log, {"type": "tool_call_scheduled", "sessionId": "s", "timestamp": "2026-10-04T09:00:00Z", "payload": {
        "toolName": "Skill", "toolCallId": "call", "input": {"skill": "nested"}}})
    append(log, {"type": "model_request", "sessionId": "s", "payload": {
        "catalog": "Get-Content C:/skills/not-invoked/SKILL.md"}})
    state, data = {}, {"skills": {}}
    assert sm.scan_history(state, data, [str(log)], "zcode") == 1
    copy = tmp_path / "rollout.jsonl"
    append(copy, {"sessionId": "s", "response": {"toolCalls": [
        {"id": "call", "name": "Skill", "input": {"skill": "nested"}}]}})
    assert sm.scan_history(state, data, [str(copy)], "zcode") == 0
    assert set(data["skills"]) == {"nested"}


def test_collection_publishes_progress_after_a_completed_file(tmp_path):
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    for index, log in enumerate((first, second)):
        append(log, codex_call(str(index), "cat /skills/live/SKILL.md"))
    progress = []
    stats = {}
    try:
        sm.scan_history({}, {"skills": {}}, [str(first), str(second)], "codex", stats,
                        on_progress=lambda: progress.append(stats.copy()))
    except TypeError:
        pass
    assert progress and progress[-1]["files_scanned"] == 2


def test_unresolved_path_and_write_tool_are_not_skill_usage(tmp_path):
    log = tmp_path / "rollout.jsonl"
    append(log, codex_call("dynamic", "Get-Content '$env:SKILLS/debugging/SKILL.md'"))
    append(log, {"type": "tool_call_scheduled", "sessionId": "s", "payload": {
        "toolName": "Write", "toolCallId": "write", "input": {"file_path": "C:/skills/not-read/SKILL.md", "content": "text"}}})
    data = {"skills": {}}
    assert sm.scan_history({}, data, [str(log)]) == 0
    assert data["skills"] == {}


def test_cli_log_root_scans_custom_history_and_prints_codex_counts(tmp_path, monkeypatch, capsys):
    import sys
    log = tmp_path / "custom" / "history.jsonl"
    log.parent.mkdir()
    append(log, codex_call("cli", "cat /skills/custom/SKILL.md"))
    monkeypatch.setattr(sm, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(sm, "DATA_FILE", str(tmp_path / "data.json"))
    monkeypatch.setattr(sm, "ZCODE_ROLLOUT", str(tmp_path / "no-zcode"))
    monkeypatch.setattr(sm, "CLAUDE_PROJECTS", str(tmp_path / "no-claude"))
    monkeypatch.setattr(sm, "AGENTS_SKILLS", str(tmp_path / "no-skills"))
    monkeypatch.setattr(sm, "MARKER_SOURCES", [])
    monkeypatch.setattr(sys, "argv", ["skill_monitor.py", "--log-root", str(log.parent), "--log-source", "auto", "--json"])
    try:
        sm.main()
    except SystemExit:
        pass
    assert Path(sm.DATA_FILE).exists()
    exported = json.loads(Path(sm.DATA_FILE).read_text(encoding="utf-8"))
    assert exported["skills"]["custom"]["codex"] == 1
    status = exported.get("meta", {}).get("usage_status", {})
    assert status.get("phase") == "ready"
    assert status["files_scanned"] == 1
    assert status["roots"] == [{"source": "auto", "path": str(log.parent), "exists": True}]
    assert "codex+1" in capsys.readouterr().out


def test_tool_command_examples_comments_and_inplace_edits_are_excluded(tmp_path):
    log = tmp_path / "session.jsonl"
    scripts = ['// tools.exec_command({cmd: "cat /skills/comment/SKILL.md"})',
               '/* tools.exec_command({cmd: "cat /skills/comment2/SKILL.md"}) */']
    for index, script in enumerate(scripts):
        append(log, {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": str(index), "input": script}})
    append(log, codex_call("edit", "sed -i 's/old/new/' /skills/modified/SKILL.md"))
    import shlex
    command = "python -c " + shlex.quote('print("Path(\'/skills/example/SKILL.md\').read_text()")')
    append(log, codex_call("python-example", command))
    data = {"skills": {}}
    assert sm.scan_history({}, data, [str(log)]) == 0
    assert data["skills"] == {}


def test_literal_python_read_is_recognized(tmp_path):
    log = tmp_path / "session.jsonl"
    import shlex
    command = "python -c " + shlex.quote("from pathlib import Path; print(Path('/skills/python/SKILL.md').read_text())")
    append(log, codex_call("read", command))
    data = {"skills": {}}
    assert sm.scan_history({}, data, [str(log)]) == 1
    assert set(data["skills"]) == {"python"}


def test_latest_tool_use_compares_actual_instants_across_timezones(tmp_path):
    log = tmp_path / "session.jsonl"
    append(log, codex_call("early", "cat /skills/timezone/SKILL.md", "2026-10-04T18:00:00+14:00"))
    append(log, codex_call("late", "cat /skills/timezone/SKILL.md", "2026-10-04T05:00:00Z"))
    data = {"skills": {}}
    assert sm.scan_history({}, data, [str(log)], "codex") == 2
    assert data["skills"]["timezone"]["last_tool_use"] == "2026-10-04T05:00:00Z"


def test_invalid_timestamp_does_not_hide_valid_last_use_or_drop_counts(tmp_path):
    log = tmp_path / "session.jsonl"
    append(log, codex_call("valid", "cat /skills/valid/SKILL.md", "2026-10-04T05:00:00Z"))
    append(log, codex_call("invalid", "cat /skills/valid/SKILL.md", "not-a-timestamp"))
    data = {"skills": {}}
    assert sm.scan_history({}, data, [str(log)], "codex") == 2
    assert data["skills"]["valid"]["codex"] == 2
    assert data["skills"]["valid"]["last_tool_use"] == "2026-10-04T05:00:00Z"
    assert "not-a-time" not in data["daily"]


def test_cli_default_scan_does_not_invent_completed_history_coverage(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sm, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(sm, "DATA_FILE", str(tmp_path / "data.json"))
    monkeypatch.setattr(sm, "MARKER_SOURCES", [])
    monkeypatch.setattr(sm, "ZCODE_ROLLOUT", str(tmp_path / "missing-zcode"))
    monkeypatch.setattr(sm, "CLAUDE_PROJECTS", str(tmp_path / "missing-claude"))
    monkeypatch.setattr(sm, "AGENTS_SKILLS", str(tmp_path / "missing-skills"))
    monkeypatch.setattr(sys, "argv", ["skill_monitor.py", "--json"])
    sm.main()
    exported = json.loads(Path(sm.DATA_FILE).read_text(encoding="utf-8"))
    status = exported.get("meta", {}).get("usage_status", {})
    assert status.get("phase") == "idle"
    assert status["files_scanned"] == 0 and status["scanning"] is False
