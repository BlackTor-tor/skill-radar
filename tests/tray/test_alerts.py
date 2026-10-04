# tests/tray/test_alerts.py — 任务 3：Toast 文案清洗、隔离路径校验、恢复说明
import os
import subprocess

import pytest

import skill_guard
from tray import alerts
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


def test_toast_failure_falls_back_to_notify(monkeypatch):
    """终审 M-4：平台子进程路径失败（OSError 或非零返回码）时降级 notify(t, m)；
    notify=None（默认）时静默兜底不炸。monkeypatch 掉 subprocess.run，不真跑。"""
    monkeypatch.setattr(alerts.sys, "platform", "win32")
    got = []

    def boom(*a, **kw):
        raise OSError("powershell missing")

    monkeypatch.setattr(alerts.subprocess, "run", boom)
    toast("t1", "m1", notify=lambda t, m: got.append((t, m)))
    assert got == [("t1", "m1")]              # 降级回调被调，参数为清洗后文案
    assert toast("t2", "m2") == "t2m2"        # notify 缺省：静默，不抛

    got.clear()

    def nonzero(argv, **kw):
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(alerts.subprocess, "run", nonzero)
    toast("t3", "m3", notify=lambda t, m: got.append((t, m)))
    assert got == [("t3", "m3")]              # 非零返回码同走降级


def test_win_toast_script_carries_text_and_quotes(monkeypatch):
    """Windows toast 脚本四要素齐 + t/m 已插值且单引号被转义。
    monkeypatch 截获 subprocess.run 参数——不真跑 powershell。"""
    if os.name != "nt":
        pytest.skip("win32 toast script only exercised on Windows")
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return subprocess.CompletedProcess(argv, 0)

    import subprocess as _sp   # noqa: F401  (kept for clarity)
    monkeypatch.setattr(alerts.subprocess, "run", fake_run)
    monkeypatch.setattr(alerts.sys, "platform", "win32")
    title = "Skill Radar 'alert'"
    msg = "blocked 'rm' and \"quotes\""
    toast(title, msg)
    argv = captured["argv"]
    assert argv[0:2] == ["powershell", "-NoProfile"] and argv[2] == "-Command"
    ps = argv[3]
    # 四要素：LoadXml 装载 XML、ToastNotification::new、CreateToastNotifier(...).Show
    assert "XmlDocument" in ps and "LoadXml(" in ps
    assert "ToastNotificationManager" in ps and "ToastGeneric" in ps
    assert "::new($x)" in ps
    assert "CreateToastNotifier('SkillRadarTray').Show($toast)" in ps
    # t/m 已插值，且单引号被转义为两个单引号（防注入：整段 XML 是一个
    # PowerShell 单引号字符串，内部单引号——含 t/m 携带的——统一双写）
    assert f"<text id=\"1\">{title.replace(chr(39), chr(39)*2)}</text>" in ps
    assert f"<text id=\"2\">{msg.replace(chr(39), chr(39)*2)}</text>" in ps


def test_win_toast_escapes_xml_metachars(monkeypatch):
    """升级 T3：文案含 & < > 时 t/m 必须过 XML 实体转义——否则
    XmlDocument.LoadXml 解析失败，Toast 永久静默失效（目录名含 & 即触发）。
    转义只覆盖 & < > 三实体；引号落在文本节点无需转义（PS 单引号层另管）。"""
    if os.name != "nt":
        pytest.skip("win32 toast script only exercised on Windows")
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(alerts.subprocess, "run", fake_run)
    monkeypatch.setattr(alerts.sys, "platform", "win32")
    toast("A & B < C", "x > y & z")
    ps = captured["argv"][3]
    assert "A &amp; B &lt; C" in ps
    assert "x &gt; y &amp; z" in ps
    assert "A & B" not in ps and "x > y" not in ps   # 裸元字符不再出现


def test_quarantine_same_name_same_second_no_nesting(tmp_path, monkeypatch):
    """同名技能同秒二次隔离：第二个 dest 追加 -2，不嵌套进第一个。"""
    _redirect(tmp_path, monkeypatch)
    pool1 = tmp_path / "pool1"; skill1 = pool1 / "evil"
    pool2 = tmp_path / "pool2"; skill2 = pool2 / "evil"
    for s in (skill1, skill2):
        s.mkdir(parents=True)
        (s / "SKILL.md").write_text("bad", encoding="utf-8")
    dest1 = quarantine_skill(str(skill1), allowed_roots=[str(pool1)])
    dest2 = quarantine_skill(str(skill2), allowed_roots=[str(pool2)])
    assert dest1 and dest2
    assert dest2 != dest1                       # 未嵌套进第一个 dest
    assert os.path.isdir(dest1) and os.path.isdir(dest2)
    assert not os.path.isdir(os.path.join(dest1, "evil"))   # dest1 内无嵌套目录
    assert os.path.isdir(os.path.join(dest2, "SKILL.md").rsplit(os.sep, 1)[0])
    note2 = os.path.join(dest2, "RESTORE.txt")
    assert os.path.isfile(note2) and str(skill2) in open(note2, encoding="utf-8").read()
