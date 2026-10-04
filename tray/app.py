# tray/app.py — SkillRadar Tray 入口：线程模型（设计裁定 1）+ pystray 托盘 +
# pywebview 窗口 + js_bridge。关窗 = 缩托盘（hide），托盘菜单退出才真退出。
import concurrent.futures
import contextlib
import io
import os
import subprocess
import sys
import threading
import types

GUARD_TIMEOUT_S = 300   # 壳层守卫动作预算（对齐原 subprocess timeout=300）


def _res_base():
    """资源基目录：frozen（PyInstaller onefile）下数据文件（tray/web、rules）
    解包在 sys._MEIPASS；源码运行时是仓库根。不能统一用
    dirname(dirname(__file__))——frozen 下入口脚本 __file__ 落在 _MEIPASS 根，
    二层 dirname 会指到 %TEMP%（D-1 白屏根因：index.html 实际在
    _MEIPASS/tray/web/ 下）。frozen 分支优先，回退源码口径。"""
    return getattr(sys, "_MEIPASS", None) or \
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE = _res_base()
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
            "get_usage": self._get_usage,
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
        # 终审 I-1：注册根是技能池（root 下每个子目录一个技能），对 root 本身
        # mark_dirty("…/SKILL.md") 经 locate_changed_skill 向上找不到技能——
        # 对池式根是静默 no-op。改为枚举根下含 SKILL.md 的技能子目录逐一投递。
        n = 0
        for r in self.daemon.roots:
            if not os.path.isdir(r):
                continue
            for entry in os.listdir(r):
                skill = os.path.join(r, entry)
                if os.path.isfile(os.path.join(skill, "SKILL.md")):
                    self.daemon.mark_dirty(skill, "manual rescan")
                    n += 1
        return {"ok": True, "queued": n}

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
        # 进程内执行 skill_guard.main（dev/frozen 通吃）。原 subprocess 形态在
        # frozen 下是坏的：sys.executable 是托盘 exe 本体，且包内没有可执行的
        # skill_guard.py——show_diff/accept_drift 会重启托盘而非执行命令（D-1
        # 同源接缝）。sg.main 的 stdout reconfigure 自带 try/except，
        # 对 redirect 的 StringIO 桩安全。
        # 超时保护：ThreadPoolExecutor(1) + result(timeout)。已知限制（注释
        # 固化不修）：redirect_stdout/stderr 是进程级全局态，超时后滞留的
        # 工作线程若继续输出会串流到壳层 stdout——守卫子命令均为有界扫描，
        # GUARD_TIMEOUT_S 预算内必然退出；executor 线程非 daemon，极端挂死
        # 会拖住进程退出（与 subprocess 形态的 kill 缺口同量级，可接受）。
        buf_out, buf_err = io.StringIO(), io.StringIO()

        def _invoke():
            rc_local = None
            try:
                with contextlib.redirect_stdout(buf_out), \
                        contextlib.redirect_stderr(buf_err):
                    rc_local = sg.main(args)
            except SystemExit as e:   # argparse / sg 显式 exit 口径
                rc_local = e.code
                if not isinstance(rc_local, int) and rc_local:
                    buf_err.write(str(rc_local))   # 带消息的 SystemExit → 进 output
            except Exception as e:
                rc_local = 1
                buf_err.write(str(e))
            return rc_local

        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            rc = ex.submit(_invoke).result(timeout=GUARD_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            ex.shutdown(wait=False)   # 不能 wait：会阻塞到卡死调用结束
            return {"error": "guard action timed out"}
        finally:
            ex.shutdown(wait=False)
        return {"ok": rc == 0,
                "output": (buf_out.getvalue() + buf_err.getvalue())[-4000:]}

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

    def _get_usage(self, _):
        # 终审 I-3：Usage 屏数据源。读 config 的 usage_file（覆盖键，冻结 exe
        # 下 skill_monitor.DATA_FILE 落临时目录属已知限制，README 有说明），
        # 未配置则回落 skill_monitor.DATA_FILE。坏 JSON/缺文件回退空行集，
        # 不炸 UI 轮询。total 口径与 skill_monitor 报告一致（zcode+claude+marker）。
        import json
        try:
            import skill_monitor as sm
            data_file = sm.DATA_FILE
        except ImportError:
            data_file = None
        path = sg.load_config().get("usage_file") or data_file
        data = {}
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
        rows = []
        for name, s in (data.get("skills") or {}).items():
            if not isinstance(s, dict):
                continue
            zcode, claude, marker = (int(s.get("zcode", 0) or 0),
                                     int(s.get("claude", 0) or 0),
                                     int(s.get("marker", 0) or 0))
            rows.append({"name": str(name), "total": zcode + claude + marker,
                         "zcode": zcode, "claude": claude, "marker": marker,
                         "atime": int(s.get("atime", 0) or 0),
                         "last": (s.get("last_tool_use")
                                  or s.get("last_marker") or "")[:10]})
        rows.sort(key=lambda r: (-r["total"], r["name"]))
        return {"ok": True, "rows": rows[:25]}


def build_runtime(roots, mode):
    """装配守护运行时（监听打桩由 _install_gui_stubs 控制；测试直调本函数）。
    终审 M-3：TrayState 落盘 ~/.skill-radar/tray_state.json——托盘重启后
    今日计数/事件/守护态不断档（坏文件静默回默认）。"""
    state = TrayState(path=os.path.join(sg.GUARD_DIR, "tray_state.json"))
    daemon = Daemon(roots=roots, mode=mode, state=state)
    runtime = types.SimpleNamespace(icon=None, window=None, watcher=None)
    bridge = JsBridge(daemon, state, runtime)

    def _on_block(skill_path, rep):
        verdict = "CRITICAL"
        cfg = sg.load_config()

        def _icon_notify(t, m):
            # 终审 M-4：toast 子进程路径失败时的降级回调——pystray 图标气泡。
            # pystray notify(message, title) 签名（message 在前）；icon 未就绪
            # 或后端再失败时静默，托盘徽标照常兜底。
            if runtime.icon:
                try:
                    runtime.icon.notify(m, t)
                except Exception:
                    pass

        if cfg.get("consent", {}).get("quarantine"):
            dest = alerts.quarantine_skill(skill_path, allowed_roots=daemon.roots)
            alerts.toast("skill-radar 已隔离技能",
                         f"{os.path.basename(skill_path)}（{verdict}）"
                         if dest else f"隔离失败，请人工处理 {skill_path}",
                         notify=_icon_notify)
            # 事件文案与 toast 同口径：隔离失败如实记，不伪造"已隔离 → None"
            if dest:
                state.add_event("quarantine", f"已隔离 {skill_path} → {dest}")
            else:
                state.add_event("quarantine", f"隔离失败，请人工处理 {skill_path}")
        else:
            alerts.toast("skill-radar 检出 CRITICAL", f"{os.path.basename(skill_path)}",
                         notify=_icon_notify)

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
        try:
            icon.update_menu()   # 动态暂停/恢复项随 guard 态重取文本（终审 I-2）
        except Exception:
            pass   # 图标未就绪（run_detached 前）时静默，文本下轮刷新
    state.on_guard_change = _set_icon_color   # 告警变色钩子（托盘三色，规格 §1b）
    # 终审 I-2：「暂停守护」曾是死胡同（单向 Pause、UI 无恢复控件、resume 零
    # 调用方）。改为按当前态分发的动态项：pystray MenuItem 文本支持可调用
    # （update_menu 时重取），action 依 state.paused 分发 pause/resume——
    # 与 Overview 屏 paused 时显示的「恢复守护 Resume」按钮同一 act 通道。
    menu = pystray.Menu(
        pystray.MenuItem("打开界面 Open", lambda: _reopen_window(bridge)),
        pystray.MenuItem("立即巡检 Rescan now", lambda: bridge.act("rescan")),
        pystray.MenuItem(
            lambda item: "恢复守护 Resume" if bridge.state.paused
            else "暂停守护 Pause",
            lambda: bridge.act("resume" if bridge.state.paused else "pause")),
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
