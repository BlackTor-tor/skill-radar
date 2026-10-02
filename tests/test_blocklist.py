# tests/test_blocklist.py
from skill_guard import check_blocklist

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
