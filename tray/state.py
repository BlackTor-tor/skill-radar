# tray/state.py — 托盘状态机 + 事件环形日志 + 今日计数（纯标准库，UI/托盘的唯一直接数据源）
from datetime import datetime

MAX_EVENTS = 200


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
    """守护运行时状态（内存态）。线程安全策略：CPython GIL 下对
    list/dict 的单操作原子，add_event/bump/snapshot 的读改写用一把锁
    保护（UI 每 2s 轮询 snapshot 与扫描线程并发写）。"""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self.guard = "running"            # running|paused|alert|quarantine
        self.watched_roots = 0
        self.events = []                  # [(kind, text, ts)] 最新在前，环形上限
        self.today = {today_key(): {"new": 0, "drift": 0, "block": 0}}
        self.skills = {}                  # skill_path → {name, score, status}
        self.paused = False
        self.on_guard_change = lambda g: None   # 壳层钩子（托盘变色）；默认无操作

    def add_event(self, kind, text):
        with self._lock:
            self.events.insert(0, (kind, _sanitize(text),
                                   datetime.now().strftime("%H:%M:%S")))
            del self.events[MAX_EVENTS:]

    def set_guard(self, g):
        assert g in ("running", "paused", "alert", "quarantine")
        self.guard = g
        self.paused = (g == "paused")
        self.on_guard_change(g)   # 锁外调用：壳层回调不得再进 state 的锁（防死锁）

    def bump(self, key):
        k = today_key()
        with self._lock:
            self.today = {d: v for d, v in self.today.items() if d == k}
            self.today.setdefault(k, {"new": 0, "drift": 0, "block": 0})[key] += 1

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
