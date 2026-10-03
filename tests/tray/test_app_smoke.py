# tests/tray/test_app_smoke.py — 任务 5：壳层装配（不真起 GUI，全部打桩）
import sys

import pytest


def test_app_importable_without_gui(monkeypatch):
    # 壳层缺 GUI 依赖时：给清晰错误而非裸 traceback（设计裁定 8）。
    # main() 判可用性用 importlib.util.find_spec 而非裸 import（绕开
    # sys.modules 缓存桩），故「未安装」语义要打在 find_spec 上：对
    # pystray/webview 恒返回 None——本机装没装 GUI 依赖都走同一提示路径，
    # 测试在两种环境下行为一致（monkeypatch 自动还原，不泄漏到后续用例）。
    import importlib.util
    real_find_spec = importlib.util.find_spec

    def _no_gui(name, *args, **kwargs):
        if name in ("pystray", "webview"):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _no_gui)
    monkeypatch.setitem(sys.modules, "pystray", None)   # import 层同步桩住
    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.delitem(sys.modules, "tray.app", raising=False)   # 强制重导入
    import tray.app as app_mod
    with pytest.raises(SystemExit) as ei:
        app_mod.main()
    assert "requirements-gui" in str(ei.value.code)


def test_build_daemon_wires_on_block(tmp_path, monkeypatch):
    import skill_guard
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path / "home"))
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / "home/.skill-radar"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME",
                        str(tmp_path / "home/.skill-radar/snapshots.json"))
    import tray.app as app_mod
    pool = tmp_path / "pool"; pool.mkdir()
    app_mod._install_gui_stubs()   # 测试模式：托盘/窗口/监听全打桩
    daemon, state, bridge = app_mod.build_runtime(roots=[str(pool)], mode="warn")
    hits = []
    daemon.on_block = lambda p, rep: hits.append(p)
    assert state.watched_roots == 1
    assert daemon.mode == "warn"
    # bridge 动作白名单（设计裁定 6）
    snap = bridge.get_state()
    assert set(snap) >= {"guard", "watched_roots", "events", "today"}
    assert bridge.act("no_such_action", {}) == {"error": "unknown action"}


def test_bridge_actions_whitelist(tmp_path, monkeypatch):
    import skill_guard
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path / "home"))
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / "home/.skill-radar"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME",
                        str(tmp_path / "home/.skill-radar/snapshots.json"))
    import tray.app as app_mod
    pool = tmp_path / "pool"; pool.mkdir()
    app_mod._install_gui_stubs()
    daemon, state, bridge = app_mod.build_runtime(roots=[str(pool)], mode="warn")
    assert bridge.act("pause", {}) == {"ok": True, "guard": "paused"}
    assert bridge.act("resume", {}) == {"ok": True, "guard": "running"}
