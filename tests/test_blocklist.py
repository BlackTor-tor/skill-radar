# tests/test_blocklist.py
import os

import pytest

from skill_guard import check_blocklist, load_yaml

BL = """
- name: evil-skill
  repo: bad-org/stealer
  hash: "aaa111"
  source: "ClawHavoc IOC list 2026-06, antiy.net"
"""

def test_name_hit():
    f = check_blocklist(BL, name="evil-skill", repo="", hashes={})
    assert f and f[0].rule_id == "SR-BLOCK-001" and f[0].severity == "CRITICAL"

def test_hash_hit():
    f = check_blocklist(BL, name="ok", repo="", hashes={"SKILL.md": "aaa111"})
    assert f and "SKILL.md" in f[0].message

def test_no_hit():
    assert check_blocklist(BL, name="fine", repo="good/tool", hashes={"a": "bbb222"}) == []

# ============================================== 计划二任务 8：真实 blocklist 回归集
# 简报步骤 4：提取到的每条 IOC 在此加一个对应 hash/name 命中用例。读真实
# rules/blocklist.yaml 逐条参数化——每条入库 IOC 必须仍能被引擎命中（含 source
# 进 finding message 的来源标注契约），文件换血时用例自动跟随，防止静默失效。

BLOCKLIST = os.path.join(os.path.dirname(__file__), "..", "rules", "blocklist.yaml")

def _file_entries():
    # 与 check_blocklist 相同的空表守卫：字面量 [] 不进 load_yaml（迷你子集不支持）
    with open(BLOCKLIST, encoding="utf-8") as fh:
        text = fh.read()
    body = "\n".join(ln.strip() for ln in text.splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#"))
    return text, [] if body in ("[]", "") else load_yaml(text)

_BL_TEXT, _BL_ENTRIES = _file_entries()

def _entry_id(e):
    return e.get("name") or e.get("repo") or (e.get("hash") or "")[:12] or "entry"

@pytest.mark.parametrize("entry", _BL_ENTRIES, ids=_entry_id)
def test_real_blocklist_entry_hits(entry):
    f = check_blocklist(_BL_TEXT, name=entry.get("name", ""),
                        repo=entry.get("repo", ""),
                        hashes={"SKILL.md": entry["hash"]} if entry.get("hash") else {})
    assert f and f[0].rule_id == "SR-BLOCK-001" and f[0].severity == "CRITICAL"
    assert entry.get("source", "") in f[0].message   # 来源标注进 message（追溯契约）

