#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill-radar: cross-agent skill usage monitor
============================================
Counts how often your AI agents actually load/invoke the skills you installed,
across every agent on the machine. Zero dependencies.

Four counting layers:

  1. precise (zcode)    parse ~/.zcode/cli/rollout response.toolCalls
  2. precise (claude)   parse ~/.claude/projects tool_use(Skill) events
  3. marker (universal) grep session logs of ANY agent (codex, cursor, ...) for
     the `<!-- skill-marker:NAME -->` injected by apply_skill_markers.py into
     every SKILL.md. One count per (transcript file, skill) = a "session hit".
  4. atime (fallback)   SKILL.md last-access-time changes (filesystem dependent)

All layers scan incrementally (per-file byte offsets): the first run does a full
historical scan, later runs take seconds.

Counters: skill_usage.json   State: skill_usage_state.json

Usage:
    python skill_monitor.py              # incremental scan + report
    python skill_monitor.py --top 20     # show top N
    python skill_monitor.py --watch 60   # live mode, rescan every N seconds
    python skill_monitor.py --json       # dump raw counters
    python skill_monitor.py --reset      # wipe state and rescan from scratch
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
ZCODE_ROLLOUT = os.path.join(HOME, ".zcode", "cli", "rollout")
CLAUDE_PROJECTS = os.path.join(HOME, ".claude", "projects")
CODEX_SESSIONS = os.path.join(HOME, ".codex", "sessions")
CURSOR_DIR = os.path.join(HOME, ".cursor")
AGENTS_SKILLS = os.path.join(HOME, ".agents", "skills")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "skill_usage_state.json")
DATA_FILE = os.path.join(BASE_DIR, "skill_usage.json")

RECENT_IDS_LIMIT = 50000
CHUNK = 4 * 1024 * 1024
SENTINEL = b"skill-marker:"


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _skill(data, name):
    return data["skills"].setdefault(
        name, {"zcode": 0, "claude": 0, "marker": 0, "atime": 0, "marker_sources": {},
               "last_tool_use": "", "last_marker": ""})


# ------------------------------------------------------------- layer 1: zcode
def scan_zcode(state, data):
    """Exact Skill-tool invocations from zcode model-I/O rollout logs."""
    offsets = state.setdefault("offsets", {})
    seen = set(state.get("recent_ids", []))
    new = 0
    for path in glob.glob(os.path.join(ZCODE_ROLLOUT, "*.jsonl")):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        off = offsets.get(path, 0)
        if off > size:  # truncated/recreated file
            off = 0
        if off == size:
            continue
        try:
            with open(path, "rb") as f:
                f.seek(off)
                buf = f.read()
        except OSError:
            continue
        last = buf.rfind(b"\n")
        if last == -1:
            continue
        offsets[path] = off + last + 1
        for raw in buf[: last + 1].split(b"\n"):
            if b'"Skill"' not in raw and b"SKILL.md" not in raw:
                continue  # cheap pre-filter
            try:
                rec = json.loads(raw.decode("utf-8", errors="ignore"))
            except ValueError:
                continue
            session = rec.get("sessionId", "?")
            ts = (rec.get("completedAt") or "")[:19]
            for tc in (rec.get("response") or {}).get("toolCalls") or []:
                if not isinstance(tc, dict):
                    continue
                key = f"z|{session}|{tc.get('id')}"
                if key in seen:
                    continue
                name = tc.get("name")
                inp = tc.get("input") or {}
                if name == "Skill":
                    seen.add(key)
                    sname = str(inp.get("skill") or inp.get("args") or "?")
                    s = _skill(data, sname)
                    s["zcode"] += 1
                    if ts:
                        s["last_tool_use"] = max(s["last_tool_use"], ts)
                    new += 1
                elif name == "Read" and "SKILL.md" in str(inp.get("file_path", "")):
                    seen.add(key)
    state["recent_ids"] = list(seen)[-RECENT_IDS_LIMIT:]
    return new


# ------------------------------------------------------------ layer 2: claude
def scan_claude(state, data):
    """Exact Skill-tool invocations from Claude Code session transcripts."""
    offsets = state.setdefault("offsets", {})
    new = 0
    for path in glob.glob(os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl")):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        off = offsets.get(path, 0)
        if off > size:
            off = 0
        if off == size:
            continue
        try:
            with open(path, "rb") as f:
                f.seek(off)
                buf = f.read()
        except OSError:
            continue
        last = buf.rfind(b"\n")
        if last == -1:
            continue
        offsets[path] = off + last + 1
        for raw in buf[: last + 1].split(b"\n"):
            if b'"Skill"' not in raw:
                continue
            try:
                rec = json.loads(raw.decode("utf-8", errors="ignore"))
            except ValueError:
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            ts = (rec.get("timestamp") or "")[:19]
            for c in content:
                if (isinstance(c, dict) and c.get("type") == "tool_use"
                        and c.get("name") == "Skill"):
                    sname = str((c.get("input") or {}).get("skill") or "?")
                    s = _skill(data, sname)
                    s["claude"] += 1
                    if ts:
                        s["last_tool_use"] = max(s["last_tool_use"], ts)
                    new += 1
    return new


# ------------------------------------------------------- layer 3: marker grep
MARKER_SOURCES = [
    ("codex", CODEX_SESSIONS, ("**", "*.jsonl")),
    ("claude", CLAUDE_PROJECTS, ("*", "*.jsonl")),
    ("cursor", CURSOR_DIR, ("**", "*.json")),
    ("zcode", ZCODE_ROLLOUT, ("*", "*.jsonl")),
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
        if seg and len(seg) < 120:
            names.append(seg)
        pos = end + 3
    return names


def scan_markers(state, data):
    """Universal layer: grep any agent's session logs for injected markers.

    Counts one "session hit" per (transcript file, skill) so replayed history
    inside one log never inflates counts.
    """
    offsets = state.setdefault("marker_offsets", {})
    file_skills = state.setdefault("file_skills", {})
    new = 0
    for src, root, pattern in MARKER_SOURCES:
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
                    f.seek(off)
                    tail = f.read(CHUNK)
                    while tail:
                        keep = tail[-(len(SENTINEL) + 8):]  # sentinel overlap across chunks
                        for name in _marker_hits(tail):
                            if name in counted:
                                continue
                            counted.add(name)
                            s = _skill(data, name)
                            s["marker"] += 1
                            s["marker_sources"][src] = s["marker_sources"].get(src, 0) + 1
                            new += 1
                        if len(tail) < CHUNK:
                            offsets[path] = size
                            break
                        next_off = off + len(tail) - len(keep)
                        f.seek(next_off)
                        off = next_off
                        tail = keep + f.read(CHUNK)
            except OSError:
                continue
            file_skills[path] = sorted(counted)[-50:]
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
        if name not in data["skills"] and name not in base:
            base[skill_md] = at  # first sighting: baseline only, no count
            continue
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
            total = s["zcode"] + s["claude"] + s["marker"]
            rows.append((total, n, s))
        rows.sort(key=lambda r: (-r[0], r[1]))
        print(f"\n{'skill':<36}{'zcode':>6}{'claude':>7}{'session':>9}{'atime':>7}   last used")
        for total, n, s in rows[:top]:
            last = (s.get("last_tool_use") or s.get("last_marker") or "")[:10]
            print(f"{n[:34]:<36}{s['zcode']:>6}{s['claude']:>7}{s['marker']:>9}{s['atime']:>7}   {last}")
        print(f"\n{len(rows)} skill(s) with records;  invocations: zcode={sum(s['zcode'] for _,_,s in rows)},"
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
    ap.add_argument("--reset", action="store_true", help="wipe state and do a full rescan")
    args = ap.parse_args()

    if args.reset:
        for p in (STATE_FILE, DATA_FILE):
            if os.path.exists(p):
                os.remove(p)
        print("state cleared, full rescan…")

    state = _load(STATE_FILE, {})
    data = _load(DATA_FILE, {})
    # v1 -> v2 schema migration (v1 stored flat {name: zcode_count})
    if data and data.get("skills") and isinstance(
            next(iter(data["skills"].values()), None), int):
        data = {"skills": {k: {"zcode": v, "claude": 0, "marker": 0, "atime": 0,
                               "marker_sources": {}, "last_tool_use": "", "last_marker": ""}
                           for k, v in data["skills"].items()}}
    data.setdefault("skills", {})
    data.setdefault("daily", {})

    if args.watch:
        print(f"watching (rescan every {args.watch}s, Ctrl+C to exit)…")
        while True:
            z = scan_zcode(state, data)
            c = scan_claude(state, data)
            m = scan_markers(state, data)
            a = scan_atime(state, data)
            data["meta"] = {"last_scan": datetime.now().isoformat(timespec="seconds")}
            _save(STATE_FILE, state)
            _save(DATA_FILE, data)
            if z or c or m or a:
                print(f"[{datetime.now():%H:%M:%S}] zcode+{z} claude+{c} sessions+{m} atime+{a}")
            time.sleep(args.watch)
    else:
        first = not state.get("offsets")
        if first:
            print("first run: full historical scan (may take a few minutes)…")
        t0 = time.time()
        z = scan_zcode(state, data)
        c = scan_claude(state, data)
        m = scan_markers(state, data)
        a = scan_atime(state, data)
        data["meta"] = {"last_scan": datetime.now().isoformat(timespec="seconds")}
        _save(STATE_FILE, state)
        _save(DATA_FILE, data)
        print(f"scan done: zcode+{z} claude+{c} sessions+{m} atime+{a}"
              f" ({time.time() - t0:.1f}s)")
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        report(data, args.top)


if __name__ == "__main__":
    main()
