"""Collect agent session history in the background, without editing agent logs."""
import copy
import os
import threading
import time
from datetime import datetime

import skill_monitor as sm
from skill_inventory import latest_time, parse_time


COVERAGE = {
    "codex": "Structured tool calls that read a literal SKILL.md, including exec wrappers; catalogs and search commands are excluded.",
    "zcode": "Structured Skill calls and literal SKILL.md loads from model-I/O rollout and nested agent event logs.",
    "claude": "Structured Skill calls and literal SKILL.md reads from session transcripts.",
    "limitations": "Counts observed load commands, not successful completion of a skill. Dynamic paths, missing/deleted logs, and unsupported agent schemas cannot be reconstructed. Marker hits and atime are legacy/coarse evidence, not precise invocations.",
}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "$Recycle.Bin", "System Volume Information"}


def _canonical(path):
    return os.path.realpath(os.path.expanduser(os.fspath(path)))


def discover_history_roots():
    """Known locations plus agent home overrides; aliases resolve to one root."""
    home = os.path.expanduser("~")
    candidates = []
    for base in dict.fromkeys([os.environ.get("CODEX_HOME") or os.path.join(home, ".codex"),
                              os.path.join(home, ".codex")]):
        candidates.extend(("codex", os.path.join(base, sub)) for sub in ("sessions", "archived_sessions"))
    zcode = os.environ.get("ZCODE_HOME") or os.path.join(home, ".zcode")
    claude = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
    candidates.extend([("zcode", os.path.join(zcode, "cli", "rollout")),
                       ("zcode", os.path.join(zcode, "cli", "agents")),
                       ("claude", os.path.join(claude, "projects"))])
    roots, seen = [], set()
    for source, path in candidates:
        path = _canonical(path)
        key = (source, os.path.normcase(path))
        if key not in seen:
            seen.add(key)
            roots.append({"source": source, "path": path})
    return roots


def _error(path, error):
    return {"path": str(path), "error": type(error).__name__}


class UsageService:
    """One daemon worker with persisted offsets/counters in a stable data folder.

    get_usage() schedules incremental refresh without delaying the UI. scan(path)
    is the explicit optional directory/drive action; automatic startup scans only
    the agent session locations, never every disk. No transcript text is exported.
    """

    def __init__(self, data_dir=None, history_roots=None, auto_start=True, refresh_seconds=30):
        self.data_dir = _canonical(data_dir or os.environ.get("SKILL_RADAR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".skill-radar"))
        self.state_file = os.path.join(self.data_dir, "skill_usage_state.json")
        self.data_file = os.path.join(self.data_dir, "skill_usage.json")
        self._lock = threading.RLock()
        self._thread = None
        self._stop_event = threading.Event()
        self.refresh_seconds = refresh_seconds
        self._last_refresh = 0.0
        self._state = sm._load(self.state_file, {})
        if not isinstance(self._state, dict):
            self._state = {}
        self._data = self._state.get("counters") or sm._load(self.data_file, {})
        if not isinstance(self._data, dict):
            self._data = {}
        if not isinstance(self._data.get("skills"), dict):
            self._data["skills"] = {}
        self._roots = []
        candidates = discover_history_roots() if history_roots is None else history_roots
        for root in list(candidates) + self._state.get("usage_roots", []):
            if isinstance(root, dict) and root.get("source") in ("auto", "codex", "zcode", "claude") \
                    and isinstance(root.get("path"), str) and os.path.isabs(root["path"]):
                self._add_root(root["path"], root["source"])
        self._status = {"phase": "idle", "scanning": False, "last_scan": self._data.get("meta", {}).get("last_scan", ""),
                        "files_scanned": 0, "errors": [], "new_records": 0, "coverage": dict(COVERAGE)}
        saved = self._data.get("meta", {}).get("usage_status")
        if isinstance(saved, dict) and saved.get("phase") in ("ready", "stopped", "error", "scanning", "idle"):
            for field in ("phase", "last_scan", "files_scanned", "errors", "new_records", "coverage"):
                if field in saved:
                    self._status[field] = copy.deepcopy(saved[field])
            # A process restart cannot resume the old worker or declare its partial scan complete.
            if self._status["phase"] == "scanning" or saved.get("scanning"):
                self._status["phase"] = "stopped"
        if auto_start:
            self.scan()

    def _add_root(self, path, source):
        root = {"source": source, "path": _canonical(path)}
        if not any(os.path.normcase(r["path"]) == os.path.normcase(root["path"]) and r["source"] == source
                   for r in self._roots):
            self._roots.append(root)

    def snapshot(self):
        with self._lock:
            result = copy.deepcopy(self._status)
            result["roots"] = [{**root, "exists": os.path.isdir(root["path"])} for root in self._roots]
            return result

    def stop(self, timeout=5):
        """Stop between JSONL records and wait briefly for the final checkpoint."""
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0, timeout))
        return not thread or not thread.is_alive()

    def scan(self, path=None, source="auto", background=True):
        if self._stop_event.is_set():
            return {"ok": False, "error": "usage scanner has stopped"}
        if source not in ("auto", "codex", "zcode", "claude"):
            return {"ok": False, "error": "unsupported session log source"}
        if path is not None and (not isinstance(path, str) or not os.path.isabs(path) or
                                 any(c in path for c in "\r\n\x00")):
            return {"ok": False, "error": "choose an absolute session-log directory or drive root"}
        if path is not None and not os.path.isdir(path):
            return {"ok": False, "error": "session-log directory does not exist"}
        with self._lock:
            if self._status["scanning"]:
                return {"ok": path is None, "queued": False, "busy": True, "status": self.snapshot(),
                        **({"error": "a session-log scan is already running"} if path is not None else {})}
            if path is not None:
                self._add_root(path, source)
            self._status.update(phase="scanning", scanning=True, errors=[], files_scanned=0, new_records=0)
            self._last_refresh = time.monotonic()
            if background:
                self._thread = threading.Thread(target=self._collect, name="skill-usage-history", daemon=True)
                self._thread.start()
            else:
                self._collect()
        return {"ok": True, "queued": background, "status": self.snapshot()}

    def _collect(self):
        with self._lock:
            roots = copy.deepcopy(self._roots)
            state, data = copy.deepcopy(self._state), copy.deepcopy(self._data)
        stats = {"files_scanned": 0, "errors": [], "new_records": 0}
        seen = set()
        def saved_status(phase, timestamp=None):
            return {**copy.deepcopy(self._status), **copy.deepcopy(stats), "phase": phase,
                    "scanning": phase == "scanning",
                    "last_scan": timestamp or self._status.get("last_scan", ""),
                    "roots": [{**root, "exists": os.path.isdir(root["path"])} for root in roots]}
        try:
            for root in roots:
                if self._stop_event.is_set():
                    break
                if not os.path.isdir(root["path"]):
                    continue
                def walking_error(error):
                    stats["errors"].append(_error(error.filename or root["path"], error))
                files = []
                for directory, dirs, names in os.walk(root["path"], onerror=walking_error, followlinks=False):
                    if self._stop_event.is_set():
                        break
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(directory, d))]
                    for name in names:
                        if not name.lower().endswith(".jsonl"):
                            continue
                        filename = _canonical(os.path.join(directory, name))
                        key = os.path.normcase(filename)
                        if key not in seen:
                            seen.add(key)
                            files.append(filename)
                # Show recent history first; older files continue in the worker.
                def modified(filename):
                    try:
                        return os.path.getmtime(filename)
                    except OSError:
                        return 0
                files.sort(key=modified, reverse=True)
                checkpoint_at = [0.0]
                def publish_progress():
                    with self._lock:
                        # Keep matching in-memory offsets with visible counters
                        # even if writing the next checkpoint is denied.
                        self._state = copy.deepcopy(state)
                        self._data = copy.deepcopy(data)
                        self._status.update(copy.deepcopy(stats))
                    # First useful result, then bounded checkpoints. Final scan
                    # and graceful shutdown always commit the remaining offsets.
                    if time.monotonic() - checkpoint_at[0] >= 5:
                        checkpoint_at[0] = time.monotonic()
                        data.setdefault("meta", {})["usage_status"] = saved_status("scanning")
                        state["counters"] = data
                        state["usage_roots"] = roots
                        os.makedirs(self.data_dir, exist_ok=True)
                        sm._save(self.state_file, state)
                        with self._lock:
                            self._state = copy.deepcopy(state)
                        sm._save(self.data_file, data)
                sm.scan_history(state, data, files, root["source"], stats, on_progress=publish_progress,
                                should_stop=self._stop_event.is_set)
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            phase = "error" if stats["errors"] else "stopped" if self._stop_event.is_set() else "ready"
            data["meta"] = {"last_scan": timestamp, "usage_status": saved_status(phase, timestamp)}
            state["counters"] = data
            state["usage_roots"] = roots
            os.makedirs(self.data_dir, exist_ok=True)
            sm._save(self.state_file, state)
            # The committed state owns matching offsets/counters even if the
            # convenience export fails; a retry must start from this checkpoint.
            with self._lock:
                self._state, self._data = state, data
            sm._save(self.data_file, data)
            with self._lock:
                self._status.update(stats, last_scan=timestamp, phase=phase)
        except (OSError, ValueError, TypeError) as error:
            stats["errors"].append(_error(self.data_dir, error))
            with self._lock:
                self._status.update(stats, phase="error")
                # If a matching counter checkpoint was committed before the export failed,
                # persist its failure state without changing offsets or exposing log content.
                failed_data = copy.deepcopy(self._data)
                failed_data.setdefault("meta", {})["usage_status"] = saved_status("error")
                failed_state = copy.deepcopy(self._state)
                failed_state["counters"] = failed_data
                try:
                    sm._save(self.state_file, failed_state)
                    self._state, self._data = failed_state, failed_data
                    sm._save(self.data_file, failed_data)
                except (OSError, ValueError, TypeError):
                    pass
        finally:
            with self._lock:
                self._status["scanning"] = False

    def get_usage(self, refresh=True, force=False):
        if refresh and (force or time.monotonic() - self._last_refresh >= self.refresh_seconds):
            self.scan()
        with self._lock:
            skills = copy.deepcopy(self._data.get("skills", {}))
            # Rows and coverage must describe the same moment. The worker can finish
            # while rows are formatted, so a later status could falsely imply no calls.
            status = self.snapshot()
        rows = []
        for name, entry in skills.items():
            if isinstance(entry, int):
                entry = {"zcode": entry}
            if not isinstance(entry, dict):
                continue
            counts = {}
            for field in ("codex", "zcode", "claude", "marker", "atime"):
                value = entry.get(field, 0)
                try:
                    counts[field] = max(0, int(value or 0))
                except (ValueError, TypeError, OverflowError):
                    counts[field] = 0
            rows.append({"name": str(name), **counts,
                         "total": sum(counts[k] for k in ("codex", "zcode", "claude", "marker")),
                         "last": latest_time(entry.get("last_tool_use"), entry.get("last_marker"))})
        # The ranking table is useful for recency first: a recently used skill
        # should remain visible even when an older skill has accumulated more calls.
        # Missing/invalid times stay at the end, then total and name provide a
        # deterministic tie-break.
        rows.sort(key=lambda row: (parse_time(row.get("last")) is None,
                                   -(parse_time(row.get("last")).timestamp()
                                     if parse_time(row.get("last")) is not None else 0),
                                   -row["total"], row["name"]))
        return {"ok": True, "rows": rows, "status": status}
