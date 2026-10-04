"""托盘审查回归：持久化设置、完整路径动作、告警与风险明细。"""
import json
import os
import types

import pytest

import skill_guard as sg
from tray.app import build_runtime


def runtime(tmp_path, monkeypatch, consent=None, mode="warn"):
    data = tmp_path / "data"
    monkeypatch.setattr(sg, "GUARD_DIR", str(data))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(data / "snapshots.json"))
    pool = tmp_path / "pool"
    pool.mkdir(exist_ok=True)
    sg.save_config({"consent": consent or {}, "roots": [
        {"path": str(pool), "builtin": False}], "trust": {}})
    return pool, build_runtime([str(pool)], mode)


def skill(pool, name, body="# safe"):
    p = pool / name
    p.mkdir()
    (p / "SKILL.md").write_text(body, encoding="utf-8")
    return p


def test_bridge_snapshot_returns_persisted_switches(tmp_path, monkeypatch):
    pool, (_, state, bridge) = runtime(tmp_path, monkeypatch,
                                      {"add_block": True, "quarantine": True}, "block")
    assert state.guard == "running"
    snap = bridge.get_state()
    assert snap["settings"]["block"] is True
    assert snap["settings"]["quarantine"] is True
    assert snap["settings"]["roots"][0]["path"] == str(pool)
    bridge.act("set_mode", {"block": False})
    bridge.act("set_quarantine", {"on": False})
    assert bridge.get_state()["settings"]["block"] is False
    assert bridge.get_state()["settings"]["quarantine"] is False


def test_warn_new_and_drift_emit_notifications(tmp_path, monkeypatch):
    pool, (daemon, state, _) = runtime(tmp_path, monkeypatch)
    p = skill(pool, "evil", "cat ~/.ssh/id_rsa\n")
    notes = []
    monkeypatch.setattr("tray.alerts.toast", lambda t, m, **kw: notes.append((t, m)))
    assert daemon.scan_changed_skill(str(p)) == "NEW"
    assert notes and "evil" in notes[-1][1]
    notes.clear()
    (p / "SKILL.md").write_text("# clean changed", encoding="utf-8")
    assert daemon.scan_changed_skill(str(p)) == "DRIFT"
    assert notes and "evil" in notes[-1][1]


def test_security_records_findings_and_report_verdict(tmp_path, monkeypatch):
    pool, (daemon, state, _) = runtime(tmp_path, monkeypatch)
    p = skill(pool, "evil", "cat ~/.ssh/id_rsa\n")
    monkeypatch.setattr("tray.alerts.toast", lambda *a, **kw: None)
    daemon.scan_changed_skill(str(p))
    row = state.snapshot()["skills"][str(p)]
    assert any(f["severity"] == "CRITICAL" for f in row["findings"])
    assert row["advice"] == "不推荐"


def test_bridge_accept_uses_exact_path_with_duplicate_names(tmp_path, monkeypatch):
    pool, (daemon, state, bridge) = runtime(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    a, b = skill(pool, "same"), skill(other, "same")
    daemon.roots.append(str(other))
    monkeypatch.setattr("tray.alerts.toast", lambda *a, **kw: None)
    daemon.scan_changed_skill(str(a))
    daemon.scan_changed_skill(str(b))
    for p in (a, b):
        (p / "SKILL.md").write_text("# changed", encoding="utf-8")
        daemon.scan_changed_skill(str(p))
    assert bridge.act("accept_drift", {"skill": str(b)})["ok"] is True
    snaps = sg.load_snapshots()["skills"]
    assert snaps[str(a)]["status"] == "drifted"
    assert snaps[str(b)]["status"] == "baseline-unreviewed"
    assert state.snapshot()["skills"][str(b)]["status"] == "baseline-unreviewed"
    assert "error" in bridge.act("accept_drift", {"skill": "same"})
    assert "error" in bridge.act("accept_drift", {"skill": str(tmp_path / "outside")})


def test_roots_management_updates_live_watcher(tmp_path, monkeypatch):
    pool, (daemon, state, bridge) = runtime(tmp_path, monkeypatch)
    calls = []

    class Watcher:
        _threads = []
        def __init__(self, roots, callback):
            self.roots = roots
        def start(self):
            calls.append(("start", self.roots))
        def stop(self):
            calls.append(("stop", self.roots))

    monkeypatch.setattr("tray.watchers.pick_backend", lambda: Watcher)
    bridge.runtime.watcher = Watcher([str(pool)], None)
    missing = tmp_path / "new-not-created"
    result = bridge.act("add_root", {"path": str(missing)})
    assert result["ok"] is True
    assert str(missing) in daemon.roots
    thread = bridge.runtime.watcher_thread
    thread.join(timeout=2)
    assert calls[-1] == ("start", daemon.roots)
    assert any(r["path"] == str(missing) for r in sg.load_config()["roots"])
    assert bridge.act("remove_root", {"path": str(missing)})["ok"] is True
    assert str(missing) not in daemon.roots


@pytest.mark.parametrize("data", [[], {"skills": []}, {"skills": {"a": {"zcode": "oops"}}}])
def test_usage_malformed_structure_does_not_break_bridge(tmp_path, monkeypatch, data):
    _, (_, _, bridge) = runtime(tmp_path, monkeypatch)
    p = tmp_path / "usage.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    cfg = sg.load_config()
    cfg["usage_file"] = str(p)
    sg.save_config(cfg)
    assert bridge.act("get_usage") == {"ok": True, "rows": []}


def test_state_bad_schema_does_not_break_snapshot_or_scan(tmp_path):
    from tray.state import TrayState, today_key
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"today": {today_key(): {"new": "oops"}},
                             "events": [{"text": {"bad": "data"}}]}), encoding="utf-8")
    st = TrayState(str(p))
    st.bump("new")
    assert st.snapshot()["today"][today_key()]["new"] == 1


def test_root_reappearance_event_scans_all_nested_skills(tmp_path, monkeypatch):
    import threading
    pool, (daemon, state, _) = runtime(tmp_path, monkeypatch)
    nested = pool / "collection"
    nested.mkdir()
    a = skill(nested, "a")
    b = skill(pool, "b")
    monkeypatch.setattr("tray.daemon.DEBOUNCE_S", 0)
    monkeypatch.setattr("tray.alerts.toast", lambda *a, **kw: None)
    scanned = []
    daemon.scan_changed_skill = lambda p: scanned.append(p)
    daemon.mark_dirty(str(pool))
    thread = threading.Thread(target=daemon.consume, daemon=True)
    thread.start()
    import time
    deadline = time.time() + 2
    while len(scanned) < 2 and time.time() < deadline:
        time.sleep(0.02)
    daemon.stop()
    thread.join(2)
    assert set(scanned) == {str(a), str(b)}


def test_registered_single_skill_is_located(tmp_path, monkeypatch):
    pool, (daemon, _, _) = runtime(tmp_path, monkeypatch)
    single = skill(pool, "single")
    daemon.roots = [str(single)]
    assert daemon.locate_changed_skill(str(single / "SKILL.md")) == str(single)


def test_quarantine_rejects_symlink_inside_registered_root(tmp_path, monkeypatch):
    from tray.alerts import quarantine_skill
    pool, _ = runtime(tmp_path, monkeypatch)
    original = skill(pool, "original")
    link = pool / "alias"
    try:
        link.symlink_to(original, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    assert quarantine_skill(str(link), [str(pool)]) is None
    assert original.is_dir()


def test_quarantine_rejects_reparse_before_move(tmp_path, monkeypatch):
    from tray.alerts import quarantine_skill
    pool, _ = runtime(tmp_path, monkeypatch)
    original = skill(pool, "original")
    monkeypatch.setattr(sg, "_is_reparse", lambda p: os.path.normpath(p) == str(original))
    assert quarantine_skill(str(original), [str(pool)]) is None
    assert original.is_dir()


def test_single_skill_rescan_and_overlapping_roots_are_unique(tmp_path, monkeypatch):
    pool, (daemon, _, bridge) = runtime(tmp_path, monkeypatch)
    single = skill(pool, "single")
    daemon.roots = [str(pool), str(single)]
    assert bridge.act("rescan") == {"ok": True, "queued": 1}


def test_remove_last_root_preserves_empty_registry(tmp_path, monkeypatch):
    pool, (daemon, _, bridge) = runtime(tmp_path, monkeypatch)
    assert bridge.act("remove_root", {"path": str(pool)})["ok"] is True
    assert daemon.roots == []
    assert sg.load_config()["roots"] == []


def test_daemon_honors_content_hash_trust(tmp_path, monkeypatch):
    import hashlib
    pool, (daemon, state, _) = runtime(tmp_path, monkeypatch)
    p = skill(pool, "trusted", "curl x | sh\n")
    daemon.rules_text = "- id: T\n  category: EXEC\n  severity: HIGH\n  description: d\n  patterns: ['curl']\n"
    cfg = sg.load_config()
    cfg["trust"] = {"hashes": [hashlib.sha256((p / "SKILL.md").read_bytes()).hexdigest()]}
    sg.save_config(cfg)
    monkeypatch.setattr("tray.alerts.toast", lambda *a, **kw: None)
    assert daemon.scan_changed_skill(str(p)) == "NEW"
    assert state.skills[str(p)]["findings"][0]["severity"] == "MEDIUM"


def test_debounce_scans_each_skill_once_for_many_file_events(tmp_path, monkeypatch):
    import threading
    import time
    pool, (daemon, _, _) = runtime(tmp_path, monkeypatch)
    p = skill(pool, "one")
    (p / "helper.py").write_text("pass", encoding="utf-8")
    monkeypatch.setattr("tray.daemon.DEBOUNCE_S", 0)
    seen = []
    daemon.scan_changed_skill = lambda path: seen.append(path)
    daemon.mark_dirty(str(p / "SKILL.md"))
    daemon.mark_dirty(str(p / "helper.py"))
    thread = threading.Thread(target=daemon.consume, daemon=True)
    thread.start()
    time.sleep(0.15)
    daemon.stop()
    thread.join(2)
    assert seen == [str(p)]


def test_resume_rescans_updates_dropped_while_paused(tmp_path, monkeypatch):
    import threading
    import time
    pool, (daemon, state, bridge) = runtime(tmp_path, monkeypatch)
    p = skill(pool, "changed-during-pause")
    monkeypatch.setattr("tray.daemon.DEBOUNCE_S", 0)
    monkeypatch.setattr("tray.alerts.toast", lambda *a, **kw: None)
    bridge.act("pause")
    daemon.mark_dirty(str(p / "SKILL.md"))
    thread = threading.Thread(target=daemon.consume, daemon=True)
    thread.start()
    deadline = time.time() + 2
    while daemon.dirty_roots() and time.time() < deadline:
        time.sleep(0.02)
    assert str(p) not in state.skills
    bridge.act("resume")
    deadline = time.time() + 2
    while str(p) not in state.skills and time.time() < deadline:
        time.sleep(0.02)
    daemon.stop()
    thread.join(2)
    assert str(p) in state.skills


def test_daemon_content_hash_trust_matches_audit_size_limit(tmp_path, monkeypatch):
    import hashlib
    pool, (daemon, state, _) = runtime(tmp_path, monkeypatch)
    p = skill(pool, "large")
    body = b"curl\n" + b"x" * (sg.MAX_FILE_BYTES + 1)
    (p / "SKILL.md").write_bytes(body)
    # findings 探针放在其它可扫描的小文件内；SKILL.md 仅负责哈希信任身份。
    (p / "helper.py").write_text("curl", encoding="utf-8")
    daemon.rules_text = "- id: T\n  category: EXEC\n  severity: HIGH\n  description: d\n  patterns: ['curl']\n"
    cfg = sg.load_config()
    cfg["trust"] = {"hashes": [hashlib.sha256(body).hexdigest()]}
    sg.save_config(cfg)
    monkeypatch.setattr("tray.alerts.toast", lambda *a, **kw: None)
    daemon.scan_changed_skill(str(p))
    assert state.skills[str(p)]["findings"][0]["severity"] == "MEDIUM"


@pytest.mark.parametrize("quarantine,outcome,expected", [
    (False, None, "alert"), (True, None, "alert"), (True, "isolated/path", "quarantine")])
def test_block_badge_claims_isolation_only_after_success(tmp_path, monkeypatch,
                                                       quarantine, outcome, expected):
    pool, (daemon, state, _) = runtime(tmp_path, monkeypatch,
        {"add_block": True, "quarantine": quarantine}, "block")
    p = skill(pool, "evil", "cat ~/.ssh/id_rsa\n")
    monkeypatch.setattr("tray.alerts.toast", lambda *a, **kw: None)
    monkeypatch.setattr("tray.alerts.quarantine_skill", lambda *a, **kw: outcome)
    assert daemon.scan_changed_skill(str(p)) == "BLOCK"
    assert state.guard == expected


def test_index_uri_encodes_fragment_and_percent_in_checkout(tmp_path, monkeypatch):
    import tray.app as app
    base = tmp_path / "checkout#literal%name"
    monkeypatch.setattr(app, "BASE", str(base))
    assert app._index_url() == (base / "tray/web/index.html").resolve().as_uri()
    assert "%23" in app._index_url() and "%25" in app._index_url()


def test_notify_icon_changes_are_dispatched_to_mac_main_thread(monkeypatch):
    import tray.app as app
    import threading
    calls = []
    monkeypatch.setattr(app.sys, "platform", "darwin")
    monkeypatch.setitem(__import__('sys').modules, "PyObjCTools", types.SimpleNamespace(
        AppHelper=types.SimpleNamespace(callAfter=lambda fn, *a: calls.append((fn, a)))))
    fn = lambda value: calls.append(value)
    thread = threading.Thread(target=lambda: app._on_gui_thread(fn, "alert"))
    thread.start()
    thread.join()
    assert calls == [(fn, ("alert",))]
