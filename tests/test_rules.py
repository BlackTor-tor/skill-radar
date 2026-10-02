# tests/test_rules.py
import pytest, textwrap
from skill_guard import parse_rules, Rule

VALID = textwrap.dedent("""
- id: SR-EXEC-001
  category: EXEC
  severity: HIGH
  description: curl pipe to shell
  file_globs: ["*.sh", "SKILL.md"]
  patterns: ["curl [^\\n]*\\|\\s*(ba)?sh", "Invoke-Expression"]
  refs: [CWE-78]
- id: SR-EXFIL-002
  category: EXFIL
  severity: CRITICAL
  description: sensitive file exfil
  pattern_source: ["id_rsa", "\\.env\\b"]
  pattern_sink: ["curl [^\\n]*-d", "requests\\.post"]
  pairing: cross_file
""")

def test_parse_two_rule_shapes():
    rules = parse_rules(VALID)
    assert isinstance(rules[0], Rule)
    assert rules[0].patterns == ["curl [^\\n]*\\|\\s*(ba)?sh", "Invoke-Expression"]
    assert rules[0].source is None and rules[0].pairing is None
    assert rules[1].source == ["id_rsa", "\\.env\\b"]
    assert rules[1].pairing == "cross_file"

def test_invalid_severity_rejected():
    with pytest.raises(ValueError, match="severity"):
        parse_rules("- id: X\n  category: EXEC\n  severity: FATAL\n")

def test_pair_rule_requires_both_sides():
    with pytest.raises(ValueError, match="pairing"):
        parse_rules("- id: X\n  category: EXFIL\n  severity: HIGH\n  pattern_source: [a]\n")

def test_pattern_must_compile():
    with pytest.raises(ValueError, match="regex"):
        parse_rules("- id: X\n  category: EXEC\n  severity: LOW\n  patterns: [\"([unclosed\"]\n")
