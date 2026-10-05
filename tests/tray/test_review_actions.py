"""当前完整检查版本及人工决定的行为回归。"""
import hashlib
import json
from pathlib import Path

import pytest

import skill_guard as sg
from tray.daemon import Daemon
from tray.state import TrayState


RULES = "- id: T\n  category: EXEC\n  severity: HIGH\n  description: d\n  patterns: ['curl']\n"


@pytest.fixture
def pool(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(sg, "GUARD_DIR", str(data))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(data / "snapshots.json"))
    path = tmp_path / "pool"
    path.mkdir()
    sg.save_config({"roots": [{"path": str(path), "builtin": False}], "trust": {}})
    return path


def skill(pool, name="one", body="# A safe skill"):
    path = pool / name
    path.mkdir()
    (path / "SKILL.md").write_text(body, encoding="utf-8")
    return path


def test_complete_clean_skill_is_healthy_without_manual_confirmation(pool):
    path = skill(pool)
    daemon = Daemon([str(pool)])
    daemon.scan_changed_skill(str(path))
    row = daemon.state.snapshot()["skills"][str(path)]
    assert row["check_status"] == "healthy"
    assert row["scan_complete"] is True
    assert row["review_status"] == "not_required"
    assert row["scan_issues"] == []
    assert len(row["version"]) == 64


def test_check_time_has_explicit_local_timezone_in_state_and_baseline(pool):
    from datetime import datetime
    path = skill(pool)
    daemon = Daemon([str(pool)])
    daemon.scan_changed_skill(str(path))
    row = daemon.state.snapshot()["skills"][str(path)]
    baseline = sg.load_snapshots()["skills"][str(path)]
    assert datetime.fromisoformat(row["scanned_at"]).tzinfo is not None
    assert datetime.fromisoformat(baseline["scanned_at"]).tzinfo is not None


@pytest.mark.parametrize("name,header", [
    ("preview.png", b"\x89PNG\r\n\x1a\n\x00image"),
    ("photo.jpg", b"\xff\xd8\xff\xe0\x00photo"),
    ("demo.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00audio"),
    ("type.ttf", b"\x00\x01\x00\x00\x00font"),
    ("icon.webp", b"RIFF\x00\x00\x00\x00WEBPimage"),
])
def test_signature_validated_assets_do_not_leave_completed_check_incomplete(pool, name, header):
    path = skill(pool)
    assets = path / "assets"
    assets.mkdir()
    (assets / name).write_bytes(header)
    daemon = Daemon([str(pool)])
    daemon.scan_changed_skill(str(path))
    row = daemon.state.snapshot()["skills"][str(path)]
    assert row["check_status"] == "healthy"
    assert row["scan_complete"] is True
    assert row["scan_issues"] == []
    coverage = row["scan_coverage"]
    assert coverage["text_files_checked"] == 1
    assert coverage["asset_files_hashed"] == 1
    assert coverage["hashed_files"] == 2
    assert coverage["assets_without_text_check"] == ["assets/" + name]


def test_asset_classification_does_not_depend_on_null_byte_heuristic(pool):
    from tray.review import capture_version
    path = skill(pool)
    (path / "animation.gif").write_bytes(b"GIF89aimagecontent")
    captured = capture_version(str(path), RULES, "[]")
    assert captured["complete"] is True
    assert captured["coverage"]["asset_files_hashed"] == 1


@pytest.mark.parametrize("body", [b"MZexecutablewithoutnull", b"payload\x00data"])
def test_renamed_or_unrecognized_binary_is_still_a_check_gap(pool, body):
    path = skill(pool)
    (path / "fake.png").write_bytes(body)
    daemon = Daemon([str(pool)])
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["scan_complete"] is False
    assert "binary_not_text_checked:fake.png" in row["scan_issues"]


def test_supported_asset_still_matches_original_byte_hash_blocklist(pool):
    path = skill(pool)
    raw = b"\x89PNG\r\n\x1a\n\x00IOC"
    (path / "image.png").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    daemon = Daemon([str(pool)], blocklist_text=f"- hash: {digest}\n")
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["scan_complete"] is True
    assert row["check_status"] == "attention"
    assert any(f["rule_id"] == "SR-BLOCK-001" for f in row["raw_findings"])


def test_excluded_dependency_directory_is_explicit_in_check_coverage(pool):
    path = skill(pool)
    ignored = path / "node_modules"
    ignored.mkdir()
    (ignored / "payload.bin").write_bytes(b"payload\x00")
    daemon = Daemon([str(pool)])
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["scan_complete"] is True
    assert row["scan_coverage"]["excluded_directories"] == ["node_modules"]


def test_large_asset_is_hashed_but_known_oversized_text_remains_incomplete(pool, monkeypatch):
    from tray.review import capture_version
    path = skill(pool)
    monkeypatch.setattr(sg, "MAX_FILE_BYTES", 64)
    (path / "large.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 65)
    captured = capture_version(str(path), RULES, "[]")
    assert captured["complete"] is True
    assert captured["coverage"]["assets_without_text_check"] == ["large.png"]
    (path / "large.md").write_text("x" * 65, encoding="utf-8")
    captured = capture_version(str(path), RULES, "[]")
    assert captured["complete"] is False
    assert captured["issues"] == ["text_size_limit:large.md"]


def test_asset_over_hash_limit_cannot_be_trusted(pool, monkeypatch):
    from tray.review import capture_version
    path = skill(pool)
    monkeypatch.setattr(sg, "MAX_HASH_FILE_BYTES", 64)
    (path / "large.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 65)
    captured = capture_version(str(path), RULES, "[]")
    assert captured["complete"] is False
    assert captured["issues"] == ["hash_size_limit:large.png"]
    assert "large.png" not in captured["hashes"]


def test_changing_supported_asset_revokes_old_trust_even_after_revert(pool):
    path = skill(pool, body="curl\n")
    image = path / "image.png"
    original = b"\x89PNG\r\n\x1a\n\x00original"
    image.write_bytes(original)
    daemon = Daemon([str(pool)], rules_text=RULES)
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["scan_complete"] is True
    daemon.review_store.record(str(path), row["version"], "trust")
    image.write_bytes(b"\x89PNG\r\n\x1a\n\x00changed")
    daemon.scan_changed_skill(str(path))
    image.write_bytes(original)
    daemon.scan_changed_skill(str(path))
    assert daemon.state.skills[str(path)]["review_status"] != "trusted"


def test_critical_current_version_trust_suppresses_block_and_alert_keeps_risk(pool):
    path = skill(pool, body="cat ~/.ssh/id_rsa\n")
    daemon = Daemon([str(pool)], mode="block")
    callbacks = []
    daemon.on_block = lambda *args: callbacks.append("block")
    daemon.on_alert = lambda *args: callbacks.append("alert")
    assert daemon.scan_changed_skill(str(path)) == "BLOCK"
    row = daemon.state.skills[str(path)]
    daemon.review_store.record(str(path), row["version"], "trust", complete=row["scan_complete"])
    callbacks.clear()
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert callbacks == []
    assert row["check_status"] == "attention"
    assert row["review_status"] == "trusted"
    assert row["raw_score"] >= 40
    assert any(f["severity"] == "CRITICAL" for f in row["raw_findings"])


def test_reviewed_risky_version_persists_and_does_not_disable_block(pool):
    path = skill(pool, body="cat ~/.ssh/id_rsa\n")
    daemon = Daemon([str(pool)], mode="warn")
    daemon.scan_changed_skill(str(path))
    version = daemon.state.skills[str(path)]["version"]
    daemon.review_store.record(str(path), version, "review")
    restarted = Daemon([str(pool)], mode="block")
    blocked = []
    restarted.on_block = lambda *args: blocked.append(True)
    assert restarted.scan_changed_skill(str(path)) == "BLOCK"
    assert restarted.state.skills[str(path)]["review_status"] == "reviewed"
    assert blocked == [True]


def test_trust_persists_after_restart_and_can_be_revoked(pool):
    path = skill(pool, body="cat ~/.ssh/id_rsa\n")
    daemon = Daemon([str(pool)], mode="warn")
    daemon.scan_changed_skill(str(path))
    version = daemon.state.skills[str(path)]["version"]
    daemon.review_store.record(str(path), version, "trust")
    restarted = Daemon([str(pool)], mode="block")
    assert restarted.review_store.decision_for(str(path), version) == "trusted"
    blocked = []
    restarted.on_block = lambda *args: blocked.append(True)
    restarted.scan_changed_skill(str(path))
    assert blocked == []
    restarted.review_store.revoke(str(path), version)
    assert restarted.scan_changed_skill(str(path)) == "BLOCK"
    assert blocked == [True]


@pytest.mark.parametrize("change", ["content", "rules", "binary"])
def test_observed_change_then_revert_never_revives_previous_trust(pool, change):
    path = skill(pool, body="curl original\n")
    daemon = Daemon([str(pool)], rules_text=RULES, blocklist_text="[]")
    daemon.scan_changed_skill(str(path))
    version = daemon.state.skills[str(path)]["version"]
    daemon.review_store.record(str(path), version, "trust")
    if change == "content":
        (path / "SKILL.md").write_text("curl updated\n", encoding="utf-8")
    elif change == "rules":
        daemon.rules_text = RULES.replace("HIGH", "CRITICAL")
    else:
        (path / "payload.bin").write_bytes(b"payload\x00")
    daemon.scan_changed_skill(str(path))
    if change == "content":
        (path / "SKILL.md").write_text("curl original\n", encoding="utf-8")
    elif change == "rules":
        daemon.rules_text = RULES
    else:
        (path / "payload.bin").unlink()
    restarted = Daemon([str(pool)], rules_text=RULES, blocklist_text="[]")
    restarted.scan_changed_skill(str(path))
    row = restarted.state.skills[str(path)]
    assert row["version"] == version
    assert row["review_status"] != "trusted"
    assert restarted.review_store.decision_for(str(path), version) != "trusted"


@pytest.mark.parametrize("change", ["script", "add", "remove", "rename", "rules", "blocklist"])
def test_file_or_policy_change_invalidates_current_version_trust(pool, change):
    path = skill(pool, body="curl x\n")
    helper = path / "helper.py"
    helper.write_text("print('safe')", encoding="utf-8")
    daemon = Daemon([str(pool)], rules_text=RULES, blocklist_text="[]")
    daemon.scan_changed_skill(str(path))
    version = daemon.state.skills[str(path)]["version"]
    daemon.review_store.record(str(path), version, "trust")
    notes = []
    daemon.on_alert = lambda *args: notes.append(True)
    if change == "script":
        helper.write_text("print('changed')", encoding="utf-8")
    elif change == "add":
        (path / "new.py").write_text("pass", encoding="utf-8")
    elif change == "remove":
        helper.unlink()
    elif change == "rename":
        helper.rename(path / "renamed.py")
    elif change == "rules":
        daemon.rules_text = RULES.replace("severity: HIGH", "severity: CRITICAL")
    else:
        daemon.blocklist_text = "- name: unmatched\n"
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["version"] != version
    assert row["review_status"] == "pending"
    assert notes


def test_version_uses_original_bytes_and_does_not_depend_on_absolute_location(pool, tmp_path):
    from tray.review import capture_version
    a = skill(pool)
    (a / "raw.txt").write_bytes(b"\xff")
    first = capture_version(str(a), RULES, "[]")
    assert first["hashes"]["raw.txt"] == hashlib.sha256(b"\xff").hexdigest()
    (a / "raw.txt").write_bytes(b"\xfe")
    second = capture_version(str(a), RULES, "[]")
    assert first["version"] != second["version"]
    import shutil
    copied = tmp_path / "copied" / a.name
    shutil.copytree(a, copied)
    assert capture_version(str(copied), RULES, "[]")["version"] == second["version"]


def test_version_binds_skill_name_for_name_blocklist_and_allows_quarantine_alias(pool, tmp_path):
    from tray.review import capture_version
    import shutil
    original = skill(pool, "one")
    moved = tmp_path / "container" / "skill"
    shutil.copytree(original, moved)
    captured = capture_version(str(original), RULES, "- name: one\n")
    assert capture_version(str(moved), RULES, "- name: one\n")["version"] != captured["version"]
    assert capture_version(str(moved), RULES, "- name: one\n", skill_name="one")["version"] == captured["version"]


@pytest.mark.parametrize("kind", ["binary", "large", "unreadable", "link"])
def test_incomplete_scan_never_healthy_or_trustable(pool, monkeypatch, kind):
    from tray.review import capture_version
    path = skill(pool)
    file = path / "payload.dat"
    if kind == "binary":
        file.write_bytes(b"payload\x00data")
    elif kind == "large":
        file.write_bytes(b"x" * (sg.MAX_FILE_BYTES + 1))
    elif kind == "unreadable":
        file.write_text("plain", encoding="utf-8")
        read = sg._read_regular_file
        monkeypatch.setattr(sg, "_read_regular_file", lambda p, limit: None if str(p) == str(file) else read(p, limit))
    else:
        file.write_text("original", encoding="utf-8")
        linked = path / "linked"
        try:
            linked.symlink_to(file)
        except OSError:
            pytest.skip("symlink privilege unavailable")
    daemon = Daemon([str(pool)])
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["check_status"] == "incomplete"
    assert row["scan_complete"] is False
    assert row["scan_issues"]
    captured = capture_version(str(path), daemon.rules_text, daemon.blocklist_text)
    with pytest.raises(ValueError):
        daemon.review_store.record(str(path), captured["version"], "trust", complete=captured["complete"])


def test_file_changes_during_engine_scan_invalidates_evidence(pool, monkeypatch):
    path = skill(pool)
    engine = sg.run_engine
    def changing_engine(*args, **kwargs):
        report = engine(*args, **kwargs)
        (path / "SKILL.md").write_text("curl changed\n", encoding="utf-8")
        return report
    monkeypatch.setattr(sg, "run_engine", changing_engine)
    daemon = Daemon([str(pool)], rules_text=RULES)
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["scan_complete"] is False
    assert row["check_status"] == "incomplete"
    assert "changed_during_scan" in row["scan_issues"]


def test_scan_changed_critical_content_does_not_quarantine_a_different_version(pool, monkeypatch):
    path = skill(pool, body="cat ~/.ssh/id_rsa\n")
    engine = sg.run_engine
    def changing_engine(*args, **kwargs):
        report = engine(*args, **kwargs)
        (path / "SKILL.md").write_text("# now safe\n", encoding="utf-8")
        return report
    monkeypatch.setattr(sg, "run_engine", changing_engine)
    daemon = Daemon([str(pool)], mode="block")
    blocked = []
    daemon.on_block = lambda *args: blocked.append(True)
    assert daemon.scan_changed_skill(str(path)) != "BLOCK"
    assert blocked == []
    assert daemon.state.skills[str(path)]["check_status"] == "incomplete"


def test_engine_exception_replaces_stale_healthy_state_with_error(pool, monkeypatch):
    path = skill(pool)
    daemon = Daemon([str(pool)])
    daemon.scan_changed_skill(str(path))
    assert daemon.state.skills[str(path)]["check_status"] == "healthy"
    def failing_engine(*args, **kwargs):
        raise OSError("read failed")
    monkeypatch.setattr(sg, "run_engine", failing_engine)
    assert daemon.scan_changed_skill(str(path)) == "ERROR"
    row = daemon.state.skills[str(path)]
    assert row["check_status"] == "error"
    assert row["scan_complete"] is False
    assert row["review_status"] == "pending"


def test_incomplete_new_scan_cannot_apply_a_previous_trust_decision(pool, monkeypatch):
    path = skill(pool, body="curl\n")
    daemon = Daemon([str(pool)], rules_text=RULES)
    daemon.scan_changed_skill(str(path))
    first = daemon.state.skills[str(path)]["version"]
    daemon.review_store.record(str(path), first, "trust")
    engine = sg.run_engine
    def truncated_engine(*args, **kwargs):
        report = engine(*args, **kwargs)
        report.findings.append(sg.Finding("SR-OBFUS-004", "OBFUS", "LOW", "SKILL.md", 1, "", "truncated", []))
        return report
    monkeypatch.setattr(sg, "run_engine", truncated_engine)
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["version"] == first
    assert row["review_status"] == "pending"
    assert row["scan_complete"] is False
    assert "decode_limit" in row["scan_issues"]


def test_temporary_read_failure_pauses_trust_without_permanently_revoking_it(pool, monkeypatch):
    path = skill(pool, body="curl original\n")
    helper = path / "helper.py"
    helper.write_text("pass", encoding="utf-8")
    daemon = Daemon([str(pool)], rules_text=RULES)
    daemon.scan_changed_skill(str(path))
    version = daemon.state.skills[str(path)]["version"]
    daemon.review_store.record(str(path), version, "trust")
    read = sg._read_regular_file
    monkeypatch.setattr(sg, "_read_regular_file", lambda p, limit: None if str(p) == str(helper) else read(p, limit))
    daemon.scan_changed_skill(str(path))
    assert daemon.state.skills[str(path)]["review_status"] != "trusted"
    assert daemon.review_store.decision_for(str(path), version) == "trusted"
    monkeypatch.setattr(sg, "_read_regular_file", read)
    daemon.scan_changed_skill(str(path))
    assert daemon.state.skills[str(path)]["review_status"] == "trusted"


def test_incomplete_check_can_record_review_without_becoming_healthy(pool):
    path = skill(pool)
    (path / "payload.bin").write_bytes(b"binary\x00payload")
    daemon = Daemon([str(pool)])
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    daemon.review_store.record(str(path), row["version"], "review", complete=False)
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["review_status"] == "reviewed"
    assert row["check_status"] == "incomplete"
    assert row["scan_complete"] is False


def test_read_only_store_failure_never_claims_saved_trust(pool, monkeypatch):
    from tray.review import ReviewStore
    path = skill(pool)
    store = ReviewStore()
    def write_failure(*args, **kwargs):
        raise PermissionError("cannot save decision")
    monkeypatch.setattr(sg, "_atomic_write", write_failure)
    with pytest.raises(PermissionError):
        store.record(str(path), "v1", "trust")
    assert store.decision_for(str(path), "v1") == "pending"


def test_plain_directory_without_skill_descriptor_has_incomplete_evidence(pool):
    from tray.review import capture_version
    path = pool / "not_a_skill"
    path.mkdir()
    captured = capture_version(str(path), RULES, "[]")
    assert captured["complete"] is False
    assert "invalid_skill_directory" in captured["issues"]


def test_rule_path_version_binds_effective_file_contents(pool, tmp_path):
    from tray.review import capture_version
    path = skill(pool)
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES, encoding="utf-8")
    first = capture_version(str(path), str(rules), "[]")
    assert first["version"] == capture_version(str(path), RULES, "[]")["version"]
    rules.write_text(RULES.replace("HIGH", "CRITICAL"), encoding="utf-8")
    assert capture_version(str(path), str(rules), "[]")["version"] != first["version"]


def test_content_identity_survives_rule_changes_but_tracks_all_payload_entries(pool):
    from tray.review import capture_version
    path = skill(pool)
    first = capture_version(str(path), RULES, "[]")
    changed_rules = capture_version(str(path), RULES.replace("HIGH", "CRITICAL"), "[]")
    assert changed_rules["version"] != first["version"]
    assert changed_rules["content_version"] == first["content_version"]
    (path / "payload.bin").write_bytes(b"payload\x00")
    with_binary = capture_version(str(path), RULES, "[]")
    assert with_binary["content_version"] != first["content_version"]
    assert with_binary["complete"] is False


def test_existing_cli_trust_still_downgrades_but_raw_ui_risk_is_preserved(pool):
    path = skill(pool, body="curl\n")
    cfg = sg.load_config()
    cfg["trust"] = {"hashes": [hashlib.sha256((path / "SKILL.md").read_bytes()).hexdigest()]}
    sg.save_config(cfg)
    daemon = Daemon([str(pool)], rules_text=RULES)
    daemon.scan_changed_skill(str(path))
    row = daemon.state.skills[str(path)]
    assert row["findings"][0]["severity"] == "MEDIUM"
    assert row["raw_findings"][0]["severity"] == "HIGH"
    assert row["raw_score"] == 25
    assert row["check_status"] == "attention"


def test_manual_decisions_are_exact_path_and_preserve_original_path(pool):
    from tray.review import ReviewStore
    a, b = skill(pool, "a"), skill(pool, "b")
    store = ReviewStore()
    store.record(str(a), "v1", "trust")
    assert store.decision_for(str(a), "v1") == "trusted"
    assert store.decision_for(str(b), "v1") == "pending"
    saved = json.loads((Path(sg.GUARD_DIR) / "review_decisions.json").read_text(encoding="utf-8"))
    assert next(iter(saved["decisions"].values()))["path"] == str(a)


def test_state_records_extra_metadata_and_removes_only_selected_path():
    state = TrayState()
    issues = ["file_unreadable:x"]
    state.record_skill("/pool/a", "same", 0, "scanned", scan_issues=issues, check_status="incomplete")
    state.record_skill("/pool/b", "same", 0, "scanned")
    issues.clear()
    assert state.skills["/pool/a"]["scan_issues"] == ["file_unreadable:x"]
    state.remove_skill("/pool/a")
    assert list(state.skills) == ["/pool/b"]
