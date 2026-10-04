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
        self._lock = threading.Lock()   # 保护 _handles（worker append vs stop 遍历）
        self._stopped = threading.Event()
        # RDCW 常量（winternl.h / winbase.h）
        self.FILE_NOTIFY_CHANGE_FILE_NAME = 0x1   # 创建/删除/改名
        self.FILE_NOTIFY_CHANGE_LAST_WRITE = 0x10
        self.FILE_NOTIFY_CHANGE_SIZE = 0x8
        self.BUFFER = 64 * 1024

    def _watch_one(self, root):
        k32 = ctypes.windll.kernel32
        h = k32.CreateFileW(root, FILE_LIST_DIRECTORY,
                            0x7,  # FILE_SHARE_READ|WRITE|DELETE
                            None, 3,  # OPEN_EXISTING
                            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS（目录必需）
                            None)
        # 失败哨兵：k32 经 windll 取到时 restype 为默认 c_int，64 位句柄被
        # 截断后 INVALID_HANDLE_VALUE（0xFFFF...F）落成 -1——与整型 -1 比较
        # 才能命中（同 0/NULL 一并覆盖；不设 restype=c_void_p 以免牵动后续
        # 句柄传参类型，见 stop() 的 CloseHandle 口径）。
        if h in (-1, None, 0):
            return
        with self._lock:
            self._handles.append(h)
        buf = ctypes.create_string_buffer(self.BUFFER)
        nbytes = ctypes.wintypes.DWORD()   # RDCW 写回的实际字节数
        while not self._stopped.is_set():
            ok = k32.ReadDirectoryChangesW(
                h, buf, self.BUFFER, True,   # watch subtree
                self.FILE_NOTIFY_CHANGE_FILE_NAME | self.FILE_NOTIFY_CHANGE_LAST_WRITE
                | self.FILE_NOTIFY_CHANGE_SIZE,
                ctypes.byref(nbytes), None, None)
            if not ok or nbytes.value == 0:
                break
            off = 0
            raw = buf.raw[:nbytes.value]
            while off < len(raw):
                nxt, action, plen = (int.from_bytes(raw[off:off + 4], "little"),
                                     int.from_bytes(raw[off + 4:off + 8], "little"),
                                     int.from_bytes(raw[off + 8:off + 12], "little"))
                if plen:
                    name = raw[off + 12: off + 12 + plen].decode("utf-16-le",
                                                                 errors="ignore")
                    # 事件合并守则：改名成对到达（旧名+新名），一律投新名
                    if action != 4:   # 4 = FILE_ACTION_RENAMED_OLD_NAME
                        self.callback(os.path.join(root, name))
                if nxt == 0:
                    break
                off += nxt
        # 句柄关闭统一由 stop() 独占负责（见 stop 注释）；worker 在任何退出
        # 路径（自然退出或被取消）都只返回、不关句柄，杜绝双关同一句柄值
        # 可能命中间隔复用新句柄的竞态。未调 stop() 时句柄随进程退出回收。

    def start(self):
        for r in self.roots:
            t = threading.Thread(target=self._watch_one, args=(r,), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stopped.set()
        k32 = ctypes.windll.kernel32
        # 句柄生命周期：stop() 独占关闭。worker 不关（哪怕自然退出路径），
        # 因此这里对每个句柄恰好关一次，不存在双关。
        with self._lock:
            handles = list(self._handles)
            self._handles.clear()
        for h in handles:   # 先取消未决 I/O，再关句柄
            try:
                # 直接 CloseHandle 会挂死：另一线程仍阻塞在该句柄的同步
                # ReadDirectoryChangesW 上，关闭线程会等它返回（探针实证）。
                # CancelIoEx(h, None) 取消该句柄上本进程的全部未决 I/O，
                # 被阻塞的 RDCW 立即以 FALSE/ERROR_OPERATION_ABORTED 返回，
                # worker 循环的 `if not ok` 分支随之退出。
                k32.CancelIoEx(h, None)
            except Exception:
                pass
            try:
                k32.CloseHandle(h)
            except Exception:
                pass


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

    def _c_callback(self, stream, client_info, count, paths, event_flags, event_ids):
        arr = ctypes.cast(paths, ctypes.POINTER(ctypes.c_void_p))
        for i in range(count):
            p = ctypes.c_char_p(arr[i]).value
            if p:
                self.callback(p.decode("utf-8", errors="ignore"))

    def start(self):
        self._threads.append(threading.current_thread())   # 升级 T5：见 __init__
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/"
                         "CoreFoundation")
        fse = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/"
                          "CoreServices")
        CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p),
                              ctypes.c_void_p, ctypes.c_void_p)
        cb = CB(self._c_callback)
        ctx = ctypes.c_void_p(0)
        stream = fse.FSEventStreamCreate(
            ctx, cb, ctypes.byref(ctypes.c_void_p(0)),
            (ctypes.c_void_p * len(self.roots))(
                *[ctypes.c_char_p(r.encode()) for r in self.roots]),
            len(self.roots), 3.0,   # latency 3s：FSEvents 侧原生 debounce
            0x02 | 0x10)   # kFSEventStreamCreateFlagFileEvents | WatchRoot
        self._loop = cf.CFRunLoopGetCurrent()
        fse.FSEventStreamScheduleWithRunLoop(stream, self._loop, ctypes.c_char_p(b"kCFRunLoopDefaultMode"))
        fse.FSEventStreamStart(stream)
        cf.CFRunLoopRun()   # 阻塞至 CFRunLoopStop

    def stop(self):
        if self._loop is not None:
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/"
                             "CoreFoundation")
            cf.CFRunLoopStop(self._loop)


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
