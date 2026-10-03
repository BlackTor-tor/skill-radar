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
