"""The desktop report uses the installed inventory without starting a history scan."""
import skill_guard as sg
from tray.app import build_runtime
from tray.usage import UsageService


def test_report_bridge_includes_unrecorded_installed_skill_without_rescanning(tmp_path, monkeypatch):
    data, pool = tmp_path / "data", tmp_path / "skills"
    unused = pool / "unused"
    unused.mkdir(parents=True)
    (unused / "SKILL.md").write_text("# unused", encoding="utf-8")
    monkeypatch.setattr(sg, "GUARD_DIR", str(data))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(data / "snapshots.json"))
    sg.save_config({"roots": [{"path": str(pool)}]})
    _, state, bridge = build_runtime([str(pool)], "warn")
    bridge.usage = UsageService(str(data), history_roots=[], auto_start=False)
    bridge.usage._status.update(phase="ready", files_scanned=1)
    monkeypatch.setattr(bridge.usage, "snapshot", lambda: {
        "phase": "ready", "scanning": False, "files_scanned": 1,
        "roots": [{"exists": True}], "errors": []})
    monkeypatch.setattr(bridge.usage, "scan", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("report must not start collecting session logs")))
    before = state.snapshot()
    try:
        report = bridge.act("generate_report")["report"]
        markdown = report["markdown"]
        assert str(unused) in markdown
        assert markdown.index("### 无调用记录") < markdown.index("### 调用排行")
        assert markdown.index("### 调用排行") < markdown.index("## 检查概览")
        assert state.snapshot() == before
        saved = bridge.act("get_report", {"id": report["id"]})["report"]["markdown"]
        assert saved == markdown
    finally:
        bridge.processing.stop()
        bridge.usage.stop()
