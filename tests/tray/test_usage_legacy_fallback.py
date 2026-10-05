"""旧统计文件作为补扫前的兼容回退，不覆盖已采集的当前调用记录。"""
import json

import pytest

import skill_guard as sg
from tray.app import build_runtime
from tray.usage import UsageService


@pytest.fixture
def client(tmp_path, monkeypatch):
    data, pool = tmp_path / "data", tmp_path / "pool"
    pool.mkdir()
    monkeypatch.setattr(sg, "GUARD_DIR", str(data))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(data / "snapshots.json"))
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"skills": {"legacy": {
        "zcode": 4, "claude": 0, "marker": 0}}}), encoding="utf-8")
    sg.save_config({"usage_file": str(legacy), "roots": [{"path": str(pool)}]})
    _, _, bridge = build_runtime([str(pool)], "warn")
    # 真实采集服务读取临时 fixture，绝不扫描机器上的真实会话历史。
    bridge.usage = UsageService(str(data), history_roots=[], auto_start=False)
    yield bridge, tmp_path
    bridge.usage.stop()


def test_existing_usage_file_keeps_legacy_counts_until_history_is_collected(client):
    bridge, _ = client
    result = bridge.act("get_usage", {"refresh": False})
    assert result["rows"][0]["name"] == "legacy"
    assert result["rows"][0]["total"] == 4
    assert bridge.usage.snapshot()["phase"] == "idle"


def test_historical_scan_records_take_precedence_over_legacy_file_without_summing(client):
    bridge, base = client
    logs = base / "logs"
    logs.mkdir()
    (logs / "session.jsonl").write_text(json.dumps({
        "sessionId": "session", "completedAt": "2026-10-04T18:15:00Z",
        "response": {"toolCalls": [{"id": "read", "name": "Skill", "input": {"skill": "recent"}}]}
    }) + "\n", encoding="utf-8")
    assert bridge.usage.scan(path=str(logs), source="zcode", background=False)["ok"]
    result = bridge.act("get_usage", {"refresh": False})
    assert [row["name"] for row in result["rows"]] == ["recent"]
    assert result["rows"][0]["total"] == 1
    assert result["status"]["phase"] == "ready"


def make_skill(pool, name):
    skill = pool / name
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: " + name + "\n---\n", encoding="utf-8")
    return skill


def test_inventory_zero_rows_do_not_disable_legacy_fallback_or_start_scanning(client):
    bridge, base = client
    make_skill(base / "pool", "legacy")
    make_skill(base / "pool", "unused")
    result = bridge.act("get_usage", {"refresh": False})
    by_name = {row["name"]: row for row in result.get("installed_rows", [])}
    assert set(by_name) == {"legacy", "unused"}
    assert by_name["legacy"]["total"] == 4
    assert by_name["unused"]["total"] == 0
    assert by_name["unused"]["usage_state"] == "unknown"
    assert result["status"]["phase"] == "idle"
    assert bridge.usage._thread is None


def test_usage_has_all_ranked_rows_and_all_installed_skills_over_twenty_five(client):
    bridge, base = client
    skills = {}
    for index in range(31):
        name = f"skill-{index:02d}"
        make_skill(base / "pool", name)
        skills[name] = {"codex": index + 1, "last_tool_use": "2026-09-01T00:00:00Z"}
    (base / "legacy.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")
    result = bridge.act("get_usage", {"refresh": False})
    assert len(result["rows"]) == 31
    assert len(result["installed_rows"]) == 31
    assert len(result["idle_groups"]["inactive"]) == 31
    assert result["inventory_summary"]["total_invocations"] == sum(range(1, 32))


def test_completed_history_merges_never_called_installation_without_legacy_double_count(client):
    bridge, base = client
    make_skill(base / "pool", "used")
    make_skill(base / "pool", "unused")
    logs = base / "logs"
    logs.mkdir()
    (logs / "session.jsonl").write_text(json.dumps({
        "sessionId": "session", "completedAt": "2026-10-04T18:15:00Z",
        "response": {"toolCalls": [{"id": "read", "name": "Skill", "input": {"skill": "used"}}]}
    }) + "\n", encoding="utf-8")
    bridge.usage.scan(path=str(logs), source="zcode", background=False)
    result = bridge.act("get_usage", {"refresh": False})
    assert [row["name"] for row in result["rows"]] == ["used"]
    assert result["idle_groups"]["never"][0]["name"] == "unused"
    assert result["inventory_summary"]["total_invocations"] == 1


def test_legacy_latest_use_selects_the_latest_tool_or_marker_time(client):
    bridge, base = client
    make_skill(base / "pool", "legacy")
    (base / "legacy.json").write_text(json.dumps({"skills": {"legacy": {
        "zcode": 4, "last_tool_use": "2026-09-01T23:00:00+14:00",
        "last_marker": "2026-09-01T10:00:00Z"}}}), encoding="utf-8")
    result = bridge.act("get_usage", {"refresh": False})
    assert result["rows"][0]["last"] == "2026-09-01T10:00:00Z"


def test_missing_installation_root_is_visible_in_inventory_coverage(client):
    bridge, base = client
    missing = str(base / "unavailable-skills")
    bridge.daemon.roots.append(missing)
    result = bridge.act("get_usage", {"refresh": False})
    summary = result["inventory_summary"]
    assert summary.get("inventory_complete") is False
    assert summary["unavailable_inventory_roots"] == [missing]
    assert "技能安装目录不可用" in summary["coverage_note"]
