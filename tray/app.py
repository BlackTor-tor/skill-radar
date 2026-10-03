# tray/app.py — SkillRadar Tray 入口：线程模型（设计裁定 1）+ pystray 托盘 +
# pywebview 窗口 + js_bridge。关窗 = 缩托盘（hide），托盘菜单退出才真退出。
import os
import subprocess
import sys
import threading
import types

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import skill_guard as sg            # noqa: E402
from tray.state import TrayState    # noqa: E402
from tray.daemon import Daemon      # noqa: E402
from tray import alerts             # noqa: E402

# 图标：1x1 PNG 放大后纯色渲染（避免二进制资产入仓；pystray 接受 PIL 之外
# 的 icon 参数在无 PIL 环境受限——见 _install_gui_stubs 注释）
ICON_COLORS = {"running": (30, 41, 59), "alert": (220, 38, 38),
               "quarantine": (217, 119, 6), "paused": (100, 116, 139)}


class JsBridge:
    """pywebview js_bridge：get_state 轮询 + act(name, payload) 白名单分发。"""

    def __init__(self, daemon, state, runtime):
        self.daemon = daemon
        self.state = state
        self.runtime = runtime   # 持托盘图标/窗口引用，供动作调用

    def get_state(self):
        return self.state.snapshot()

    def act(self, name, payload=None):
        handlers = {
            "pause": self._pause, "resume": self._resume,
            "rescan": self._rescan, "open_data_dir": self._open_data_dir,
            "show_diff": self._show_diff, "accept_drift": self._accept_drift,
            "set_mode": self._set_mode, "set_quarantine": self._set_quarantine,
        }
        h = handlers.get(name)
        if h is None:
            return {"error": "unknown action"}
        return h(payload or {})

    def _pause(self, _):
        self.state.set_guard("paused")
        return {"ok": True, "guard": "paused"}

    def _resume(self, _):
        self.state.set_guard("running")
        return {"ok": True, "guard": "running"}

    def _rescan(self, _):
        for r in self.daemon.roots:
            self.daemon.mark_dirty(os.path.join(r, "SKILL.md"), "manual rescan")
        return {"ok": True}

    def _open_data_dir(self, _):
        d = sg.GUARD_DIR
        try:
            if sys.platform == "win32":
                os.startfile(d)   # noqa
            elif sys.platform == "darwin":
                subprocess.run(["open", d])
            else:
                subprocess.run(["xdg-open", d])
            return {"ok": True}
        except OSError as e:
            return {"error": str(e)}

    def _show_diff(self, payload):
        skill = str(payload.get("skill", ""))
        return self._run_guard(["audit", "--show-diff", skill])

    def _accept_drift(self, payload):
        skill = str(payload.get("skill", ""))
        return self._run_guard(["audit", "--accept-drift", skill])

    @staticmethod
    def _run_guard(args):
        try:
            p = subprocess.run([sys.executable, os.path.join(BASE, "skill_guard.py"), *args],
                               capture_output=True, text=True, timeout=300)
            return {"ok": p.returncode == 0, "output": (p.stdout or p.stderr)[-4000:]}
        except (OSError, subprocess.SubprocessError) as e:
            return {"error": str(e)}

    def _set_mode(self, payload):
        cfg = sg.load_config()
        cfg.setdefault("consent", {})["add_block"] = bool(payload.get("block"))
        sg.save_config(cfg)
        self.daemon.mode = "block" if payload.get("block") else "warn"
        return {"ok": True, "mode": self.daemon.mode}

    def _set_quarantine(self, payload):
        cfg = sg.load_config()
        cfg.setdefault("consent", {})["quarantine"] = bool(payload.get("on"))
        sg.save_config(cfg)
        return {"ok": True}


def build_runtime(roots, mode):
    """装配守护运行时（监听打桩由 _install_gui_stubs 控制；测试直调本函数）。"""
    state = TrayState()
    daemon = Daemon(roots=roots, mode=mode, state=state)
    runtime = types.SimpleNamespace(icon=None, window=None, watcher=None)
    bridge = JsBridge(daemon, state, runtime)

    def _on_block(skill_path, rep):
        verdict = "CRITICAL"
        cfg = sg.load_config()
        if cfg.get("consent", {}).get("quarantine"):
            dest = alerts.quarantine_skill(skill_path, allowed_roots=daemon.roots)
            alerts.toast("skill-radar 已隔离技能",
                         f"{os.path.basename(skill_path)}（{verdict}）"
                         if dest else f"隔离失败，请人工处理 {skill_path}")
            # 事件文案与 toast 同口径：隔离失败如实记，不伪造"已隔离 → None"
            if dest:
                state.add_event("quarantine", f"已隔离 {skill_path} → {dest}")
            else:
                state.add_event("quarantine", f"隔离失败，请人工处理 {skill_path}")
        else:
            alerts.toast("skill-radar 检出 CRITICAL", f"{os.path.basename(skill_path)}")

    daemon.on_block = _on_block

    def _on_fs_event(abs_path):
        daemon.mark_dirty(abs_path)

    runtime.daemon = daemon   # shutdown 停机序列用（stop + join consume）
    runtime._on_fs_event = _on_fs_event
    return daemon, state, bridge


_STUBS_INSTALLED = False


def _install_gui_stubs():
    """测试模式：把 pystray/webview 换成桩，main() 装配后立即返回。

    双层打桩：sys.modules 注入空桩模块（挡 import），同时把
    importlib.util.find_spec 对 pystray/webview 打成 None——本机可能装了
    真依赖，find_spec 会找到真 spec 绕过桩；打掉它后「桩环境 = 未安装」
    语义一致，main() 的依赖探测走同一条 SystemExit 提示路径。
    （测试支持代码：仅在显式调用的进程内生效；wrapper 对其余名字透传真
    find_spec，不影响用例内其他 importlib 消费方。）"""
    global _STUBS_INSTALLED
    if _STUBS_INSTALLED:
        return
    sys.modules.setdefault("pystray", types.ModuleType("pystray"))
    sys.modules.setdefault("webview", types.ModuleType("webview"))
    import importlib.util
    _real_find_spec = importlib.util.find_spec

    def _find_spec(name, *args, **kwargs):
        if name in ("pystray", "webview"):
            return None
        return _real_find_spec(name, *args, **kwargs)

    importlib.util.find_spec = _find_spec
    _STUBS_INSTALLED = True


def _mode_from_config(cfg):
    return "block" if cfg.get("consent", {}).get("add_block") else "warn"


def _index_url():
    return "file:///" + os.path.join(BASE, "tray", "web", "index.html").replace("\\", "/")


def _window_lifecycle(runtime, action):
    """窗口生命周期状态机（规格 §1b：关窗=缩托盘不退出；Quit 是唯一退出通道）。

    action: "close"（UI 窗 closing 事件）| "reopen"（托盘「打开界面」）|
    "quit"（托盘 Quit）。返回决策："hide"（veto 关闭 = 缩托盘）|
    "recreate"（重开 UI 窗）| "quit"（放行关闭并退出）。

    quitting 期间一律 "quit"：pywebview 6.2.1 实证 destroy 也走 FormClosing
    （winforms on_closing → closing.set()，任一 handler 返回 False 即
    args.Cancel=True），不放行则 Quit 永远关不掉窗口、start() 永不返回。"""
    if action == "quit" or getattr(runtime, "quitting", False):
        return "quit"
    if action == "reopen":
        return "recreate"
    return "hide"   # "close"（及未知动作）保活优先


def _handle_ui_closing(runtime, window):
    """UI 窗 closing 处理器（pywebview veto 语义：返回 False 取消关闭）。
    常态 hide 缩托盘（窗口保活，「打开界面」show 复原）；退出流程放行。"""
    if _window_lifecycle(runtime, "close") == "hide":
        window.hide()
        return False
    return None   # quitting：放行，让 destroy 生效


def _handle_quit(runtime):
    """托盘 Quit（唯一退出通道）：置 quitting → shutdown。关窗/重开不经此。"""
    if _window_lifecycle(runtime, "quit") == "quit":
        runtime.quitting = True   # closing 处理器随之放行
        shutdown(runtime)


def _reopen_window(bridge):
    """托盘「打开界面」：常态下窗口只是被 closing-veto hide，show() 复原；
    窗口已真关（异常路径）则从本线程 create_window 重建（pywebview 支持
    运行中建窗），并重新绑定 closing 处理器。"""
    runtime = bridge.runtime
    if _window_lifecycle(runtime, "reopen") != "recreate":
        return
    if runtime.window is not None:
        try:
            runtime.window.show()
            return
        except Exception:
            runtime.window = None
    import webview
    w = webview.create_window("SkillRadar", url=_index_url(), js_api=bridge,
                              width=1080, height=720, min_size=(860, 560))
    w.events.closing += lambda: _handle_ui_closing(runtime, w)
    runtime.window = w


def main():
    import importlib.util
    for mod in ("pystray", "webview"):
        # find_spec 而非裸 import：绕过 sys.modules 缓存桩（测试注入 None），
        # 且真缺依赖时同产 ImportError 语义的提示路径
        if importlib.util.find_spec(mod) is None:
            raise SystemExit("[SkillRadar Tray] 缺 GUI 依赖：pip install -r "
                             "requirements-gui.txt（核心五件 .py 仍然纯标准库）")
    cfg = sg.load_config()
    roots = [r["path"] for r in cfg.get("roots", []) if os.path.isdir(r["path"])]
    daemon, state, bridge = build_runtime(roots, _mode_from_config(cfg))

    from tray import watchers
    watcher_cls = watchers.pick_backend()
    watcher = watcher_cls(roots, callback=runtime_event(daemon))
    w_thread = threading.Thread(target=watcher.start, daemon=True)
    w_thread.start()
    s_thread = threading.Thread(target=daemon.consume, daemon=True)
    s_thread.start()
    daemon.consume_thread = s_thread   # shutdown join consume 用（审查 Important-1）

    # 托盘（独立线程 detach；菜单四项，规格 §1b）。
    # 图标：pystray 依赖 Pillow 的 Image——requirements-gui.txt 追加 pillow
    #（pystray 的 Windows 后端本就要求它；16x16 纯色块由代码生成，无二进制资产）。
    import pystray
    from PIL import Image
    img = Image.new("RGB", (16, 16), ICON_COLORS["running"])
    def _set_icon_color(g):
        icon.icon = Image.new("RGB", (16, 16), ICON_COLORS.get(g, ICON_COLORS["running"]))
    state.on_guard_change = _set_icon_color   # 告警变色钩子（托盘三色，规格 §1b）
    menu = pystray.Menu(
        pystray.MenuItem("打开界面 Open", lambda: _reopen_window(bridge)),
        pystray.MenuItem("立即巡检 Rescan now", lambda: bridge.act("rescan")),
        pystray.MenuItem("暂停守护 Pause", lambda: bridge.act("pause")),
        pystray.MenuItem("退出 Quit", lambda: _handle_quit(bridge.runtime)),
    )
    icon = pystray.Icon("SkillRadar", img, "SkillRadar Tray", menu)
    bridge.runtime.icon = icon
    bridge.runtime.watcher = watcher

    # 主线程：pywebview（mac 上 NSApplication 必须主线程，设计裁定 1）。
    # 关窗 = 缩托盘（closing 事件 veto → hide，规格 §1b / 审查 Important-2），
    # 「打开界面」show() 复原；真退出只走托盘 Quit → _handle_quit → shutdown。
    import webview
    webview.create_window("SkillRadar", url=_index_url(), js_api=bridge,
                          width=1080, height=720, min_size=(860, 560))
    ui = webview.windows[0]
    bridge.runtime.window = ui
    ui.events.closing += lambda: _handle_ui_closing(bridge.runtime, ui)
    icon.run_detached()
    webview.start()
    shutdown(bridge.runtime)   # start() 返回 = 退出流程收尾（shutdown 幂等）


def runtime_event(daemon):
    return daemon.mark_dirty


def shutdown(runtime):
    """幂等停机（审查 Important-1）：停监听（join 平台线程）→ 停守护
    （stop + join consume 线程）→ 停托盘 → 销毁窗口。托盘 Quit 与 main()
    尾部（start() 返回后）都会调，_shutdown_done 防重入。

    顺序依据：先停事件源（watcher 线程不再 mark_dirty），再停消费者
    （daemon），等待收尾各给 5s——RDCW 线程 stop 后由 CancelIoEx 立即退出，
    consume 循环 _stop 置位后最迟 0.5s 退出。"""
    if getattr(runtime, "_shutdown_done", False):
        return
    runtime._shutdown_done = True
    watcher = getattr(runtime, "watcher", None)
    if watcher:
        watcher.stop()
        for t in list(getattr(watcher, "_threads", None) or []):
            t.join(timeout=5)
    daemon = getattr(runtime, "daemon", None)
    if daemon:
        daemon.stop()
        if hasattr(daemon, "join"):
            daemon.join(timeout=5)
    if runtime.icon:
        runtime.icon.stop()
    if runtime.window:
        try:
            runtime.window.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
