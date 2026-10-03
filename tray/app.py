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
            state.add_event("quarantine", f"已隔离 {skill_path} → {dest}")
        else:
            alerts.toast("skill-radar 检出 CRITICAL", f"{os.path.basename(skill_path)}")

    daemon.on_block = _on_block

    def _on_fs_event(abs_path):
        daemon.mark_dirty(abs_path)

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
        pystray.MenuItem("打开界面 Open", lambda: bridge.runtime.window and
                         bridge.runtime.window.show()),
        pystray.MenuItem("立即巡检 Rescan now", lambda: bridge.act("rescan")),
        pystray.MenuItem("暂停守护 Pause", lambda: bridge.act("pause")),
        pystray.MenuItem("退出 Quit", lambda: shutdown(bridge.runtime)),
    )
    icon = pystray.Icon("SkillRadar", img, "SkillRadar Tray", menu)
    bridge.runtime.icon = icon
    bridge.runtime.watcher = watcher

    # 主线程：pywebview（mac 上 NSApplication 必须主线程，设计裁定 1）。
    # 关窗 = hide（缩托盘），真退出走托盘菜单 shutdown()。
    webview.create_window("SkillRadar", url="file:///" + os.path.join(
        BASE, "tray", "web", "index.html").replace("\\", "/"),
        js_api=bridge, width=1080, height=720, min_size=(860, 560))
    bridge.runtime.window = webview.windows[0]
    icon.run_detached()
    webview.start()
    shutdown(bridge.runtime)   # start() 返回 = 全部窗口关闭/退出


def runtime_event(daemon):
    return daemon.mark_dirty


def shutdown(runtime):
    if runtime.watcher:
        runtime.watcher.stop()
    if runtime.icon:
        runtime.icon.stop()
    if runtime.window:
        try:
            runtime.window.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
