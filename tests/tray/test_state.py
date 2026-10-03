# tests/tray/test_state.py — 任务 1：托盘状态机 + 环形事件日志 + 今日计数
from datetime import datetime, timedelta
from tray.state import TrayState, MAX_EVENTS, today_key


def _st():
    return TrayState()


def test_initial_state():
    st = _st()
    assert st.guard == "running"          # running / paused / alert / quarantine
    assert st.watched_roots == 0
    assert st.events == []
    assert st.today == {today_key(): {"new": 0, "drift": 0, "block": 0}}


def test_event_log_is_ring_buffer():
    st = _st()
    for i in range(MAX_EVENTS + 50):
        st.add_event("scan", f"evt-{i}")
    assert len(st.events) == MAX_EVENTS
    assert st.events[0][1] == f"evt-{MAX_EVENTS + 49}"      # 最新在前
    assert st.events[-1][1] == "evt-50"                     # 最旧保留的是第 50 条


def test_event_fields_sanitized():
    st = _st()
    st.add_event("scan", "bad\u200btext\ufeff")
    assert "\u200b" not in st.events[0][1] and "\ufeff" not in st.events[0][1]
    assert st.events[0][0] == "scan"


def test_set_guard_transitions():
    st = _st()
    seen = []
    st.on_guard_change = seen.append                    # 壳层钩子被通知（托盘变色）
    st.set_guard("alert")
    assert st.guard == "alert" and seen == ["alert"]
    st.set_guard("paused")
    assert st.guard == "paused" and seen[-1] == "paused"
    st.set_guard("running")   # alert → 恢复
    assert st.guard == "running" and seen[-1] == "running"


def test_bump_today_rolls_over_date():
    st = _st()
    st.bump("new"); st.bump("new"); st.bump("drift")
    assert st.today[today_key()] == {"new": 2, "drift": 1, "block": 0}
    # 手动注入过期日期 → bump 时清理
    st.today["2020-01-01"] = {"new": 99, "drift": 0, "block": 0}
    st.bump("block")
    assert "2020-01-01" not in st.today
    assert st.today[today_key()]["block"] == 1


def test_snapshot_for_ui_is_plain_json():
    st = _st()
    st.add_event("scan", "hello")
    st.watched_roots = 3
    snap = st.snapshot()
    import json
    assert set(snap) >= {"guard", "watched_roots", "events", "today"}
    json.dumps(snap)   # 可序列化，不抛
    assert snap["events"][0]["kind"] == "scan"


def test_snapshot_skills_record_and_deep_copy():
    # 终审 I-3：record_skill 是 Security 屏数据源的写入口（与 add_event/bump
    # 同锁）；snapshot 对 skills/today 深拷一层——UI 改快照不别名回写内部态
    # （today 同口径修账本 T1-1）。
    st = _st()
    assert st.skills == {}
    st.record_skill("C:/pool/a", "a", 12, "scanned")
    st.record_skill("C:/pool/a", "a", 18, "drifted")   # 同路径覆盖旧值
    st.bump("new")
    snap = st.snapshot()
    assert snap["skills"]["C:/pool/a"] == {"name": "a", "score": 18,
                                           "status": "drifted"}
    snap["skills"]["C:/pool/a"]["score"] = 999
    snap["today"][today_key()]["new"] = 999
    assert st.skills["C:/pool/a"]["score"] == 18
    assert st.today[today_key()]["new"] == 1
