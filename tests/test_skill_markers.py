"""Marker setup must preserve the skill bytes it modifies."""
import sys

import pytest

import apply_skill_markers as am


def _skill(tmp_path, monkeypatch, original):
    md = tmp_path / "skills" / "demo" / "SKILL.md"
    md.parent.mkdir(parents=True)
    md.write_bytes(original)
    monkeypatch.setattr(am, "SKILL_ROOTS", [str(md.parent.parent)])
    monkeypatch.setattr(am, "MARKER_MAP", str(tmp_path / "markers.json"))
    monkeypatch.setattr(sys, "argv", ["apply_skill_markers.py"])
    return md


@pytest.mark.parametrize("original", [b"# demo", b"# demo\n", b"# demo\r\n", b"# invalid \xff byte\r\n",
                                      b"\xef\xbb\xbf---\r\nname: demo\r\n---\r\n# demo\r\n\r\n"])
def test_marker_roundtrip_preserves_original_bytes(tmp_path, monkeypatch, original):
    md = _skill(tmp_path, monkeypatch, original)
    am.main()
    injected = md.read_bytes()
    assert injected.startswith(original)
    assert b"<!-- skill-marker:demo -->" in injected
    am.main()
    assert md.read_bytes() == injected
    monkeypatch.setattr(sys, "argv", ["apply_skill_markers.py", "--remove"])
    am.main()
    assert md.read_bytes() == original


def test_marker_example_in_document_does_not_block_injection(tmp_path, monkeypatch):
    original = b"# usage\n`<!-- skill-marker:example -->` is an example.\n"
    md = _skill(tmp_path, monkeypatch, original)
    am.main()
    assert md.read_bytes().count(b"skill-marker:") == 2
    monkeypatch.setattr(sys, "argv", ["apply_skill_markers.py", "--remove"])
    am.main()
    assert md.read_bytes() == original


def test_marker_injector_covers_nested_skill_containers(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    nested = root / ".system" / "nested" / "SKILL.md"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"# nested\n")
    monkeypatch.setattr(am, "SKILL_ROOTS", [str(root)])
    monkeypatch.setattr(am, "MARKER_MAP", str(tmp_path / "markers.json"))
    monkeypatch.setattr(sys, "argv", ["apply_skill_markers.py"])
    am.main()
    assert b"<!-- skill-marker:nested -->" in nested.read_bytes()


@pytest.mark.parametrize("name", [b'"demo --> <!-- skill-marker:injected"', b"", b"|", b">", b"demo\x01",
                                 b"x" * 120])
def test_marker_rejects_unsafe_frontmatter_names(tmp_path, monkeypatch, name):
    md = _skill(tmp_path, monkeypatch, b"---\nname: " + name + b"\n---\n# demo\n")
    am.main()
    assert md.read_bytes().endswith(b"<!-- skill-marker:demo -->\n")


def test_marker_name_frontmatter_comment_is_not_part_of_skill_name(tmp_path, monkeypatch):
    md = _skill(tmp_path, monkeypatch, b'---\nname: "real-skill" # explanatory comment\n---\n# demo\n')
    am.main()
    assert md.read_bytes().endswith(b"<!-- skill-marker:real-skill -->\n")
