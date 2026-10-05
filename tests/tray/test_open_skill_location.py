"""打开技能位置只允许登记范围内的完整技能目录，不执行路径中的命令。"""
import os
import subprocess
import types

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import skill_guard as sg
    from tray.app import JsBridge
    from tray.daemon import Daemon
    from tray.state import TrayState

    monkeypatch.setattr(sg, "HOME", str(tmp_path))
    monkeypatch.setattr(sg, "GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(tmp_path / "guard/snapshots.json"))
    pools = [tmp_path / "first pool", tmp_path / "second pool"]
    skills = []
    state = TrayState()
    for pool in pools:
        skill = pool / "same"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# same\n", encoding="utf-8")
        state.record_skill(str(skill), "same", 0, "scanned", version="checked")
        skills.append(skill)
    daemon = Daemon([str(pool) for pool in pools], state=state,
                    rules_text="", blocklist_text="[]")
    bridge = JsBridge(daemon, state, types.SimpleNamespace(window=None, icon=None))
    yield bridge, skills
    bridge.processing.stop()


def stub_opener(monkeypatch, platform="win32", error=None):
    import tray.app as app
    opened = []
    monkeypatch.setattr(app.sys, "platform", platform)

    def startfile(path, operation="open"):
        if error:
            raise error
        opened.append((path, operation))

    def run(args, **kwargs):
        if error:
            raise error
        opened.append((args, kwargs))

    monkeypatch.setattr(app.os, "startfile", startfile, raising=False)
    monkeypatch.setattr(app.subprocess, "run", run)
    return opened


def test_windows_opens_selected_same_name_directory_without_mutating_state(client, monkeypatch):
    bridge, skills = client
    opened = stub_opener(monkeypatch)
    before = bridge.state.snapshot()
    result = bridge.act("open_skill_location", {"skill": str(skills[1])})
    assert result == {"ok": True, "path": str(skills[1])}
    assert opened == [(str(skills[1]), "open")]
    assert bridge.state.snapshot() == before
    assert (skills[0] / "SKILL.md").read_text(encoding="utf-8") == "# same\n"


@pytest.mark.parametrize("platform,executable", [("darwin", "open"), ("linux", "xdg-open")])
def test_other_platforms_use_native_argument_array_without_shell(client, monkeypatch, platform, executable):
    bridge, skills = client
    opened = stub_opener(monkeypatch, platform)
    result = bridge.act("open_skill_location", {"skill": str(skills[0])})
    assert result["ok"] is True
    assert opened[0][0] == [executable, str(skills[0])]
    assert opened[0][1].get("check") is True
    assert not opened[0][1].get("shell", False)


@pytest.mark.parametrize("value", [None, "same", "../same", [], {}, "C:/missing", "\x00bad", "bad\npath"])
def test_malformed_or_unlisted_requests_do_not_open_anything(client, monkeypatch, value):
    bridge, _ = client
    opened = stub_opener(monkeypatch)
    result = bridge.act("open_skill_location", {"skill": value})
    assert "error" in result and not result.get("ok")
    assert opened == []


def test_current_row_outside_registered_roots_cannot_be_opened(client, tmp_path, monkeypatch):
    bridge, _ = client
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("# outside", encoding="utf-8")
    bridge.state.record_skill(str(outside), "outside", 0, "scanned")
    opened = stub_opener(monkeypatch)
    assert "error" in bridge.act("open_skill_location", {"skill": str(outside)})
    assert opened == []


def test_removed_skill_directory_is_not_opened_using_old_row(client, monkeypatch):
    bridge, skills = client
    skills[0].rename(skills[0].with_name("moved"))
    opened = stub_opener(monkeypatch)
    result = bridge.act("open_skill_location", {"skill": str(skills[0])})
    assert "刷新报告" in result["error"]
    assert opened == []


def test_missing_skill_document_is_rejected(client, monkeypatch):
    bridge, skills = client
    (skills[0] / "SKILL.md").unlink()
    opened = stub_opener(monkeypatch)
    assert "刷新报告" in bridge.act("open_skill_location", {"skill": str(skills[0])})["error"]
    assert opened == []


def test_reparse_point_in_ancestor_chain_is_rejected_before_os_open(client, monkeypatch):
    import skill_guard as sg
    bridge, skills = client
    ancestor = skills[0].parent
    original = sg._is_reparse
    monkeypatch.setattr(sg, "_is_reparse", lambda path: os.path.normpath(path) == str(ancestor) or original(path))
    opened = stub_opener(monkeypatch)
    assert "刷新报告" in bridge.act("open_skill_location", {"skill": str(skills[0])})["error"]
    assert opened == []


def test_registered_root_above_declared_junction_opens_verified_realpath(client, tmp_path, monkeypatch):
    import skill_guard as sg
    bridge, skills = client
    alias = tmp_path / "codex-home-alias"
    target = tmp_path / "codex-home-real"
    canonical = target / "skills" / "same"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("# same", encoding="utf-8")
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(target, target_is_directory=True)
    selected = alias / "skills" / "same"
    bridge.daemon.roots = [str(alias / "skills")]
    bridge.state.record_skill(str(selected), "same", 0, "scanned", version="checked")
    assert sg._is_reparse(str(alias)) is True
    opened = stub_opener(monkeypatch)
    response = bridge.act("open_skill_location", {"skill": str(selected)})
    assert response == {"ok": True, "path": os.path.realpath(canonical)}
    assert opened == [(os.path.realpath(canonical), "open")]


def test_unchecked_inventory_realpath_opens_under_registered_ancestor_alias(client, tmp_path, monkeypatch):
    bridge, _ = client
    alias = tmp_path / "client-alias"
    target = tmp_path / "client-real"
    skill = target / "skills" / "unchecked"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# unchecked", encoding="utf-8")
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(target, target_is_directory=True)
    bridge.daemon.roots = [str(alias / "skills")]
    opened = stub_opener(monkeypatch)
    response = bridge.act("open_skill_location", {"skill": str(skill)})
    assert response == {"ok": True, "path": str(skill)}
    assert opened == [(str(skill), "open")]


def test_canonical_request_cannot_bypass_linked_registered_root(client, monkeypatch):
    import skill_guard as sg
    bridge, skills = client
    root = skills[0].parent
    original = sg._is_reparse
    monkeypatch.setattr(sg, "_is_reparse", lambda path: os.path.normpath(path) == str(root) or original(path))
    opened = stub_opener(monkeypatch)
    assert "error" in bridge.act("open_skill_location", {"skill": os.path.realpath(skills[0])})
    assert opened == []


def test_linked_container_below_declared_root_still_rejected(client, tmp_path, monkeypatch):
    import skill_guard as sg
    bridge, skills = client
    container = skills[0].parent / "nested"
    nested = container / "same"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("# nested", encoding="utf-8")
    bridge.state.record_skill(str(nested), "same", 0, "scanned", version="checked")
    original = sg._is_reparse
    monkeypatch.setattr(sg, "_is_reparse", lambda path: os.path.normpath(path) == str(container) or original(path))
    opened = stub_opener(monkeypatch)
    assert "刷新报告" in bridge.act("open_skill_location", {"skill": str(nested)})["error"]
    assert opened == []


def test_skill_symlink_cannot_redirect_open_to_other_same_name_copy(client, monkeypatch):
    bridge, skills = client
    skills[0].rename(skills[0].with_name("original"))
    try:
        skills[0].symlink_to(skills[1], target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    opened = stub_opener(monkeypatch)
    assert "error" in bridge.act("open_skill_location", {"skill": str(skills[0])})
    assert opened == []


@pytest.mark.parametrize("platform,error", [("win32", OSError("folder open failed\u200b")),
    ("linux", subprocess.CalledProcessError(1, ["xdg-open", "folder"]))])
def test_native_open_failure_returns_clean_error(client, monkeypatch, platform, error):
    bridge, skills = client
    stub_opener(monkeypatch, platform, error)
    result = bridge.act("open_skill_location", {"skill": str(skills[0])})
    assert "error" in result and not result.get("ok")
    assert result["error"] != "unknown action"
    assert "\u200b" not in result["error"]
    assert "文件管理器" in result["error"]


def test_removed_registered_root_prevents_open(client, monkeypatch):
    bridge, skills = client
    bridge.daemon.roots.remove(str(skills[0].parent))
    opened = stub_opener(monkeypatch)
    assert "error" in bridge.act("open_skill_location", {"skill": str(skills[0])})
    assert opened == []


def test_unchecked_inventory_skill_opens_without_scanning_or_changing_state(client, monkeypatch):
    bridge, skills = client
    unlisted = skills[0].parent / "unlisted"
    unlisted.mkdir()
    (unlisted / "SKILL.md").write_text("# unlisted", encoding="utf-8")
    before = bridge.state.snapshot()
    opened = stub_opener(monkeypatch)
    result = bridge.act("open_skill_location", {"skill": str(unlisted)})
    assert result == {"ok": True, "path": str(unlisted)}
    assert opened == [(str(unlisted), "open")]
    assert bridge.state.snapshot() == before
    assert bridge.daemon.dirty_roots() == set()
    assert bridge.usage is None


def test_registered_pool_itself_cannot_be_opened_even_with_skill_document(client, monkeypatch):
    bridge, skills = client
    root = skills[0].parent
    (root / "SKILL.md").write_text("# pool", encoding="utf-8")
    bridge.state.record_skill(str(root), "pool", 0, "scanned")
    opened = stub_opener(monkeypatch)
    result = bridge.act("open_skill_location", {"skill": str(root)})
    assert "error" in result
    assert opened == []


def test_skill_registered_as_its_own_root_is_rejected_even_under_another_pool(client, monkeypatch):
    bridge, skills = client
    bridge.daemon.roots.append(str(skills[0]))
    opened = stub_opener(monkeypatch)
    assert "error" in bridge.act("open_skill_location", {"skill": str(skills[0])})
    assert opened == []


def test_unchecked_folder_inside_another_skill_is_not_an_inventory_installation(client, monkeypatch):
    bridge, skills = client
    nested = skills[0] / "example-skill"
    nested.mkdir()
    (nested / "SKILL.md").write_text("# example", encoding="utf-8")
    opened = stub_opener(monkeypatch)
    assert "error" in bridge.act("open_skill_location", {"skill": str(nested)})
    assert opened == []


def test_removed_unchecked_skill_is_not_opened_from_stale_report_path(client, monkeypatch):
    bridge, skills = client
    previous = skills[0].parent / "report-candidate"
    previous.mkdir()
    (previous / "SKILL.md").write_text("# candidate", encoding="utf-8")
    requested = str(previous)
    previous.rename(previous.with_name("moved-candidate"))
    opened = stub_opener(monkeypatch)
    result = bridge.act("open_skill_location", {"skill": requested})
    assert "error" in result
    assert opened == []


def test_inventory_candidate_moved_during_validation_is_not_opened(client, monkeypatch):
    import skill_inventory
    bridge, skills = client
    candidate = skills[0].parent / "candidate"
    candidate.mkdir()
    (candidate / "SKILL.md").write_text("# candidate", encoding="utf-8")
    collect = skill_inventory.collect_inventory

    def collect_then_move(roots):
        rows = collect(roots)
        candidate.rename(candidate.with_name("moved-candidate"))
        return rows

    monkeypatch.setattr(skill_inventory, "collect_inventory", collect_then_move)
    opened = stub_opener(monkeypatch)
    result = bridge.act("open_skill_location", {"skill": str(candidate)})
    assert "error" in result
    assert opened == []


def test_quarantine_copy_cannot_be_opened_as_an_active_skill(client, monkeypatch):
    bridge, _ = client
    isolated = os.path.join(bridge.processing.quarantine_dir, "isolated")
    os.makedirs(isolated)
    with open(os.path.join(isolated, "SKILL.md"), "w", encoding="utf-8") as file:
        file.write("# isolated")
    bridge.daemon.roots.append(bridge.processing.quarantine_dir)
    bridge.state.record_skill(isolated, "isolated", 0, "scanned")
    opened = stub_opener(monkeypatch)
    assert "error" in bridge.act("open_skill_location", {"skill": isolated})
    assert opened == []


def test_active_directory_replaced_by_file_is_rejected(client, monkeypatch):
    bridge, skills = client
    skills[0].rename(skills[0].with_name("moved"))
    skills[0].write_text("replaced", encoding="utf-8")
    opened = stub_opener(monkeypatch)
    assert "刷新报告" in bridge.act("open_skill_location", {"skill": str(skills[0])})["error"]
    assert opened == []
