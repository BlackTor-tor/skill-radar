# tests/test_discover.py
from skill_guard import discover_roots

def make_skill(base, rel):
    d = base / rel; d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("# s")

def test_bounded_home_discovery(tmp_path, monkeypatch):
    make_skill(tmp_path, ".agents/skills/a")
    make_skill(tmp_path, "projects/myapp/.claude/skills/b")
    make_skill(tmp_path, "l1/l2/l3/l4/l5/.agents/skills/c")   # 第 5 层，超界
    make_skill(tmp_path, "l1/l2/skills4/c")               # 第 4 层可见（技能根 c 位于第 4 层）
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    roots = [r.replace("\\", "/") for r in discover_roots(deep=False)]
    assert any(".agents/skills" in r for r in roots)
    assert any("skills4" in r for r in roots)        # 第 4 层可见
    assert not any("l5" in r for r in roots)         # 第 5 层不可见

def test_excludes_junk_dirs(tmp_path, monkeypatch):
    make_skill(tmp_path, "proj/node_modules/pkg/SKILL_DIR")
    (tmp_path / "proj/node_modules/pkg/SKILL_DIR/SKILL.md").write_text("# x")
    make_skill(tmp_path, "proj/real-skills")
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path))
    roots = [r.replace("\\", "/") for r in discover_roots(deep=False)]
    assert any("real-skills" in r for r in roots) and not any("node_modules" in r for r in roots)
