# tests/test_audit_cli.py — 任务 6：audit 子命令接入 CLI（--show-diff / --accept-drift / --strict）
# 简报两测为契约原文；三处既定调整：
# 1) 补齐简报遗漏的 import（load_config/save_config/load_snapshots）；
# 2) monkeypatch 集合去掉 CONFIG_NAME——它是文档性常量，load_config 经 _config_path()
#    运行时从 GUARD_DIR 派生，patch GUARD_DIR 即同时隔离 config；SNAPSHOTS_NAME 仍需
#    单独 patch（load_snapshots/save_snapshots/snapshot_dir 直接读该模块属性）；
# 3) 第三测为自审补测：钉死 --show-diff 只打印 diff、不落盘（存储基线仍是首 audit 内容）。
import hashlib

from skill_guard import main, load_config, save_config, load_snapshots

def _prep(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME", str(tmp_path / ".sr/snapshots.json"))
    skill = tmp_path / "pool" / "demo"; skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# d")
    cfg = load_config(); cfg["roots"] = [{"path": str(tmp_path / "pool"), "builtin": False}]
    save_config(cfg)
    return skill

def test_audit_then_show_diff_then_accept(tmp_path, monkeypatch, capsys):
    skill = _prep(tmp_path, monkeypatch)
    main(["audit"])                                   # 基线
    (skill / "SKILL.md").write_text("# d changed")
    main(["audit", "--show-diff", "demo"])            # 看 diff（不落盘状态）
    out = capsys.readouterr().out
    assert "changed" in out or "~1" in out
    main(["audit"])                                   # 触发 drift
    (skill / "SKILL.md").write_text("# d changed more")
    main(["audit", "--accept-drift", "demo"])         # 重建基线
    snaps = load_snapshots()
    assert snaps["skills"][str(skill)]["status"] == "baseline-unreviewed"

def test_audit_strict_exit_on_drift(tmp_path, monkeypatch):
    skill = _prep(tmp_path, monkeypatch)
    main(["audit"])
    (skill / "SKILL.md").write_text("# d2")
    assert main(["audit", "--strict"]) == 1

def test_show_diff_does_not_persist(tmp_path, monkeypatch):
    # 自审钉死：--show-diff 不落盘——存储哈希仍是首 audit 基线（"# d"）
    skill = _prep(tmp_path, monkeypatch)
    main(["audit"])
    (skill / "SKILL.md").write_text("# d changed")
    main(["audit", "--show-diff", "demo"])
    stored = load_snapshots()["skills"][str(skill)]
    assert stored["hashes"]["SKILL.md"] == hashlib.sha256(b"# d").hexdigest()
