# tests/test_config.py
from skill_guard import load_config, save_config, builtin_roots, GUARD_DIR, CONFIG_NAME

def test_builtin_roots_are_marked(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path))
    roots = builtin_roots()
    assert all(r["builtin"] for r in roots)
    assert any(r["path"].endswith(".agents/skills") for r in roots)

def test_config_roundtrip_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".skill-radar"))
    cfg = load_config()
    assert cfg["consent"] == {"deep_scan": False, "watch": False}
    assert isinstance(cfg["roots"], list) and cfg["roots"]
    assert cfg["trust"] == {"owners": [], "repos": [], "hashes": []}
    cfg["consent"]["deep_scan"] = True
    save_config(cfg)
    assert load_config()["consent"]["deep_scan"] is True

def test_user_roots_merge_with_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".skill-radar"))
    cfg = load_config()
    cfg["roots"].append({"path": str(tmp_path / "custom"), "builtin": False})
    save_config(cfg)
    merged = load_config()
    assert any(not r.get("builtin") for r in merged["roots"])
