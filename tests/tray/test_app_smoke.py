# tests/tray/test_app_smoke.py — 任务 5：壳层装配（不真起 GUI，全部打桩）
import json
import sys
import threading
import time
import types

import pytest


def _redirect_guard(tmp_path, monkeypatch):
    import skill_guard
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path / "home"))
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / "home/.skill-radar"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME",
                        str(tmp_path / "home/.skill-radar/snapshots.json"))


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
    _redirect_guard(tmp_path, monkeypatch)
    import tray.app as app_mod
    pool = tmp_path / "pool"; pool.mkdir()
    app_mod._install_gui_stubs()
    daemon, state, bridge = app_mod.build_runtime(roots=[str(pool)], mode="warn")
    assert bridge.act("pause", {}) == {"ok": True, "guard": "paused"}
    assert bridge.act("resume", {}) == {"ok": True, "guard": "running"}


def test_rescan_marks_skill_dirs_not_pool_root(tmp_path, monkeypatch):
    # 终审 I-1：注册根是技能池（root 下每个子目录一个技能），旧实现对
    # <root>/SKILL.md mark_dirty 是静默 no-op——locate_changed_skill 向上
    # 找不到含 SKILL.md 的目录。改为枚举根下技能子目录逐一投递并回报 queued。
    _redirect_guard(tmp_path, monkeypatch)
    import tray.app as app_mod
    pool = tmp_path / "pool"; pool.mkdir()
    for name in ("a", "b"):
        d = pool / name; d.mkdir()
        (d / "SKILL.md").write_text("# s", encoding="utf-8")
    (pool / "notaskill").mkdir()            # 非技能目录：不投
    app_mod._install_gui_stubs()
    daemon, state, bridge = app_mod.build_runtime(roots=[str(pool)], mode="warn")
    r = bridge.act("rescan", {})
    assert r == {"ok": True, "queued": 2}
    dirty = daemon.dirty_roots()
    assert str(pool / "a") in dirty and str(pool / "b") in dirty
    assert str(pool / "notaskill") not in dirty
    assert str(pool / "SKILL.md") not in dirty   # 不再投池根本身的伪路径


def test_pause_resume_loop_restores_processing(tmp_path, monkeypatch):
    # 终审 I-2 闭环：pause → paused；resume → running；恢复后再次投递的事件
    # 可被守护处理（不真起监听：DEBOUNCE_S=0 + 直接 mark_dirty，consume 线程
    # 单技能扫描到 NEW，断言 state 计数与 Security 数据源一并更新）。
    _redirect_guard(tmp_path, monkeypatch)
    import tray.app as app_mod
    from tray.state import today_key
    pool = tmp_path / "pool"; pool.mkdir()
    d = pool / "fresh"; d.mkdir()
    (d / "SKILL.md").write_text("# fresh skill\n", encoding="utf-8")
    app_mod._install_gui_stubs()
    daemon, state, bridge = app_mod.build_runtime(roots=[str(pool)], mode="warn")
    assert bridge.act("pause", {})["ok"] is True
    assert state.paused is True and state.guard == "paused"
    assert bridge.act("resume", {})["ok"] is True
    assert state.paused is False and state.guard == "running"

    monkeypatch.setattr("tray.daemon.DEBOUNCE_S", 0)
    daemon.mark_dirty(str(d), "manual rescan")
    t = threading.Thread(target=daemon.consume, daemon=True)
    t.start()
    deadline = time.time() + 5
    # 等 record_skill（_scan_once 收尾最后一步）而非 new 计数，避免
    # bump→record 之间的微窗口竞态
    while time.time() < deadline and str(d) not in state.skills:
        time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3)
    assert state.today[today_key()]["new"] == 1
    rec = state.skills[str(d)]              # 扫描后 Security 数据源同步更新
    assert rec["name"] == "fresh" and rec["status"] == "baseline-unreviewed"


def test_get_usage_reads_config_file_and_falls_back(tmp_path, monkeypatch):
    # 终审 I-3：Usage 明细数据源——config 的 usage_file 优先；坏 JSON / 缺
    # 文件回退空行集不炸 UI 轮询；total=zcode+claude+marker，按 total 降序。
    _redirect_guard(tmp_path, monkeypatch)
    import skill_guard
    import tray.app as app_mod
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({"skills": {
        "a-skill": {"zcode": 3, "claude": 1, "marker": 2, "atime": 7,
                    "last_tool_use": "2026-10-01T09:00:00"},
        "b-skill": {"zcode": 0, "claude": 0, "marker": 1, "atime": 0,
                    "last_marker": "2026-09-30"},
        "c-skill": {"zcode": 0, "claude": 0, "marker": 0, "atime": 0},
    }}), encoding="utf-8")
    cfg = skill_guard.load_config()
    cfg["usage_file"] = str(usage)
    skill_guard.save_config(cfg)
    pool = tmp_path / "pool"; pool.mkdir()
    app_mod._install_gui_stubs()
    daemon, state, bridge = app_mod.build_runtime(roots=[str(pool)], mode="warn")
    r = bridge.act("get_usage", {})
    assert r["ok"] is True
    rows = r["rows"]
    assert [x["name"] for x in rows] == ["a-skill", "b-skill", "c-skill"]
    assert rows[0]["total"] == 6 and rows[0]["last"] == "2026-10-01"
    assert rows[1]["total"] == 1 and rows[1]["last"] == "2026-09-30"
    assert rows[2]["total"] == 0 and rows[2]["last"] == ""
    # 坏 JSON → 空行集回退
    usage.write_text("{not-json", encoding="utf-8")
    assert bridge.act("get_usage", {}) == {"ok": True, "rows": []}
    # 缺文件同样回退
    cfg = skill_guard.load_config()
    cfg["usage_file"] = str(tmp_path / "missing.json")
    skill_guard.save_config(cfg)
    assert bridge.act("get_usage", {}) == {"ok": True, "rows": []}


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


def test_res_base_frozen_vs_source(monkeypatch):
    # D-1 根因修复：frozen 下资源基 = sys._MEIPASS（数据文件解包处），
    # 源码运行回退仓库根（tray/ 的父级）。
    import os
    import tray.app as app_mod
    monkeypatch.setattr(sys, "_MEIPASS", "C:/fake/_MEI1234", raising=False)
    assert app_mod._res_base() == "C:/fake/_MEI1234"
    monkeypatch.delattr(sys, "_MEIPASS")
    assert app_mod._res_base() == os.path.dirname(
        os.path.dirname(os.path.abspath(app_mod.__file__)))


def test_run_guard_in_process(monkeypatch):
    # D-1 同源接缝：_run_guard 改进程内调 sg.main（dev/frozen 通吃）。
    # 原 subprocess 形态在 frozen 下 sys.executable=托盘 exe 本体且包内无
    # skill_guard.py——show_diff/accept_drift 会重启托盘而非执行命令。
    import tray.app as app_mod

    calls = []

    def fake_main(args):
        calls.append(list(args))
        print("guard-output-line")
        return 0

    monkeypatch.setattr(app_mod.sg, "main", fake_main)
    r = app_mod.JsBridge._run_guard(["audit", "--show-diff", "X"])
    assert r["ok"] is True
    assert calls == [["audit", "--show-diff", "X"]]
    assert "guard-output-line" in r["output"]

    # argparse 口径：SystemExit(2) → ok=False、不炸、无多余 output
    def exit_code(args):
        raise SystemExit(2)

    monkeypatch.setattr(app_mod.sg, "main", exit_code)
    r2 = app_mod.JsBridge._run_guard(["audit", "--show-diff", "missing"])
    assert r2["ok"] is False

    # sg 显式带消息 SystemExit → 消息进 output 通道
    def exit_msg(args):
        raise SystemExit("技能不在快照中")

    monkeypatch.setattr(app_mod.sg, "main", exit_msg)
    r3 = app_mod.JsBridge._run_guard(["audit", "--show-diff", "missing"])
    assert r3["ok"] is False
    assert "技能不在快照中" in r3["output"]

    # 一般异常 → ok=False、异常文本进 output、不向壳层抛
    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app_mod.sg, "main", boom)
    r4 = app_mod.JsBridge._run_guard(["audit"])
    assert r4["ok"] is False
    assert "kaboom" in r4["output"]


def test_run_guard_timeout(monkeypatch):
    # 超时保护：卡死的守卫动作在 GUARD_TIMEOUT_S 内返回 error，不挂壳层。
    import time
    import tray.app as app_mod
    monkeypatch.setattr(app_mod, "GUARD_TIMEOUT_S", 0.05)

    def slow(args):
        time.sleep(0.5)
        return 0

    monkeypatch.setattr(app_mod.sg, "main", slow)
    r = app_mod.JsBridge._run_guard(["audit"])
    assert r == {"error": "guard action timed out"}
