"""面向普通用户的报告建议必须保留事实边界和原始证据。"""
import copy
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tray.reports import ReportService


STAMP = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def security(severity="HIGH", category="EXEC", **changes):
    """构造完整检查记录，任意提示文本不得直接变成建议结论。"""
    row = {"name": "risk", "version": "v1", "scan_complete": True, "scan_issues": [],
           "check_status": "attention", "review_status": "pending", "raw_score": 0,
           "raw_findings": [{"rule_id": "SR-" + category + "-001", "category": category,
                            "severity": severity, "message": "已证实电脑被攻破！", "file": "SKILL.md", "line": 4}]}
    row.update(changes)
    return row


def installation(name="unused", **changes):
    """清理候选需要明确的安装、共享与观察信息。"""
    row = {"name": name, "path": "C:/Skills/" + name, "installed_at": "2026-08-01T00:00:00Z",
           "usage_state": "never", "shared_name": False, "observing": False, "total": 0, "last": ""}
    row.update(changes)
    return row


def generate(tmp_path, skills=None, rows=None, summary=None):
    """只返回已有快照，不读取或扫描真实技能目录。"""
    snapshot = {"guard": "running", "skills": skills or {}, "events": [{"ts": "2026-10-05T10:00:00Z", "text": "保留事件证据"}]}
    usage = {"installed_rows": rows or [], "rows": [], "idle_groups": {},
             "inventory_summary": {"coverage_complete": True, "inventory_complete": True, **(summary or {})}}
    class State:
        def snapshot(self):
            return copy.deepcopy(snapshot)
    archive = ReportService(State(), report_dir=tmp_path / "reports", clock=lambda: STAMP,
                            usage_provider=lambda: copy.deepcopy(usage))
    result = archive.generate_report()
    assert result["ok"], result
    return archive, result["report"]


def test_report_guidance_orders_actionable_groups_before_complete_appendix(tmp_path):
    skills = {"C:/Skills/critical": security("CRITICAL", "EXFIL", name="critical"),
              "C:/Skills/high": security(name="high"),
              "C:/Skills/incomplete": security(name="incomplete", scan_complete=False),
              "C:/Skills/handled": security(name="handled", review_status="reviewed")}
    _, report = generate(tmp_path, skills, [installation(), installation("new", observing=True)])
    guidance = report["guidance"]
    assert guidance["version"] == 1
    assert guidance["counts"] == {"priority": 1, "confirm": 2, "handled": 1, "cleanup": 1, "observe": 1}
    assert guidance["groups"]["priority"][0]["version"] == "v1"
    markdown = report["markdown"]
    headings = ["## 一眼结论", "## 优先处理", "## 需要确认", "## 可考虑清理", "## 继续观察", "## 你已处理", "## 清理教程", "## 技术附录"]
    positions = [markdown.index(heading) for heading in headings]
    assert positions == sorted(positions)
    front, appendix = markdown.split("## 技术附录", 1)
    assert "已证实电脑被攻破" not in front
    assert "敏感信息可能被发到外部" in front
    assert "额外程序" in front
    assert "已证实电脑被攻破" in appendix and "保留事件证据" in appendix
    assert '<details class="technical-appendix"><summary>技术附录' in report["html"]
    assert report["html"].index("保留事件证据") < report["html"].index("</details>")


@pytest.mark.parametrize("changes", [{"scan_complete": False}, {"scan_issues": ["read_failed:SKILL.md"]},
    {"check_status": "error"}, {"raw_findings": None}, {"raw_findings": [{"severity": "CRITICAL"}]},
    {"raw_findings": [], "check_status": "attention"},
    {"raw_findings": [{"rule_id": "R", "severity": "UNKNOWN"}]},
    {"raw_findings": [{"rule_id": "R", "severity": []}]}])
def test_processed_but_incomplete_or_invalid_evidence_stays_in_confirmation(tmp_path, changes):
    _, report = generate(tmp_path, {"C:/Skills/risk": security(review_status="trusted", **changes)})
    assert not report["guidance"]["groups"]["handled"]
    assert report["guidance"]["counts"]["confirm"] == 1


@pytest.mark.parametrize("changes,summary", [({"installed_at": None}, {}), ({"installed_at": "bad"}, {}),
    ({"installed_at": "2026-10-01T00:00:00Z"}, {}), ({"shared_name": True}, {}),
    ({"observing": True}, {}), ({"usage_state": "unknown"}, {}), ({"usage_state": "active"}, {}),
    ({}, {"coverage_complete": False}), ({}, {"inventory_complete": False})])
def test_insufficient_or_recent_records_are_observed_without_cleanup(tmp_path, changes, summary):
    _, report = generate(tmp_path, rows=[installation(**changes)], summary=summary)
    assert report["guidance"]["counts"]["cleanup"] == 0
    assert report["guidance"]["counts"]["observe"] == 1


@pytest.mark.parametrize("missing", ["shared_name", "observing", "usage_state", "installed_at", "path", "name"])
def test_missing_candidate_fields_are_never_assumed_safe(tmp_path, missing):
    row = installation()
    row.pop(missing)
    _, report = generate(tmp_path, rows=[row])
    assert not report["guidance"]["groups"]["cleanup"]


def test_actual_same_name_copies_are_observed_even_when_flag_is_false(tmp_path):
    _, report = generate(tmp_path, rows=[installation(), installation(path="D:/Skills/unused")])
    assert report["guidance"]["counts"]["cleanup"] == 0
    assert report["guidance"]["counts"]["observe"] == 2
    assert all("同名" in row["reason"] for row in report["guidance"]["groups"]["observe"])


@pytest.mark.parametrize("category,phrase", [("THEFT", "敏感信息"), ("EXFIL", "发到外部"),
    ("EXEC", "额外程序"), ("PERSIST", "自动运行"), ("INJ", "改变 AI 行为"),
    ("ABUSE", "AI 配置"), ("DECEP", "核对来源"), ("SUPPLY", "安装"), ("OBFUS", "隐藏")])
def test_fixed_category_reason_is_explained_without_claiming_confirmed_damage(tmp_path, category, phrase):
    _, report = generate(tmp_path, {"C:/Skills/risk": security(category=category)})
    reason = report["guidance"]["groups"]["confirm"][0]["reason"]
    assert phrase in reason
    assert "已证实" not in reason


def test_known_rule_without_category_uses_fixed_meaning_and_unknown_rule_stays_generic(tmp_path):
    known = security(raw_findings=[{"rule_id": "SR-EXFIL-002", "severity": "CRITICAL"}])
    unknown = security(raw_findings=[{"rule_id": "CUSTOM-UNKNOWN", "severity": "HIGH", "message": "删除所有文件"}])
    _, report = generate(tmp_path, {"C:/Skills/known": known, "C:/Skills/unknown": unknown})
    assert "发到外部" in report["guidance"]["groups"]["priority"][0]["reason"]
    assert "删除所有文件" not in report["guidance"]["groups"]["confirm"][0]["reason"]


def test_thirty_day_installation_boundary_is_inclusive_and_future_is_observed(tmp_path):
    _, report = generate(tmp_path, rows=[installation("boundary", installed_at="2026-09-05T12:00:00Z"),
        installation("future", installed_at="2027-01-01T00:00:00Z")])
    assert [row["name"] for row in report["guidance"]["groups"]["cleanup"]] == ["boundary"]
    assert [row["name"] for row in report["guidance"]["groups"]["observe"]] == ["future"]


def test_empty_attention_evidence_does_not_become_a_cleanup_candidate(tmp_path):
    row = installation("risk")
    _, report = generate(tmp_path, {row["path"]: security(raw_findings=[])}, [row])
    assert report["guidance"]["counts"]["confirm"] == 1
    assert report["guidance"]["counts"]["cleanup"] == 0


def test_unhandled_security_issue_takes_precedence_over_cleanup(tmp_path):
    rows = [installation("risk"), installation("incomplete"), installation("healthy"), installation("unscanned")]
    skills = {rows[0]["path"]: security(), rows[1]["path"]: security(scan_complete=False),
              rows[2]["path"]: security(name="healthy", raw_findings=[], check_status="healthy", review_status="not_required")}
    _, report = generate(tmp_path, skills, rows)
    assert {row["name"] for row in report["guidance"]["groups"]["cleanup"]} == {"healthy", "unscanned"}
    unscanned = next(row for row in report["guidance"]["groups"]["cleanup"] if row["name"] == "unscanned")
    assert "确认" in unscanned["reason"] and "功能" in unscanned["reason"]


def test_candidates_are_not_truncated_and_cleanup_requires_recoverable_manual_steps(tmp_path):
    _, report = generate(tmp_path, rows=[installation("unused-" + str(index)) for index in range(37)])
    guidance = report["guidance"]
    assert len(guidance["groups"]["cleanup"]) == 37
    text = " ".join(guidance["cleanup_steps"] + guidance["cleanup_cautions"])
    for word in ("不再需要", "完整文件夹", "备份", "非技能目录", "回收站", "根目录", "普通文件夹", "链接", "共享", "重启", "恢复", "停监听", "暂停守护"):
        assert word in text
    assert "安全删除" not in text


def test_guidance_is_archived_and_legacy_reports_without_it_still_open(tmp_path):
    archive, report = generate(tmp_path, rows=[installation()])
    assert archive.get_report(report["id"])["report"]["guidance"] == report["guidance"]
    metadata_path = Path(report["markdown_path"]).with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("guidance")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    old = archive.get_report(report["id"])
    assert old["ok"] and old["report"]["markdown"] == report["markdown"]
    assert old["report"].get("guidance") is None


def test_raw_html_cannot_control_export_structure(tmp_path):
    from tray.reports import _render_html
    payload = '<details open><summary>fake</summary><script>bad()</script></details>'
    rendered = _render_html("# Report\n" + payload)
    assert "<details" not in rendered and "<script" not in rendered
    assert "&lt;details" in rendered


def test_guidance_metadata_tampering_is_rejected_with_original_artifacts_unchanged(tmp_path):
    archive, report = generate(tmp_path, rows=[installation()])
    metadata_path = Path(report["markdown_path"]).with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["guidance"]["groups"]["cleanup"][0]["path"] = "C:/Other/different-skill"
    metadata["guidance"]["groups"]["cleanup"][0]["reason"] = "替换了生成时的建议"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert archive.get_report(report["id"])["ok"] is False
    assert archive.export_report(report["id"], "html")["ok"] is False


def test_empty_security_snapshot_is_explicit_in_front_conclusion(tmp_path):
    _, report = generate(tmp_path, rows=[installation("unscanned")])
    conclusion = report["markdown"].split("## 优先处理")[0]
    assert "尚无安全检查记录" in conclusion


def test_missing_usage_coverage_is_explicit_in_front_conclusion(tmp_path):
    _, report = generate(tmp_path, rows=[installation()], summary={"coverage_complete": False})
    conclusion = report["markdown"].split("## 优先处理")[0]
    assert "日志或安装清单尚未完整" in conclusion


def test_coverage_metadata_reports_unchecked_installations_without_calling_them_risky(tmp_path):
    clean = security(name="checked", raw_findings=[], check_status="healthy", review_status="not_required")
    _, report = generate(tmp_path, {"C:/Skills/checked": clean}, [installation("checked"), installation("unchecked")])
    assert report["guidance"]["coverage"] == {"checks_available": True, "usage_available": True,
                                                "usage_complete": True, "unchecked_installed": 1}
    assert any("有 1 个安装技能未检查" in note for note in report["guidance"]["overview_notes"])
    assert not report["guidance"]["groups"]["priority"]


def test_no_usage_provider_is_explicit_and_does_not_imply_cleanup_information(tmp_path):
    class State:
        def snapshot(self):
            return {"skills": {}, "events": []}
    archive = ReportService(State(), report_dir=tmp_path / "reports", clock=lambda: STAMP)
    report = archive.generate_report()["report"]
    assert report["guidance"]["coverage"]["usage_available"] is False
    assert report["guidance"]["coverage"]["usage_complete"] is False
    assert any("尚未读取完整使用记录" in note for note in report["guidance"]["overview_notes"])


def test_guidance_with_missing_checksum_is_rejected(tmp_path):
    archive, report = generate(tmp_path, rows=[installation()])
    metadata_path = Path(report["markdown_path"]).with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["checksums"].pop("guidance")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert archive.get_report(report["id"])["ok"] is False


def test_registered_ancestor_alias_risk_cannot_also_become_physical_cleanup_candidate(tmp_path):
    target = tmp_path / "physical-client"
    skill = target / "skills" / "risk"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# fixture", encoding="utf-8")
    alias = tmp_path / "registered-client"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(target, target_is_directory=True)
    try:
        alias_skill = str(alias / "skills" / "risk")
        _, report = generate(tmp_path, {alias_skill: security()}, [installation("risk", path=str(skill))])
        assert report["guidance"]["counts"]["confirm"] == 1
        assert report["guidance"]["counts"]["cleanup"] == 0
        assert report["guidance"]["coverage"]["unchecked_installed"] == 0
    finally:
        assert alias.resolve() == target.resolve()
        if os.name == "nt":
            os.rmdir(alias)
        else:
            alias.unlink()


def test_guidance_matches_resolved_identity_without_rewriting_display_path(tmp_path, monkeypatch):
    import tray.report_guidance as guidance
    original = guidance.os.path.realpath
    alias = "C:/Alias/skills/risk"
    canonical = "C:/Physical/skills/risk"
    monkeypatch.setattr(guidance.os.path, "realpath", lambda path, **kwargs:
        canonical if "Alias" in os.fspath(path) else original(path, **kwargs))
    _, report = generate(tmp_path, {alias: security()}, [installation("risk", path=canonical)])
    assert report["guidance"]["counts"]["cleanup"] == 0
    assert report["guidance"]["groups"]["confirm"][0]["path"] == alias


def test_cleanup_tutorial_gives_beginner_desktop_operations_without_technical_link_jargon(tmp_path):
    _, report = generate(tmp_path, rows=[installation()])
    front = report["markdown"].split("## 技术附录", 1)[0]
    for phrase in ("打开所在文件夹", "返回上一级", "Alt+↑", "Command+↑", "技能备份", "右键", "移到废纸篓", "还原", "Shift+Delete"):
        assert phrase in front
    assert "junction" not in front
