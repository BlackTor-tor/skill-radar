# tests/test_consent.py
"""consent 授权门 + discover --deep 语义锁定。

环境约定：pytest 下 sys.stdin 非 TTY（capture 替换），CI 同理 ——
gate_consent 无 --yes 且未授权时必须走 SystemExit 分支；TTY 分支用
_FakeStdin(tty=True) + monkeypatch builtins.input 驱动。
"""
import sys

import pytest
import skill_guard
from skill_guard import (check_consent, discover_roots, gate_consent,
                         interactive_consent, load_config)


class _FakeStdin:
    """替身 stdin：仅提供 isatty（gate_consent 只用它判定交互能力）。"""
    def __init__(self, tty):
        self._tty = tty
    def isatty(self):
        return self._tty


# ---------------- check_consent：读 cfg["consent"][action]，缺失即未授权

def test_consent_gate_blocks_until_yes(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    cfg = load_config()
    assert check_consent(cfg, "deep_scan") is False          # 未授权
    cfg["consent"]["deep_scan"] = True
    assert check_consent(cfg, "deep_scan") is True

def test_check_consent_other_action_defaults_false(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    assert check_consent(load_config(), "watch") is False


# ---------------- interactive_consent：必须完整输入 yes

def test_interactive_requires_full_yes(monkeypatch):
    answers = iter(["y", "no", "yes"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert interactive_consent("deep_scan") is False   # "y" 不够
    assert interactive_consent("deep_scan") is False   # "no" 不够
    assert interactive_consent("deep_scan") is True    # 完整 "yes"

def test_interactive_strips_whitespace(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "  yes  ")
    assert interactive_consent("deep_scan") is True


# ---------------- gate_consent 三路：已授权 / --yes / 交互 TTY / 否则 SystemExit

def test_gate_already_authorized_returns_cfg_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    cfg = load_config()
    cfg["consent"]["deep_scan"] = True
    assert gate_consent(cfg, "deep_scan", False) is cfg

def test_gate_yes_flag_grants(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    out = gate_consent(load_config(), "deep_scan", True)
    assert out["consent"]["deep_scan"] is True

def test_gate_non_tty_without_yes_exits(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))   # CI 即此环境
    with pytest.raises(SystemExit) as ei:
        gate_consent(load_config(), "deep_scan", False)
    assert "未授权" in str(ei.value)

def test_gate_tty_yes_grants(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    out = gate_consent(load_config(), "deep_scan", False)
    assert out["consent"]["deep_scan"] is True

def test_gate_tty_no_exits(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda _: "no")
    with pytest.raises(SystemExit):
        gate_consent(load_config(), "deep_scan", False)

def test_gate_tty_eof_treated_as_refusal(tmp_path, monkeypatch):
    # isatty=True 但 input() EOF（Ctrl-D，或 /dev/null 被误判 TTY 的环境）
    # ⇒ 走干净的 SystemExit，而不是 EOFError 裸 traceback
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    def _eof(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    with pytest.raises(SystemExit):
        gate_consent(load_config(), "deep_scan", False)


# ---------------- main() discover 子命令：--deep 过 consent 门且授权落盘

def test_main_discover_deep_yes_persists_consent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path))
    monkeypatch.setattr("skill_guard.discover_roots",
                        lambda deep=False, max_depth=4:
                            [str(tmp_path / ".agents" / "skills"),   # 内置已注册根
                             str(tmp_path / "newloc" / "skills")])   # 新根
    rc = skill_guard.main(["discover", "--deep", "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[已注册]" in out and "[新]" in out
    assert str(tmp_path / "newloc" / "skills") in out
    # consent 状态真的落盘（save_config 被调用）
    assert (tmp_path / ".sr" / "config.yaml").is_file()
    assert load_config()["consent"]["deep_scan"] is True

def test_main_discover_non_deep_skips_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / ".sr"))
    monkeypatch.setattr("skill_guard.discover_roots", lambda deep=False, max_depth=4: [])
    assert skill_guard.main(["discover"]) == 0
    assert not (tmp_path / ".sr" / "config.yaml").exists()   # 未过门 ⇒ 不落盘


# ---------------- deep 起点跨平台（任务 3 遗留：POSIX 下 os.getcwd()[:3] 垃圾起点）

def test_deep_starts_posix_semantics(monkeypatch):
    # 任意平台模拟 POSIX：splitdrive 恒空串 ⇒ 起点应为 ["/"]
    monkeypatch.setattr(skill_guard.os.path, "splitdrive", lambda p: ("", ""))
    assert skill_guard._deep_starts("/home/u", "/home/u/work") == ["/"]

def test_deep_starts_windows_semantics(monkeypatch):
    # 任意平台模拟盘符语义：HOME 盘与 cwd 盘不同 ⇒ 两个盘根
    def fake_splitdrive(p):
        d = p[:2] if len(p) >= 2 and p[1] == ":" else ""
        return (d, p[len(d):])
    monkeypatch.setattr(skill_guard.os.path, "splitdrive", fake_splitdrive)
    starts = skill_guard._deep_starts("C:/Users/u", "F:/work")
    assert sorted(starts) == sorted(["C:" + skill_guard.os.sep, "F:" + skill_guard.os.sep])

def test_deep_starts_windows_semantics_dedupes_same_drive(monkeypatch):
    def fake_splitdrive(p):
        d = p[:2] if len(p) >= 2 and p[1] == ":" else ""
        return (d, p[len(d):])
    monkeypatch.setattr(skill_guard.os.path, "splitdrive", fake_splitdrive)
    assert skill_guard._deep_starts("C:/Users/u", "C:/work") == ["C:" + skill_guard.os.sep]

def _recording_walk(seen):
    def fake_walk(start):
        seen.append(start)
        return iter(())
    return fake_walk

def test_discover_deep_walks_expected_start(monkeypatch):
    # 本平台真实路径语义的端到端验证：cwd=HOME ⇒ 单一起点（Windows 盘根 / POSIX "/"）
    seen = []
    monkeypatch.setattr(skill_guard.os, "walk", _recording_walk(seen))
    monkeypatch.setattr(skill_guard.os, "getcwd", lambda: skill_guard.HOME)
    assert discover_roots(deep=True) == []
    if skill_guard.os.path.splitdrive(skill_guard.HOME)[0]:   # Windows 语义
        assert seen == [skill_guard.os.path.normpath(skill_guard.HOME)[:2] + skill_guard.os.sep]
    else:                                                     # POSIX 语义
        assert seen == ["/"]
