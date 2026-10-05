# tray/watchers.py — 平台目录监听层（纯 ctypes，规格 §0/§3）
# 三平台：Windows ReadDirectoryChangesW / macOS FSEvents / Linux inotify（接口位）。
# 回调契约：callback(abs_path) —— 只做路径入队，绝不在回调里扫描（规格 §5）。
import ctypes
import os
import sys
import threading

try:
    import ctypes.wintypes   # noqa: F401  仅 Windows 分支消费（RDCW 的 DWORD/句柄类型）
except ImportError:          # 非 Windows 平台无此模块，占位类不触发加载
    pass

FILE_LIST_DIRECTORY = 0x0001


class WatchUnavailable(Exception):
    pass


class ReadDirectoryChangesWatcher:
    """Windows：一个线程 + 每根一个目录句柄，GetQueuedCompletionStatus
    或阻塞 ReadDirectoryChangesW。实现用最朴素的「每根一个线程」模型——
    技能根数量个位数，简单优于 IOCP 复杂度。"""

    def __init__(self, roots, callback):
        self.roots = [str(r) for r in roots]
        self.callback = callback
        self._threads = []
        self._handles = []
        self._identities = {}   # handle → (注册路径, 打开前的目录身份)。
        self._reopen = set()
        self._lock = threading.Lock()   # 保护 _handles（worker append vs stop 遍历）
        self._stopped = threading.Event()
        # RDCW 常量（winternl.h / winbase.h）
        self.FILE_NOTIFY_CHANGE_FILE_NAME = 0x1   # 创建/删除/改名
        self.FILE_NOTIFY_CHANGE_DIR_NAME = 0x2
        self.FILE_NOTIFY_CHANGE_LAST_WRITE = 0x10
        self.FILE_NOTIFY_CHANGE_SIZE = 0x8
        self.BUFFER = 64 * 1024

    def _watch_one(self, root):
        k32 = ctypes.windll.kernel32
        wt = ctypes.wintypes
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                   ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
        k32.CreateFileW.restype = wt.HANDLE
        k32.ReadDirectoryChangesW.argtypes = [wt.HANDLE, ctypes.c_void_p,
            wt.DWORD, wt.BOOL, wt.DWORD, ctypes.POINTER(wt.DWORD),
            ctypes.c_void_p, ctypes.c_void_p]
        k32.ReadDirectoryChangesW.restype = wt.BOOL
        k32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.c_void_p]
        k32.CancelIoEx.restype = wt.BOOL
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.CloseHandle.restype = wt.BOOL
        missing = not os.path.isdir(root)
        while not self._stopped.is_set():
            identity = self._directory_identity(root)
            h = k32.CreateFileW(root, FILE_LIST_DIRECTORY, 0x7, None, 3,
                               0x02000000, None)
            if h in (-1, ctypes.c_void_p(-1).value, None, 0):
                missing = True
                self._stopped.wait(0.2)
                continue
            with self._lock:
                if self._stopped.is_set():
                    k32.CloseHandle(h)
                    return
                self._handles.append(h)
                self._identities[h] = (root, identity)
            if missing:
                self.callback(root)  # 重现的根可能已包含完整技能，安排补扫。
            buf = ctypes.create_string_buffer(self.BUFFER)
            nbytes = wt.DWORD()
            try:
                while not self._stopped.is_set():
                    with self._lock:
                        if h in self._reopen:
                            break
                    ok = k32.ReadDirectoryChangesW(h, buf, self.BUFFER, True,
                        self.FILE_NOTIFY_CHANGE_FILE_NAME | self.FILE_NOTIFY_CHANGE_DIR_NAME
                        | self.FILE_NOTIFY_CHANGE_LAST_WRITE | self.FILE_NOTIFY_CHANGE_SIZE,
                        ctypes.byref(nbytes), None, None)
                    if not ok:
                        break
                    if nbytes.value == 0:
                        self.callback(root)  # 溢出不能永久停止监听：全根补扫。
                        continue
                    off = 0
                    raw = buf.raw[:nbytes.value]
                    while off + 12 <= len(raw):
                        nxt, action, plen = (int.from_bytes(raw[off:off + 4], "little"),
                            int.from_bytes(raw[off + 4:off + 8], "little"),
                            int.from_bytes(raw[off + 8:off + 12], "little"))
                        if plen and off + 12 + plen <= len(raw) and action != 4:
                            name = raw[off + 12:off + 12 + plen].decode("utf-16-le", errors="ignore")
                            self.callback(os.path.join(root, name))
                        if nxt == 0 or nxt < 12:
                            break
                        off += nxt
            finally:
                with self._lock:
                    if h in self._handles:
                        self._handles.remove(h)
                        self._identities.pop(h, None)
                        self._reopen.discard(h)
                        k32.CloseHandle(h)
            missing = True

    def start(self):
        monitor = threading.Thread(target=self._monitor_roots, daemon=True)
        self._threads.append(monitor)
        monitor.start()
        for r in self.roots:
            t = threading.Thread(target=self._watch_one, args=(r,), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stopped.set()
        # 观察线程重复取消至 worker 退出；所有句柄只由 worker 关闭。
        # 单次 CancelIoEx 若先于下一次同步 RDCW，会丢掉取消信号，不能只发一次。

    @staticmethod
    def _directory_identity(root):
        try:
            info = os.stat(root)
            return info.st_dev, info.st_ino
        except OSError:
            return None

    def _monitor_roots(self):
        """目录句柄会跟随移走的目录；路径身份变化时取消 I/O 并重新打开。"""
        import time
        k32 = ctypes.windll.kernel32
        k32.CancelIoEx.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p]
        k32.CancelIoEx.restype = ctypes.wintypes.BOOL
        while True:
            with self._lock:
                if self._stopped.is_set() and not self._handles:
                    return
                for h, (root, original) in list(self._identities.items()):
                    if self._stopped.is_set() or self._directory_identity(root) != original:
                        self._reopen.add(h)
                        # 持锁时 worker 无法关闭并复用 h，取消不会碰到下一代句柄。
                        k32.CancelIoEx(h, None)
            time.sleep(0.1)


class FSEventsWatcher:
    """macOS：FSEvents（CFRunLoop 线程模型，规格 §3 原文）。回调线程 = 本线程
    的 runloop；stop() 用 CFRunLoopStop。ctypes 装配在 mac 上验收（任务 10）。"""

    def __init__(self, roots, callback):
        self.roots = [str(r) for r in roots]
        self.callback = callback
        self._loop = None
        # 升级 T5：start() 的调用线程即承载 runloop 的监听线程（app 层把
        # start 放线程跑），登记进 _threads 后 shutdown 的 join 才不静默
        # no-op——与 RDCW watcher 的 _threads 停机口径一致。
        self._threads = []
        self._stopped = threading.Event()
        self._cf = None
        self._lock = threading.Lock()

    def _c_callback(self, stream, client_info, count, paths, event_flags, event_ids):
        arr = ctypes.cast(paths, ctypes.POINTER(ctypes.c_void_p))
        for i in range(count):
            p = ctypes.c_char_p(arr[i]).value
            if p:
                path = p.decode("utf-8", errors="ignore")
                flags = event_flags[i] if event_flags else 0
                if flags & (0x01 | 0x02 | 0x04 | 0x20):
                    # MustScanSubDirs/UserDropped/KernelDropped/RootChanged：
                    # 只排队注册根，实际补扫仍在 daemon 工作线程。
                    for root in self.roots:
                        self.callback(root)
                    continue
                affected = False
                for root in self.roots:
                    r = root.replace("\\", "/").rstrip("/")
                    p_norm = path.replace("\\", "/").rstrip("/")
                    if r.startswith(p_norm + "/"):
                        self.callback(root)  # 根的祖先被原子安装/重新创建。
                        affected = True
                    elif p_norm == r or p_norm.startswith(r + "/"):
                        affected = True
                if affected:
                    self.callback(path)

    def start(self):
        if self._stopped.is_set() or not self.roots:
            return
        self._threads.append(threading.current_thread())   # 升级 T5：见 __init__
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/"
                         "CoreFoundation")
        fse = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/"
                          "CoreServices")
        CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_size_t, ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint64))
        class Context(ctypes.Structure):
            _fields_ = [("version", ctypes.c_long), ("info", ctypes.c_void_p),
                        ("retain", ctypes.c_void_p), ("release", ctypes.c_void_p),
                        ("copyDescription", ctypes.c_void_p)]
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                    ctypes.c_long, ctypes.c_void_p]
        cf.CFArrayCreate.restype = ctypes.c_void_p
        cf.CFRunLoopGetCurrent.argtypes = []
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
        cf.CFRunLoopRunInMode.restype = ctypes.c_int32
        cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
        cf.CFRunLoopStop.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        fse.FSEventStreamCreate.argtypes = [ctypes.c_void_p, CB, ctypes.POINTER(Context),
            ctypes.c_void_p, ctypes.c_uint64, ctypes.c_double, ctypes.c_uint32]
        fse.FSEventStreamCreate.restype = ctypes.c_void_p
        fse.FSEventStreamScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        fse.FSEventStreamScheduleWithRunLoop.restype = None
        fse.FSEventStreamStart.argtypes = [ctypes.c_void_p]
        fse.FSEventStreamStart.restype = ctypes.c_bool
        for name in ("FSEventStreamStop", "FSEventStreamInvalidate", "FSEventStreamRelease"):
            fn = getattr(fse, name)
            fn.argtypes = [ctypes.c_void_p]
            fn.restype = None
        cb = CB(self._c_callback)
        ctx = Context()
        paths = []
        for root in self.roots:
            # 监听最近存在祖先，未来才创建的注册根仍能收到事件。
            p = os.path.abspath(root)
            while not os.path.isdir(p) and os.path.dirname(p) != p:
                p = os.path.dirname(p)
            paths.append(p)
        strings, array, stream = [], None, None
        try:
            for p in dict.fromkeys(paths):
                value = cf.CFStringCreateWithCString(None, p.encode("utf-8"), 0x08000100)
                if not value:
                    raise WatchUnavailable("CFString creation failed")
                strings.append(value)
            refs = (ctypes.c_void_p * len(strings))(*strings)
            array = cf.CFArrayCreate(None, refs, len(strings), None)
            if not array:
                raise WatchUnavailable("CFArray creation failed")
            stream = fse.FSEventStreamCreate(None, cb, ctypes.byref(ctx), array,
                0xFFFFFFFFFFFFFFFF, 3.0, 0x04 | 0x10)  # WatchRoot | FileEvents
            if not stream:
                raise WatchUnavailable("FSEvents stream creation failed")
            with self._lock:
                self._cf = cf
                self._loop = cf.CFRunLoopGetCurrent()
                mode = _default_runloop_mode(cf)
                fse.FSEventStreamScheduleWithRunLoop(stream, self._loop, mode)
                if self._stopped.is_set():
                    return
                if not fse.FSEventStreamStart(stream):
                    raise WatchUnavailable("FSEvents stream start failed")
            while not self._stopped.is_set():
                # Stop 恰落在 Run 之前也只延迟 0.25s；避免 CFRunLoopStop 的
                # 单次信号被下一轮无限 Run 消费后永久滞留线程。
                if cf.CFRunLoopRunInMode(mode, 0.25, True) == 1:
                    break  # kCFRunLoopRunFinished：事件源已经移除。
        finally:
            if stream:
                fse.FSEventStreamStop(stream)
                fse.FSEventStreamInvalidate(stream)
                fse.FSEventStreamRelease(stream)
            if array:
                cf.CFRelease(array)
            for value in strings:
                cf.CFRelease(value)
            with self._lock:
                self._loop = None

    def stop(self):
        self._stopped.set()
        with self._lock:
            if self._loop is not None:
                self._cf.CFRunLoopStop(self._loop)


def _default_runloop_mode(cf):
    return ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")


class INotifyWatcher:
    """Linux 接口位（规格 §4：不装，留 inotify ctypes 接口位）。"""

    def __init__(self, roots, callback):
        raise WatchUnavailable("Linux inotify watcher is roadmap (spec §4)")


def pick_backend():
    if sys.platform == "win32":
        return ReadDirectoryChangesWatcher
    if sys.platform == "darwin":
        return FSEventsWatcher
    if sys.platform == "linux":
        # 规格契约：linux 是接口位，pick_backend 本身即抛（INotifyWatcher 桩类
        # 构造同样抛，双保险）。测试 monkeypatch 平台后直接调 pick_backend。
        raise WatchUnavailable("Linux inotify watcher is roadmap (spec §4)")
    raise WatchUnavailable(f"unsupported platform: {sys.platform}")
