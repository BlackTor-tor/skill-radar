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


@pytest.mark.skipif(sys.platform != "win32", reason="RDCW 真机测试")
def test_rdcw_reports_atomic_directory_install(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    source = tmp_path / "ready"
    source.mkdir()
    (source / "SKILL.md").write_text("# prepared", encoding="utf-8")
    events = []
    watcher = watchers.ReadDirectoryChangesWatcher([str(pool)], events.append)
    watcher.start()
    time.sleep(0.3)
    try:
        source.rename(pool / "installed")
        deadline = time.time() + 2
        while not events and time.time() < deadline:
            time.sleep(0.05)
        assert any(p == str(pool / "installed") for p in events)
    finally:
        watcher.stop()
        for thread in watcher._threads:
            thread.join(timeout=2)


@pytest.mark.skipif(sys.platform != "win32", reason="RDCW 真机测试")
def test_rdcw_waits_for_missing_root(tmp_path):
    root = tmp_path / "future"
    events = []
    watcher = watchers.ReadDirectoryChangesWatcher([str(root)], events.append)
    watcher.start()
    time.sleep(0.2)
    try:
        root.mkdir()
        skill = root / "arrived"
        skill.mkdir()
        (skill / "SKILL.md").write_text("# safe", encoding="utf-8")
        deadline = time.time() + 3
        while not events and time.time() < deadline:
            time.sleep(0.05)
        assert events
    finally:
        watcher.stop()
        for thread in watcher._threads:
            thread.join(timeout=2)


def test_fsevents_uses_cf_objects_and_releases_stream(monkeypatch):
    import ctypes
    calls = []

    class Fn:
        def __init__(self, name, result=None):
            self.name, self.result = name, result
        def __call__(self, *args):
            calls.append((self.name, args))
            return self.result

    cf = type("CF", (), {})()
    fse = type("FSE", (), {})()
    # 指针显著大于 32 位：模拟 CF ABI，不依赖 macOS 框架。
    for name, value in (("CFStringCreateWithCString", 0x100000001),
                        ("CFArrayCreate", 0x100000002),
                        ("CFRunLoopGetCurrent", 0x100000003),
                        ("CFRunLoopRunInMode", 1), ("CFRunLoopStop", None),
                        ("CFRelease", None)):
        setattr(cf, name, Fn(name, value))
    for name, value in (("FSEventStreamCreate", 0x100000004),
                        ("FSEventStreamStart", True),
                        ("FSEventStreamScheduleWithRunLoop", None),
                        ("FSEventStreamStop", None),
                        ("FSEventStreamInvalidate", None),
                        ("FSEventStreamRelease", None)):
        setattr(fse, name, Fn(name, value))
    monkeypatch.setattr(watchers.ctypes, "CDLL", lambda path: cf if "CoreFoundation" in path else fse)
    monkeypatch.setattr(watchers, "_default_runloop_mode", lambda library: ctypes.c_void_p(0x100000005), raising=False)
    watcher = watchers.FSEventsWatcher(["/tmp/pool"], lambda p: None)
    watcher.start()
    create = next(args for name, args in calls if name == "FSEventStreamCreate")
    assert create[3] == 0x100000002
    assert create[4] == 0xFFFFFFFFFFFFFFFF   # since-now，不是根数量。
    assert create[6] & 0x10                # FileEvents
    assert cf.CFRunLoopGetCurrent.restype is ctypes.c_void_p
    assert fse.FSEventStreamCreate.restype is ctypes.c_void_p
    names = [name for name, _ in calls]
    assert "CFRunLoopRunInMode" in names
    assert names[names.index("FSEventStreamStop"):names.index("FSEventStreamRelease") + 1] == [
        "FSEventStreamStop", "FSEventStreamInvalidate", "FSEventStreamRelease"]
    assert watcher._loop is None


def test_fsevents_stop_before_start_does_not_run_loop(monkeypatch):
    watcher = watchers.FSEventsWatcher(["/tmp/pool"], lambda p: None)
    watcher.stop()
    monkeypatch.setattr(watchers.ctypes, "CDLL", lambda path: pytest.fail("stopped watcher loads framework"))
    watcher.start()


def test_fsevents_ancestor_and_overflow_queue_registered_roots():
    import ctypes
    events = []
    watcher = watchers.FSEventsWatcher(["/tmp/newparent/skills"], events.append)
    values = [ctypes.c_char_p(b"/tmp/newparent")]
    pointers = (ctypes.c_void_p * 1)(ctypes.cast(values[0], ctypes.c_void_p).value)
    flags = (ctypes.c_uint32 * 1)(0)
    watcher._c_callback(None, None, 1, pointers, flags, None)
    assert "/tmp/newparent/skills" in events
    events.clear()
    values[0] = ctypes.c_char_p(b"/unrelated/dropped")
    pointers[0] = ctypes.cast(values[0], ctypes.c_void_p).value
    flags[0] = 0x01  # MustScanSubDirs
    watcher._c_callback(None, None, 1, pointers, flags, None)
    assert events == ["/tmp/newparent/skills"]


@pytest.mark.skipif(sys.platform != "win32", reason="RDCW 真机测试")
def test_rdcw_reopens_replaced_root_and_stops_cleanly(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    events = []
    watcher = watchers.ReadDirectoryChangesWatcher([str(pool)], events.append)
    watcher.start()
    time.sleep(0.3)
    try:
        pool.rename(tmp_path / "old-pool")
        pool.mkdir()
        # 等注册路径重连；旧句柄仍有效但跟随旧目录，不能靠 RDCW 自行返回。
        time.sleep(0.6)
        events.clear()
        skill = pool / "new-skill"
        skill.mkdir()
        marker = skill / "SKILL.md"
        marker.write_text("# safe", encoding="utf-8")
        deadline = time.time() + 2
        while str(marker) not in events and time.time() < deadline:
            time.sleep(0.03)
        assert str(marker) in events
    finally:
        watcher.stop()
        for thread in watcher._threads:
            thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in watcher._threads)
    assert not watcher._handles
