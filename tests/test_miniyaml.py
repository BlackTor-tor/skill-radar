# tests/test_miniyaml.py
from skill_guard import load_yaml

def test_top_level_list_of_maps():
    text = """
# 注释
- id: SR-TEST-001
  severity: HIGH
  enabled: true
  weight: 3
  refs: [OWASP-AST02, CWE-200]
- id: SR-TEST-002
  severity: LOW
"""
    data = load_yaml(text)
    assert data[0]["id"] == "SR-TEST-001"
    assert data[0]["enabled"] is True
    assert data[0]["weight"] == 3
    assert data[0]["refs"] == ["OWASP-AST02", "CWE-200"]
    assert data[1]["severity"] == "LOW"

def test_top_level_map_with_block_list():
    text = """
consent:
  deep_scan: false
roots:
  - /a/skills
  - /b/skills
"""
    data = load_yaml(text)
    assert data["consent"] == {"deep_scan": False}
    assert data["roots"] == ["/a/skills", "/b/skills"]

def test_inline_list_of_paths_with_spaces():
    assert load_yaml("x: [a b, c d]\n")["x"] == ["a b", "c d"]
