#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill-radar: cross-agent skill usage monitor
============================================
Counts how often your AI agents actually load/invoke the skills you installed,
across every agent on the machine. Zero dependencies.

Four counting layers:

  1. structured (codex) literal SKILL.md read commands in transcript tool calls
  2. structured (zcode/claude) Skill calls and literal skill-file loads
  3. marker (legacy)   optional universal marker scanner; CLI keeps it only for
     unsupported agents so structured loads are not double-counted
  4. atime (fallback)   SKILL.md last-access-time changes (filesystem dependent)

All layers scan incrementally (per-file byte offsets): the first run does a full
historical scan, later runs take seconds.

Counters/state: ~/.skill-radar/skill_usage{,_state}.json (SKILL_RADAR_DATA_DIR override)

Usage:
    python skill_monitor.py              # incremental scan + report
    python skill_monitor.py --top 20     # show top N
    python skill_monitor.py --watch 60   # live mode, rescan every N seconds
    python skill_monitor.py --json       # dump raw counters
    python skill_monitor.py --reset      # wipe state and rescan from scratch
    python skill_monitor.py --log-root /absolute/history --log-source auto
"""
import argparse
import ast
import glob
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime

from skill_inventory import latest_time, parse_time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
ZCODE_ROLLOUT = os.path.join(os.environ.get("ZCODE_HOME") or os.path.join(HOME, ".zcode"), "cli", "rollout")
CLAUDE_PROJECTS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "projects")
CODEX_SESSIONS = os.path.join(os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex"), "sessions")
CURSOR_DIR = os.path.join(HOME, ".cursor")
AGENTS_SKILLS = os.path.join(HOME, ".agents", "skills")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
def storage_paths(data_dir=None):
    """Persistent counters never live in a frozen executable's extraction folder."""
    root = data_dir or os.environ.get("SKILL_RADAR_DATA_DIR") or os.path.join(HOME, ".skill-radar")
    root = os.path.abspath(os.path.expanduser(root))
    return os.path.join(root, "skill_usage_state.json"), os.path.join(root, "skill_usage.json")


STATE_FILE, DATA_FILE = storage_paths()

RECENT_IDS_LIMIT = 50000
CHUNK = 4 * 1024 * 1024
SENTINEL = b"skill-marker:"
# A valid marker name is shorter than 120 Unicode characters (up to 4 bytes each).
MARKER_LOOKBEHIND = len(SENTINEL) + 120 * 4 + 8


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _checkpoint(state, data):
    """Commit offsets and counters together; the public counters file is an export."""
    state["counters"] = data
    _save(STATE_FILE, state)
    _save(DATA_FILE, data)


def _skill(data, name):
    return data["skills"].setdefault(
        name, {"zcode": 0, "codex": 0, "claude": 0, "marker": 0, "atime": 0, "marker_sources": {},
               "last_tool_use": "", "last_marker": ""})


# -------------------------------------------- structured agent transcript logs

def _skill_path_name(path):
    """Derive a skill identifier from a literal SKILL.md path on either OS."""
    if not isinstance(path, str):
        return None
    normalized = path.strip(" \"'").replace("\\", "/")
    if any(char in normalized for char in "$%{}<>\r\n"):
        return None
    parts = normalized.rstrip("/").split("/")
    if len(parts) < 2 or parts[-1].lower() != "skill.md":
        return None
    name = parts[-2]
    return name if name and not any(c in name for c in "$%{}<>\r\n") else None


def _shell_read_skills(command):
    """Recognize literal file-read operands, never catalog/search/echo text.

    This is a deliberately conservative static parser; no command is executed.
    Unresolved variables and dynamically assembled paths are left uncounted.
    """
    if not isinstance(command, str):
        return []
    # Split shell statements while respecting quoted paths and quoted echo text.
    segments, current, quote = [], [], None
    for char in command:
        if char in "\"'":
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        if quote is None and char in ";|\n&":
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
    segments.append("".join(current))
    names = []
    for segment in segments:
        try:
            tokens = shlex.split(segment.strip(), posix=False)
        except ValueError:
            continue
        if not tokens:
            continue
        reader = tokens[0].lower()
        if reader not in ("get-content", "gc", "cat", "type", "sed", "head", "tail"):
            continue
        if reader == "sed" and any(token == "-i" or token.startswith("-i") or token.startswith("--in-place") for token in tokens[1:]):
            continue
        for token in tokens[1:]:
            name = _skill_path_name(token)
            if name and name not in names:
                names.append(name)
    # Parse Python code as syntax rather than searching strings: printed examples
    # and comments are not reads. Only literal Path(...).read_text/read_bytes.
    python_code = None
    if re.match(r"^\s*(?:python(?:3|\.exe)?|py)(?:\s|$)", command, re.I):
        try:
            tokens = shlex.split(command, posix=True)
            if "-c" in tokens:
                python_code = tokens[tokens.index("-c") + 1]
        except (ValueError, IndexError):
            pass
    heredoc = re.search(r"(?:^|[;|\n])\s*python(?:3)?\s+-\s*<<\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\1(?:\n|$)", command, re.S)
    if heredoc:
        python_code = heredoc.group(2)
    powershell = re.search(r"@['\"]\s*\n(.*?)\n['\"]@\s*\|\s*(?:python(?:3|\.exe)?|py)\s*-", command, re.S | re.I)
    if powershell:
        python_code = powershell.group(1)
    if python_code:
        try:
            tree = ast.parse(python_code)
        except SyntaxError:
            tree = None
        if tree:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr not in ("read_text", "read_bytes"):
                    continue
                operand = node.func.value
                if not isinstance(operand, ast.Call) or not isinstance(operand.func, ast.Name) or operand.func.id != "Path" or not operand.args:
                    continue
                literal = operand.args[0]
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                    name = _skill_path_name(literal.value)
                    if name and name not in names:
                        names.append(name)
    return names


def _js_commands(script):
    """Read only static cmd strings from tools.exec_command calls in exec input."""
    if not isinstance(script, str):
        return []
    # Remove comments and quoted example strings while preserving offsets.
    tokens = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`", re.S)
    executable = list(script)
    for token in tokens.finditer(script):
        for index in range(token.start(), token.end()):
            executable[index] = " "
    executable = "".join(executable)
    commands = []
    pattern = r"(?:tools\.)?exec_command\s*\(\s*\{[^{}]*?\bcmd\s*:\s*((?:\"(?:\\.|[^\"\\])*\")|(?:'(?:\\.|[^'\\])*')|(?:`[^`]*`))"
    for match in re.finditer(pattern, script, re.S):
        if not executable[match.start():match.start() + 5].strip():
            continue
        literal = match.group(1)
        try:
            if literal.startswith('"'):
                command = json.loads(literal)
            elif literal.startswith("`"):
                if "${" in literal:
                    continue
                command = literal[1:-1]
            else:
                command = literal[1:-1].replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
        except ValueError:
            continue
        commands.append(command)
    return commands


def _tool_skill_names(name, arguments):
    if name in ("exec", "functions.exec"):
        return list(dict.fromkeys(skill for cmd in _js_commands(arguments)
                                  for skill in _shell_read_skills(cmd)))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return []
    if not isinstance(arguments, dict):
        return []
    if name in ("Skill", "skill"):
        value = arguments.get("skill") or arguments.get("args")
        return [str(value)] if value else []
    if name in ("Read", "read_file"):
        value = _skill_path_name(arguments.get("file_path") or arguments.get("path"))
        return [value] if value else []
    if name in ("exec_command", "functions.exec_command", "Bash", "shell_command", "shell"):
        command = arguments.get("cmd") or arguments.get("command")
        return _shell_read_skills(command)
    return []


def _record_calls(record, source):
    """Return source, session, timestamp, and observed structured tool calls."""
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    response = record.get("response")
    message = record.get("message")
    if source in ("codex", "auto") and record.get("type") == "response_item" \
            and isinstance(payload, dict) and payload.get("type") in ("function_call", "custom_tool_call"):
        return "codex", "", record.get("timestamp"), [
            {"id": payload.get("call_id") or payload.get("id"), "name": payload.get("name"),
             "input": payload.get("arguments", payload.get("input"))}]
    if source in ("zcode", "auto") and record.get("type") == "tool_call_scheduled" \
            and isinstance(payload, dict):
        return "zcode", record.get("sessionId"), record.get("timestamp"), [
            {"id": payload.get("toolCallId"), "name": payload.get("toolName"), "input": payload.get("input")}]
    if source in ("zcode", "auto") and isinstance(response, dict) \
            and isinstance(response.get("toolCalls"), list):
        return "zcode", record.get("sessionId"), record.get("completedAt"), response["toolCalls"]
    if source in ("claude", "auto") and isinstance(message, dict) \
            and isinstance(message.get("content"), list):
        calls = [c for c in message["content"] if isinstance(c, dict) and c.get("type") == "tool_use"]
        return "claude", record.get("sessionId"), record.get("timestamp"), calls
    return None


def scan_history(state, data, files, source="auto", stats=None, on_progress=None, should_stop=None):
    """Incremental, bounded-memory collection of structured agent tool calls.

    Offsets are committed after complete JSONL lines. Tool IDs deduplicate replay,
    relocated copies, and overlapping selected roots without reading skill files.
    """
    offsets = state.setdefault("history_offsets", {})
    ids = state.setdefault("history_ids", {})
    file_sessions = state.setdefault("history_sessions", {})
    legacy_offsets = {_path: offset for path, offset in state.get("offsets", {}).items()
                      for _path in [os.path.realpath(path)]}
    legacy_zcode_ids = set(state.get("recent_ids", []))
    legacy_claude_ids = {os.path.realpath(path): set(values) for path, values in state.get("claude_ids", {}).items()}
    new = 0
    for filename in dict.fromkeys(os.path.realpath(os.fspath(f)) for f in files):
        if should_stop and should_stop():
            break
        try:
            size = os.path.getsize(filename)
            off = offsets.get(filename, legacy_offsets.get(filename, 0))
            if off > size:
                off = 0
            if stats is not None:
                stats["files_scanned"] = stats.get("files_scanned", 0) + 1
            if off == size:
                if on_progress:
                    on_progress()
                continue
            with open(filename, "rb") as log:
                log.seek(off)
                while True:
                    if should_stop and should_stop():
                        break
                    raw = log.readline()
                    if not raw or not raw.endswith(b"\n"):
                        break
                    offsets[filename] = log.tell()
                    if not any(token in raw for token in (b"SKILL.md", b'"Skill"', b'"skill"', b'"session_meta"')):
                        continue
                    try:
                        record = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
                        file_sessions[filename] = record["payload"].get("id") or filename
                    calls = _record_calls(record, source)
                    if not calls:
                        continue
                    agent, session, timestamp, tool_calls = calls
                    session = str(session or file_sessions.get(filename) or filename)
                    ts = timestamp if isinstance(timestamp, str) else ""
                    explicit = set(skill for c in tool_calls if isinstance(c, dict) and c.get("name") in ("Skill", "skill")
                                   for skill in _tool_skill_names(c.get("name"), c.get("input")))
                    for index, call in enumerate(tool_calls):
                        if not isinstance(call, dict):
                            continue
                        names = _tool_skill_names(call.get("name"), call.get("input"))
                        if call.get("name") not in ("Skill", "skill"):
                            names = [n for n in names if n not in explicit]
                        call_id = call.get("id") or f"{filename}:{offsets[filename]}:{index}"
                        if agent == "zcode" and f"z|{session}|{call_id}" in legacy_zcode_ids:
                            continue
                        if agent == "claude" and str(call_id) in legacy_claude_ids.get(filename, set()):
                            continue
                        for name in names:
                            key = f"{agent}|{session}|{call_id}|{name}"
                            if key in ids:
                                continue
                            ids[key] = True
                            entry = _skill(data, name)
                            entry[agent] = entry.get(agent, 0) + 1
                            if parse_time(ts) is not None:
                                entry["last_tool_use"] = latest_time(entry.get("last_tool_use", ""), ts)
                                daily = data.setdefault("daily", {}).setdefault(ts[:10], {})
                                daily[agent] = daily.get(agent, 0) + 1
                            new += 1
                            if stats is not None:
                                stats["new_records"] = stats.get("new_records", 0) + 1
            if on_progress:
                on_progress()
        except OSError as error:
            if stats is not None:
                stats.setdefault("errors", []).append({"path": filename, "error": type(error).__name__})
    return new


def _history_files(roots):
    return [path for root in roots for path in
            glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)]


def scan_zcode(state, data, roots=None):
    """Structured Skill invocation and literal skill-file load commands in zcode."""
    return scan_history(state, data, _history_files(roots or [ZCODE_ROLLOUT, os.path.join(os.path.dirname(ZCODE_ROLLOUT), "agents")]), "zcode")


def scan_codex(state, data, roots=None):
    """Codex function calls and exec wrappers that load a literal SKILL.md."""
    return scan_history(state, data, _history_files(roots or [CODEX_SESSIONS]), "codex")


def scan_claude(state, data, roots=None):
    """Claude Code structured Skill invocations and literal skill-file reads."""
    return scan_history(state, data, _history_files(roots or [CLAUDE_PROJECTS]), "claude")


# ------------------------------------------------------- layer 3: marker grep
MARKER_SOURCES = [
    ("codex", CODEX_SESSIONS, ("**", "*.jsonl")),
    ("claude", CLAUDE_PROJECTS, ("**", "*.jsonl")),
    ("cursor", CURSOR_DIR, ("**", "*.json")),
    ("zcode", ZCODE_ROLLOUT, ("**", "*.jsonl")),
]


def _marker_hits(buf):
    """Return all skill names whose marker appears in this byte chunk."""
    names = []
    pos = 0
    while True:
        i = buf.find(SENTINEL, pos)
        if i == -1:
            break
        end = buf.find(b"-->", i)
        if end == -1:  # truncated across chunks; next chunk rescans it
            break
        seg = buf[i + len(SENTINEL): end].decode("utf-8", errors="ignore").strip()
        if seg and len(seg) < 120 and not any(
                ch in "<>" or ord(ch) < 32 or ord(ch) == 127 for ch in seg):
            names.append(seg)
        pos = end + 3
    return names


def scan_markers(state, data, sources=None):
    """Universal layer: grep any agent's session logs for injected markers.

    Counts one "session hit" per (transcript file, skill) so replayed history
    inside one log never inflates counts.
    """
    offsets = state.setdefault("marker_offsets", {})
    file_skills = state.setdefault("file_skills", {})
    new = 0
    for src, root, pattern in (MARKER_SOURCES if sources is None else sources):
        if not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, *pattern), recursive=True):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            off = offsets.get(path, 0)
            if off > size:
                off = 0
                file_skills.pop(path, None)
            if off == size:
                continue
            counted = set(file_skills.get(path, []))
            try:
                with open(path, "rb") as f:
                    # Re-read a bounded overlap to finish markers split across scans.
                    f.seek(max(0, off - MARKER_LOOKBEHIND))
                    carry = b""
                    while True:
                        chunk = f.read(CHUNK)
                        if not chunk:
                            offsets[path] = f.tell()
                            break
                        tail = carry + chunk
                        for name in _marker_hits(tail):
                            if name in counted:
                                continue
                            counted.add(name)
                            s = _skill(data, name)
                            s["marker"] += 1
                            s["marker_sources"][src] = s["marker_sources"].get(src, 0) + 1
                            new += 1
                        carry = tail[-MARKER_LOOKBEHIND:]
            except OSError:
                continue
            file_skills[path] = sorted(counted)
    return new


# ---------------------------------------------------------- layer 4: atime
def scan_atime(state, data):
    """Fallback layer: SKILL.md atime changes = read by some tool (coarse)."""
    base = state.setdefault("atime_base", {})
    new = 0
    if not os.path.isdir(AGENTS_SKILLS):
        return 0
    for entry in os.listdir(AGENTS_SKILLS):
        skill_md = os.path.join(AGENTS_SKILLS, entry, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        try:
            at = os.stat(skill_md).st_atime
        except OSError:
            continue
        name = entry
        prev = base.get(skill_md)
        if prev is None:
            base[skill_md] = at
            continue
        if at > prev + 2:
            s = _skill(data, name)
            s["atime"] += 1
            new += 1
        base[skill_md] = at
    return new


# ----------------------------------------------------------------- report
def report(data, top=15):
    skills = data.get("skills", {})
    meta = data.get("meta", {})
    print("=" * 76)
    print(f"skill-radar: skill usage report   last scan: {meta.get('last_scan', '?')}")
    print("=" * 76)
    if skills:
        rows = []
        for n, s in skills.items():
            total = s.get("codex", 0) + s["zcode"] + s["claude"] + s["marker"]
            rows.append((total, n, s))
        rows.sort(key=lambda r: (-r[0], r[1]))
        print(f"\n{'skill':<30}{'codex':>6}{'zcode':>6}{'claude':>7}{'session':>9}{'atime':>7}   last used")
        for total, n, s in rows[:top]:
            last = (s.get("last_tool_use") or s.get("last_marker") or "")[:10]
            print(f"{n[:28]:<30}{s.get('codex', 0):>6}{s['zcode']:>6}{s['claude']:>7}{s['marker']:>9}{s['atime']:>7}   {last}")
        print(f"\n{len(rows)} skill(s) with records;  invocations: codex={sum(s.get('codex', 0) for _,_,s in rows)}, zcode={sum(s['zcode'] for _,_,s in rows)},"
              f" claude={sum(s['claude'] for _,_,s in rows)};"
              f" session hits={sum(s['marker'] for _,_,s in rows)}; atime reads={sum(s['atime'] for _,_,s in rows)}")
        src_total = {}
        for _, _, s in rows:
            for k, v in s.get("marker_sources", {}).items():
                src_total[k] = src_total.get(k, 0) + v
        if src_total:
            print("session-hit sources:", ", ".join(f"{k}={v}" for k, v in sorted(src_total.items(), key=lambda x: -x[1])))
    else:
        print("(no records yet)")
    print(f"\ncounters: {DATA_FILE}")


def main():
    ap = argparse.ArgumentParser(description="skill-radar: cross-agent skill usage monitor")
    ap.add_argument("--top", type=int, default=15, help="rows to show in the report")
    ap.add_argument("--json", action="store_true", help="dump raw counters as JSON")
    ap.add_argument("--watch", type=int, metavar="SEC", help="live mode: rescan every SEC seconds")
    ap.add_argument("--log-root", action="append", default=[], metavar="PATH",
                    help="also scan this session-log directory or drive root recursively (repeatable)")
    ap.add_argument("--log-source", choices=("auto", "codex", "zcode", "claude"), default="auto",
                    help="schema for --log-root; auto detects supported structured session records")
    ap.add_argument("--reset", action="store_true", help="wipe state and do a full rescan")
    args = ap.parse_args()
    for root in args.log_root:
        if not os.path.isabs(root) or not os.path.isdir(root):
            ap.error("--log-root must name an existing absolute directory or drive root")

    if args.reset:
        for p in (STATE_FILE, DATA_FILE):
            if os.path.exists(p):
                os.remove(p)
        print("state cleared, full rescan…")

    state = _load(STATE_FILE, {})
    # Recover matching counters if the previous export was interrupted after
    # committing offsets; using an older DATA_FILE here would lose events.
    data = state.get("counters") or _load(DATA_FILE, {})
    # v1 -> v2 schema migration (v1 stored flat {name: zcode_count})
    if data and data.get("skills") and isinstance(
            next(iter(data["skills"].values()), None), int):
        data = {"skills": {k: {"zcode": v, "claude": 0, "marker": 0, "atime": 0,
                               "marker_sources": {}, "last_tool_use": "", "last_marker": ""}
                           for k, v in data["skills"].items()}}
    data.setdefault("skills", {})
    data.setdefault("daily", {})

    codex_roots = [root for source, root, _pattern in MARKER_SOURCES if source == "codex"]
    marker_roots = [entry for entry in MARKER_SOURCES if entry[0] not in ("codex", "zcode", "claude")]
    last_custom_status = {}

    def custom_scan():
        nonlocal last_custom_status
        before = {source: sum(entry.get(source, 0) for entry in data["skills"].values() if isinstance(entry, dict))
                  for source in ("codex", "zcode", "claude")}
        if args.log_root:
            # Same conservative walk as the client, without importing tray code.
            files = []
            errors = []
            for root in args.log_root:
                for directory, dirs, names in os.walk(root, followlinks=False,
                        onerror=lambda error: errors.append({"path": error.filename or root,
                                                             "error": type(error).__name__})):
                    dirs[:] = [name for name in dirs if name not in
                               (".git", "node_modules", "__pycache__", "$Recycle.Bin", "System Volume Information")
                               and not os.path.islink(os.path.join(directory, name))]
                    files.extend(os.path.join(directory, name) for name in names if name.lower().endswith(".jsonl"))
            stats = {"errors": [], "files_scanned": 0, "new_records": 0}
            scan_history(state, data, files, args.log_source, stats)
            all_errors = errors + stats["errors"]
            last_custom_status = {**stats, "errors": all_errors,
                "phase": "error" if all_errors else "ready", "scanning": False,
                "roots": [{"source": args.log_source, "path": os.path.realpath(root),
                           "exists": os.path.isdir(root)} for root in args.log_root]}
            if errors or stats["errors"]:
                print(f"log scan: {len(errors) + len(stats['errors'])} inaccessible file/directory entries", file=sys.stderr)
        return {source: sum(entry.get(source, 0) for entry in data["skills"].values() if isinstance(entry, dict)) - before[source]
                for source in before}

    def scan_metadata():
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        # Only explicit scans have complete file/error accounting here. Legacy default
        # source collectors cannot establish coverage for zero-invocation skills.
        status = last_custom_status or {"phase": "idle", "scanning": False, "files_scanned": 0,
                                       "errors": [], "new_records": 0, "roots": []}
        return {"last_scan": timestamp, "usage_status": {**status, "last_scan": timestamp}}

    if args.watch:
        print(f"watching (rescan every {args.watch}s, Ctrl+C to exit)…")
        while True:
            extra = custom_scan()
            x = (scan_codex(state, data, roots=codex_roots) if codex_roots else 0) + extra["codex"]
            z = scan_zcode(state, data) + extra["zcode"]
            c = scan_claude(state, data) + extra["claude"]
            m = scan_markers(state, data, sources=marker_roots)
            a = scan_atime(state, data)
            data["meta"] = scan_metadata()
            _checkpoint(state, data)
            if x or z or c or m or a:
                print(f"[{datetime.now():%H:%M:%S}] codex+{x} zcode+{z} claude+{c} sessions+{m} atime+{a}")
            time.sleep(args.watch)
    else:
        first = not (state.get("history_offsets") or state.get("offsets"))
        if first:
            print("first run: full historical scan (may take a few minutes)…")
        t0 = time.time()
        extra = custom_scan()
        x = (scan_codex(state, data, roots=codex_roots) if codex_roots else 0) + extra["codex"]
        z = scan_zcode(state, data) + extra["zcode"]
        c = scan_claude(state, data) + extra["claude"]
        m = scan_markers(state, data, sources=marker_roots)
        a = scan_atime(state, data)
        data["meta"] = scan_metadata()
        _checkpoint(state, data)
        print(f"scan done: codex+{x} zcode+{z} claude+{c} sessions+{m} atime+{a}"
              f" ({time.time() - t0:.1f}s)")
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        report(data, args.top)


if __name__ == "__main__":
    main()
