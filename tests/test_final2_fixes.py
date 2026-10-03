# tests/test_final2_fixes.py — 最终审查修复波 2（1 关键 + 4 重要）的回归测试
# 每项先写失败测试（TDD），修复随同一提交落地；对应实现见 skill_guard.py 标注。
import hashlib
import io
import json
import os
import sys
import textwrap

import pytest

import skill_guard
from skill_guard import (main, load_config, save_config, load_snapshots,
                         audit_roots, discover_roots)

# 夹具规则：HIGH 一条（信任降级 / §9 计分用）；CRITICAL 一条（emoji 端到端用）。
HIGH_RULE = ("- id: T-HIGH\n  category: EXEC\n  severity: HIGH\n"
             "  description: d\n  patterns: ['curl [^\\n]*|sh']\n")
CRIT_RULE = ("- id: T-CRIT\n  category: EXEC\n  severity: CRITICAL\n"
             "  description: d\n  patterns: ['curl [^\\n]*\\|\\s*(ba)?sh']\n")


def make_skill(base, rel, body="# s"):
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


def _cfg(**trust):
    return {"trust": {"owners": [], "repos": [], "hashes": [], **trust}}


def _prep(tmp_path, monkeypatch):
    # 与 test_audit_cli._prep 同口径：GUARD_DIR/SNAPSHOTS_NAME 双重定向隔离真实用户目录
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME", str(tmp_path / ".sr/snapshots.json"))
    skill = tmp_path / "pool" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# d", encoding="utf-8")
    cfg = load_config()
    cfg["roots"] = [{"path": str(tmp_path / "pool"), "builtin": False}]
    save_config(cfg)
    return skill


# ============================================ 发现 1（关键）：无变化 re-audit 关闭 --show-diff 窗口

def test_nochange_reaudit_keeps_prev_hashes_and_show_diff(tmp_path, monkeypatch, capsys):
    # 主工作流：audit（基线）→ 改内容 → audit（DRIFT，prev_hashes 落盘）→
    # **无变化** re-audit（watch 一轮轮询即触发）→ 状态仍 drifted、报告仍指路
    # --show-diff，此刻 --show-diff 必须仍非空（修复前：else 分支不透传
    # prev_hashes，条目覆写后 diff 退化为空——inspect 窗口被无变化轮询关闭）。
    skill = _prep(tmp_path, monkeypatch)
    main(["audit"])                                   # 基线
    (skill / "SKILL.md").write_text("# d changed", encoding="utf-8")
    capsys.readouterr()
    main(["audit"])                                   # DRIFT → prev_hashes 落盘
    capsys.readouterr()
    main(["audit"])                                   # 无变化 re-audit：状态保留 drifted
    entry = load_snapshots()["skills"][str(skill)]
    assert entry["status"] == "drifted"
    assert entry["prev_hashes"]["SKILL.md"] == hashlib.sha256(b"# d").hexdigest()
    capsys.readouterr()
    main(["audit", "--show-diff", "demo"])
    d = json.loads(capsys.readouterr().out)
    assert d["changed"] == ["SKILL.md"]               # 修复前：空 diff
