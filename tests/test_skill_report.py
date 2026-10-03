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
