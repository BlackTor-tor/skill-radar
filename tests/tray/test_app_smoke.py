# tests/tray/test_app_smoke.py — 任务 5：壳层装配（不真起 GUI，全部打桩）
import sys
import types

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
    # quarantine on：默认 on_block 运行时经 sg.load_config 读取（GUARD_DIR 已重定向）
    skill_guard.save_config({"consent": {"quarantine": True, "add_block": True},
                             "roots": [], "trust": {}})
    daemon, state, bridge = app_mod.build_runtime(roots=[str(pool)], mode="block")
    assert state.watched_roots == 1
    assert daemon.mode == "block"
    # bridge 动作白名单（设计裁定 6）
    snap = bridge.get_state()
    assert set(snap) >= {"guard", "watched_roots", "events", "today"}
    assert bridge.act("no_such_action", {}) == {"error": "unknown action"}

    # 默认 on_block 钩子真触发：quarantine on → 隔离 + toast + 事件（审查 Minor 2）
    dest = str(tmp_path / "q" / "demo-x")
    calls, toasts = [], []
    monkeypatch.setattr(app_mod.alerts, "quarantine_skill",
                        lambda p, allowed_roots: calls.append((p, allowed_roots)) or dest)
    monkeypatch.setattr(app_mod.alerts, "toast", lambda t, m: toasts.append((t, m)))
    daemon.on_block(str(pool / "demo"), object())
    assert calls == [(str(pool / "demo"), daemon.roots)]   # allowed_roots 传守护根
    assert toasts and "已隔离" in toasts[0][0]
    assert state.events[0][0] == "quarantine" and "已隔离" in state.events[0][1]
    # 隔离失败（dest=None）如实记「隔离失败」，不伪造"已隔离 → None"（审查 Minor 1）
    monkeypatch.setattr(app_mod.alerts, "quarantine_skill", lambda p, allowed_roots: None)
    daemon.on_block(str(pool / "demo"), object())
    assert state.events[0][0] == "quarantine" and "隔离失败" in state.events[0][1]


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


def test_shutdown_stops_and_joins(tmp_path, monkeypatch):
    # 审查 Important-1：停机序列 stop + join（watcher 平台线程、consume 线程），
    # 且幂等——托盘 Quit 与 main() 尾部两次调用只执行一遍。
    import tray.app as app_mod
    app_mod._install_gui_stubs()
    seq = []

    class _FakeThread:
        def __init__(self, name): self.name = name
        def is_alive(self): return True
        def join(self, timeout=None): seq.append(("join", self.name, timeout))

    class _FakeWatcher:
        _threads = [_FakeThread("watcher-w0"), _FakeThread("watcher-w1")]
        def stop(self): seq.append(("stop", "watcher"))

    class _FakeDaemon:
        def stop(self): seq.append(("stop", "daemon"))
        def join(self, timeout=5): seq.append(("join", "daemon", timeout))

    class _FakeIcon:
        def stop(self): seq.append(("stop", "icon"))

    class _FakeWindow:
        def destroy(self): seq.append(("destroy", "window"))

    runtime = types.SimpleNamespace(icon=_FakeIcon(), window=_FakeWindow(),
                                    watcher=_FakeWatcher(), daemon=_FakeDaemon())
    app_mod.shutdown(runtime)
    app_mod.shutdown(runtime)   # 二次调用：防重入，不重放
    assert seq == [("stop", "watcher"),
                   ("join", "watcher-w0", 5), ("join", "watcher-w1", 5),
                   ("stop", "daemon"), ("join", "daemon", 5),
                   ("stop", "icon"), ("destroy", "window")]


def test_window_close_keeps_app_alive(monkeypatch):
    # 规格 §1b（审查 Important-2）：关窗 = 缩托盘不退出。closing 处理器走
    # 状态机 "hide"（veto），绝不触发 shutdown；Quit 是唯一退出通道。
    import tray.app as app_mod
    called = []
    monkeypatch.setattr(app_mod, "shutdown", lambda rt: called.append(rt))
    runtime = types.SimpleNamespace(icon=None, window=None, watcher=None,
                                    daemon=None, quitting=False)
    hidden = []
    win = types.SimpleNamespace(hide=lambda: hidden.append("hide"))
    assert app_mod._window_lifecycle(runtime, "close") == "hide"
    assert app_mod._handle_ui_closing(runtime, win) is False   # veto：取消关闭
    assert hidden == ["hide"]
    assert called == []                                        # shutdown 未被调


def test_quit_is_the_only_exit(monkeypatch):
    # 规格 §1b（审查 Important-2）：托盘 Quit 才真退出；置 quitting 后，
    # 随后的 closing 事件放行（destroy 也走 closing，veto 不放行 Quit 会关不掉）。
    import tray.app as app_mod
    called = []
    monkeypatch.setattr(app_mod, "shutdown", lambda rt: called.append(rt))
    runtime = types.SimpleNamespace(icon=None, window=None, watcher=None,
                                    daemon=None, quitting=False)
    assert app_mod._window_lifecycle(runtime, "quit") == "quit"
    app_mod._handle_quit(runtime)
    assert called == [runtime]          # shutdown 恰被调一次
    assert runtime.quitting is True
    assert app_mod._window_lifecycle(runtime, "close") == "quit"   # 放行后续关闭
