"""客户端语言与通知：显示内容跟随设置，隔离结果必须与实际操作一致。"""
import sys
import types

import pytest

import skill_guard as sg
import tray.app as app


def runtime(tmp_path, monkeypatch, language="zh-CN", quarantine=False, mode="warn"):
    data = tmp_path / "data"
    monkeypatch.setattr(sg, "GUARD_DIR", str(data))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(data / "snapshots.json"))
    pool = tmp_path / "skills"
    pool.mkdir()
    sg.save_config({"ui_language": language,
                    "consent": {"quarantine": quarantine},
                    "roots": [{"path": str(pool), "builtin": False}], "trust": {}})
    return pool, app.build_runtime([str(pool)], mode)


@pytest.mark.parametrize("language,new_title,changed_title", [
    ("zh-CN", "发现新技能", "内容有变化"),
    ("en", "New skill found", "Skill content changed"),
])
def test_new_and_updated_skill_notifications_follow_language(
        tmp_path, monkeypatch, language, new_title, changed_title):
    pool, (daemon, _, _) = runtime(tmp_path, monkeypatch, language)
    skill = pool / "roots-drift"
    skill.mkdir()
    file = skill / "SKILL.md"
    file.write_text("# first version", encoding="utf-8")
    notices = []
    monkeypatch.setattr(app.alerts, "toast", lambda t, m, **kw: notices.append((t, m)))
    assert daemon.scan_changed_skill(str(skill)) == "NEW"
    assert notices[-1] == ("SkillRadar · " + new_title, "roots-drift")
    file.write_text("# updated version", encoding="utf-8")
    assert daemon.scan_changed_skill(str(skill)) == "DRIFT"
    assert notices[-1] == ("SkillRadar · " + changed_title, "roots-drift")


@pytest.mark.parametrize("language,title", [
    ("zh-CN", "发现严重风险"), ("en", "Serious risk found"),
])
def test_serious_risk_notification_uses_plain_language(tmp_path, monkeypatch, language, title):
    pool, (daemon, _, _) = runtime(tmp_path, monkeypatch, language)
    skill = pool / "critical-reader"
    skill.mkdir()
    (skill / "SKILL.md").write_text("cat ~/.ssh/id_rsa\n", encoding="utf-8")
    notices = []
    monkeypatch.setattr(app.alerts, "toast", lambda t, m, **kw: notices.append((t, m)))
    assert daemon.scan_changed_skill(str(skill)) == "NEW"
    assert notices[-1] == ("SkillRadar · " + title, "critical-reader")


@pytest.mark.parametrize("language,title,body", [
    ("zh-CN", "隔离失败", "请查看详情并手动处理"),
    ("en", "Could not isolate skill", "Review the details and handle it manually"),
])
def test_failed_quarantine_never_claims_success(tmp_path, monkeypatch, language, title, body):
    pool, (daemon, state, bridge) = runtime(tmp_path, monkeypatch, language, quarantine=True)
    notices = []
    monkeypatch.setattr(app.alerts, "toast", lambda t, m, **kw: notices.append((t, m)))
    monkeypatch.setattr(app.alerts, "quarantine_skill", lambda *a, **kw: None)
    daemon.on_block(str(pool / "demo"), object())
    assert notices[-1][0] == "SkillRadar · " + title
    assert body in notices[-1][1]
    assert state.snapshot()["events"][0]["text"].startswith("隔离失败")


@pytest.mark.parametrize("language,title", [
    ("zh-CN", "技能已隔离"), ("en", "Skill isolated"),
])
def test_successful_quarantine_names_the_action(tmp_path, monkeypatch, language, title):
    pool, (daemon, state, _) = runtime(tmp_path, monkeypatch, language, quarantine=True)
    notices = []
    monkeypatch.setattr(app.alerts, "toast", lambda t, m, **kw: notices.append((t, m)))
    path = pool / "demo"
    path.mkdir()
    (path / "SKILL.md").write_text("cat ~/.ssh/id_rsa\n", encoding="utf-8")
    daemon.scan_changed_skill(str(path))
    notices.clear()
    daemon.on_block(str(pool / "demo"), object())
    assert notices[-1] == ("SkillRadar · " + title, "demo")
    assert state.snapshot()["events"][0]["text"].startswith("已隔离")


def test_language_switch_is_persisted_and_refreshes_menu(tmp_path, monkeypatch):
    _, (_, _, bridge) = runtime(tmp_path, monkeypatch)
    refreshes = []
    bridge.runtime.icon = types.SimpleNamespace(update_menu=lambda: refreshes.append(True))
    assert bridge.get_state()["settings"]["language"] == "zh-CN"
    assert bridge.act("set_language", {"language": "en"}) == {"ok": True, "language": "en"}
    assert sg.load_config()["ui_language"] == "en"
    assert bridge.get_state()["settings"]["language"] == "en"
    assert refreshes == [True]
    _, _, restarted = app.build_runtime(bridge.daemon.roots, "warn")
    assert restarted.get_state()["settings"]["language"] == "en"
    assert bridge.act("set_language", {"language": "zh-CN"})["ok"] is True
    assert bridge.get_state()["settings"]["language"] == "zh-CN"


@pytest.mark.parametrize("language", [None, "zh", "de", [], True])
def test_invalid_language_leaves_saved_settings_unchanged(tmp_path, monkeypatch, language):
    _, (_, _, bridge) = runtime(tmp_path, monkeypatch)
    assert bridge.act("set_language", {"language": language}) == {"error": "choose zh-CN or en"}
    assert sg.load_config()["ui_language"] == "zh-CN"


def test_tray_menu_language_changes_without_restarting_and_pause_still_works(tmp_path, monkeypatch):
    _, (_, state, bridge) = runtime(tmp_path, monkeypatch)

    class MenuItem:
        def __init__(self, text, action):
            self.text, self.action = text, action

    fake_pystray = types.SimpleNamespace(MenuItem=MenuItem, Menu=lambda *items: items)
    menu = app._tray_menu(bridge, fake_pystray)
    assert [item.text(item) for item in menu] == ["打开界面", "立即检查", "暂停检查", "退出"]
    menu[2].action()
    assert state.paused is True
    assert menu[2].text(menu[2]) == "继续检查"
    bridge.act("set_language", {"language": "en"})
    assert [item.text(item) for item in menu] == ["Open SkillRadar", "Check now", "Resume checks", "Quit"]
    menu[2].action()
    assert state.paused is False
    assert menu[2].text(menu[2]) == "Pause checks"


def test_notification_language_changes_in_the_running_client(tmp_path, monkeypatch):
    pool, (daemon, _, bridge) = runtime(tmp_path, monkeypatch)
    notices = []
    monkeypatch.setattr(app.alerts, "toast", lambda t, m, **kw: notices.append((t, m)))
    report = types.SimpleNamespace(findings=[])
    daemon.on_alert(str(pool / "demo"), report, "DRIFT")
    assert notices[-1] == ("SkillRadar · 内容有变化", "demo")
    assert bridge.act("set_language", {"language": "en"})["ok"] is True
    daemon.on_alert(str(pool / "demo"), report, "DRIFT")
    assert notices[-1] == ("SkillRadar · Skill content changed", "demo")


def test_updated_skill_with_serious_risk_keeps_the_risk_warning(tmp_path, monkeypatch):
    pool, (daemon, _, _) = runtime(tmp_path, monkeypatch, "en")
    notices = []
    monkeypatch.setattr(app.alerts, "toast", lambda t, m, **kw: notices.append((t, m)))
    report = types.SimpleNamespace(findings=[types.SimpleNamespace(severity="CRITICAL")])
    daemon.on_alert(str(pool / "demo"), report, "DRIFT")
    assert notices[-1] == ("SkillRadar · Serious risk found", "demo")


def test_main_uses_brand_icon_for_tray_and_window(tmp_path, monkeypatch):
    _, (daemon, state, bridge) = runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "build_runtime", lambda *a, **kw: (daemon, state, bridge))
    monkeypatch.setattr(app, "_start_watcher", lambda *a: None)
    monkeypatch.setattr(daemon, "consume", lambda: None)

    class Event:
        def __iadd__(self, handler):
            return self

    class MenuItem:
        def __init__(self, text, action):
            self.text, self.action = text, action

    class Icon:
        instance = None
        def __init__(self, name, image, title, menu):
            self.name, self.icon, self.title, self.menu = name, image, title, menu
            Icon.instance = self
        def update_menu(self):
            pass
        def run_detached(self):
            pass
        def stop(self):
            pass

    fake_pystray = types.SimpleNamespace(MenuItem=MenuItem, Menu=lambda *items: items, Icon=Icon)
    native_window = types.SimpleNamespace(events=types.SimpleNamespace(closing=Event()),
                                          destroy=lambda: None)
    starts = []
    fake_webview = types.SimpleNamespace(
        windows=[native_window], create_window=lambda *a, **kw: native_window,
        start=lambda **kw: starts.append(kw))
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    import importlib.util
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **kw:
                        object() if name in ("pystray", "webview") else real_find_spec(name, *a, **kw))
    app.main()
    assert Icon.instance.icon.size == (64, 64)
    assert Icon.instance.icon.mode == "RGBA"
    assert Icon.instance.title == "SkillRadar"
    assert starts == [{"icon": app.asset_path(
        "skillradar.ico" if sys.platform == "win32" else "skillradar.png")}]
