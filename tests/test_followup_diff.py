# tests/test_followup_diff.py — Followup 缺陷修复：DRIFT 落盘后 --show-diff 立即为空
#
# 根因（任务 6 审查立案）：audit_roots 在 DRIFT 时把当前内容哈希覆写进快照，
# 而 DRIFT 状态行指路「（用 --show-diff {entry} 查看）」——此刻 s["hashes"]
# 已等于当前内容，diff 恒为空，有效窗口只剩「内容变化后、下次 audit 前」。
# 规格 §4「人工 inspect 后裁定」的闭环缺了 inspect 的眼睛。
#
# 修法（控制者裁定）：DRIFT 落盘的新快照条目额外携带 prev_hashes = 被覆写前的
# 旧基线 hashes；--show-diff 优先用 prev_hashes vs 当前内容（语义即"自上次
# 基线以来改了什么"，漂移被 audit 确认后窗口不再关闭）；--accept-drift 重建
# 基线时清除 prev_hashes（接受后不再有"上个基线"）。
import hashlib
import json

from skill_guard import main, load_config, save_config, load_snapshots


def _prep(tmp_path, monkeypatch):
    # 与 test_audit_cli._prep 同口径：GUARD_DIR/SNAPSHOTS_NAME 双重定向隔离真实用户目录
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME", str(tmp_path / ".sr/snapshots.json"))
    skill = tmp_path / "pool" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# d")
    cfg = load_config()
    cfg["roots"] = [{"path": str(tmp_path / "pool"), "builtin": False}]
    save_config(cfg)
    return skill


def test_show_diff_alive_right_after_drift_audit(tmp_path, monkeypatch, capsys):
    # 主工作流：audit（基线）→ 改内容 → audit（报 DRIFT，此刻旧基线被覆写）
    # → **此刻** --show-diff 必须非空（修复前为死胡同）
    skill = _prep(tmp_path, monkeypatch)
    main(["audit"])                                   # 基线
    (skill / "SKILL.md").write_text("# d changed")
    out = capsys.readouterr()
    main(["audit"])                                   # 报 DRIFT
    assert "DRIFT" in capsys.readouterr().out
    main(["audit", "--show-diff", "demo"])
    d = json.loads(capsys.readouterr().out)
    assert d["changed"] == ["SKILL.md"]


def test_accept_drift_clears_prev_hashes_and_diff_goes_empty(tmp_path, monkeypatch, capsys):
    # --accept-drift 重建基线：条目不再有 prev_hashes 键；--show-diff 回落
    # s["hashes"] 分支（接受后不再有"上个基线"，diff 为空）
    skill = _prep(tmp_path, monkeypatch)
    main(["audit"])
    (skill / "SKILL.md").write_text("# d changed")
    capsys.readouterr()
    main(["audit"])                                   # DRIFT → prev_hashes 落盘
    entry = load_snapshots()["skills"][str(skill)]
    assert entry["prev_hashes"]["SKILL.md"] == hashlib.sha256(b"# d").hexdigest()
    capsys.readouterr()
    main(["audit", "--accept-drift", "demo"])
    entry = load_snapshots()["skills"][str(skill)]
    assert "prev_hashes" not in entry
    capsys.readouterr()   # 消费 accept-drift 的 "re-baselined:" 行
    main(["audit", "--show-diff", "demo"])
    d = json.loads(capsys.readouterr().out)
    assert d == {"added": [], "removed": [], "changed": []}


def test_show_diff_regression_no_drift_path(tmp_path, monkeypatch, capsys):
    # 回归：无漂移路径 --show-diff 行为与修复前一致（vs 存储基线 s["hashes"]），
    # 且基线条目（NEW / 无变化）不携带 prev_hashes
    skill = _prep(tmp_path, monkeypatch)
    main(["audit"])
    capsys.readouterr()   # 消费基线 audit 输出，保证后续 out 只含 --show-diff 的 JSON
    assert "prev_hashes" not in load_snapshots()["skills"][str(skill)]
    (skill / "SKILL.md").write_text("# d changed")
    main(["audit", "--show-diff", "demo"])
    d = json.loads(capsys.readouterr().out)
    assert d["changed"] == ["SKILL.md"]
