"""客户端报告应保存当时事实，不重扫、不改变人工决定。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tray.state import TrayState


STAMP = datetime(2026, 10, 4, 13, 14, 15, 123456,
                 tzinfo=timezone(timedelta(hours=8)))


def service(tmp_path, state=None):
    from tray.reports import ReportService
    return ReportService(state or TrayState(), report_dir=tmp_path / "reports",
                         clock=lambda: STAMP)


def finding(severity="CRITICAL", **changes):
    value = dict(rule_id="R-1", category="EXEC", severity=severity,
                 file="scripts/check.py", line=7, excerpt="dangerous command",
                 message="核查命令的用途", tags=["shell"])
    value.update(changes)
    return value


def populated_state():
    state = TrayState()
    state.watched_roots = 2
    state.record_skill("C:/pool-a/same", "same", 25, "drifted",
                       findings=[finding("HIGH")], raw_findings=[finding()],
                       raw_score=50, check_status="attention", scan_complete=True,
                       review_status="trusted", scanned_at="2026-10-04T12:00:00+08:00",
                       scan_issues=[], coverage_notes=["binary_not_text_checked:icon.png"],
                       version="version-a")
    state.record_skill("D:/pool-b/same", "same", 0, "baseline-unreviewed",
                       findings=[], raw_findings=[], raw_score=0,
                       check_status="incomplete", scan_complete=False,
                       review_status="pending", scanned_at="2026-10-04T12:10:00+08:00",
                       scan_issues=["file_unreadable:SKILL.md"], version="version-b")
    state.record_skill("D:/pool-b/clean", "clean", 0, "scanned",
                       findings=[], raw_findings=[], raw_score=0,
                       check_status="healthy", scan_complete=True,
                       review_status="not_required", scanned_at="2026-10-04T12:20:00+08:00",
                       scan_issues=[], version="version-c")
    state.add_event("scan", "重新检查完成 same")
    return state


def test_generates_complete_timestamped_snapshot_without_changing_state(tmp_path):
    state = populated_state()
    before = state.snapshot()
    result = service(tmp_path, state).generate_report()
    assert result["ok"] is True
    report = result["report"]
    assert report["generated_at"] == "2026-10-04T13:14:15+08:00"
    assert report["summary"] == {"total": 3, "healthy": 1, "attention": 1,
                                  "incomplete": 1, "findings": 1}
    markdown = report["markdown"]
    for text in ("C:/pool-a/same", "D:/pool-b/same", "version-a", "CRITICAL",
                 "原始风险分", "50", "scripts/check.py", "7", "dangerous command",
                 "已人工信任", "检查未完成", "file_unreadable:SKILL.md",
                 "binary_not_text_checked:icon.png", "2026-10-04T12:00:00+08:00",
                 "重新检查完成 same", "仅反映生成时保存的检查结果"):
        assert text in markdown
    assert state.snapshot() == before
    assert Path(report["markdown_path"]).read_text(encoding="utf-8") == markdown
    assert Path(report["html_path"]).read_text(encoding="utf-8") == report["html"]


def test_two_reports_at_same_time_never_overwrite_existing_artifacts(tmp_path):
    archive = service(tmp_path, populated_state())
    first = archive.generate_report()["report"]
    original = Path(first["markdown_path"]).read_bytes()
    second = archive.generate_report()["report"]
    assert first["id"] != second["id"]
    assert first["markdown_path"] != second["markdown_path"]
    assert Path(first["markdown_path"]).read_bytes() == original
    assert len(list((tmp_path / "reports").glob("*.md"))) == 2


def test_archive_survives_service_restart_and_returns_original_snapshot(tmp_path):
    state = populated_state()
    original = service(tmp_path, state).generate_report()["report"]
    state.record_skill("C:/pool-a/same", "changed later", 0, "scanned")
    restarted = service(tmp_path, state)
    listed = restarted.list_reports()
    assert listed["ok"] is True
    assert listed["reports"][0]["id"] == original["id"]
    assert "markdown" not in listed["reports"][0]
    fetched = restarted.get_report(original["id"])
    assert fetched["ok"] is True
    assert fetched["report"]["markdown"] == original["markdown"]
    assert "changed later" not in fetched["report"]["markdown"]


@pytest.mark.parametrize("report_id", ["../secret", "../../x.md", "C:/secret",
                                      "/etc/passwd", "check-report-other", None, []])
def test_report_reads_reject_paths_and_malformed_identifiers(tmp_path, report_id):
    archive = service(tmp_path)
    assert archive.get_report(report_id)["ok"] is False
    assert archive.export_report(report_id)["ok"] is False


def test_archive_ignores_broken_metadata_or_missing_artifacts(tmp_path):
    archive = service(tmp_path)
    made = archive.generate_report()["report"]
    root = tmp_path / "reports"
    (root / "unrelated.json").write_text('{"id":"../../secret"}', encoding="utf-8")
    (root / (made["id"] + ".json")).write_text("{broken", encoding="utf-8")
    assert archive.list_reports() == {"ok": True, "reports": []}
    assert archive.get_report(made["id"])["ok"] is False


def test_archive_rejects_artifacts_modified_after_generation(tmp_path):
    archive = service(tmp_path)
    report = archive.generate_report()["report"]
    Path(report["html_path"]).write_text('<script>alert("forged report")</script>', encoding="utf-8")
    assert archive.get_report(report["id"])["ok"] is False
    assert archive.export_report(report["id"], "html")["ok"] is False
    assert archive.list_reports()["reports"] == []


def test_archive_rejects_artifact_symlink_to_external_file(tmp_path):
    archive = service(tmp_path)
    report = archive.generate_report()["report"]
    external = tmp_path / "external.md"
    external.write_text("external private data", encoding="utf-8")
    artifact = Path(report["markdown_path"])
    artifact.unlink()
    try:
        artifact.symlink_to(external)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    assert archive.get_report(report["id"])["ok"] is False
    assert archive.export_report(report["id"])["ok"] is False


def test_archive_rejects_reparse_metadata_before_reading(tmp_path, monkeypatch):
    import tray.reports as reports
    archive = service(tmp_path)
    report = archive.generate_report()["report"]
    original = reports._is_link
    monkeypatch.setattr(reports, "_is_link", lambda path: Path(path).suffix == ".json" or original(path))
    assert archive.get_report(report["id"])["ok"] is False
    assert archive.list_reports()["reports"] == []


def test_partial_write_failure_leaves_no_visible_or_incomplete_archive(tmp_path, monkeypatch):
    archive = service(tmp_path)
    original = Path.open

    def fail_html(path, *args, **kwargs):
        if path.suffix == ".html" and args and args[0] == "x":
            raise PermissionError("cannot create html")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_html)
    result = archive.generate_report()
    assert result["ok"] is False
    assert "cannot create html" in result["error"]
    assert list((tmp_path / "reports").iterdir()) == []
    assert archive.list_reports()["reports"] == []


def test_export_returns_utf8_payload_and_copies_to_selected_destination(tmp_path):
    archive = service(tmp_path, populated_state())
    report = archive.generate_report()["report"]
    markdown = archive.export_report(report["id"], "md")
    assert markdown["ok"] is True
    assert markdown["mime"] == "text/markdown; charset=utf-8"
    assert markdown["content"] == report["markdown"]
    destination = tmp_path / "download.md"
    copied = archive.export_report(report["id"], "md", destination=str(destination))
    assert copied["ok"] is True
    assert destination.read_text(encoding="utf-8") == report["markdown"]
    html_result = archive.export_report(report["id"], "html")
    assert html_result["content"] == report["html"]
    assert html_result["mime"] == "text/html; charset=utf-8"
    assert archive.export_report(report["id"], "exe")["ok"] is False


def test_export_cannot_overwrite_immutable_archive(tmp_path):
    archive = service(tmp_path, populated_state())
    report = archive.generate_report()["report"]
    artifact = Path(report["markdown_path"])
    original = artifact.read_bytes()
    result = archive.export_report(report["id"], "html", destination=str(artifact))
    assert result["ok"] is False
    assert artifact.read_bytes() == original


def test_export_cannot_overwrite_archive_through_external_hardlink(tmp_path):
    archive = service(tmp_path)
    report = archive.generate_report()["report"]
    artifact = Path(report["markdown_path"])
    destination = tmp_path / "same-file.md"
    destination.hardlink_to(artifact)
    original = artifact.read_bytes()
    assert archive.export_report(report["id"], "html", destination=destination)["ok"] is False
    assert artifact.read_bytes() == original


def test_scan_error_keeps_failure_status_visible(tmp_path):
    state = TrayState()
    state.record_skill("C:/failed", "failed", 0, "error", check_status="error",
                       scan_complete=False, scan_issues=["scan_failed"], raw_findings=[])
    report = service(tmp_path, state).generate_report()["report"]
    assert "检查失败" in report["markdown"]
    assert "scan_failed" in report["markdown"]
    assert report["summary"]["incomplete"] == 1


def test_empty_or_legacy_scan_data_does_not_claim_healthy(tmp_path):
    state = TrayState()
    state.record_skill("C:/old", "legacy", 0, "scanned", findings=[])
    report = service(tmp_path, state).generate_report()["report"]
    assert report["summary"]["healthy"] == 0
    assert report["summary"]["incomplete"] == 1
    assert "缺少完整检查元数据" in report["markdown"]
    assert "原始发现未保存" in report["markdown"]
    empty = service(tmp_path).generate_report()["report"]
    assert empty["summary"]["total"] == 0
    assert "当前没有保存的技能检查结果" in empty["markdown"]


def test_report_documents_actual_scan_scope_without_claiming_asset_contents_checked(tmp_path):
    state = TrayState()
    state.record_skill("C:/assets", "assets", 0, "scanned", check_status="healthy",
                       review_status="not_required", raw_findings=[], raw_score=0,
                       scan_complete=True, scan_issues=[], scan_coverage={
                           "text_files_checked": 2, "asset_files_hashed": 1, "hashed_files": 3,
                           "assets_without_text_check": ["images/banner.png"],
                           "excluded_directories": ["node_modules"],
                           "limits": {"text_bytes": 2097152, "hash_bytes": 8388608}})
    report = service(tmp_path, state).generate_report()["report"]
    markdown = report["markdown"]
    for text in ("实际检查范围", "文字检查文件", "素材哈希", "原始哈希文件",
                 "images/banner.png", "node_modules", "2097152", "8388608",
                 "素材仅做格式识别和原始哈希核对", "不表示素材内容已通过安全审查"):
        assert text in markdown
    assert report["summary"]["healthy"] == 1
    assert report["summary"]["incomplete"] == 0


def test_safe_offline_html_escapes_untrusted_names_findings_and_events(tmp_path):
    state = TrayState()
    payload = '<script>alert("x")</script><img src="https://evil/x" onerror="evil()">'
    state.record_skill("C:/" + payload, payload, 50, "scanned",
                       findings=[], raw_findings=[finding(message=payload)],
                       raw_score=50, check_status="attention", scan_complete=True,
                       review_status="pending", scanned_at="invalid", scan_issues=[payload])
    state.events = [("error", payload + "\n```\n# forged result", "2026-10-04T12:10:00+08:00")]
    report = service(tmp_path, state).generate_report()["report"]
    html = report["html"]
    assert "<script" not in html and "<img" not in html and "<iframe" not in html
    assert "&lt;script&gt;" in html
    assert "Content-Security-Policy" in html
    assert "default-src 'none'" in html
    assert "<h1>forged result</h1>" not in html


def test_markdown_copies_keep_multiline_finding_and_event_content_in_code_blocks():
    from tray.reports import render_events_markdown, render_skill_markdown
    excerpt = "first line\n```\n# untrusted heading\nlast line"
    row = dict(name="example", raw_findings=[finding(excerpt=excerpt)],
               scan_complete=True, check_status="attention", review_status="pending")
    detail = render_skill_markdown("C:/example", row)
    assert excerpt in detail
    assert "````text\n" in detail
    events = render_events_markdown([dict(kind="error", text=excerpt, ts="12:30:00")])
    assert excerpt in events
    assert "原事件未保存日期" in events


def test_malformed_snapshot_values_are_visible_as_incomplete_not_crashes(tmp_path):
    class BrokenSnapshot:
        def snapshot(self):
            return {"skills": {"C:/bad": [], "D:/odd": {"name": ["odd"],
                    "raw_findings": "bad", "scan_issues": "problem"}},
                    "events": [None, {"text": ["data"], "kind": "error"}], "today": []}

    result = service(tmp_path, BrokenSnapshot()).generate_report()
    assert result["ok"] is True
    assert result["report"]["summary"]["incomplete"] == 2
    assert "C:/bad" in result["report"]["markdown"]


def test_naive_clock_is_localized_with_explicit_timezone(tmp_path):
    from tray.reports import ReportService
    archive = ReportService(TrayState(), report_dir=tmp_path / "reports",
                            clock=lambda: STAMP.replace(tzinfo=None))
    result = archive.generate_report()["report"]
    assert datetime.fromisoformat(result["generated_at"]).utcoffset() is not None


def test_default_directory_follows_guard_dir(tmp_path, monkeypatch):
    import skill_guard as sg
    from tray.reports import ReportService
    monkeypatch.setattr(sg, "GUARD_DIR", str(tmp_path / "guard"))
    result = ReportService(TrayState()).generate_report()["report"]
    assert Path(result["markdown_path"]).parent == tmp_path / "guard" / "reports"


def test_skill_detail_keeps_install_update_and_check_times_separate():
    from tray.reports import render_skill_markdown
    row = dict(name="dated", installed_at="2026-08-01T10:00:00+08:00",
               updated_at="2026-09-02T10:00:00+08:00",
               install_time_source="skill_md_birthtime",
               scanned_at="2026-10-04T12:00:00+08:00")
    detail = render_skill_markdown("C:/dated", row)
    assert "安装时间（估算）" in detail and "最近更新时间" in detail
    for date in ("2026-08-01", "2026-09-02", "2026-10-04"):
        assert date in detail
    assert "SKILL.md" in detail and "复制" in detail
    unknown = render_skill_markdown("C:/missing", {})
    assert "安装时间未知" in unknown and "更新时间未知" in unknown


def test_report_usage_snapshot_lists_all_idle_paths_before_checks_and_rank(tmp_path, monkeypatch):
    from tray.reports import ReportService
    state = populated_state()
    before = state.snapshot()
    calls = []
    never = [dict(name=f"unused-{index}", path=f"C:/pool/unused-{index}",
                  total=0, last="", observing=index == 0) for index in range(31)]
    usage = dict(rows=[dict(name="popular", total=12, last="2026-10-03")],
                 installed_rows=never,
                 idle_groups=dict(never=never, inactive=[dict(name="old", path="D:/old", total=3,
                                  last="2026-08-01")], unknown=[dict(name="gap", path="E:/gap", total=0)]),
                 inventory_summary=dict(total_installed=33, never=31, inactive=1, unknown=1,
                                        active=0, total_invocations=3, coverage_complete=False,
                                        coverage_note="仅覆盖已采集日志"))
    def provider():
        calls.append(True)
        return usage
    def forbidden(*args, **kwargs):
        pytest.fail("report generation must not rescan or alter decisions")
    monkeypatch.setattr("skill_guard.run_engine", forbidden)
    report_service = ReportService(state, report_dir=tmp_path / "reports",
                                   clock=lambda: STAMP, usage_provider=provider)
    result = report_service.generate_report()
    assert result["ok"] is True and calls == [True]
    markdown = result["report"]["markdown"]
    for row in never:
        assert row["path"] in markdown
    for label in ("使用概览", "无调用记录", "近 30 天未调用", "记录不足", "观察中", "仅覆盖已采集日志"):
        assert label in markdown
    assert markdown.index("C:/pool/unused-30") < markdown.index("调用排行") < markdown.index("检查概览")
    assert state.snapshot() == before
    usage["idle_groups"]["never"].clear()
    saved = report_service.get_report(result["report"]["id"])["report"]["markdown"]
    assert saved == markdown and calls == [True]


def test_usage_section_escapes_untrusted_paths_in_offline_html(tmp_path):
    from tray.reports import ReportService
    payload = '<img src="https://evil" onerror="boom()">'
    usage = dict(rows=[], installed_rows=[],
                 idle_groups={"never": [dict(name=payload, path="C:/" + payload, total=0)],
                              "inactive": [], "unknown": []},
                 inventory_summary={"coverage_note": payload})
    report = ReportService(TrayState(), report_dir=tmp_path / "reports", usage_provider=lambda: usage).generate_report()
    assert report["ok"] is True
    assert "<img" not in report["report"]["html"]
    assert "&lt;img" in report["report"]["html"]


def test_idle_usage_without_security_snapshot_preserves_file_times_and_unavailable_roots(tmp_path):
    from tray.reports import ReportService
    usage = dict(rows=[], installed_rows=[],
                 idle_groups=dict(never=[], inactive=[], unknown=[dict(name="unscanned", path="C:/unscanned",
                     total=0, installed_at="2026-08-01T00:00:00+08:00",
                     updated_at="2026-09-01T00:00:00+08:00", install_time_source="skill_md_birthtime")]),
                 inventory_summary=dict(inventory_complete=False, unavailable_inventory_roots=["D:/missing"],
                                        coverage_note="部分技能安装目录不可用"))
    report = ReportService(TrayState(), report_dir=tmp_path / "reports",
                           usage_provider=lambda: usage).generate_report()["report"]["markdown"]
    section = report.split("## 检查概览")[0]
    assert "安装时间（估算）" in section and "2026-08-01" in section
    assert "最近更新时间" in section and "2026-09-01" in section
    assert "SKILL.md" in section and "D:/missing" in section
    assert "安装清单未完整读取" in section


def test_empty_inventory_with_missing_roots_does_not_claim_no_idle_skills(tmp_path):
    from tray.reports import ReportService
    usage = dict(rows=[], installed_rows=[], idle_groups=dict(never=[], inactive=[], unknown=[]),
                 inventory_summary=dict(total_installed=0, inventory_complete=False,
                    unavailable_inventory_roots=["E:/unavailable"], coverage_note="安装目录不可用"))
    markdown = ReportService(TrayState(), report_dir=tmp_path / "reports",
                            usage_provider=lambda: usage).generate_report()["report"]["markdown"]
    section = markdown.split("## 检查概览")[0]
    assert "安装清单未完整读取" in section and "E:/unavailable" in section
    assert "当前没有符合该口径的技能" not in section
