"""桌面外观偏好保存在客户端配置中，退出重启后仍可恢复。"""
import pytest

import skill_guard as sg
from tray.app import build_runtime


@pytest.fixture
def theme_client(tmp_path, monkeypatch):
    """使用隔离配置与技能目录，避免读取用户会话或改动真实偏好。"""
    monkeypatch.setattr(sg, "HOME", str(tmp_path))
    monkeypatch.setattr(sg, "GUARD_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(tmp_path / "data/snapshots.json"))
    pool = tmp_path / "skills"
    pool.mkdir()
    clients = []

    def create():
        client = build_runtime([str(pool)], "warn")[2]
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.processing.stop()
        if client.usage is not None:
            client.usage.stop()


def test_theme_defaults_to_light(theme_client):
    assert theme_client().get_state()["settings"]["theme"] == "light"


def test_dark_theme_persists_across_client_restart(theme_client):
    client = theme_client()
    assert client.act("set_theme", {"theme": "dark"}) == {"ok": True, "theme": "dark"}
    assert sg.load_config()["ui_theme"] == "dark"
    assert client.get_state()["settings"]["theme"] == "dark"
    assert theme_client().get_state()["settings"]["theme"] == "dark"
    assert client.act("set_theme", {"theme": "light"}) == {"ok": True, "theme": "light"}
    assert sg.load_config()["ui_theme"] == "light"


@pytest.mark.parametrize("theme", [None, "", "system", "DARK", [], {}, True])
def test_invalid_theme_does_not_change_config(theme_client, theme):
    config = {"ui_theme": "dark", "ui_language": "en", "roots": [], "trust": {},
              "custom_preference": "preserved"}
    sg.save_config(config)
    before = sg.load_config()
    client = theme_client()
    assert client.act("set_theme", {"theme": theme}) == {"error": "choose light or dark"}
    assert sg.load_config() == before
    assert client.get_state()["settings"]["theme"] == "dark"


def test_setting_theme_preserves_language_and_other_config(theme_client):
    config = {"ui_language": "en", "roots": [{"path": "F:/custom"}],
              "consent": {"quarantine": False}, "trust": {"demo": "v1"},
              "custom_preference": "preserved"}
    sg.save_config(config)
    before = sg.load_config()
    client = theme_client()
    assert client.act("set_theme", {"theme": "dark"})["ok"] is True
    assert sg.load_config() == {**before, "ui_theme": "dark"}
    assert client.get_state()["settings"]["language"] == "en"


def test_unknown_saved_theme_falls_back_to_light(theme_client):
    sg.save_config({"ui_theme": "system"})
    assert theme_client().get_state()["settings"]["theme"] == "light"
