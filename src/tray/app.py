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
    if getattr(sys, "_MEIPASS", None):
        return sys._MEIPASS
    source_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(source_dir) if os.path.basename(source_dir) == "src" else source_dir


BASE = _res_base()
# 源码模式下入口位于 src/tray，生产模块位于其上一级 src；迁移后不再依赖根目录兼容脚本。
SOURCE_MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(SOURCE_MODULE_ROOT):
    sys.path.insert(0, SOURCE_MODULE_ROOT)
sys.path.insert(0, BASE)

import skill_guard as sg            # noqa: E402
from tray.state import TrayState    # noqa: E402
from tray.daemon import Daemon      # noqa: E402
from tray import alerts             # noqa: E402
from tray.branding import asset_path, tray_icon  # noqa: E402
from tray.processing import ProcessingService, _safe_chain  # noqa: E402
from tray.reports import ReportService, render_events_markdown, render_skill_markdown  # noqa: E402
from tray.usage import UsageService  # noqa: E402

_CLIENT_COPY = {
    "zh-CN": {
        "open": "打开界面", "rescan": "立即检查", "pause": "暂停检查",
        "resume": "继续检查", "quit": "退出",
        "changed": "内容有变化", "critical": "发现严重风险", "new": "发现新技能",
        "isolated": "技能已隔离", "isolate_failed": "隔离失败",
        "manual_review": "请查看详情并手动处理",
    },
    "en": {
        "open": "Open SkillRadar", "rescan": "Check now", "pause": "Pause checks",
        "resume": "Resume checks", "quit": "Quit",
        "changed": "Skill content changed", "critical": "Serious risk found",
        "new": "New skill found", "isolated": "Skill isolated",
        "isolate_failed": "Could not isolate skill",
        "manual_review": "Review the details and handle it manually",
    },
}


def _ui_language(cfg=None):
    """读取已保存的界面语言，旧配置及不支持的语言默认使用中文。"""
    language = (sg.load_config() if cfg is None else cfg).get("ui_language", "zh-CN")
    return language if language in ("zh-CN", "en") else "zh-CN"


def _ui_theme(cfg=None):
    """读取保存的外观，旧配置及不支持的值默认使用浅色。"""
    theme = (sg.load_config() if cfg is None else cfg).get("ui_theme", "light")
    return theme if theme in ("light", "dark") else "light"


def _client_text(key, cfg=None):
    """托盘菜单及系统通知共用当前语言，技能名称和文件路径保持原样。"""
    return _CLIENT_COPY[_ui_language(cfg)][key]


class JsBridge:
    """pywebview js_bridge：get_state 轮询 + act(name, payload) 白名单分发。"""

    def __init__(self, daemon, state, runtime):
        self.daemon = daemon
        self.state = state
        self.runtime = runtime   # 持托盘图标/窗口引用，供动作调用
        self._settings_lock = threading.RLock()
        self.processing = ProcessingService(daemon, state)
        self.reports = ReportService(state, usage_provider=lambda: self._get_usage({"refresh": False}))
        self.usage = None  # 状态请求仅建服务；main 首次补扫在后台读取已知会话目录。

    def get_state(self):
        snap = self.state.snapshot()
        snap.update(self.processing.snapshot())
        cfg = sg.load_config()
        snap["settings"] = {"block": self.daemon.mode == "block",
                            "quarantine": bool(cfg.get("consent", {}).get("quarantine")),
                            "roots": cfg.get("roots", []),
                            "language": _ui_language(cfg),
                            "theme": _ui_theme(cfg)}
        snap["usage_status"] = self._usage_service().snapshot()
        return snap

    def _usage_service(self):
        with self._settings_lock:
            if self.usage is None:
                self.usage = UsageService(data_dir=sg.GUARD_DIR, auto_start=False)
                self.runtime.usage = self.usage
            return self.usage

    def act(self, name, payload=None):
        handlers = {
            "pause": self._pause, "resume": self._resume,
            "rescan": self._rescan, "open_data_dir": self._open_data_dir,
            "open_skill_location": self._open_skill_location,
            "show_diff": self._show_diff, "accept_drift": self._accept_drift,
            "set_mode": self._set_mode, "set_quarantine": self._set_quarantine,
            "set_language": self._set_language, "set_theme": self._set_theme,
            "get_usage": self._get_usage,
            "scan_usage": self._scan_usage,
            "select_usage_directory": self._select_usage_directory,
            "generate_report": lambda _: self.reports.generate_report(),
            "list_reports": lambda _: self.reports.list_reports(),
            "get_report": lambda p: self.reports.get_report(p.get("id")),
            "export_report": self._export_report,
            "get_markdown": self._get_markdown,
            "copy_markdown": self._copy_markdown,
            "add_root": self._add_root, "remove_root": self._remove_root,
            "batch_action": self._batch_action,
        }
        h = handlers.get(name)
        if h is None:
            return {"error": "unknown action"}
        if payload is not None and not isinstance(payload, dict):
            return {"error": "payload must be an object"}
        try:
            return h(payload or {})
        except (OSError, ValueError, TypeError, subprocess.SubprocessError) as e:
            return {"error": sg._sanitize(str(e))}

    def _get_markdown(self, payload):
        if payload.get("subject") == "events":
            return {"ok": True, "markdown": render_events_markdown(self.state.snapshot()["events"])}
        if payload.get("subject") == "skill":
            skill = self._selected_skill(payload)
            if skill is None:
                return {"error": "select a registered skill by its full path"}
            return {"ok": True, "markdown": render_skill_markdown(skill, self.state.snapshot()["skills"][skill])}
        return {"error": "unknown markdown subject"}

    def _copy_markdown(self, payload):
        """系统剪贴板兜底；不把报告文本当作命令执行。"""
        value = payload.get("text")
        if not isinstance(value, str):
            return {"error": "provide markdown text"}
        if sys.platform == "win32":
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "$reader = [IO.StreamReader]::new([Console]::OpenStandardInput(), [Text.UTF8Encoding]::new($false)); "
                            "$markdownText = $reader.ReadToEnd(); Set-Clipboard -Value $markdownText"], input=value, text=True,
                           encoding="utf-8", check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=value, text=True, check=True)
        else:
            return {"error": "clipboard unavailable; select and copy the Markdown source"}
        return {"ok": True}

    def _export_report(self, payload):
        report_id, fmt = payload.get("id"), payload.get("format", "md")
        result = self.reports.export_report(report_id, fmt)
        if not result.get("ok") or self.runtime.window is None:
            return result
        # pywebview SAVE_DIALOG=30；只使用用户选定的目标，不接受网页传入写入路径。
        target = self.runtime.window.create_file_dialog(30, save_filename=result["filename"],
            file_types=(("Markdown (*.md)" if fmt == "md" else "HTML (*.html)"),))
        if not target:
            return {"ok": True, "cancelled": True}
        destination = target[0] if isinstance(target, (tuple, list)) else target
        return self.reports.export_report(report_id, fmt, destination=destination)

    def _scan_usage(self, payload):
        return self._usage_service().scan(payload.get("path"), payload.get("source", "auto"))

    def _select_usage_directory(self, _):
        if self.runtime.window is None:
            return {"error": "directory dialog unavailable"}
        target = self.runtime.window.create_file_dialog(20)  # FOLDER_DIALOG
        if not target:
            return {"ok": True, "cancelled": True}
        path = target[0] if isinstance(target, (tuple, list)) else target
        return {"ok": True, "path": path}

    def _batch_action(self, payload):
        return self.processing.submit(payload.get("action"), payload.get("items"))

    def _pause(self, _):
        self.state.set_guard("paused")
        return {"ok": True, "guard": "paused"}

    def _resume(self, _):
        self.state.set_guard("running")
        self._rescan({})   # 暂停期间到期事件已丢弃，恢复时补扫防漏检。
        return {"ok": True, "guard": "running"}

    def _rescan(self, _):
        # 终审 I-1：注册根是技能池（root 下每个子目录一个技能），对 root 本身
        # mark_dirty("…/SKILL.md") 经 locate_changed_skill 向上找不到技能——
        # 对池式根是静默 no-op。改为枚举根下含 SKILL.md 的技能子目录逐一投递。
        n = 0
        for skill in sg.iter_skill_dirs(self.daemon.roots):
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

    def _open_skill_location(self, payload):
        """只读打开已检查或安装清单中的技能，不要求先执行安全检查。"""
        requested = payload.get("skill")
        if not isinstance(requested, str) or not os.path.isabs(requested) or \
                any(char in requested for char in "\x00\r\n"):
            return {"error": "请选择报告或技能列表中的完整技能文件夹路径。"}
        with self.daemon.scan_lock:
            skill = self._selected_skill(payload)
            canonical = self._skill_location(requested)
            if canonical is None:
                return {"error": "技能文件夹已移走、已隔离或位置无法确认；请刷新报告后重试。"}
            if skill is None:
                # 使用报告包含尚未检查的安装项；只枚举登记目录，不读取会话日志。
                from skill_inventory import collect_inventory
                identity = sg._canon_path(canonical)
                if not any(sg._canon_path(row["path"]) == identity
                           for row in collect_inventory(self.daemon.roots)):
                    return {"error": "这个位置不在当前技能清单中；请刷新报告并核对监听目录。"}
                verified = self._skill_location(requested)
                if verified is None or sg._canon_path(verified) != identity:
                    return {"error": "技能文件夹位置发生变化；请刷新报告后重试。"}
                canonical = verified
            try:
                if sys.platform == "win32":
                    os.startfile(canonical, "open")
                else:
                    executable = "open" if sys.platform == "darwin" else "xdg-open"
                    subprocess.run([executable, canonical], check=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                return {"error": "无法打开技能文件夹；请复制完整路径，在文件管理器中手动打开。"}
            return {"ok": True, "path": canonical}

    def _skill_location(self, skill):
        """从完整登记根向下复核路径；规范目录用于打开，操作权限不因此放宽。"""
        if not sg._is_skill_dir(skill):
            return None
        canonical = os.path.realpath(skill)
        canonical_identity = sg._canon_path(canonical)
        if self.daemon._under(canonical, self.processing.quarantine_dir) or \
                any(canonical_identity == sg._canon_path(os.path.realpath(root))
                    for root in self.daemon.roots):
            return None
        for root in self.daemon.roots:
            boundary = os.path.normpath(os.path.abspath(root))
            current = os.path.normpath(os.path.abspath(skill))
            identity = sg._canon_path(boundary)
            selected = sg._canon_path(current)
            real_root = os.path.realpath(boundary)
            real_identity = sg._canon_path(real_root)
            if not selected.startswith(identity.rstrip("/") + "/"):
                if not selected.startswith(real_identity.rstrip("/") + "/"):
                    continue
                # 安装清单使用真实路径；仍从原登记入口检查，防止绕过根内链接。
                current = os.path.join(boundary, os.path.relpath(current, real_root))
            while True:
                if not os.path.isdir(current) or sg._is_reparse(current):
                    break
                if sg._canon_path(current) == identity:
                    # 根以上的 junction 可是已登记别名；根以下不可被链接替换。
                    if self.daemon._under(canonical, real_root) and _safe_chain(canonical) and \
                            sg._is_skill_dir(canonical):
                        return canonical
                    break
                parent = os.path.dirname(current)
                if parent == current:
                    break
                current = parent
        return None

    def _show_diff(self, payload):
        skill = self._selected_skill(payload)
        if skill is None:
            return {"error": "select a registered skill by its full path"}
        with self.daemon.scan_lock:
            return self._run_guard(["audit", "--show-diff", skill])

    def _accept_drift(self, payload):
        skill = self._selected_skill(payload)
        if skill is None:
            return {"error": "select a registered skill by its full path"}
        with self.daemon.scan_lock:
            row = self.state.snapshot()["skills"][skill]
            result = self.processing.process_one("review", {"path": skill, "version": row["version"]})
            return {"ok": result["status"] == "success", **result}

    def _selected_skill(self, payload):
        """名称可在多根重复；只接受当前风险表中的完整注册路径。"""
        requested = payload.get("skill")
        if not isinstance(requested, str) or not os.path.isabs(requested):
            return None
        for path in self.state.snapshot()["skills"]:
            if sg._canon_path(path) == sg._canon_path(requested) \
                    and any(self.daemon._under(path, root) for root in self.daemon.roots):
                return path
        return None

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
        with self._settings_lock:
            cfg = sg.load_config()
            cfg.setdefault("consent", {})["add_block"] = bool(payload.get("block"))
            sg.save_config(cfg)
            self.daemon.mode = "block" if payload.get("block") else "warn"
        return {"ok": True, "mode": self.daemon.mode}

    def _set_quarantine(self, payload):
        with self._settings_lock:
            cfg = sg.load_config()
            cfg.setdefault("consent", {})["quarantine"] = bool(payload.get("on"))
            sg.save_config(cfg)
        return {"ok": True}

    def _set_language(self, payload):
        """保存界面语言并刷新托盘菜单，立即生效且重启后保留选择。"""
        language = payload.get("language")
        if language not in ("zh-CN", "en"):
            return {"error": "choose zh-CN or en"}
        with self._settings_lock:
            cfg = sg.load_config()
            cfg["ui_language"] = language
            sg.save_config(cfg)
        if self.runtime.icon is not None:
            try:
                _on_gui_thread(self.runtime.icon.update_menu)
            except Exception:
                pass  # 菜单后端暂时不可用不影响已保存的语言选择。
        return {"ok": True, "language": language}

    def _set_theme(self, payload):
        """保存主窗口外观，避免隐私模式清理浏览器存储后丢失选择。"""
        theme = payload.get("theme")
        if theme not in ("light", "dark"):
            return {"error": "choose light or dark"}
        with self._settings_lock:
            cfg = sg.load_config()
            cfg["ui_theme"] = theme
            sg.save_config(cfg)
        return {"ok": True, "theme": theme}

    def _add_root(self, payload):
        return self._change_root(payload, remove=False)

    def _remove_root(self, payload):
        return self._change_root(payload, remove=True)

    def _change_root(self, payload, remove):
        """根注册写回 CLI 配置后替换监听器；不存在的根由平台监听器等待。"""
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip() \
                or any(c in path for c in "\r\n\x00"):
            return {"error": "provide an absolute root path"}
        path = os.path.expanduser(path.strip())
        if not os.path.isabs(path):
            return {"error": "provide an absolute root path"}
        path = os.path.normpath(path)
        with self._settings_lock:
            cfg = sg.load_config()
            records = [r for r in cfg.get("roots", [])
                       if isinstance(r, dict) and isinstance(r.get("path"), str)]
            existing = [r for r in records if sg._canon_path(r["path"]) == sg._canon_path(path)]
            if remove:
                records = [r for r in records if r not in existing]
            elif not existing:
                records.append({"path": path, "builtin": False})
            cfg["roots"] = records
            sg.save_config(cfg)
            self.daemon.roots = [os.path.normpath(r["path"]) for r in records]
            self.state.watched_roots = len(self.daemon.roots)
            if self.runtime.watcher is not None:
                _stop_watcher(self.runtime.watcher)
                _start_watcher(self.runtime, self.daemon)
            if not remove and os.path.isdir(path):
                self._rescan({})
        return {"ok": True, "roots": records}

    def _get_usage(self, _):
        from skill_inventory import collect_inventory, latest_time, merge_usage
        cfg = sg.load_config()
        service = self._usage_service()
        if not cfg.get("usage_file"):
            result = service.get_usage(refresh=_.get("refresh", True), force=_.get("force", False))
        else:
            collected = service.get_usage(refresh=False)
            if collected.get("rows"):
                result = service.get_usage(refresh=_.get("refresh", True), force=_.get("force", False))
            else:
                result = self._legacy_usage(cfg["usage_file"], collected.get("status", service.snapshot()), latest_time)
        result.update(merge_usage(result.get("rows", []), collect_inventory(self.daemon.roots), result.get("status")))
        missing = [path for path in self.daemon.roots if not os.path.isdir(path)]
        summary = result["inventory_summary"]
        summary["inventory_complete"] = not missing
        summary["unavailable_inventory_roots"] = missing
        if missing:
            summary["coverage_note"] += " 部分技能安装目录不可用，安装清单仅包含当前可读取的目录。"
        return result

    def _legacy_usage(self, path, status, latest_time):
        # 旧 usage_file 只在尚未采集到历史调用时兼容回退；不与新计数相加，避免重复。
        # 正常客户端 main 会自动补扫；单独读取兼容文件无需启动真实日志扫描。
        import json
        data = {}
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
        skills = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(skills, dict):
            return {"ok": True, "rows": [], "status": status}
        rows = []
        for name, s in skills.items():
            if not isinstance(s, dict):
                continue
            try:
                codex = max(0, int(s.get("codex", 0) or 0))
                zcode, claude, marker = (max(0, int(s.get("zcode", 0) or 0)),
                                     max(0, int(s.get("claude", 0) or 0)),
                                     max(0, int(s.get("marker", 0) or 0)))
                atime = max(0, int(s.get("atime", 0) or 0))
            except (ValueError, TypeError, OverflowError):
                continue
            rows.append({"name": str(name), "total": codex + zcode + claude + marker,
                         "zcode": zcode, "claude": claude, "marker": marker,
                         "atime": atime,
                         "last": latest_time(s.get("last_tool_use"), s.get("last_marker"))})
            if codex:
                rows[-1]["codex"] = codex
        rows.sort(key=lambda r: (-r["total"], r["name"]))
        return {"ok": True, "rows": rows, "status": status}


def build_runtime(roots, mode):
    """装配守护运行时（监听打桩由 _install_gui_stubs 控制；测试直调本函数）。
    终审 M-3：TrayState 落盘 ~/.skill-radar/tray_state.json——托盘重启后
    今日计数/事件/守护态不断档（坏文件静默回默认）。"""
    state = TrayState(path=os.path.join(sg.GUARD_DIR, "tray_state.json"))
    daemon = Daemon(roots=roots, mode=mode, state=state)
    runtime = types.SimpleNamespace(icon=None, window=None, watcher=None)
    bridge = JsBridge(daemon, state, runtime)

    def _icon_notify(t, m):
        if runtime.icon:
            try:
                runtime.icon.notify(m, t)
            except Exception:
                pass

    def _on_alert(skill_path, rep, status):
        """警告模式同样弹通知；通知失败不能中断快照与守护。"""
        critical = any(f.severity == "CRITICAL" for f in rep.findings)
        key = "critical" if critical else "changed" if status == "DRIFT" else "new"
        title = "SkillRadar · " + _client_text(key)
        try:
            alerts.toast(title, os.path.basename(skill_path), notify=_icon_notify)
        except Exception as e:
            state.add_event("error", f"通知失败: {e}")

    def _on_block(skill_path, rep):
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
            outcome = bridge.processing.isolate_automatic(skill_path)
            dest = next((row["destination"] for row in bridge.processing.snapshot()["quarantine"]
                         if row["id"] == outcome.get("quarantine_id")), None)
            title = "SkillRadar · " + _client_text("isolated" if dest else "isolate_failed", cfg)
            message = os.path.basename(skill_path) if dest else \
                _client_text("manual_review", cfg) + " · " + skill_path
            alerts.toast(title, message,
                         notify=_icon_notify)
            # 事件文案与 toast 同口径：隔离失败如实记，不伪造"已隔离 → None"
            if dest:
                state.set_guard("quarantine")   # 只有移动成功才显示隔离状态。
                state.add_event("quarantine", f"已隔离 {skill_path} → {dest}")
            else:
                state.add_event("quarantine", f"隔离失败，请人工处理 {skill_path}")
        else:
            alerts.toast("SkillRadar · " + _client_text("critical", cfg), os.path.basename(skill_path),
                         notify=_icon_notify)

    daemon.on_block = _on_block
    daemon.on_alert = _on_alert

    def _on_fs_event(abs_path):
        daemon.mark_dirty(abs_path)

    runtime.daemon = daemon   # shutdown 停机序列用（stop + join consume）
    runtime.processing = bridge.processing
    runtime._on_fs_event = _on_fs_event
    return daemon, state, bridge


def _stop_watcher(watcher):
    """先取消事件源，再等待平台线程，避免替换根时留下旧监听。"""
    watcher.stop()
    for thread in list(getattr(watcher, "_threads", None) or []):
        if thread is not threading.current_thread():
            thread.join(timeout=5)


def _start_watcher(runtime, daemon):
    from tray import watchers
    watcher = watchers.pick_backend()(daemon.roots, callback=runtime_event(daemon))
    runtime.watcher = watcher

    def start():
        try:
            watcher.start()
        except Exception as e:
            daemon.state.add_event("error", f"目录监听启动失败: {e}")
            daemon.state.set_guard("alert")

    thread = threading.Thread(target=start, daemon=True)
    runtime.watcher_thread = thread
    thread.start()


def _on_gui_thread(fn, *args):
    """macOS 的 NSStatusItem/UI 操作必须投递到宿主 NSApplication 主线程。"""
    if sys.platform == "darwin" and threading.current_thread() is not threading.main_thread():
        from PyObjCTools import AppHelper
        AppHelper.callAfter(fn, *args)
    else:
        fn(*args)


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
    from pathlib import Path
    return Path(BASE, "tray", "web", "index.html").resolve().as_uri()


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


def _tray_menu(bridge, pystray):
    """每次打开菜单时读取语言和暂停状态，无需重启客户端。"""
    return pystray.Menu(
        pystray.MenuItem(lambda item: _client_text("open"), lambda: _reopen_window(bridge)),
        pystray.MenuItem(lambda item: _client_text("rescan"), lambda: bridge.act("rescan")),
        pystray.MenuItem(
            lambda item: _client_text("resume" if bridge.state.paused else "pause"),
            lambda: bridge.act("resume" if bridge.state.paused else "pause")),
        pystray.MenuItem(lambda item: _client_text("quit"), lambda: _handle_quit(bridge.runtime)),
    )


def main():
    import importlib.util
    for mod in ("pystray", "webview"):
        # find_spec 而非裸 import：绕过 sys.modules 缓存桩（测试注入 None），
        # 且真缺依赖时同产 ImportError 语义的提示路径
        if importlib.util.find_spec(mod) is None:
            raise SystemExit("[SkillRadar Tray] 缺 GUI 依赖：pip install -r "
                             "requirements-gui.txt（核心五件 .py 仍然纯标准库）")
    cfg = sg.load_config()
    roots = [os.path.normpath(r["path"]) for r in cfg.get("roots", [])
             if isinstance(r, dict) and isinstance(r.get("path"), str)]
    daemon, state, bridge = build_runtime(roots, _mode_from_config(cfg))

    _start_watcher(bridge.runtime, daemon)
    s_thread = threading.Thread(target=daemon.consume, daemon=True)
    s_thread.start()
    daemon.consume_thread = s_thread   # shutdown join consume 用（审查 Important-1）

    # 托盘（独立线程 detach；菜单四项，规格 §1b）。
    # 品牌图标始终保留雷达标识，状态圆点提示运行、待确认、严重风险及暂停。
    import pystray
    img = tray_icon("running")
    def _set_icon_state(g):
        icon.icon = tray_icon(g)
        try:
            icon.update_menu()   # 动态暂停/恢复项随 guard 态重取文本（终审 I-2）
        except Exception:
            pass   # 图标未就绪（run_detached 前）时静默，文本下轮刷新
    state.on_guard_change = lambda g: _on_gui_thread(_set_icon_state, g)
    # 终审 I-2：「暂停守护」曾是死胡同（单向 Pause、UI 无恢复控件、resume 零
    # 调用方）。改为按当前态分发的动态项：pystray MenuItem 文本支持可调用
    # （update_menu 时重取），action 依 state.paused 分发 pause/resume——
    # 与 Overview 屏 paused 时显示的「恢复守护 Resume」按钮同一 act 通道。
    menu = _tray_menu(bridge, pystray)
    icon = pystray.Icon("SkillRadar", img, "SkillRadar", menu)
    bridge.runtime.icon = icon
    state.on_guard_change(state.guard)   # 恢复态与启动时已有告警必须同步图标。

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
    bridge.act("rescan")   # 首屏已有技能也应显示风险记录。
    bridge._usage_service().scan()  # 已知会话目录自动补扫，耗时操作留在后台。
    # 当前 Windows WinForms 后端也读取 start(icon)；macOS 应用包图标由打包配置提供。
    webview.start(icon=asset_path("skillradar.ico" if sys.platform == "win32" else "skillradar.png"))
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
    processing = getattr(runtime, "processing", None)
    if processing:
        processing.stop()
        processing.join()
    usage = getattr(runtime, "usage", None)
    if usage is not None:
        usage.stop()
    watcher = getattr(runtime, "watcher", None)
    if watcher:
        _stop_watcher(watcher)
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
