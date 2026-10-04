# tray/daemon.py — 守护核心：脏根 debounce、变更技能定位、增量扫描、结果分发
# frozen 安全注记（D-1 轮审查固化）：本模块是**被 import 的库模块**，__file__
# 由 import 机制解析为 _MEIPASS/tray/daemon.py（frozen）或源码路径（dev），
# 二层 dirname 在两种形态下都得到正确的资源基目录——与入口脚本 app.py 不同
# （入口 __file__ 在 frozen 下落 _MEIPASS 根，需 _res_base 的 _MEIPASS 分支）。
# 改动本模块资源定位前先回看该差异，勿套用入口脚本的修法。
import os
import threading
import time
from datetime import datetime
from dataclasses import asdict

import skill_guard as sg

DEBOUNCE_S = 3


class Daemon:
    """守护核心（纯标准库）。监听层把 (abs_path) 投给 mark_dirty；
    扫描线程（app 层起）循环 consume()：等 debounce 稳定 → 定位技能 →
    增量扫描 → 分发 NEW/DRIFT/BLOCK/ERROR + state 更新。

    mode: "warn" | "block"（config consent.add_block 派生，app 层传入）。
    on_block: 钩子（app 层接 alerts.quarantine_or_alert），默认只记事件。"""

    def __init__(self, roots, mode="warn", state=None, rules_text=None,
                 blocklist_text=None):
        from tray.state import TrayState
        self.roots = [os.path.normpath(r) for r in roots]
        self.mode = mode
        self.state = state or TrayState()
        self.state.watched_roots = len(self.roots)
        self._dirty = {}          # normpath → (稳定时间戳, last_event_ts)
        self._lock = threading.Lock()
        self.scan_lock = threading.RLock()   # 与 UI 接受漂移共享快照读改写锁。
        self._stop = threading.Event()
        self.consume_thread = None   # app 层把 consume 线程句柄回挂于此（join 用）
        self.on_block = lambda skill_path, rep: None
        self.on_alert = lambda skill_path, rep, status: None
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.rules_text = rules_text if rules_text is not None else \
            self._read(os.path.join(base, "rules", "defaults.yaml"))
        self.blocklist_text = blocklist_text if blocklist_text is not None else \
            self._read(os.path.join(base, "rules", "blocklist.yaml"))

    @staticmethod
    def _read(p):
        try:
            return open(p, encoding="utf-8").read()
        except OSError:
            return "[]" if p.endswith("blocklist.yaml") else ""

    # ---- 监听层接口（回调线程调用；只入队，规格 §5）----

    def mark_dirty(self, abs_path, rel_hint=""):
        p = os.path.normpath(abs_path)
        with self._lock:
            self._dirty[p] = time.time() + DEBOUNCE_S

    def dirty_roots(self):
        with self._lock:
            return set(self._dirty)

    def pending_since(self):
        with self._lock:
            return min(self._dirty.values()) if self._dirty else None

    # ---- 技能定位 ----

    def locate_changed_skill(self, path):
        """从变更文件路径向上找含 SKILL.md 的最近目录；找到且其父是注册 root
        之下才返回该技能目录，否则 None（root 之外的变更不管）。"""
        p = os.path.normpath(os.path.abspath(path))
        while True:
            parent = os.path.dirname(p)
            if sg._is_skill_dir(p):
                if any(self._under(p, r) for r in self.roots):
                    return p
                return None
            if parent == p:
                return None
            p = parent

    @staticmethod
    def _under(child, root):
        c = sg._canon_path(os.path.realpath(child))
        r = sg._canon_path(os.path.realpath(root))
        return c == r or c.startswith(r + "/")

    # ---- 增量扫描（扫描线程调用；异常全包，规格 §5）----

    def scan_changed_skill(self, skill_path):
        """单技能增量：run_engine → trust → 快照比对（audit_roots 的单技能切片）。
        返回 NEW|DRIFT|BLOCK|ERROR。**不在 BLOCK 分支落快照**：隔离钩子（任务 3
        的 quarantine_skill）可能把技能目录移走，事后基线一个已不存在的路径
        既无意义还会让下轮 audit 按快照语义残留幽灵条目；warn 模式的 CRITICAL
        不隔离、照常落基线（audit 增量闭环不因模式分叉）。"""
        try:
            with self.scan_lock:
                return self._scan_once(skill_path)
        except Exception as e:   # 扫描异常不崩守护（规格 §5）
            self.state.add_event("error", f"扫描异常 {skill_path}: {e}")
            return "ERROR"

    def _scan_once(self, skill_path):
        cfg = sg.load_config()
        rules = sg.parse_rules(self.rules_text)
        import hashlib
        body = sg._read_regular_file(os.path.join(skill_path, "SKILL.md"), sg.MAX_HASH_FILE_BYTES)
        trusted = sg._is_trusted(cfg, skill_path,
                                 hashlib.sha256(body).hexdigest() if body is not None else "")
        rep = sg.run_engine(skill_path, rules, self.blocklist_text)
        rep.findings = sg.apply_trust(rep.findings, trusted)
        rep.score = sg.score_findings(rep.findings)

        has_crit = any(f.severity == "CRITICAL" for f in rep.findings)
        if has_crit and self.mode == "block":
            self.state.bump("block")
            self.state.set_guard("alert")   # 成功隔离由 app 钩子升级为 quarantine。
            self.state.add_event("block", f"CRITICAL 拦截 {skill_path}")
            # 终审 I-3：BLOCK 分支也进 Security 屏数据（快照不落，state 直写）
            self._record_report(skill_path, rep, "blocked")
            self.on_block(skill_path, rep)
            return "BLOCK"

        snaps = sg.load_snapshots()
        old = snaps["skills"].get(skill_path)
        new_hashes = sg.snapshot_dir(skill_path, "x", rep.score)["hashes"]
        if old is None:
            status = "NEW"
        else:
            d = sg.diff_snapshot(old["hashes"], new_hashes)
            status = "DRIFT" if (d["added"] or d["removed"] or d["changed"]) \
                else "OK"
        entry = {"name": os.path.basename(skill_path),
                 "status": {"NEW": "baseline-unreviewed",
                            "DRIFT": "drifted"}.get(status, old["status"] if old else "scanned"),
                 "score": rep.score,
                 "scanned_at": datetime.now().isoformat(timespec="seconds"),
                 "hashes": new_hashes}
        # prev_hashes 语义与 audit_roots 一致：DRIFT 确认时留住被覆写的旧基线
        #（--show-diff 的 inspect 通道）；无变化轮次透传，防 drift 后一轮 OK 把它冲掉
        if status == "DRIFT" and old:
            # old.get("prev_hashes", old["hashes"]) 保最初基线：连续多轮漂移（用户
            # 改完又改、一直未 accept）时 diff 始终回溯到"自上次被审基线以来改了
            # 什么"。与 audit_roots 的累计未审漂移口径一致。
            entry["prev_hashes"] = old.get("prev_hashes", old["hashes"])
        elif status == "OK" and old and "prev_hashes" in old:
            # OK 轮次透传（对齐 audit_roots 902-907 行）：DRIFT 确认后内容不变的
            # 再扫描不透传的话，本轮覆写条目即把 prev_hashes 冲掉——状态仍透传
            # "drifted"、状态行仍指路 --show-diff，diff 却退化为空（--show-diff
            # 回落 s["hashes"] 自比；watch 模式一轮轮询即触发）。
            entry["prev_hashes"] = old["prev_hashes"]
        snaps["skills"][skill_path] = entry
        sg.save_snapshots(snaps)

        if status == "NEW":
            self.state.bump("new")
            self.state.add_event("new", f"新技能 {entry['name']}")
            self.state.set_guard("alert")
        elif status == "DRIFT":
            self.state.bump("drift")
            self.state.add_event("drift", f"内容漂移 {entry['name']}")
            self.state.set_guard("alert")
        # 终审 I-3：成功路径（NEW/DRIFT/OK）把 name/score/status 写进 Security
        # 屏数据源；status 用快照条目口径（NEW→baseline-unreviewed、DRIFT→drifted、
        # OK 透传旧值），UI 的建议列由 status+score 推导
        self._record_report(skill_path, rep, entry["status"])
        if status in ("NEW", "DRIFT") or has_crit:
            self.state.set_guard("alert")
            self.on_alert(skill_path, rep, status)
        return status

    def _record_report(self, skill_path, rep, status):
        """风险表与 CLI 报告共用安装建议，明细只传纯 JSON 数据。"""
        from skill_report import install_verdict
        advice, _, reason = install_verdict(rep, status)
        self.state.record_skill(skill_path, os.path.basename(skill_path),
                                rep.score, status,
                                sg._sanitize_json([asdict(f) for f in rep.findings]),
                                advice, sg._sanitize(reason))

    # ---- 扫描线程主循环（app 层起线程跑）----

    def consume(self):
        """循环：取出已过稳定期的脏技能 → 增量扫描。paused 时只清理不扫描。
        stop() 后返回。"""
        while not self._stop.is_set():
            now = time.time()
            due = []
            with self._lock:
                for p, stable_at in list(self._dirty.items()):
                    if stable_at <= now:
                        due.append(p)
                        del self._dirty[p]
            scanned = set()
            for p in due:
                if self.state.paused:
                    self.state.add_event("skip", f"守护暂停，跳过 {p}")
                    continue
                skill = self.locate_changed_skill(p)
                if skill:
                    if skill not in scanned:
                        scanned.add(skill)
                        self.scan_changed_skill(skill)
                elif os.path.isdir(p) and any(
                        sg._canon_path(p) == sg._canon_path(r) or self._under(p, r)
                        for r in self.roots):
                    # 原子目录安装、缺失根重现、RDCW 溢出可能只给目录事件。
                    # 目录事件需向下枚举技能，普通文件事件仍走单技能增量。
                    try:
                        for child in sg.iter_skill_dirs([p]):
                            if child not in scanned:
                                scanned.add(child)
                                self.scan_changed_skill(child)
                    except Exception as e:
                        self.state.add_event("error", f"目录补扫异常 {p}: {e}")
            self._stop.wait(0.5)

    def stop(self):
        self._stop.set()

    def join(self, timeout=5):
        """等扫描线程收尾（app 层 shutdown 调用；未回挂句柄或扫描线程自身
        调用时为 no-op）。stop() 后 consume 最迟 0.5s 内退出，timeout=5 足够。"""
        t = self.consume_thread
        if t is not None and t is not threading.current_thread() and t.is_alive():
            t.join(timeout=timeout)
