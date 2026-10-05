"""解码策略参与检查身份，真实覆盖截断携带具体文件和行号。"""
from dataclasses import asdict
import json

import pytest

import skill_guard as sg
from tray.app import build_runtime
from tray.review import capture_version


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    data, pool = tmp_path / "data", tmp_path / "pool"
    pool.mkdir()
    monkeypatch.setattr(sg, "GUARD_DIR", str(data))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(data / "snapshots.json"))
    sg.save_config({"roots": [{"path": str(pool)}], "trust": {}})
    skill = pool / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# safe", encoding="utf-8")
    monkeypatch.setattr("tray.alerts.toast", lambda *args, **kwargs: None)
    daemon, state, bridge = build_runtime([str(pool)], "warn")
    return skill, daemon, state, bridge


def decode_gap(monkeypatch):
    original = sg.run_engine
    gap = sg.Finding("SR-OBFUS-004", "OBFUS", "LOW", "scripts/encoded.py", 17,
                     "encoded\x1bcontent", "候选数达到上限\x1b，部分载荷未检查", [])

    def limited(*args, **kwargs):
        report = original(*args, **kwargs)
        report.findings.append(gap)
        report.score = sg.score_findings(report.findings)
        return report

    monkeypatch.setattr(sg, "run_engine", limited)
    return sg._sanitize_json(asdict(gap))


def test_decode_policy_change_updates_review_version_but_preserves_content_identity(runtime, monkeypatch):
    skill, daemon, _, _ = runtime
    first = capture_version(str(skill), daemon.rules_text, daemon.blocklist_text)
    monkeypatch.setattr(sg, "DECODE_POLICY_VERSION", sg.DECODE_POLICY_VERSION + "-changed")
    changed = capture_version(str(skill), daemon.rules_text, daemon.blocklist_text)
    assert changed["version"] != first["version"]
    assert changed["content_version"] == first["content_version"]
    assert changed["hashes"] == first["hashes"]


def test_daemon_and_rescan_preserve_sanitized_decode_gap_details(runtime, monkeypatch):
    skill, daemon, state, bridge = runtime
    expected = decode_gap(monkeypatch)
    daemon.scan_changed_skill(str(skill))
    row = state.snapshot()["skills"][str(skill)]
    assert row["scan_complete"] is False
    assert row["scan_issues"] == ["decode_limit"]
    assert row["scan_gap_details"] == [expected]
    assert "\x1b" not in json.dumps(row["scan_gap_details"])
    item = {"path": str(skill), "version": row["version"]}
    result = bridge.processing.process_one("rescan", item)
    assert (result["status"], result["code"]) == ("success", "scan_incomplete")
    assert result["scan_gap_details"] == [expected]
    trust = bridge.processing.process_one("trust", item)
    assert (trust["status"], trust["code"]) == ("failed", "scan_incomplete")
    assert daemon.review_store.decision_for(str(skill), item["version"]) != "trusted"


def test_isolation_record_and_background_check_keep_decode_gap_details(runtime, monkeypatch):
    skill, daemon, state, bridge = runtime
    expected = decode_gap(monkeypatch)
    daemon.scan_changed_skill(str(skill))
    item = {"path": str(skill), "version": state.skills[str(skill)]["version"]}
    result = bridge.processing.process_one("quarantine", item)
    assert result["status"] == "success"
    record = bridge.processing._quarantine[0]
    assert record["scan_gap_details"] == [expected]
    bridge.processing._refresh_quarantine([record], bridge.processing._policy_stamp())
    refreshed = bridge.processing.snapshot()["quarantine"][0]
    assert refreshed["scan_complete"] is False
    assert refreshed["scan_gap_details"] == [expected]
