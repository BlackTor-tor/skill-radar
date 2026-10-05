# tests/tray/test_daemon.py — 任务 2：debounce 合并、变更技能定位、增量扫描、异常不崩
import os
import time

import pytest

import skill_guard
from tray.daemon import Daemon, DEBOUNCE_S


def _redirect(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path / "home"))
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / "home/.skill-radar"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME",
                        str(tmp_path / "home/.skill-radar/snapshots.json"))


def _pool(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir(exist_ok=True)
    return pool


def _mk_skill(pool, name, body="# s", extra=None):
    d = pool / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    if extra:
        (d / extra).write_text("x = 1\n", encoding="utf-8")
    return d


def _register(pool, tmp_path, monkeypatch):
    cfg = skill_guard.load_config()
    cfg["roots"] = [{"path": str(pool), "builtin": False}]
    skill_guard.save_config(cfg)


RULES = "- id: T\n  category: EXEC\n  severity: HIGH\n  description: d\n  patterns: ['curl [^\\n]*|sh']\n"
# 注：daemon 走真实 rules/defaults.yaml（与 CLI 同口径）；恶意夹具必须命中
# 真实 CRITICAL 规则 SR-THEFT-001（id_rsa + cat 同行），见 test_scan_critical_in_block_mode_blocks


def test_daemon_persists_file_times_and_does_not_reset_install_on_rescan(tmp_path, monkeypatch):
    from datetime import datetime
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path)
    skill = _mk_skill(pool, "dated")
    _register(pool, tmp_path, monkeypatch)
    os.utime(skill / "SKILL.md", (1720000000, 1720000000))
    daemon = Daemon(roots=[str(pool)], rules_text="", blocklist_text="[]")
    assert daemon.scan_changed_skill(str(skill)) == "NEW"
    first = daemon.state.skills[str(skill)]
    assert first["installed_at"]
    assert datetime.fromisoformat(first["updated_at"]).timestamp() == 1720000000
    assert first["install_time_source"].startswith("skill_md_")
    assert first["installed_at"] != first["scanned_at"] or first["updated_at"] != first["scanned_at"]
    assert daemon.scan_changed_skill(str(skill)) == "OK"
    assert daemon.state.skills[str(skill)]["installed_at"] == first["installed_at"]
    persisted = skill_guard.load_snapshots()["skills"][str(skill)]
    assert persisted["installed_at"] == first["installed_at"]
    assert persisted["updated_at"] == first["updated_at"]


def test_scan_failure_keeps_available_file_time_metadata(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path)
    skill = _mk_skill(pool, "failed")
    daemon = Daemon(roots=[str(pool)])
    def boom(*args, **kwargs):
        raise RuntimeError("failure")
    monkeypatch.setattr(skill_guard, "run_engine", boom)
    assert daemon.scan_changed_skill(str(skill)) == "ERROR"
    row = daemon.state.skills[str(skill)]
    assert row["installed_at"] and row["updated_at"]
    assert row["check_status"] == "error" and row["scan_complete"] is False


def test_mark_dirty_and_debounce_merge(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path); _register(pool, tmp_path, monkeypatch)
    d = Daemon(roots=[str(pool)])
    d.mark_dirty(str(pool / "a"), "SKILL.md")
    d.mark_dirty(str(pool / "a"), "helper.py")
    d.mark_dirty(str(pool / "b"), "SKILL.md")
    assert d.dirty_roots() == {str(pool / "a"), str(pool / "b")}
    assert d.pending_since() is not None


def test_locate_changed_skills(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path)
    a = _mk_skill(pool, "a"); _mk_skill(pool, "b")
    d = Daemon(roots=[str(pool)])
    assert d.locate_changed_skill(str(pool / "a" / "SKILL.md")) == str(a)
    assert d.locate_changed_skill(str(pool / "notaskill.txt")) is None


def test_scan_changed_skill_reports_drift_and_bumps(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path); _register(pool, tmp_path, monkeypatch)
    a = _mk_skill(pool, "a")
    # 建初始基线：audit_roots 以 root 为技能池枚举（计划裁定 2——传技能目录
    # 本身不产条目），故传 pool；这也验证 daemon 与 CLI audit 的快照键/哈希
    # 口径互通（daemon 后续 diff 能读到 audit 落的基线）
    cfg = skill_guard.load_config()
    snaps = skill_guard.load_snapshots()
    skill_guard.audit_roots([str(pool)], RULES, "[]", snaps, cfg)
    skill_guard.save_snapshots(snaps)
    # 漂移：改内容
    (a / "SKILL.md").write_text("# s changed", encoding="utf-8")
    d = Daemon(roots=[str(pool)])
    outcome = d.scan_changed_skill(str(a))
    assert outcome == "DRIFT"
    assert d.state.today[list(d.state.today)[0]]["drift"] == 1
    assert d.state.guard == "alert"
    # 终审 I-3：Security 屏数据源随扫描更新（DRIFT → drifted）
    rec = d.state.skills[str(a)]
    assert rec["name"] == "a" and rec["status"] == "drifted"


def test_drift_then_ok_round_keeps_prev_hashes(tmp_path, monkeypatch):
    # 审查第 1 轮 I-1（对齐 audit_roots 902-907 行口径）：DRIFT 确认后内容
    # 不变再扫一轮（OK）必须透传 prev_hashes——否则该轮覆写条目时把
    # prev_hashes 冲掉，状态仍透传 "drifted"、状态行仍指路 --show-diff，
    # diff 却退化为空（inspect 通道被无变化轮询关闭；watch 一轮轮询即触发）
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path); _register(pool, tmp_path, monkeypatch)
    a = _mk_skill(pool, "a")
    # 建初始基线（与 drift 测试同夹具：传 pool，audit_roots 以 root 为池枚举）
    cfg = skill_guard.load_config()
    snaps = skill_guard.load_snapshots()
    skill_guard.audit_roots([str(pool)], RULES, "[]", snaps, cfg)
    skill_guard.save_snapshots(snaps)
    d = Daemon(roots=[str(pool)])
    # 第一轮：改内容 → DRIFT，prev_hashes 留住 audit 落的最初基线
    (a / "SKILL.md").write_text("# s changed", encoding="utf-8")
    assert d.scan_changed_skill(str(a)) == "DRIFT"
    # 第二轮：内容不变 → OK，status/prev_hashes 必须透传而非被覆写冲掉
    assert d.scan_changed_skill(str(a)) == "OK"
    s = skill_guard.load_snapshots()["skills"][str(a)]
    assert s["status"] == "drifted"
    # 终审 I-3：OK 轮次 state.skills 透传快照条目状态（仍 drifted）
    assert d.state.skills[str(a)]["status"] == "drifted"
    assert "prev_hashes" in s
    # --show-diff 语义（cmd_audit 同口径 base=prev_hashes, cur=hashes）：
    # prev_hashes != hashes 且对它 diff 非空——若被冲掉则回落 s["hashes"]
    # 自比得空 diff
    assert s["prev_hashes"] != s["hashes"]
    diff = skill_guard.diff_snapshot(s["prev_hashes"], s["hashes"])
    assert diff["changed"] == ["SKILL.md"]


def test_scan_new_skill_reports_new(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path); _register(pool, tmp_path, monkeypatch)
    a = _mk_skill(pool, "fresh")
    d = Daemon(roots=[str(pool)])
    assert d.scan_changed_skill(str(a)) == "NEW"
    assert d.state.today[list(d.state.today)[0]]["new"] == 1
    snaps = skill_guard.load_snapshots()
    assert str(a) in snaps["skills"]
    # 终审 I-3：Security 屏数据源随扫描更新（NEW → baseline-unreviewed）
    rec = d.state.skills[str(a)]
    assert rec["name"] == "fresh" and rec["status"] == "baseline-unreviewed"


def test_scan_exception_does_not_crash_daemon(tmp_path, monkeypatch):
    # 规格 §5：扫描在工作线程，异常不崩守护——scan_changed_skill 全包 try/except
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path)
    d = Daemon(roots=[str(pool)])
    def boom(*a, **kw):
        raise RuntimeError("engine blew up")
    monkeypatch.setattr(skill_guard, "run_engine", boom)
    assert d.scan_changed_skill(str(pool / "whatever")) == "ERROR"
    assert d.state.events[0][0] == "error"


def test_scan_critical_in_block_mode_blocks(tmp_path, monkeypatch):
    # 拦截模式 + CRITICAL → BLOCK：不真隔离（alerts 在任务 3），
    # 分发钩子 self.on_block 被调用（app 层接 alerts.quarantine_or_alert）
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path); _register(pool, tmp_path, monkeypatch)
    a = _mk_skill(pool, "evil", body="# s\ncat ~/.ssh/id_rsa\n")
    d = Daemon(roots=[str(pool)], mode="block")
    blocked = []
    d.on_block = lambda skill_path, rep: blocked.append((skill_path, rep))
    assert d.scan_changed_skill(str(a)) == "BLOCK"
    assert blocked and blocked[0][0] == str(a)
    assert d.state.today[list(d.state.today)[0]]["block"] == 1
    # 终审 I-3：BLOCK 分支也进 Security 屏数据源（快照不落，state 直写 "blocked"）
    rec = d.state.skills[str(a)]
    assert rec["name"] == "evil" and rec["status"] == "blocked"
