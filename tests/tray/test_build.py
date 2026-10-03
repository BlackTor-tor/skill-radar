# tests/tray/test_build.py — 任务 7：打包驱动（命令拼装，不真打包）
import sys

import pytest

import build_tray


def test_pyinstaller_args_windows(monkeypatch):
    monkeypatch.setattr(build_tray.sys, "platform", "win32")
    cmd = build_tray.pyinstaller_cmd()
    assert "--onefile" in cmd and "--windowed" in cmd
    assert "tray/app.py" in " ".join(cmd).replace("\\", "/")
    assert "--add-data" in " ".join(cmd)   # web/index.html 必须随包


def test_pyinstaller_args_macos(monkeypatch):
    monkeypatch.setattr(build_tray.sys, "platform", "darwin")
    cmd = build_tray.pyinstaller_cmd()
    joined = " ".join(cmd)
    assert "--windowed" in joined and "--onefile" not in joined   # .app 形态


def test_dmg_command_macos(monkeypatch):
    monkeypatch.setattr(build_tray.sys, "platform", "darwin")
    cmd = build_tray.dmg_cmd()
    assert "hdiutil" in cmd


def test_dmg_command_windows_raises(monkeypatch):
    monkeypatch.setattr(build_tray.sys, "platform", "win32")
    with pytest.raises(SystemExit):
        build_tray.dmg_cmd()
