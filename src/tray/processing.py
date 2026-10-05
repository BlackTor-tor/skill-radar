"""桌面单项和批量处理共用服务；操作身份使用完整路径和检查版本。"""
import copy
import ctypes
import errno
import json
import os
import shutil
import threading
import time
import sys
import uuid
from datetime import datetime
from dataclasses import asdict

import skill_guard as sg
from tray.review import capture_version, effective_rules_text


ACTIONS = frozenset(("rescan", "review", "trust", "revoke_trust", "quarantine",
                     "restore", "restore_trust"))


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _safe_chain(path):
    """检查所有已有祖先，拒绝链接和 Windows junction；缺失末端允许恢复。"""
    current = os.path.normpath(os.path.abspath(path))
    while True:
        if os.path.lexists(current) and sg._is_reparse(current):
            return False
        parent = os.path.dirname(current)
        if parent == current:
            return True
        current = parent


def _contains(parent, child):
    parent = sg._canon_path(os.path.realpath(parent)).rstrip("/")
    child = sg._canon_path(os.path.realpath(child))
    return child.startswith(parent + "/")


def _rename_no_replace(source, destination):
    """平台原子改名必须拒绝已存在的目录，避免 shutil.move 的目录嵌套。"""
    if sys.platform == "win32":
        os.rename(source, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        result = library.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin" and hasattr(library, "renamex_np"):
        result = library.renamex_np(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise OSError("safe directory restore is unavailable on this platform")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


class ProcessingService:
    """一次只运行一个作业，逐项持久化进度，隔离说明保存在技能内容之外。"""

    def __init__(self, daemon, state, path=None):
        self.daemon, self.state = daemon, state
        self.path = os.fspath(path) if path else os.path.join(sg.GUARD_DIR, "processing.json")
        self.quarantine_dir = os.path.join(sg.GUARD_DIR, "quarantine")
        self._lock = threading.RLock()
        self._job = None
        self._quarantine = []
        self._thread = None
        self._stopping = False
        self.storage_error = None
        self._quarantine_checks = {}
        self._quarantine_views = {}
        self._refresh_thread = None
        self._refresh_stamp = None
        self._refreshed_at = 0
        self._load()

    def _flush(self):
        """原子保存失败由操作方报告；隔离元数据失败时绝不移动目录。"""
        sg._atomic_write(self.path, lambda file: json.dump(
            {"version": 1, "batch_job": self._job, "quarantine": self._quarantine},
            file, ensure_ascii=False, indent=2))

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as file:
                payload = json.load(file)
            if not isinstance(payload, dict) or payload.get("version") != 1 or \
                    not isinstance(payload.get("quarantine"), list):
                raise ValueError("processing records have an unsupported format")
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            self.storage_error = sg._sanitize(str(error))
            return
        rows = payload.get("quarantine", [])
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or not all(isinstance(row.get(key), str)
                        for key in ("id", "original_path", "destination", "version")):
                    continue
                if not self._valid_destination(row["destination"]):
                    continue
                # 移动后的末次写盘可能失败；实际位置决定恢复入口是否存在。
                source_exists = os.path.lexists(row["original_path"])
                destination_exists = os.path.isdir(row["destination"])
                if destination_exists and source_exists and row.get("status") in (
                        "pending", "copy_pending", "restoring", "restore_copy_pending"):
                    status = "restore_copy_pending" if row["status"] in ("restoring", "restore_copy_pending") else "copy_pending"
                    row.update(status=status, original_exists=True, destination_exists=True)
                elif destination_exists and not source_exists:
                    row.update(status="isolated", path=row["destination"])
                elif source_exists and not destination_exists:
                    row["status"] = "restored" if row.get("status") == "restoring" else "failed"
                self._quarantine.append(row)
        job = payload.get("batch_job")
        if job is not None and (not isinstance(job, dict) or
                not isinstance(job.get("items"), list) or not isinstance(job.get("results"), list) or
                not isinstance(job.get("action"), str) or job["action"] not in ACTIONS or
                not all(isinstance(item, dict) and isinstance(item.get("path"), str) and
                        os.path.isabs(item["path"]) and isinstance(item.get("version"), str)
                        for item in job.get("items", [])) or
                not all(isinstance(result, dict) and result.get("status") in
                        ("success", "failed", "skipped") for result in job.get("results", []))):
            self.storage_error = "saved processing job has an unsupported format"
            return
        if isinstance(job, dict) and isinstance(job.get("items"), list) and \
                isinstance(job.get("results"), list) and job.get("action") in ACTIONS:
            self._job = job
            if job.get("status") == "running":
                completed = len(job["results"])
                for item in job["items"][completed:]:
                    result = self._result(item, "failed", "interrupted")
                    # 隔离确已完成则保留成功，重启不能让失败重试再次移动。
                    record = next((row for row in self._quarantine if
                        row.get("original_path") == item.get("path") and
                        row.get("version") == item.get("version") and
                        row.get("status") == "isolated"), None)
                    if job["action"] == "quarantine" and record:
                        result.update(status="success", code="ok", quarantine_id=record["id"])
                    job["results"].append(result)
                job.update(status="done", completed=len(job["results"]), finished_at=_now())
                try:
                    self._flush()
                except OSError:
                    pass

    def snapshot(self):
        stamp = self._policy_stamp()
        with self._lock:
            rows = []
            records = copy.deepcopy(self._quarantine)
            stale = stamp != self._refresh_stamp
            for record in records:
                if record.get("status") not in ("isolated", "copy_pending", "restore_copy_pending") or \
                        not self._valid_destination(record["destination"]) or \
                        not os.path.isdir(record["destination"]):
                    continue
                cached = self._quarantine_views.get(record["id"], {})
                row = {**record, **cached, "status": record["status"],
                       "refreshing": stale or not cached}
                if row["refreshing"]:
                    row["scan_complete"] = False
                rows.append(row)
            due = bool(rows) and (stale or time.monotonic() - self._refreshed_at > 10 or
                                any(not self._quarantine_views.get(row["id"]) for row in rows))
            if due and not self._stopping and not (self._refresh_thread and self._refresh_thread.is_alive()):
                self._refresh_thread = threading.Thread(target=self._refresh_quarantine,
                    args=(records, stamp), daemon=True)
                self._refresh_thread.start()
            return {"batch_job": copy.deepcopy(self._job), "quarantine": rows,
                    "processing_error": self.storage_error}

    def _policy_stamp(self):
        """轮询只比较规则文本与规则文件 stat，文件内容检查留在后台。"""
        rules = self.daemon.rules_text
        if "\n" not in rules:
            try:
                info = os.stat(rules)
                return (rules, info.st_mtime_ns, info.st_size, self.daemon.blocklist_text)
            except OSError:
                pass
        return (rules, self.daemon.blocklist_text)

    def _refresh_quarantine(self, records, stamp):
        views = {}
        for record in records:
            if record.get("status") not in ("isolated", "copy_pending", "restore_copy_pending") or \
                    not self._valid_destination(record["destination"]) or \
                    not os.path.isdir(record["destination"]):
                continue
            try:
                name = os.path.basename(record["original_path"])
                captured = capture_version(record["destination"], self.daemon.rules_text,
                                           self.daemon.blocklist_text, skill_name=name)
                row = {"isolation_version": record.get("isolation_version", record["version"]),
                       "version": captured["version"], "scan_complete": captured["complete"],
                       "scan_issues": captured["issues"], "scan_coverage": captured["coverage"]}
                with self._lock:
                    check = copy.deepcopy(self._quarantine_checks.get((record["id"], captured["version"])))
                if check is None:
                    report = self._inspect_payload(record["destination"], name)
                    after = capture_version(record["destination"], self.daemon.rules_text,
                                            self.daemon.blocklist_text, skill_name=name)
                    issues = sorted(set(captured["issues"] + after["issues"]))
                    if after["version"] != captured["version"]:
                        issues.append("changed_during_scan")
                    if any(f.rule_id == "SR-OBFUS-004" for f in report.findings):
                        issues.append("decode_limit")
                    complete = captured["complete"] and after["complete"] and not issues
                    check = {"raw_findings": sg._sanitize_json([asdict(f) for f in report.findings]),
                             "raw_score": sg.score_findings(report.findings), "scan_complete": complete,
                             "version": after["version"], "scan_issues": issues,
                             "scan_gap_details": sg._sanitize_json([asdict(f) for f in report.findings
                                                                    if f.rule_id == "SR-OBFUS-004"]),
                             "scan_coverage": {**after["coverage"],
                                               "text_files_checked": report.files_scanned},
                             "scanned_at": _now(),
                             "check_status": "incomplete" if not complete else
                                 "attention" if report.findings else "healthy"}
                    with self._lock:
                        self._quarantine_checks[(record["id"], captured["version"])] = check
                row.update(check)
                views[record["id"]] = row
            except Exception as error:
                views[record["id"]] = {"scan_complete": False, "check_status": "failed",
                                        "scan_issues": [sg._sanitize(str(error))]}
        with self._lock:
            self._quarantine_views.update(views)
            self._refresh_stamp = stamp
            self._refreshed_at = time.monotonic()

    @staticmethod
    def _result(item, status, code="ok", **extra):
        return {**item, "status": status, "code": code, **extra}

    def submit(self, action, items):
        if not isinstance(action, str) or action not in ACTIONS:
            return {"ok": False, "code": "invalid_action", "error": "choose a supported action"}
        if not isinstance(items, list) or not items or len(items) > 10000:
            return {"ok": False, "code": "invalid_items", "error": "select skills to process"}
        selected, identities = [], set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or \
                    not os.path.isabs(item["path"]) or any(c in item["path"] for c in "\r\n\x00") or \
                    not isinstance(item.get("version"), str) or not item["version"] or \
                    ("id" in item and not isinstance(item["id"], str)):
                return {"ok": False, "code": "invalid_items", "error": "use full skill paths and check versions"}
            identity = sg._canon_path(item["path"])
            if identity in identities:
                return {"ok": False, "code": "invalid_items", "error": "select each skill only once"}
            identities.add(identity)
            selected.append({"path": os.path.normpath(item["path"]), "version": item["version"],
                             **({"id": item["id"]} if "id" in item else {})})
        with self._lock:
            if self.storage_error:
                return {"ok": False, "code": "metadata_failed", "error": self.storage_error}
            if self._stopping:
                return {"ok": False, "code": "stopping", "error": "the client is closing"}
            if self._job and self._job.get("status") == "running":
                return {"ok": False, "code": "job_running", "error": "another operation is running"}
            previous = self._job
            self._job = {"id": uuid.uuid4().hex, "action": action, "status": "running",
                         "items": selected, "total": len(selected), "completed": 0,
                         "results": [], "started_at": _now()}
            try:
                self._flush()
            except OSError as error:
                self._job = previous
                return {"ok": False, "code": "metadata_failed", "error": sg._sanitize(str(error))}
            job_id = self._job["id"]
            self._thread = threading.Thread(target=self._work, args=(action, selected), daemon=True)
            self._thread.start()
            return {"ok": True, "job_id": job_id}

    def _work(self, action, items):
        for item in items:
            try:
                result = self._result(item, "skipped", "interrupted") if self._stopping else \
                    self.process_one(action, item)
            except Exception as error:
                result = self._result(item, "failed", "operation_failed", error=sg._sanitize(str(error)))
            with self._lock:
                self._job["results"].append(result)
                self._job["completed"] += 1
                try:
                    self._flush()
                except OSError as error:
                    self._job["persistence_error"] = sg._sanitize(str(error))
        with self._lock:
            self._job.update(status="done", finished_at=_now())
            try:
                self._flush()
            except OSError as error:
                self._job["persistence_error"] = sg._sanitize(str(error))

    def stop(self):
        """关闭时停止接受任务，当前移动完成后剩余项记录为中断。"""
        with self._lock:
            self._stopping = True

    def join(self, timeout=None):
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        refresh = self._refresh_thread
        if refresh and refresh is not threading.current_thread():
            refresh.join(timeout=timeout)

    def _move_no_replace(self, source, destination, version, skill_name):
        """同盘原子移动；跨盘先验证副本，再独占改名，最后清除原目录。"""
        try:
            _rename_no_replace(source, destination)
            return
        except OSError as error:
            if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
                raise
        staging = destination + ".copy-" + uuid.uuid4().hex
        try:
            # 不沿符号链接复制；版本证据检查会拒绝新增链接或内容变化。
            shutil.copytree(source, staging, symlinks=True)
            if not self._capture_stable(source, version, skill_name) or not self._capture_stable(staging, version, skill_name):
                raise OSError("skill content changed while moving between drives")
            if not _safe_chain(source) or not _safe_chain(staging):
                raise OSError("linked skill folders cannot be moved")
            _rename_no_replace(staging, destination)
            # source 在注册技能范围或隔离区，并再次核对内容后才清除已复制目录。
            source_allowed = self._registered(source) or self._valid_destination(source)
            if not source_allowed or not _safe_chain(source) or \
                    not self._capture_stable(source, version, skill_name):
                raise OSError("original skill changed after copying")
            source_absolute = os.path.normcase(os.path.abspath(source))
            if source_absolute in (os.path.normcase(os.path.abspath(root)) for root in self.daemon.roots) or \
                    source_absolute == os.path.normcase(os.path.abspath(self.quarantine_dir)):
                raise OSError("registered folders cannot be removed while moving a skill")
            shutil.rmtree(source)
        finally:
            # 只清除此操作创建的同父 staging；所有祖先与绝对边界再次复核。
            if os.path.dirname(os.path.abspath(staging)) == os.path.dirname(os.path.abspath(destination)) and \
                    os.path.basename(staging).startswith(os.path.basename(destination) + ".copy-") and \
                    os.path.isdir(staging) and _safe_chain(staging):
                shutil.rmtree(staging)

    def _registered(self, path):
        return any(self.daemon._under(path, root) for root in self.daemon.roots)

    def _active_path(self, item):
        path = item["path"]
        if not self._registered(path):
            return None, self._result(item, "skipped", "not_registered")
        if not _safe_chain(path):
            return None, self._result(item, "skipped", "unsafe_path")
        if not os.path.isdir(path) or not sg._is_skill_dir(path):
            return None, self._result(item, "skipped", "missing")
        identity = sg._canon_path(path)
        active = next((key for key in self.state.snapshot()["skills"]
                       if sg._canon_path(key) == identity), None)
        if not active:
            return None, self._result(item, "skipped", "not_registered")
        return active, None

    def _capture_stable(self, path, expected, skill_name=None):
        before = capture_version(path, self.daemon.rules_text, self.daemon.blocklist_text, skill_name=skill_name)
        after = capture_version(path, self.daemon.rules_text, self.daemon.blocklist_text, skill_name=skill_name)
        if before["version"] != after["version"] or after["version"] != expected:
            return None
        return after

    def _inspect_payload(self, path, skill_name):
        """隔离位置只改变载体名称；检测内容与原技能名单名称保持一致。"""
        report = sg.run_engine(path, sg.parse_rules(effective_rules_text(self.daemon.rules_text)), "[]")
        logical_findings = sg.check_blocklist(self.daemon.blocklist_text,
            name=skill_name, repo="", hashes=sg._file_hashes(path))
        seen = {("SKILL.md", skill_name)}
        for relative, text in sg.collect_text_files(path):
            if os.path.basename(relative) != "SKILL.md":
                continue
            directory = os.path.basename(os.path.dirname(relative)) or skill_name
            for name in (directory, sg._skill_frontmatter_name(text)):
                if not name or (relative, name) in seen:
                    continue
                seen.add((relative, name))
                for finding in sg.check_blocklist(self.daemon.blocklist_text, name=name, repo="", hashes={}):
                    finding.file = relative
                    logical_findings.append(finding)
        report.findings.extend(logical_findings)
        report.score = sg.score_findings(report.findings)
        return report

    def _trust_complete(self, path, expected, skill_name):
        """信任前以当前规则运行引擎；文件摘要完整不等于解码检查完整。"""
        before = self._capture_stable(path, expected, skill_name)
        if before is None or not before["complete"]:
            return False
        report = self._inspect_payload(path, skill_name)
        after = self._capture_stable(path, expected, skill_name)
        return bool(after and after["complete"] and
                    not any(f.rule_id == "SR-OBFUS-004" for f in report.findings))

    def process_one(self, action, item):
        """与后台扫描共享锁；UI 单项、批量 worker 均在操作前核对版本。"""
        with self.daemon.scan_lock:
            if action in ("restore", "restore_trust"):
                return self._restore(item, trust=action == "restore_trust")
            path, failure = self._active_path(item)
            if failure:
                return failure
            if action == "rescan":
                # 重新检查是读取当前内容；旧界面版本不能阻止用户检查已修复的文件。
                result = self.daemon.scan_changed_skill(path)
                if result == "ERROR":
                    return self._result(item, "failed", "scan_failed", attempt_complete=True)
                row = self.state.snapshot()["skills"].get(path, {})
                isolation = {}
                if not row:
                    # 自动隔离会移除活动行；动作结果仍保留完成的检查证据和实际去向。
                    with self._lock:
                        isolated = next((record for record in reversed(self._quarantine)
                            if sg._canon_path(record["original_path"]) == sg._canon_path(path)
                            and record.get("status") == "isolated"), None)
                        if isolated:
                            row = copy.deepcopy(isolated)
                            isolation = {"isolated": True, "quarantine_id": isolated["id"]}
                return self._result(item, "success",
                    "ok" if row.get("scan_complete") else "scan_incomplete",
                    attempt_complete=True, **isolation, **{key: row.get(key) for key in (
                        "version", "scanned_at", "check_status", "scan_complete",
                        "scan_issues", "scan_coverage", "scan_gap_details")})
            evidence = self._capture_stable(path, item["version"])
            if evidence is None:
                return self._result(item, "skipped", "version_changed")
            if action == "quarantine":
                return self._isolate(path, evidence, item, "manual")
            if action in ("review", "trust"):
                if action == "trust":
                    row = self.state.snapshot()["skills"][path]
                    if not evidence["complete"] or not row.get("scan_complete") or \
                            not self._trust_complete(path, item["version"], os.path.basename(path)):
                        return self._result(item, "failed", "scan_incomplete")
                try:
                    self.daemon.review_store.record(path, evidence["version"], action,
                                                    complete=evidence["complete"])
                except OSError as error:
                    return self._result(item, "failed", "metadata_failed", error=sg._sanitize(str(error)))
            elif action == "revoke_trust":
                try:
                    self.daemon.review_store.revoke(path, evidence["version"])
                except OSError as error:
                    return self._result(item, "failed", "metadata_failed", error=sg._sanitize(str(error)))
            result = self.daemon.scan_changed_skill(path)
            if result == "ERROR":
                return self._result(item, "failed", "scan_failed")
            return self._result(item, "success")

    def _valid_destination(self, path):
        return isinstance(path, str) and os.path.isabs(path) and \
            _contains(self.quarantine_dir, path) and _safe_chain(path)

    def _isolate(self, path, evidence, item, reason):
        """移动前落恢复元数据；备注失败仍如实返回已移动的位置。"""
        name = os.path.basename(path)
        pending_copy = next((row for row in self._quarantine if
            row.get("status") == "copy_pending" and
            sg._canon_path(row["original_path"]) == sg._canon_path(path)), None)
        if pending_copy:
            destination = pending_copy["destination"]
            copied = self._capture_stable(destination, evidence["version"], name)
            if not copied or copied.get("content_version") != evidence.get("content_version") or \
                    not self._valid_destination(destination) or not _safe_chain(path):
                return self._result(item, "failed", "copy_pending", quarantine_id=pending_copy["id"])
            try:
                if not self._capture_stable(path, item["version"]):
                    return self._result(item, "skipped", "version_changed")
                if any(sg._canon_path(path) == sg._canon_path(root) for root in self.daemon.roots):
                    raise OSError("registered root cannot be removed")
                shutil.rmtree(path)
            except OSError as error:
                return self._finish_isolation(pending_copy, item, "failed", "copy_pending", str(error))
            self.state.remove_skill(path)
            self.state.set_guard("quarantine")
            return self._finish_isolation(pending_copy, item, "success", "ok")
        identifier = uuid.uuid4().hex
        container = os.path.join(self.quarantine_dir, name + "-" + identifier)
        destination = os.path.join(container, "skill")
        if not self._valid_destination(destination) or os.path.lexists(destination):
            return self._result(item, "failed", "unsafe_path")
        row = self.state.snapshot()["skills"].get(path, {})
        record = {"id": identifier, "name": name, "original_path": path,
                  "destination": destination, "path": destination,
                  "version": evidence["version"], "isolated_at": _now(),
                  "isolation_version": evidence["version"],
                  "content_version": evidence["content_version"],
                  "content_hashes": evidence["hashes"],
                  "raw_findings": copy.deepcopy(row.get("raw_findings", row.get("findings", []))),
                  "raw_score": row.get("raw_score", row.get("score", 0)),
                  "check_status": row.get("check_status", "attention"),
                  "scan_complete": row.get("scan_complete", False),
                  "scan_issues": row.get("scan_issues", []),
                  "scan_gap_details": row.get("scan_gap_details", []),
                  "scan_coverage": row.get("scan_coverage", {}),
                  "scanned_at": row.get("scanned_at", _now()),
                  "reason": reason, "status": "pending"}
        with self._lock:
            self._quarantine.append(record)
            try:
                self._flush()
            except OSError as error:
                self._quarantine.remove(record)
                return self._result(item, "failed", "metadata_failed", error=sg._sanitize(str(error)))
        if not self._capture_stable(path, evidence["version"]) or not _safe_chain(path):
            return self._finish_isolation(record, item, "skipped", "version_changed")
        try:
            os.makedirs(container, exist_ok=False)
            if os.path.lexists(destination) or not self._valid_destination(destination):
                raise OSError("quarantine destination is unavailable")
            self._move_no_replace(path, destination, evidence["version"], name)
        except OSError as error:
            moved = not os.path.lexists(path) and os.path.isdir(destination)
            if not moved:
                code = "copy_pending" if os.path.lexists(path) and os.path.isdir(destination) else "move_failed"
                return self._finish_isolation(record, item, "failed", code, error=str(error))
        if os.path.lexists(path) or not os.path.isdir(destination):
            return self._finish_isolation(record, item, "failed", "move_failed")
        self.state.remove_skill(path)
        self.state.set_guard("quarantine")
        code, warning = "ok", None
        try:
            self._write_note(record)
        except OSError as error:
            code, warning = "note_failed", str(error)
        return self._finish_isolation(record, item, "success", code, error=warning)

    def _finish_isolation(self, record, item, status, code, error=None):
        with self._lock:
            moved = not os.path.lexists(record["original_path"]) and os.path.isdir(record["destination"])
            both = os.path.lexists(record["original_path"]) and os.path.isdir(record["destination"])
            record.update(status="isolated" if moved else "copy_pending" if both else "failed",
                          original_exists=os.path.lexists(record["original_path"]),
                          destination_exists=os.path.isdir(record["destination"]))
            self._quarantine_views.pop(record["id"], None)
            if error:
                record["error"] = sg._sanitize(error)
            try:
                self._flush()
            except OSError as save_error:
                # 前置元数据仍可在重启时按真实位置恢复；不把已移动报成未移动。
                code = "record_update_failed" if moved else code
                error = str(save_error)
            return self._result(item, status, code, quarantine_id=record["id"],
                                **({"error": sg._sanitize(error)} if error else {}))

    @staticmethod
    def _write_note(record):
        """说明位于隔离容器内、技能目录外，避免改变被信任的文件版本。"""
        note = os.path.join(os.path.dirname(record["destination"]), "RESTORE.txt")
        with open(note, "w", encoding="utf-8") as file:
            file.write("SkillRadar quarantine / 隔离记录\n" +
                       "Original folder / 原位置: " + record["original_path"] + "\n" +
                       "Isolated at / 隔离时间: " + record["isolated_at"] + "\n" +
                       "Restore from SkillRadar Security > Quarantine.\n" +
                       "请在 SkillRadar 安全检查的隔离区恢复。\n")

    def isolate_automatic(self, path):
        """自动隔离复用同一记录与移动流程，风险报告不等于允许移动任意路径。"""
        with self.daemon.scan_lock:
            if self.storage_error:
                return {"status": "failed", "code": "metadata_failed", "path": path,
                        "error": self.storage_error}
            row = next((row for key, row in self.state.snapshot()["skills"].items()
                        if sg._canon_path(key) == sg._canon_path(path)), None)
            if row is None or not row.get("version"):
                return {"status": "failed", "code": "not_registered", "path": path}
            item = {"path": path, "version": row["version"]}
            active, failure = self._active_path(item)
            if failure:
                return failure
            evidence = self._capture_stable(active, item["version"])
            if evidence is None:
                return self._result(item, "skipped", "version_changed")
            return self._isolate(active, evidence, item, "automatic")

    def _restore(self, item, trust):
        with self._lock:
            record = next((row for row in self._quarantine if
                (not item.get("id") or row["id"] == item["id"]) and
                sg._canon_path(item["path"]) in (sg._canon_path(row["original_path"]),
                                                 sg._canon_path(row["destination"])) and
                row.get("status") == "isolated"), None)
        if record is None:
            return self._result(item, "skipped", "missing")
        original, destination = record["original_path"], record["destination"]
        if not self._registered(original):
            return self._result(item, "skipped", "not_registered")
        if not self._valid_destination(destination) or not _safe_chain(original):
            return self._result(item, "skipped", "unsafe_path")
        if os.path.lexists(original):
            return self._result(item, "failed", "restore_conflict")
        if not sg._is_skill_dir(destination):
            return self._result(item, "skipped", "missing")
        skill_name = os.path.basename(original)
        evidence = self._capture_stable(destination, item["version"], skill_name)
        if evidence is None:
            return self._result(item, "skipped", "version_changed")
        if record.get("content_version") and evidence["content_version"] != record["content_version"]:
            return self._result(item, "skipped", "version_changed")
        if trust and (not evidence["complete"] or
                      not self._trust_complete(destination, item["version"], skill_name)):
            return self._result(item, "failed", "scan_incomplete")
        already_trusted = self.daemon.review_store.decision_for(original, item["version"]) == "trusted"

        def failed_restore(status, code, error=None):
            # 失败动作不能给之后出现的同名目录留下新的人工例外。
            rollback_error = None
            if trust and not already_trusted:
                try:
                    self.daemon.review_store.revoke(original, item["version"])
                except OSError as issue:
                    rollback_error = sg._sanitize(str(issue))
            with self._lock:
                record.update(status="restore_copy_pending" if code == "restore_copy_pending" else "isolated",
                              original_exists=os.path.lexists(original), destination_exists=os.path.isdir(destination))
                try:
                    self._flush()
                except OSError as issue:
                    self.storage_error = sg._sanitize(str(issue))
            return self._result(item, status, code,
                                **({"error": sg._sanitize(str(error))} if error else {}),
                                **({"rollback_error": rollback_error} if rollback_error else {}))

        try:
            with self._lock:
                record["status"] = "restoring"
                self._flush()
            if trust:
                # 先保存明确的人工例外，恢复后的扫描不会抢先再次自动隔离。
                self.daemon.review_store.record(original, item["version"], "trust", complete=evidence["complete"])
            else:
                self.daemon.review_store.revoke(original, item["version"])
        except OSError as error:
            return failed_restore("failed", "metadata_failed", error)
        if os.path.lexists(original) or not _safe_chain(original) or \
                not self._capture_stable(destination, item["version"], skill_name):
            return failed_restore("skipped", "version_changed")
        try:
            os.makedirs(os.path.dirname(original), exist_ok=True)
            if os.path.lexists(original):
                return failed_restore("failed", "restore_conflict")
            self._move_no_replace(destination, original, item["version"], skill_name)
        except OSError as error:
            if not os.path.isdir(original) or os.path.lexists(destination):
                if os.path.isdir(original) and os.path.isdir(destination):
                    actual = self._capture_stable(original, item["version"], skill_name)
                    if actual and actual.get("content_version") == evidence.get("content_version"):
                        with self._lock:
                            record.update(status="restore_copy_pending", original_exists=True,
                                          destination_exists=True, error=sg._sanitize(str(error)))
                            try:
                                self._flush()
                            except OSError as saving:
                                self.storage_error = sg._sanitize(str(saving))
                        # 原位置已恢复，保留用户明确选择的信任；隔离副本清理问题单独报告。
                        scan_result = self.daemon.scan_changed_skill(original)
                        return self._result(item, "failed" if scan_result == "ERROR" else "success",
                                            "restore_copy_pending", restored=True,
                                            error=sg._sanitize(str(error)))
                code = "restore_conflict" if os.path.lexists(original) else "move_failed"
                return failed_restore("failed", code, error)
        if not os.path.isdir(original) or os.path.lexists(destination):
            return failed_restore("failed", "move_failed")
        result_code, result_error = "ok", None
        with self._lock:
            record.update(status="restored", restored_at=_now())
            try:
                self._flush()
            except OSError as error:
                record["error"] = sg._sanitize(str(error))
                result_code, result_error = "record_update_failed", record["error"]
        scan_result = self.daemon.scan_changed_skill(original)
        if scan_result == "ERROR":
            return self._result(item, "failed", "scan_failed", restored=True)
        return self._result(item, "success", result_code, restored=True,
                            **({"error": result_error} if result_error else {}))
