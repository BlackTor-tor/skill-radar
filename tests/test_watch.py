# tests/test_watch.py — 任务 7：audit --watch 轮询模式（consent 门控）
# 简报测试 1 为契约原文（一处既定调整：去掉 CONFIG_NAME patch——它是文档性常量，
# load_config 经 _config_path() 运行时从 GUARD_DIR 派生，与任务 6 口径一致）。
# 轮询死循环不可在测试中真跑：循环体抽为 _watch_once（返回命中行）单测；
# 集成测经 time.sleep 桩在首轮 sleep 时抛 KeyboardInterrupt 退出（等价真实
# Ctrl+C），同时钉死两件事：首次授权落盘、Ctrl+C 不被任何异常网吞掉。
import re
import types

import pytest

import skill_guard
from skill_guard import main, gate_consent, load_config, save_config, load_snapshots


def _prep(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME", str(tmp_path / ".sr/snapshots.json"))
    skill = tmp_path / "pool" / "demo"; skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# d")
    cfg = load_config(); cfg["roots"] = [{"path": str(tmp_path / "pool"), "builtin": False}]
    save_config(cfg)
    return skill


def test_watch_requires_consent(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    cfg = load_config()
    with pytest.raises(SystemExit):
        gate_consent(cfg, "watch", yes_flag=False)     # 非交互 CI：拒绝
    cfg2 = gate_consent(cfg, "watch", yes_flag=True)   # 显式 --yes
    assert cfg2["consent"]["watch"] is True


def test_watch_refusal_exits_and_persists_nothing(tmp_path, monkeypatch, capsys):
    # 非交互且无 --yes：--watch 直接 SystemExit（gate 三路门的拒绝路），不进轮询、
    # 不落任何授权（磁盘 config 的 consent.watch 仍为 False）。钉门消息而非裸
    # SystemExit：argparse 对未知参数也 raise SystemExit(2)，裸捕获会假绿。
    _prep(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as ei:
        main(["audit", "--watch", "1"])
    assert "watch" in str(ei.value.code) and "未授权" in str(ei.value.code)
    assert load_config()["consent"]["watch"] is False
    assert capsys.readouterr().out == ""               # 未打印任何轮询输出


def test_watch_yes_gate_persists_and_ctrl_c_exits(tmp_path, monkeypatch, capsys):
    # --yes：watch 授权写入磁盘 config（首次授权落盘），首轮审计执行并打印
    # [HH:MM:SS] 前缀的 NEW 命中行；sleep 桩抛 KeyboardInterrupt → 原样穿透 main
    #（Ctrl+C 不被异常网吞掉）
    _prep(tmp_path, monkeypatch)
    import time as time_mod

    def _ctrl_c(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(time_mod, "sleep", _ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        main(["audit", "--watch", "1", "--yes"])
    assert load_config()["consent"]["watch"] is True   # 授权已落盘
    snaps = load_snapshots()
    assert list(snaps["skills"]) and list(snaps["skills"].values())[0]["name"] == "demo"
    out = capsys.readouterr().out
    assert "watching every 1s" in out
    assert re.search(r"\[\d{2}:\d{2}:\d{2}\] NEW", out)   # 命中行带 [HH:MM:SS] 前缀


def test_watch_once_returns_hit_lines(tmp_path, monkeypatch):
    # watch 单轮命中行过滤（行级）：NEW/DRIFT 状态行与嵌在 OK 块内的
    # "  CRITICAL" finding 行进 hits；OK 行与报告正文不进（轮询只报变化与高危）
    _prep(tmp_path, monkeypatch)

    def fake_audit_roots(roots, rules_text, bl_text, snaps, cfg, max_depth=5):
        return ["OK        demo  score=0\nskill: demo   score: 0/100   verdict: PASS\n"
                "  CRITICAL EXFIL     R-001     a.py:1  out-bound\n建议: 拒绝安装",
                "NEW       beta  score=0  → baseline-unreviewed\nskill: beta ...",
                "DRIFT     gamma  +0 -1 ~0（用 --show-diff gamma 查看）\nskill: gamma ..."]

    monkeypatch.setattr(skill_guard, "audit_roots", fake_audit_roots)
    cfg = load_config(); snaps = load_snapshots()
    hits = skill_guard._watch_once(types.SimpleNamespace(watch=1), cfg, "", "", snaps)
    assert hits == ["  CRITICAL EXFIL     R-001     a.py:1  out-bound",
                    "NEW       beta  score=0  → baseline-unreviewed",
                    "DRIFT     gamma  +0 -1 ~0（用 --show-diff gamma 查看）"]
    assert skill_guard.SNAPSHOTS_NAME  # noqa: 每轮落盘发生（fake 下快照为空壳也应写文件）
    import os
    assert os.path.isfile(skill_guard.SNAPSHOTS_NAME)
