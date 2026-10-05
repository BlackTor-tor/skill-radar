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
    python src/apply_skill_markers.py            # inject
    python src/apply_skill_markers.py --remove   # remove all markers
"""
import json
import hashlib
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
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
MARKER_MAP = os.path.join(os.path.dirname(_MODULE_DIR) if os.path.basename(_MODULE_DIR) == "src" else _MODULE_DIR, "skill_markers.json")
MARKER_RE = re.compile(r"<!--\s*skill-marker:([^>]+?)-->")
TRAILING_MARKER_RE = re.compile(rb"(?:\r?\n)?[ \t]*<!--\s*skill-marker:([^\r\n>]+?)-->[ \t]*(?:\r?\n)?\Z")


def frontmatter_name(text, fallback):
    from skill_guard import _skill_frontmatter_name
    name = _skill_frontmatter_name(text)
    # Marker names are single-line plain text with a bounded size. Reject YAML
    # block scalars and HTML comment delimiters rather than emitting forged hits.
    def valid(value):
        return bool(value) and len(value) < 120 and value not in ("|", ">") and \
            not any(ch in "<>" or ord(ch) < 32 or ord(ch) == 127 for ch in value)
    if valid(name):
        return name
    if valid(fallback):
        return fallback
    return "skill-" + hashlib.sha256(fallback.encode("utf-8", errors="replace")).hexdigest()[:16]


def _skill_files(root):
    """Traverse containers without following directory links or entering skill internals."""
    if os.path.isfile(os.path.join(root, "SKILL.md")):
        yield os.path.join(root, "SKILL.md")
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        if "SKILL.md" in files:
            dirs[:] = []
            yield os.path.join(directory, "SKILL.md")
            continue
        # Agent stores may contain links to shared skill copies. Resolve only the
        # linked skill document; os.walk never descends into its target container.
        for child in dirs:
            linked = os.path.join(directory, child)
            if os.path.islink(linked) and os.path.isfile(os.path.join(linked, "SKILL.md")):
                yield os.path.join(linked, "SKILL.md")


def main():
    remove = "--remove" in sys.argv
    mapping = {}
    injected = skipped = removed = 0
    try:
        with open(MARKER_MAP, encoding="utf-8") as f:
            old_mapping = json.load(f)
        file_info = old_mapping.get("_files", {})
    except (OSError, ValueError, AttributeError):
        file_info = {}

    for root in SKILL_ROOTS:
        if not os.path.isdir(root):
            continue
        for skill_md in _skill_files(root):
            entry = os.path.basename(os.path.dirname(skill_md))
            try:
                with open(skill_md, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            name = frontmatter_name(raw.decode("utf-8", errors="replace"), entry)
            marker = f"<!-- skill-marker:{name} -->"
            mapping[name] = marker
            file_key = os.path.realpath(skill_md)
            found = TRAILING_MARKER_RE.search(raw)

            if remove:
                if found:
                    info = file_info.get(file_key, {})
                    if info.get("sha256") == hashlib.sha256(raw).hexdigest():
                        restored = raw[:info["original_size"]]
                    else:
                        # Legacy markers have no original-byte metadata. Remove only
                        # the appended marker line, preserving the remaining content.
                        restored = raw[:found.start()]
                    with open(skill_md, "wb") as f:
                        f.write(restored)
                    removed += 1
                continue

            if found:
                skipped += 1
                continue
            newline = b"\r\n" if b"\r\n" in raw else b"\n"
            suffix = newline * 2 + marker.encode("utf-8") + newline
            # Append in binary mode: do not normalize newlines or discard invalid bytes.
            with open(skill_md, "ab") as f:
                f.write(suffix)
            file_info[file_key] = {"original_size": len(raw),
                                   "sha256": hashlib.sha256(raw + suffix).hexdigest()}
            injected += 1

    if remove:
        if os.path.exists(MARKER_MAP):
            os.remove(MARKER_MAP)
        print(f"removed markers from {removed} file(s)")
    else:
        mapping["_meta"] = {"applied_at": datetime.now().isoformat(timespec="seconds"),
                            "injected": injected, "already_present": skipped}
        mapping["_files"] = file_info
        with open(MARKER_MAP, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=1)
        print(f"injected {injected} / already present {skipped}, distinct skills: {len(mapping) - 2}")
        print(f"marker map: {MARKER_MAP}")


if __name__ == "__main__":
    main()
