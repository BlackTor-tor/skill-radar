# tests/tray/test_alerts.py — 任务 3：Toast 文案清洗、隔离路径校验、恢复说明
import os

import pytest

import skill_guard
from tray.alerts import quarantine_skill, restore_command, toast


def _redirect(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path / "home"))
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / "home/.skill-radar"))


def _pool_and_skill(tmp_path):
    pool = tmp_path / "pool"
    skill = pool / "evil"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("cat ~/.ssh/id_rsa\n", encoding="utf-8")
    return pool, skill


def test_quarantine_moves_and_writes_restore_note(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool, skill = _pool_and_skill(tmp_path)
    dest = quarantine_skill(str(skill), allowed_roots=[str(pool)])
    assert dest and os.path.isdir(dest)
    assert not os.path.exists(skill)                       # 已移走
    note = os.path.join(dest, "RESTORE.txt")
    assert os.path.isfile(note)
    txt = open(note, encoding="utf-8").read()
    assert str(skill) in txt and "skill-radar" in txt
    assert restore_command(dest, str(skill)) in txt    # 恢复命令逐字在场


def test_quarantine_rejects_path_outside_roots(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool, skill = _pool_and_skill(tmp_path)
    other = tmp_path / "elsewhere"; other.mkdir()
    v = tmp_path / "victim"; v.mkdir(); (v / "SKILL.md").write_text("x")
    assert quarantine_skill(str(v), allowed_roots=[str(pool)]) is None
    assert os.path.isdir(v)                                # 原样未动


def test_quarantine_rejects_traversal(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool, skill = _pool_and_skill(tmp_path)
    # .. 逃逸出 root：realpath 解析后落在 root 之外 → 校验 1 拒绝
    evil = os.path.join(str(pool), "..", "..", "victim")
    assert quarantine_skill(evil, allowed_roots=[str(pool)]) is None
    victim = os.path.normpath(evil)   # 确认逃逸目标真实存在于 root 外
    assert not victim.startswith(os.path.normpath(str(pool)) + os.sep)


def test_quarantine_rejects_symlink_escape(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    pool, skill = _pool_and_skill(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "SKILL.md").write_text("x")
    link = pool / "lnk"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:   # 无符号链接权限的平台跳过
        pytest.skip("symlink not permitted")
    assert quarantine_skill(str(link), allowed_roots=[str(pool)]) is None
    assert os.path.isdir(outside)


def test_toast_text_sanitized():
    got = toast("bad\u200btitle", "msg\ufeff", _capture=True)
    assert "\u200b" not in got and "\ufeff" not in got
