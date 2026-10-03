# tests/tray/test_watchers.py — 任务 4：平台监听层（Windows 真跑；mac 打桩分支选择）
import os
import sys
import threading
import time

import pytest

from tray import watchers


def test_pick_backend_windows():
    if sys.platform != "win32":
        pytest.skip("Windows-only")
    assert watchers.pick_backend() is watchers.ReadDirectoryChangesWatcher


def test_pick_backend_macos_stubbed(monkeypatch):
    monkeypatch.setattr(watchers.sys, "platform", "darwin")
    assert watchers.pick_backend() is watchers.FSEventsWatcher


def test_pick_backend_linux_is_interface_stub(monkeypatch):
    monkeypatch.setattr(watchers.sys, "platform", "linux")
    with pytest.raises(watchers.WatchUnavailable):
        watchers.pick_backend()


def test_fsevents_watcher_registers_runloop_thread(monkeypatch):
    # 升级 T5：FSEventsWatcher 的 runloop 线程（= start() 的调用线程，app 层
    # 把 start 放线程跑）须登记进 _threads，mac shutdown 的 join 才不静默
    # no-op——与 RDCW watcher 的 _threads 停机口径一致。不调 start（非 mac 上
    # CDLL 会炸）：断字段存在且初始为空、shutdown 的
    # getattr(watcher, "_threads", None) or [] 消费模式可用；真跑留 macOS 清单。
    monkeypatch.setattr(watchers.sys, "platform", "darwin")
    w = watchers.FSEventsWatcher(["/tmp/pool"], callback=lambda p: None)
    assert w._threads == []
    assert (getattr(w, "_threads", None) or []) == []


@pytest.mark.skipif(sys.platform != "win32", reason="RDCW 真机测试")
def test_rdcw_reports_created_and_modified(tmp_path):
    events = []
    w = watchers.ReadDirectoryChangesWatcher([str(tmp_path)],
                                             callback=events.append)
    t = threading.Thread(target=w.start, daemon=True)
    t.start()
    time.sleep(1.0)                       # 句柄就绪
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    time.sleep(0.5)
    (tmp_path / "a.txt").write_text("2", encoding="utf-8")
    time.sleep(1.5)
    w.stop()
    t.join(timeout=5)
    assert any("a.txt" in p for p in events)   # 至少收到一次变更路径


@pytest.mark.skipif(sys.platform != "win32", reason="RDCW 真机测试")
def test_rdcw_stop_is_clean(tmp_path):
    w = watchers.ReadDirectoryChangesWatcher([str(tmp_path)], callback=lambda p: None)
    t = threading.Thread(target=w.start, daemon=True)
    t.start()
    time.sleep(1.0)
    w.stop()
    t.join(timeout=5)
    assert not t.is_alive()
