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


def test_scan_new_skill_reports_new(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool = _pool(tmp_path); _register(pool, tmp_path, monkeypatch)
    a = _mk_skill(pool, "fresh")
    d = Daemon(roots=[str(pool)])
    assert d.scan_changed_skill(str(a)) == "NEW"
    assert d.state.today[list(d.state.today)[0]]["new"] == 1
    snaps = skill_guard.load_snapshots()
    assert str(a) in snaps["skills"]


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
