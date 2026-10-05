"""Installed skills are counted even when no observed invocation exists."""
from datetime import datetime, timedelta, timezone
import importlib.util
import os
from types import SimpleNamespace

import pytest


NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
READY = {"phase": "ready", "scanning": False, "files_scanned": 1,
         "roots": [{"exists": True}], "errors": []}


def inventory_module():
    assert importlib.util.find_spec("skill_inventory") is not None, "installed skill inventory is missing"
    import skill_inventory
    return skill_inventory


def installed(name, path=None, age=60):
    return {"name": name, "path": path or "/skills/" + name,
            "installed_at": (NOW - timedelta(days=age)).isoformat(),
            "updated_at": NOW.isoformat(), "install_time_source": "skill_md_birthtime"}


def test_file_creation_time_is_separate_from_update_time(monkeypatch):
    module = inventory_module()
    monkeypatch.setattr(module.os, "stat", lambda path: SimpleNamespace(
        st_birthtime=172800, st_ctime=259200, st_mtime=345600))
    result = module.file_times("/skills/demo")
    assert module.parse_time(result["installed_at"]) == datetime(1970, 1, 3, tzinfo=timezone.utc)
    assert module.parse_time(result["updated_at"]) == datetime(1970, 1, 5, tzinfo=timezone.utc)
    assert result["install_time_source"] == "skill_md_birthtime"


@pytest.mark.parametrize("platform, expected", [("win32", "1970-01-04T00:00:00+00:00"), ("linux", None)])
def test_only_windows_ctime_is_an_install_estimate(monkeypatch, platform, expected):
    module = inventory_module()
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(module.os, "stat", lambda path: SimpleNamespace(st_ctime=259200, st_mtime=345600))
    result = module.file_times("/skills/demo")
    assert result["installed_at"] == expected
    assert result["install_time_source"] == ("skill_md_windows_ctime" if expected else None)
    assert result["updated_at"] == "1970-01-05T00:00:00+00:00"


def test_missing_skill_file_leaves_both_times_unknown(tmp_path):
    result = inventory_module().file_times(str(tmp_path / "missing"))
    assert result == {"installed_at": None, "updated_at": None, "install_time_source": None}


def test_collect_inventory_covers_nested_and_direct_roots_without_reading_contents(tmp_path, monkeypatch):
    module = inventory_module()
    pool = tmp_path / "pool"
    skill = pool / "nested" / "unused"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("content is never read", encoding="utf-8")
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: pytest.fail("inventory read skill contents"))
    rows = module.collect_inventory([str(pool), {"path": str(skill)}, str(pool / "missing")])
    assert [(row["name"], row["path"]) for row in rows] == [("unused", str(skill))]
    assert rows[0]["installed_at"] and rows[0]["updated_at"]


def test_collect_inventory_deduplicates_resolved_aliases(monkeypatch):
    module = inventory_module()
    monkeypatch.setattr(module.sg, "iter_skill_dirs", lambda roots: iter(["/skills/unused", "/alias/unused"]))
    monkeypatch.setattr(module.os.path, "realpath", lambda path: "/skills/unused")
    monkeypatch.setattr(module, "file_times", lambda path: {})
    assert len(module.collect_inventory(["/skills", {"path": "/alias"}])) == 1


def test_never_used_skills_are_merged_from_the_complete_installation_list():
    module = inventory_module()
    inventory = [installed(f"skill-{index:02d}") for index in range(31)]
    result = module.merge_usage([], inventory, READY, now=NOW)
    assert len(result["installed_rows"]) == len(result["idle_groups"]["never"]) == 31
    assert result["inventory_summary"]["total_installed"] == 31
    assert result["inventory_summary"]["total_invocations"] == 0
    assert result["inventory_summary"]["coverage_complete"] is True
    assert all(row["total"] == row["codex"] == row["zcode"] == row["claude"] == row["marker"] == 0
               for row in result["installed_rows"])


def test_inactive_is_strictly_older_than_30_days_and_groups_do_not_overlap():
    module = inventory_module()
    rows = [{"name": "old", "total": 2, "codex": 2, "last": (NOW - timedelta(days=30, seconds=1)).isoformat()},
            {"name": "edge", "total": 1, "zcode": 1, "last": (NOW - timedelta(days=30)).isoformat()}]
    result = module.merge_usage(rows, [installed("old"), installed("edge"), installed("unused")], READY, now=NOW)
    assert [r["name"] for r in result["idle_groups"]["inactive"]] == ["old"]
    assert [r["name"] for r in result["idle_groups"]["never"]] == ["unused"]
    assert {r["name"]: r["usage_state"] for r in result["installed_rows"]}["edge"] == "active"
    assert result["inventory_summary"]["total_invocations"] == 3


@pytest.mark.parametrize("last", ["", "not-a-time", (NOW + timedelta(seconds=1)).isoformat()])
def test_positive_count_with_unknown_or_future_last_time_is_record_insufficient(last):
    result = inventory_module().merge_usage([{"name": "used", "total": 1, "last": last}],
                                          [installed("used")], READY, now=NOW)
    assert result["installed_rows"][0]["usage_state"] == "unknown"
    assert result["idle_groups"]["unknown"][0]["total"] == 1


@pytest.mark.parametrize("status", [
    {"phase": "idle"}, {**READY, "phase": "scanning", "scanning": True},
    {**READY, "phase": "error", "errors": [{"error": "PermissionError"}]},
    {**READY, "files_scanned": 0}, {**READY, "roots": [{"exists": False}]},
])
def test_absent_logs_or_unfinished_scan_do_not_claim_never_used(status):
    result = inventory_module().merge_usage([], [installed("unobserved")], status, now=NOW)
    assert result["idle_groups"]["never"] == []
    assert result["installed_rows"][0]["usage_state"] == "unknown"
    assert result["inventory_summary"]["coverage_complete"] is False
    assert result["inventory_summary"]["coverage_note"]


def test_missing_optional_agent_root_keeps_never_group_for_available_scanned_logs():
    status = {**READY, "roots": [{"exists": True}, {"exists": False}]}
    result = inventory_module().merge_usage([], [installed("unused")], status, now=NOW)
    assert result["idle_groups"]["never"][0]["name"] == "unused"
    assert result["inventory_summary"]["coverage_complete"] is True
    assert "部分会话日志目录不可用" in result["inventory_summary"]["coverage_note"]


def test_last_use_uses_actual_time_and_same_name_installations_share_without_double_total():
    module = inventory_module()
    rows = [{"name": "shared", "total": 2, "codex": 2, "last": "2026-09-05T23:59:59+14:00"},
            {"name": "shared", "total": 2, "codex": 2, "last": "2026-09-05T10:00:00Z"}]
    result = module.merge_usage(rows, [installed("shared", "/a/shared"), installed("shared", "/b/shared")], READY, now=NOW)
    assert all(row["last"] == "2026-09-05T10:00:00Z" for row in result["installed_rows"])
    assert all(row["shared_name"] is True and row["total"] == 2 for row in result["installed_rows"])
    assert result["inventory_summary"]["total_invocations"] == 2
    assert len(result["idle_groups"]["inactive"]) == 2


def test_recent_installations_are_marked_observing_without_changing_the_usage_group():
    result = inventory_module().merge_usage([], [installed("new", age=2), installed("old")], READY, now=NOW)
    assert {r["name"]: r["observing"] for r in result["installed_rows"]} == {"new": True, "old": False}
    assert len(result["idle_groups"]["never"]) == 2


def test_parse_time_is_aware_utc_and_preserves_legacy_naive_dates():
    module = inventory_module()
    assert module.parse_time("2026-10-05T20:00:00+08:00") == NOW
    assert module.parse_time("2026-10-05T12:00:00") == NOW
    assert module.parse_time("2026-10-05") == NOW.replace(hour=0)
    assert module.parse_time("invalid") is None


def test_registered_directory_alias_is_resolved_but_nested_aliases_are_not_followed(tmp_path):
    import subprocess
    module = inventory_module()
    physical = tmp_path / "physical"
    visible = physical / "visible"
    hidden = tmp_path / "outside" / "hidden"
    visible.mkdir(parents=True)
    hidden.mkdir(parents=True)
    for skill in (visible, hidden):
        (skill / "SKILL.md").write_text("# fixture", encoding="utf-8")
    registered = tmp_path / "registered-alias"
    nested = physical / "nested-alias"
    def create_alias(alias, target):
        if os.name == "nt":
            created = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(alias), str(target)],
                                     capture_output=True, text=True)
            if created.returncode:
                pytest.skip("junction creation unavailable")
        else:
            alias.symlink_to(target, target_is_directory=True)
    create_alias(registered, physical)
    create_alias(nested, hidden.parent)
    try:
        rows = module.collect_inventory([{"path": str(registered)}])
        assert [row["name"] for row in rows] == ["visible"]
        assert module.os.path.realpath(rows[0]["path"]) == str(visible)
        overlapping = module.collect_inventory([str(registered), str(physical), str(visible)])
        assert len(overlapping) == 1
    finally:
        # Remove only the verified fixture aliases, never their referenced directories.
        for alias in (nested, registered):
            assert str(alias.resolve()) in (str(physical), str(hidden.parent))
            if os.name == "nt":
                os.rmdir(alias)
            else:
                alias.unlink()
        assert (visible / "SKILL.md").exists() and (hidden / "SKILL.md").exists()
