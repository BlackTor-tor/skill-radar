"""Read installed skill metadata and join it with observed usage, offline."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import os
import sys

import skill_guard as sg


def parse_time(value):
    """Compare actual UTC instants; legacy timestamps without a zone use UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def latest_time(*values):
    """Keep the original timestamp string for the latest valid instant."""
    valid = [(parse_time(value), value) for value in values]
    valid = [(instant, value) for instant, value in valid if instant is not None]
    return max(valid, key=lambda item: item[0])[1] if valid else ""


def _file_timestamp(value):
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def file_times(skill_path):
    """SKILL.md creation estimates installation; modification is its update time."""
    result = {"installed_at": None, "updated_at": None, "install_time_source": None}
    path = os.fspath(skill_path)
    if os.path.basename(path).lower() != "skill.md":
        path = os.path.join(path, "SKILL.md")
    try:
        metadata = os.stat(path)
    except (OSError, ValueError, TypeError):
        return result
    result["updated_at"] = _file_timestamp(getattr(metadata, "st_mtime", None))
    created = getattr(metadata, "st_birthtime", None)
    source = "skill_md_birthtime"
    if created is None and sys.platform == "win32":
        created = getattr(metadata, "st_ctime", None)
        source = "skill_md_windows_ctime"
    result["installed_at"] = _file_timestamp(created)
    if result["installed_at"] is not None:
        result["install_time_source"] = source
    return result


def collect_inventory(roots):
    """Enumerate complete registered pools, without loading or running skill files."""
    paths = []
    for root in roots or []:
        path = root.get("path") if isinstance(root, dict) else root
        if isinstance(path, (str, os.PathLike)):
            # An explicitly registered alias names its resolved pool. Resolve only
            # this entry; the guard walker still rejects links nested inside it.
            paths.append(os.path.realpath(os.path.normpath(os.path.expanduser(os.fspath(path)))))
    rows, seen = [], set()
    for path in sg.iter_skill_dirs(paths):
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": os.path.basename(os.path.normpath(path)), "path": path,
                     **file_times(path)})
    return sorted(rows, key=lambda row: (row["name"].casefold(), row["path"]))


def _count(value):
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError, OverflowError):
        return 0


def _coverage(status):
    """Missing or partial history cannot establish that a skill was never loaded."""
    status = status if isinstance(status, dict) else {}
    notes = []
    if status.get("phase") != "ready" or status.get("scanning"):
        notes.append("会话日志尚未完成采集")
    if status.get("errors"):
        notes.append("部分会话日志读取失败")
    if _count(status.get("files_scanned")) == 0:
        notes.append("没有可用的已扫描会话日志")
    roots = status.get("roots") or []
    available = [root for root in roots if isinstance(root, dict) and root.get("exists") is True]
    if not available:
        notes.append("会话日志目录未配置或全部目录不可用")
    if notes:
        return False, "；".join(notes) + "。零调用技能显示为记录不足；已知调用仅反映已采集日志。"
    partial = "部分会话日志目录不可用；" if len(available) < len(roots) else ""
    return True, partial + "统计仅覆盖已采集日志；无调用记录不代表技能从未使用，缺失日志和不支持的来源无法还原。"


def merge_usage(rows, inventory, status, now=None):
    """Classify each installation, retaining shared name counters only once in totals."""
    current = parse_time(now) or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=30)
    complete, note = _coverage(status)
    by_name = {}
    for original in rows or []:
        if not isinstance(original, dict) or not isinstance(original.get("name"), str):
            continue
        name = original["name"]
        previous = by_name.setdefault(name, {"name": name, "last": "", "total": 0})
        for field in ("codex", "zcode", "claude", "marker", "atime", "total"):
            previous[field] = max(_count(previous.get(field)), _count(original.get(field)))
        previous["last"] = latest_time(previous["last"], original.get("last"),
                                        original.get("last_tool_use"), original.get("last_marker"))
        previous["total"] = max(previous["total"], sum(previous.get(field, 0)
                                for field in ("codex", "zcode", "claude", "marker")))
    inventory = [row for row in inventory or [] if isinstance(row, dict) and isinstance(row.get("name"), str)]
    names = Counter(row["name"] for row in inventory)
    installed_rows, groups = [], {"never": [], "inactive": [], "unknown": []}
    totals = {"never": 0, "inactive": 0, "unknown": 0, "active": 0}
    for installation in inventory:
        name = installation["name"]
        usage = by_name.get(name, {})
        row = {**installation, "name": name, "path": installation.get("path", ""),
               **{field: _count(usage.get(field)) for field in ("total", "codex", "zcode", "claude", "marker", "atime")},
               "last": usage.get("last", ""), "shared_name": names[name] > 1}
        for field in ("installed_at", "updated_at", "install_time_source"):
            row.setdefault(field, None)
        last = parse_time(row["last"])
        if row["total"] == 0:
            state = "never" if complete else "unknown"
        elif last is None or last > current:
            state = "unknown"
        else:
            state = "inactive" if last < cutoff else "active"
        row["usage_state"] = state
        installed = parse_time(row["installed_at"])
        row["observing"] = installed is not None and cutoff < installed <= current
        totals[state] += 1
        installed_rows.append(row)
        if state in groups:
            groups[state].append(row)
    installed_rows.sort(key=lambda row: (row["name"].casefold(), row["path"]))
    for group in groups.values():
        group.sort(key=lambda row: (row["name"].casefold(), row["path"]))
    return {"installed_rows": installed_rows, "idle_groups": groups,
            "inventory_summary": {"total_installed": len(installed_rows), **totals,
                                  "total_invocations": sum(by_name.get(name, {}).get("total", 0) for name in names),
                                  "coverage_complete": complete, "coverage_note": note}}
