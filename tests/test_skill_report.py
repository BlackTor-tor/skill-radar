# tests/test_skill_report.py
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skill_report as sr  # noqa: E402

COUNTERS = {
    "skills": {
        "alpha": {"zcode": 3, "claude": 1, "marker": 0, "atime": 2,
                  "last_tool_use": "2026-10-01T10:00:00"},
        "beta": {"zcode": 0, "claude": 0, "marker": 2, "atime": 0,
                 "last_tool_use": ""},
    }
}


def _fake_rep(score=25, sev="HIGH"):
    from skill_guard import Finding, ScanReport
    rep = ScanReport("demo", "/r", [], score, 2, True)
    rep.findings = [Finding("SR-EXEC-001", "EXEC", sev, "SKILL.md", 3, "curl | sh", "d", [])]
    return rep


def test_render_usage_html_contains_layers_and_names():
    html = sr.render_usage_html(COUNTERS, top=10)
    assert "alpha" in html and "beta" in html
    assert "total invocations" in html and ">4<" in html   # 3+1+0
    assert "zcode precise" in html and "marker universal" in html
    assert "技能调用排行" in html and "四层来源分布" in html   # 双语标题


def test_install_verdict_logic():
    from skill_guard import Finding, ScanReport
    clean = ScanReport("a", "/a", [], 0, 1, True)
    assert sr.install_verdict(clean, "scanned")[0] == "推荐"
    crit = ScanReport("b", "/b", [Finding("X", "EXFIL", "CRITICAL", "s", 1, "e", "m", [])], 40, 1, False)
    v, _, reason = sr.install_verdict(crit, "scanned")
    assert v == "不推荐" and "数据外发" in reason
    high = ScanReport("c", "/c", [Finding("Y", "EXEC", "HIGH", "s", 1, "e", "m", [])], 25, 1, True)
    v, _, reason = sr.install_verdict(high, "scanned")
    assert v == "谨慎" and "恶意执行" in reason
    drift = ScanReport("d", "/d", [], 0, 1, True)
    assert sr.install_verdict(drift, "drifted")[0] == "不推荐"


def test_render_guard_html_has_advice_column_and_verdict_summary():
    snaps = {"skills": {"/p/demo": {"name": "demo", "status": "baseline-unreviewed",
                                    "score": 25, "scanned_at": "t", "hashes": {}}}}
    html = sr.render_guard_html([("demo", "/p/demo", _fake_rep())], snaps)
    assert "安装建议" in html and "推荐安装" in html and "不推荐" in html
    assert "INSTALL ADVICE · 安装建议汇总" in html


def test_render_usage_html_empty_is_safe():
    html = sr.render_usage_html({"skills": {}})
    assert "追踪技能: 0" in html and "（无记录）" in html


def test_render_guard_html_detailed_mode():
    snaps = {"skills": {"/p/demo": {"name": "demo", "status": "baseline-unreviewed",
                                    "score": 25, "scanned_at": "t", "hashes": {}}}}
    html = sr.render_guard_html([("demo", "/p/demo", _fake_rep())], snaps)
    assert "critical findings" in html
    assert "SR-EXEC-001" in html and "demo" in html
    assert "baseline-unreviewed" in html


def test_render_guard_html_fast_mode_from_snapshots():
    snaps = {"skills": {
        "/p/a": {"name": "a", "status": "drifted", "score": 100, "hashes": {}},
        "/p/b": {"name": "b", "status": "scanned", "score": 0, "hashes": {}}}}
    html = sr.render_guard_html([], snaps)
    assert "快照模式" in html
    assert "drifted" in html and "scanned" in html


def test_zero_width_and_fffd_stripped_from_output():
    """零宽与 U+FFFD 必须被剥离（它们是检测证据但无渲染价值）；
    emoji 允许保留在 HTML（浏览器可渲染，cp936 安全由 main 的 stdout reconfigure 负责）。"""
    from skill_guard import Finding, ScanReport
    rep = ScanReport("d", "/r", [Finding("X", "EXEC", "LOW", "a.md", 1,
                                         "bad\u200b\ufefftext", "m", [])], 3, 1, True)
    html = sr.render_guard_html([("d", "/r", rep)], {"skills": {}})
    assert "\u200b" not in html and "\ufeff" not in html


def test_find_browser_returns_path_or_none():
    p = sr.find_browser()
    assert p is None or os.path.isfile(p)


def test_cli_html_only_writes_files(tmp_path, monkeypatch, capsys):
    counters = tmp_path / "skill_usage.json"
    counters.write_text(json.dumps(COUNTERS), encoding="utf-8")
    out = tmp_path / "reports"
    rc = sr.main(["usage", "--html-only", "--counters", str(counters), "--out", str(out)])
    assert rc == 0
    files = list(out.glob("usage-report-*.html"))
    assert len(files) == 1
    assert not list(out.glob("*.png")) or all(p.stat().st_size == 0 for p in out.glob("*.png"))


def test_guard_collect_normpath_lookup_hits_drifted_key(tmp_path):
    """I-1 回归：audit_roots 写的快照键是 normpath 形态（Windows 反斜杠），
    _guard_collect 以 cfg roots 的正斜杠原始形态调用时，join 出的路径必须经
    normpath 归一才能命中快照键——否则 render_guard_html 裸查表落空，已漂移
    技能降级显示 rescanned、install_verdict 失去漂移输入。显式用 Windows 混合
    形态（快照键 C:\\pool\\demo、root 传 C:/pool）：normpath 在任何平台都把
    C:/pool/demo 归一成 C:\\pool\\demo，与键相等，pytest 跨平台可验证。

    目录本体用 tmp_path 实存（os.listdir/isdir 需要），快照键按 normpath(tmp)
    构造——Windows 上即反斜杠形态，POSIX 上 normpath 幂等（正斜杠），两种
    平台都走「root 正斜杠原始形态 vs 键 normpath 形态」的失配-归一路径。"""
    import skill_guard
    pool = tmp_path / "pool" / "demo"
    pool.mkdir(parents=True)
    (pool / "SKILL.md").write_text("# demo", encoding="utf-8")
    # 快照键 = normpath 形态（audit_roots 的口径）；root 传原始正斜杠形态
    key = os.path.normpath(str(tmp_path / "pool" / "demo"))
    forward_root = str(tmp_path / "pool").replace(os.sep, "/")
    snaps = {"skills": {key: {"name": "demo", "status": "drifted",
                              "score": 40, "scanned_at": "t", "hashes": {}}}}
    cfg = {"roots": [{"path": forward_root}]}
    info = sr._guard_collect(cfg, "", "")   # 空规则集：parse_rules("") → []（"[]" 不在子集内）
    assert [name for name, _, _ in info] == ["demo"]
    html = sr.render_guard_html(info, snaps)
    # 命中 drifted 键：状态徽章与 install_verdict 的「不推荐」都在场
    # （失配时 st 回落 "rescanned"，verdict 按无 CRITICAL/无 HIGH 的干净报告走「推荐」）
    assert "drifted" in html and "rescanned" not in html
    assert "不推荐" in html


def test_guard_top_finding_uses_highest_severity():
    from skill_guard import Finding, ScanReport
    rep = ScanReport("demo", "/demo", [
        Finding("CRIT-MAIN", "EXEC", "CRITICAL", "a", 1, "critical", "", []),
        Finding("LOW-OTHER", "EXEC", "LOW", "a", 2, "low", "", [])], 43, 1, False)
    html = sr.render_guard_html([("demo", "/demo", rep)], {"skills": {}})
    assert "CRIT-MAIN" in html
    assert "LOW-OTHER" not in html


def test_guard_fast_report_has_snapshot_risk_rows():
    snaps = {"skills": {
        "/demo": {"name": "unique-demo", "status": "drifted", "score": 43},
        "/low": {"name": "unique-low", "status": "scanned", "score": 3}}}
    html = sr.render_guard_html([], snaps)
    assert "unique-demo" in html and "unique-low" in html
    assert ">43<" in html and ">3<" in html
    assert "不推荐" in html
    # Missing persisted finding details must not be presented as zero findings.
    assert ">0/0/0/0<" not in html


def test_usage_all_skills_table_is_complete():
    counters = {"skills": {"skill%02d" % i: dict(COUNTERS["skills"]["alpha"]) for i in range(60)}}
    html = sr.render_usage_html(counters, top=5)
    assert html.count("<tr>") == 61


def test_usage_timestamp_is_html_escaped():
    counters = {"skills": {"demo": dict(COUNTERS["skills"]["alpha"], last_tool_use="<svg/onlo")}}
    html = sr.render_usage_html(counters)
    assert "<svg/onlo" not in html
    assert "&lt;svg/onlo" in html


def test_png_uses_absolute_escaped_file_uri(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    target = Path("reports # percent%") / "demo.png"
    target.parent.mkdir()
    calls = []
    def screenshot(argv, **kwargs):
        calls.append(argv)
        Path(next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--screenshot="))).write_bytes(b"x" * 1200)
    monkeypatch.setattr(sr.subprocess, "run", screenshot)
    assert sr.html_to_png("<html></html>", str(target), browser="fake-browser")
    assert calls[0][-1] == target.with_suffix(".html").resolve().as_uri()
    assert "--screenshot=" + str(target.resolve()) in calls[0]


def test_guard_fast_score_without_findings_does_not_claim_clean():
    snaps = {"skills": {"/demo": {"name": "high-snapshot", "status": "scanned", "score": 25}}}
    html = sr.render_guard_html([], snaps)
    assert "谨慎评估 1" in html
    assert "未发现任何风险模式" not in html
    assert "（无发现）" not in html


def test_guard_collect_preserves_case_sensitive_realpaths(tmp_path, monkeypatch):
    from skill_guard import ScanReport
    pool = tmp_path / "pool"
    for name in ("first", "second"):
        skill = pool / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# demo", encoding="utf-8")
    monkeypatch.setattr(sr.os.path, "realpath", lambda p: "/tmp/Skill" if str(p).endswith("first") else "/tmp/skill")
    monkeypatch.setattr(sr.os.path, "normcase", lambda p: p)
    monkeypatch.setattr(sr.sg, "run_engine", lambda path, *a, **kw: ScanReport("demo", path, [], 0, 1, True))
    info = sr._guard_collect({"roots": [{"path": str(pool)}]}, "", "")
    assert len(info) == 2


def test_guard_collect_trust_downgrades_only_once(tmp_path, monkeypatch):
    from skill_guard import Finding, ScanReport
    skill = tmp_path / "trusted-pool" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# demo", encoding="utf-8")
    monkeypatch.setattr(sr.sg, "run_engine", lambda path, *a, **kw: ScanReport(
        "demo", path, [Finding("RISK", "EXEC", "HIGH", "SKILL.md", 1, "", "", [])], 25, 1, True))
    cfg = {"roots": [{"path": str(skill.parent)}], "trust": {"owners": ["trusted-pool"]}}
    info = sr._guard_collect(cfg, "", "")
    assert info[0][2].findings[0].severity == "MEDIUM"
    assert info[0][2].score == sr.sg.score_findings(info[0][2].findings)


def test_guard_collect_matches_nested_and_direct_audit_roots(tmp_path):
    pool = tmp_path / "pool"
    direct = tmp_path / "direct"
    nested = pool / ".system" / "nested"
    for skill in (direct, nested):
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# demo", encoding="utf-8")
    cfg = {"roots": [{"path": str(pool)}, {"path": str(direct)}, {"path": str(nested)}]}
    info = sr._guard_collect(cfg, "", "")
    assert {name for name, _, _ in info} == {"direct", "nested"}
    assert len(info) == 2


def test_png_height_does_not_clip_large_complete_tables():
    report = "<table>" + "<tr><td>skill</td></tr>" * 300 + "</table>"
    assert sr._est_height(report) >= 560 + 300 * 30


def test_guard_preserves_same_name_copies_with_different_drift_states(tmp_path):
    first = tmp_path / "one" / "demo"
    second = tmp_path / "two" / "demo"
    for skill in (first, second):
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# clean", encoding="utf-8")
    info = sr._guard_collect({"roots": [{"path": str(first.parent)}, {"path": str(second.parent)}]}, "", "")
    snapshots = {"skills": {
        str(first): {"name": "demo", "status": "scanned", "score": 0},
        str(second): {"name": "demo", "status": "drifted", "score": 0}}}
    html = sr.render_guard_html(info, snapshots)
    assert len(info) == 2
    assert "不推荐 1" in html


def test_guard_collect_oversized_skill_md_does_not_gain_hash_trust(tmp_path, monkeypatch):
    import hashlib
    from skill_guard import Finding, ScanReport
    skill = tmp_path / "pool" / "demo"
    skill.mkdir(parents=True)
    raw = b"# demo" * 20
    (skill / "SKILL.md").write_bytes(raw)
    monkeypatch.setattr(sr.sg, "MAX_HASH_FILE_BYTES", 8)
    monkeypatch.setattr(sr.sg, "run_engine", lambda path, *a, **kw: ScanReport(
        "demo", path, [Finding("RISK", "EXEC", "HIGH", "SKILL.md", 1, "", "", [])], 25, 1, True))
    cfg = {"roots": [{"path": str(skill.parent)}], "trust": {"hashes": [hashlib.sha256(raw).hexdigest()]}}
    info = sr._guard_collect(cfg, "", "")
    assert info[0][2].findings[0].severity == "HIGH"


def test_guard_cli_detailed_empty_roots_does_not_show_stale_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(sr.sg, "load_config", lambda: {"roots": []})
    monkeypatch.setattr(sr.sg, "load_snapshots", lambda: {"skills": {
        "/gone": {"name": "stale-removed-skill", "score": 40, "status": "drifted"}}})
    out = tmp_path / "reports"
    assert sr.main(["guard", "--html-only", "--out", str(out)]) == 0
    html = next(out.glob("guard-report-*.html")).read_text(encoding="utf-8")
    assert "detailed rescan" in html
    assert "skills scanned 扫描技能: 0" in html
    assert "stale-removed-skill" not in html
