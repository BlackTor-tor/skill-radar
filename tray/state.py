# tray/state.py — 托盘状态机 + 事件环形日志 + 今日计数（纯标准库，UI/托盘的唯一直接数据源）
import json
import os
from datetime import datetime

MAX_EVENTS = 200
PERSIST_EVENTS = 50   # tray_state.json 只存最近 50 条事件（内存仍保 200 条环形）
STATE_VERSION = 1


def _sanitize(s):
    # 引 skill_guard 的输出清洗（规格 §5：Toast 文案同口径）；独立小写副本
    # 避免环依赖方向的例外——tray 允许依赖 skill_guard，但 state 是最底层，
    # 用本地实现保持「state 不 import 任何项目模块」的依赖方向
    ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"
    return "".join("?" if ch == "\ufffd" else ch
                   for ch in str(s)
                   if ch not in ZERO_WIDTH and (ch >= " " or ch == "\t"))


def today_key(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m-%d")


class TrayState:
    """守护运行时状态（默认纯内存）。线程安全策略：CPython GIL 下对
    list/dict 的单操作原子，add_event/bump/snapshot 的读改写用一把锁
    保护（UI 每 2s 轮询 snapshot 与扫描线程并发写）。

    path（终审 M-3）：可选的 tray_state.json 持久化——__init__ 时若文件存在
    则恢复 today/events/guard；bump/set_guard/add_event 之后原子落盘
    （tmp + os.replace）。坏 JSON/缺文件/写失败一律静默：状态文件只是
    「托盘重启后概览不断档」的便利层，损坏不得阻止守护启动。传 None（默认）
    保持纯内存，既有调用与测试零改动。"""

    def __init__(self, path=None):
        import threading
        self._lock = threading.Lock()
        self._path = path
        self.guard = "running"            # running|paused|alert|quarantine
        self.watched_roots = 0
        self.events = []                  # [(kind, text, ts)] 最新在前，环形上限
        self.today = {today_key(): {"new": 0, "drift": 0, "block": 0}}
        self.skills = {}                  # skill_path → {name, score, status}
        self.paused = False
        self.on_guard_change = lambda g: None   # 壳层钩子（托盘变色）；默认无操作
        if path:
            self._load()

    def _load(self):
        """从 tray_state.json 恢复 today/events/guard；坏 JSON/缺文件/结构
        不合预期静默忽略，保留内存默认值。"""
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        today = data.get("today")
        if isinstance(today, dict) and today:
            self.today = today
        events = data.get("events")
        if isinstance(events, list):
            self.events = [(e.get("kind"), e.get("text"), e.get("ts"))
                           for e in events if isinstance(e, dict)]
        guard = data.get("guard")
        if guard in ("running", "paused", "alert", "quarantine"):
            self.guard = guard
            self.paused = (guard == "paused")

    def _flush(self):
        """原子落盘：tmp + os.replace，结构 {"version":1,"today":...,
        "events":[...],"guard":...}，events 只存最近 50 条。调用方已持
        _lock 或写标量自洽（set_guard 走 GIL 单操作）；OSError 静默。"""
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            payload = {"version": STATE_VERSION,
                       "today": self.today,
                       "events": [{"kind": k, "text": t, "ts": ts}
                                  for k, t, ts in self.events[:PERSIST_EVENTS]],
                       "guard": self.guard}
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def add_event(self, kind, text):
        with self._lock:
            self.events.insert(0, (kind, _sanitize(text),
                                   datetime.now().strftime("%H:%M:%S")))
            del self.events[MAX_EVENTS:]
            self._flush()

    def set_guard(self, g):
        assert g in ("running", "paused", "alert", "quarantine")
        self.guard = g
        self.paused = (g == "paused")
        self.on_guard_change(g)   # 锁外调用：壳层回调不得再进 state 的锁（防死锁）
        self._flush()

    def bump(self, key):
        k = today_key()
        with self._lock:
            self.today = {d: v for d, v in self.today.items() if d == k}
            self.today.setdefault(k, {"new": 0, "drift": 0, "block": 0})[key] += 1
            self._flush()

    def record_skill(self, skill_path, name, score, status):
        """终审 I-3：Security 屏数据源。扫描线程每轮把结果写入（覆盖同路径
        旧值）；与 add_event/bump 同锁，snapshot 消费端拿一致视图。"""
        with self._lock:
            self.skills[skill_path] = {"name": name, "score": score,
                                       "status": status}

    def snapshot(self):
        with self._lock:
            # 深拷一层（终审 I-3 + 账本 T1-1）：skills/today 的内层 dict 一并
            # 复制，UI 侧改动快照不会别名回写内部态
            return {"guard": self.guard,
                    "watched_roots": self.watched_roots,
                    "paused": self.paused,
                    "events": [{"kind": k, "text": t, "ts": ts}
                               for k, t, ts in self.events],
                    "today": {d: dict(v) for d, v in self.today.items()},
                    "skills": {p: dict(v) for p, v in self.skills.items()}}
