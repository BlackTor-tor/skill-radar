#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill-radar: SKILL.md marker injector
=====================================
Injects a unique marker at the end of every SKILL.md copy on this machine:

    <!-- skill-marker:<name> -->

When any AI agent loads a skill's content into context, the marker rides along
into whatever session log that agent writes. skill_monitor.py greps all agents'
logs for these markers, giving universal cross-agent usage counting without
parsing any agent's log format.

Idempotent: files that already carry a marker are skipped.
NOTE: `npx skills update` overwrites SKILL.md — re-run this script afterwards.

Usage:
    python apply_skill_markers.py            # inject
    python apply_skill_markers.py --remove   # remove all markers
"""
import json
import os
import re
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
# Candidate skill roots: the skills CLI keeps separate per-agent copies
# (codex/cursor/...), all of them need the marker.
SKILL_ROOTS = [
    os.path.join(HOME, ".agents", "skills"),
    os.path.join(HOME, ".codex", "skills"),
    os.path.join(HOME, ".claude", "skills"),
    os.path.join(HOME, ".cursor", "skills"),
    os.path.join(HOME, ".qoder-cn", "skills"),
    os.path.join(HOME, ".zcode", "skills"),  # mostly symlinks to .agents; idempotent
]
MARKER_MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill_markers.json")
MARKER_RE = re.compile(r"<!--\s*skill-marker:([^>]+?)-->")


def frontmatter_name(text, fallback):
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if m:
        nm = re.search(r"^name:\s*(.+)$", m.group(1), re.M)
        if nm:
            return nm.group(1).strip().strip("'\"")
    return fallback


def main():
    remove = "--remove" in sys.argv
    mapping = {}
    injected = skipped = removed = 0

    for root in SKILL_ROOTS:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            skill_md = os.path.join(root, entry, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            try:
                text = open(skill_md, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            name = frontmatter_name(text, entry)
            marker = f"<!-- skill-marker:{name} -->"
            mapping[name] = marker

            if remove:
                if MARKER_RE.search(text):
                    text = MARKER_RE.sub("", text).rstrip() + "\n"
                    open(skill_md, "w", encoding="utf-8", newline="").write(text)
                    removed += 1
                continue

            if MARKER_RE.search(text):
                skipped += 1
                continue
            body = text.rstrip("\r\n") + "\n\n" + marker + "\n"
            open(skill_md, "w", encoding="utf-8", newline="").write(body)
            injected += 1

    if remove:
        if os.path.exists(MARKER_MAP):
            os.remove(MARKER_MAP)
        print(f"removed markers from {removed} file(s)")
    else:
        mapping["_meta"] = {"applied_at": datetime.now().isoformat(timespec="seconds"),
                            "injected": injected, "already_present": skipped}
        with open(MARKER_MAP, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=1)
        print(f"injected {injected} / already present {skipped}, distinct skills: {len(mapping) - 1}")
        print(f"marker map: {MARKER_MAP}")


if __name__ == "__main__":
    main()
