"""桌面人工决定独立保存；完整版本绑定原始文件字节与实际检查策略。"""
import copy
import hashlib
import json
import os
import stat
import threading
from datetime import datetime

import skill_guard as sg


POLICY_VERSION = "desktop-review-v2-asset-coverage"


def _asset_signature_matches(name, raw):
    """扩展名和格式头同时匹配才按素材记录；格式识别不等于素材无风险。"""
    suffix = os.path.splitext(name)[1].lower()
    signatures = {
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
        ".gif": (b"GIF87a", b"GIF89a"),
        ".bmp": (b"BM",), ".ico": (b"\x00\x00\x01\x00",),
        ".flac": (b"fLaC",), ".ogg": (b"OggS",),
        ".ttf": (b"\x00\x01\x00\x00", b"true"), ".otf": (b"OTTO",),
        ".woff": (b"wOFF",), ".woff2": (b"wOF2",),
    }
    if suffix in signatures:
        return raw.startswith(signatures[suffix])
    if suffix in (".webp", ".wav"):
        return raw.startswith(b"RIFF") and raw[8:12] == (
            b"WEBP" if suffix == ".webp" else b"WAVE")
    if suffix == ".mp3":
        return (len(raw) >= 10 and raw.startswith(b"ID3")) or (
            len(raw) >= 3 and raw[0] == 0xff and raw[1] & 0xe0 == 0xe0 and
            raw[1] & 0x06 != 0 and raw[2] & 0xf0 not in (0, 0xf0))
    return False


def _binary_signature(raw):
    """没有空字节的可执行文件和压缩包也不能误当成完整文本检查。"""
    return raw.startswith((b"MZ", b"\x7fELF", b"PK\x03\x04", b"PK\x05\x06",
                           b"\x1f\x8b", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                           b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"))


def effective_rules_text(value):
    """规则入口可为文本或文件；与引擎相同方式读取实际生效文本。"""
    if "\n" not in value and os.path.isfile(value):
        with open(value, encoding="utf-8", errors="ignore") as file:
            return file.read()
    return value


def capture_version(path, rules_text, blocklist_text, *, skill_name=None):
    """返回原始字节摘要及覆盖问题；不沿链接走出目录、不默认为检查完整。

    version 不含绝对目录，隔离后的同一内容可恢复到原位置；人工决定的
    身份另以完整路径绑定。hashes 只包含确实成功读取的原始文件字节。
    """
    path = os.path.normpath(os.path.abspath(path))
    hashes, entries, issues = {}, {}, []
    coverage = {"text_files_checked": 0, "asset_files_hashed": 0,
                "hashed_files": 0, "assets_without_text_check": [],
                "excluded_directories": [],
                "limits": {"text_bytes": sg.MAX_FILE_BYTES,
                           "hash_bytes": sg.MAX_HASH_FILE_BYTES}}

    def relative(file):
        return os.path.relpath(file, path).replace(os.sep, "/")

    def issue(kind, file):
        issues.append(f"{kind}:{relative(file)}")

    if not sg._is_skill_dir(path):
        issues.append("invalid_skill_directory")
    else:
        def walk_error(error):
            issue("directory_unreadable", error.filename or path)

        for directory, dirs, files in os.walk(path, followlinks=False, onerror=walk_error):
            if sg._is_reparse(directory):
                issue("linked_directory", directory)
                dirs[:] = []
                continue
            kept = []
            for name in sorted(dirs):
                child = os.path.join(directory, name)
                if name in sg.EXCLUDED_DIRS:
                    coverage["excluded_directories"].append(relative(child))
                    continue
                if sg._is_reparse(child):
                    issue("linked_directory", child)
                    entries[relative(child)] = "linked_directory"
                else:
                    kept.append(name)
            dirs[:] = kept
            for name in sorted(files):
                file = os.path.join(directory, name)
                rel = relative(file)
                try:
                    before = os.lstat(file)
                    if sg._is_reparse(file):
                        issue("linked_file", file)
                        entries[rel] = "linked_file"
                        continue
                    if not stat.S_ISREG(before.st_mode):
                        issue("unsupported_file", file)
                        entries[rel] = "unsupported_file"
                        continue
                    if before.st_size > sg.MAX_HASH_FILE_BYTES:
                        issue("hash_size_limit", file)
                        entries[rel] = f"size_limit:{before.st_size}:{before.st_mtime_ns}"
                        continue
                    raw = sg._read_regular_file(file, sg.MAX_HASH_FILE_BYTES)
                    if raw is None:
                        issue("file_unreadable", file)
                        entries[rel] = "unreadable"
                        continue
                    after = os.lstat(file)
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != \
                            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                        issue("file_changed_during_capture", file)
                    digest = hashlib.sha256(raw).hexdigest()
                    hashes[rel] = digest
                    entries[rel] = digest
                    text_checked = len(raw) <= sg.MAX_FILE_BYTES and b"\x00" not in raw
                    if text_checked:
                        coverage["text_files_checked"] += 1
                    if _asset_signature_matches(name, raw):
                        # 完整原始哈希仍参与 IOC 和信任版本；非文本素材显式说明覆盖范围。
                        coverage["asset_files_hashed"] += 1
                        if not text_checked:
                            coverage["assets_without_text_check"].append(rel)
                    elif len(raw) > sg.MAX_FILE_BYTES:
                        issue("text_size_limit", file)
                    elif b"\x00" in raw or _binary_signature(raw):
                        issue("binary_not_text_checked", file)
                except OSError:
                    issue("file_unreadable", file)
                    entries[rel] = "unreadable"

    policy = {"version": POLICY_VERSION,
              "skill_name": skill_name if skill_name is not None else os.path.basename(path),
              "rules": effective_rules_text(rules_text), "blocklist": blocklist_text,
              "text_limit": sg.MAX_FILE_BYTES, "hash_limit": sg.MAX_HASH_FILE_BYTES,
              "excluded_directories": sorted(sg.EXCLUDED_DIRS),
              "decode_candidates": sg.MAX_DECODE_CANDIDATES,
              "decode_bytes": sg.MAX_DECODE_TOTAL_BYTES,
              "decode_policy": sg.DECODE_POLICY_VERSION,
              "entropy_threshold": sg.ENTROPY_THRESHOLD,
              "entropy_min_length": sg.ENTROPY_MIN_LEN}
    issues = sorted(set(issues))
    coverage["hashed_files"] = len(hashes)
    coverage["assets_without_text_check"].sort()
    coverage["excluded_directories"].sort()
    content_payload = json.dumps({"files": entries, "issues": issues,
                                  "skill_name": policy["skill_name"]},
                                 ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = json.dumps({"files": entries, "issues": issues, "policy": policy},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"version": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "content_version": hashlib.sha256(content_payload.encode("utf-8")).hexdigest(),
            "complete": not issues, "issues": issues, "hashes": hashes,
            "coverage": coverage}


class ReviewStore:
    """以完整目录与完整版本保存用户决定；写入失败必须反馈给动作执行方。"""

    def __init__(self, path=None):
        self.path = os.fspath(path) if path is not None else os.path.join(
            sg.GUARD_DIR, "review_decisions.json")
        self._lock = threading.RLock()
        self._records = {}
        try:
            with open(self.path, encoding="utf-8") as file:
                data = json.load(file)
            rows = data.get("decisions", {}) if isinstance(data, dict) else {}
            if isinstance(rows, dict):
                for row in rows.values():
                    if isinstance(row, dict) and all(isinstance(row.get(key), str)
                            for key in ("path", "version", "decision")) and \
                            row["decision"] in ("reviewed", "trusted"):
                        self._records[self._key(row["path"])] = row
        except (OSError, ValueError):
            pass

    @staticmethod
    def _key(path):
        return sg._canon_path(os.path.abspath(os.fspath(path)))

    def decision_for(self, path, version):
        """只有目录和版本均匹配才返回已处理；旧版本仍保留可核对记录。"""
        with self._lock:
            row = self._records.get(self._key(path))
            return row["decision"] if row and row["version"] == version else "pending"

    decision = decision_for

    def invalidate_changed(self, path, current_version):
        """已观察到新的完整版本时永久撤销旧信任，恢复旧文件也不自动复活。"""
        with self._lock:
            old = self._records.get(self._key(path))
            if not old or old["decision"] != "trusted" or old["version"] == current_version:
                return False
            return self.revoke(path, old["version"])

    def record(self, path, version, action="review", *, complete=True):
        """调用方先检查稳定版本；不完整检查不允许建立信任例外。"""
        if action not in ("review", "trust") or not isinstance(version, str) or not version:
            raise ValueError("invalid review action or version")
        if action == "trust" and not complete:
            raise ValueError("cannot trust an incomplete check")
        with self._lock:
            key = self._key(path)
            old = self._records.get(key)
            decision = "trusted" if action == "trust" or (old and
                old["version"] == version and old["decision"] == "trusted") else "reviewed"
            row = {"path": os.path.abspath(os.fspath(path)), "version": version,
                   "decision": decision, "action": action,
                   "at": datetime.now().isoformat(timespec="seconds")}
            updated = dict(self._records)
            updated[key] = row
            self._save(updated)
            self._records = updated
            return copy.deepcopy(row)

    def revoke(self, path, version=None):
        """撤销匹配的信任，保留已经查看的记录；不会影响同名其它目录。"""
        with self._lock:
            key = self._key(path)
            old = self._records.get(key)
            if not old or old["decision"] != "trusted" or \
                    (version is not None and version != old["version"]):
                return False
            updated = dict(self._records)
            updated[key] = {**old, "decision": "reviewed", "action": "revoke_trust",
                            "at": datetime.now().isoformat(timespec="seconds")}
            self._save(updated)
            self._records = updated
            return True

    def _save(self, records):
        sg._atomic_write(self.path, lambda file: json.dump(
            {"version": 1, "decisions": records}, file, ensure_ascii=False, indent=2))
