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


def test_render_usage_html_empty_is_safe():
    html = sr.render_usage_html({"skills": {}})
    assert "skills tracked: 0" in html and "（无记录）" in html


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
    assert "fast (snapshot scores)" in html
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
