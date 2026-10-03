# tests/test_snapshots.py
from skill_guard import load_snapshots, save_snapshots, snapshot_dir, diff_snapshot

def test_snapshot_and_diff(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME", str(tmp_path / "s.json"))
    (tmp_path / "SKILL.md").write_text("v1")
    snap = snapshot_dir(str(tmp_path), status="scanned", score=0)
    save_snapshots({"skills": {str(tmp_path): snap}})
    assert load_snapshots()["skills"][str(tmp_path)]["hashes"]["SKILL.md"]
    (tmp_path / "SKILL.md").write_text("v2 evil")
    (tmp_path / "new.sh").write_text("x")
    new = snapshot_dir(str(tmp_path), status="drifted", score=40)
    d = diff_snapshot(snap["hashes"], new["hashes"])
    assert d["changed"] == ["SKILL.md"] and d["added"] == ["new.sh"] and d["removed"] == []
