"""桌面批量操作的真实目录、处理记录和恢复边界回归。"""
import json
import os
import threading
import time
from pathlib import Path

import pytest

import skill_guard as sg
from tray.app import build_runtime


@pytest.fixture
def client(tmp_path, monkeypatch):
    data, pool = tmp_path / "data", tmp_path / "pool"
    pool.mkdir()
    monkeypatch.setattr(sg, "GUARD_DIR", str(data))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(data / "snapshots.json"))
    sg.save_config({"roots": [{"path": str(pool), "builtin": False}],
                    "consent": {}, "trust": {}})
    monkeypatch.setattr("tray.alerts.toast", lambda *a, **kw: None)
    return pool, build_runtime([str(pool)], "warn")


def skill(pool, daemon, name="demo", body="# safe"):
    path = pool / name
    path.mkdir()
    (path / "SKILL.md").write_text(body, encoding="utf-8")
    daemon.scan_changed_skill(str(path))
    return path


def selected(path, state):
    return {"path": str(path), "version": state.snapshot()["skills"][str(path)]["version"]}


def finished(bridge):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = bridge.get_state().get("batch_job")
        if job and job["status"] == "done":
            return job
        time.sleep(.01)
    pytest.fail("batch operation did not finish")


def ready_quarantine(bridge):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = bridge.get_state()["quarantine"]
        if rows and not any(row.get("refreshing") for row in rows):
            return rows
        time.sleep(.01)
    pytest.fail("quarantine checks did not finish")


def run(bridge, action, items):
    response = bridge.act("batch_action", {"action": action, "items": items})
    assert response.get("ok"), response
    job = finished(bridge)
    assert job["id"] == response["job_id"]
    assert job["completed"] == len(items)
    return job


def test_bridge_exposes_persistent_processing_state(client):
    _, (_, _, bridge) = client
    assert bridge.get_state().get("batch_job", "missing") is None
    assert bridge.get_state().get("quarantine", "missing") == []


def test_batch_rejects_invalid_and_name_only_selections(client):
    _, (_, _, bridge) = client
    for action, items in [("erase", []), ("review", []),
                          ("review", [{"path": "demo", "version": "v"}])]:
        response = bridge.act("batch_action", {"action": action, "items": items})
        assert response.get("ok") is False
        assert response.get("code") in ("invalid_action", "invalid_items")


def test_review_targets_exact_full_path_with_duplicate_names(client, tmp_path):
    pool, (daemon, state, bridge) = client
    other = tmp_path / "other"
    other.mkdir()
    daemon.roots.append(str(other))
    a = skill(pool, daemon, "same", "cat ~/.ssh/id_rsa\n")
    b = skill(other, daemon, "same", "cat ~/.ssh/id_rsa\n")
    result = run(bridge, "review", [selected(b, state)])["results"][0]
    assert result["status"] == "success"
    assert state.snapshot()["skills"][str(a)]["review_status"] == "pending"
    assert state.snapshot()["skills"][str(b)]["review_status"] == "reviewed"


def test_changed_version_is_skipped_without_trust(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    item = selected(path, state)
    (path / "SKILL.md").write_text("# edited after selection", encoding="utf-8")
    result = run(bridge, "trust", [item])["results"][0]
    assert (result["status"], result["code"]) == ("skipped", "version_changed")
    assert daemon.review_store.decision_for(str(path), item["version"]) == "pending"


def test_recheck_refreshes_old_incomplete_row_after_user_fixes_file(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    payload = path / "payload.bin"
    payload.write_bytes(b"unrecognized\x00binary")
    daemon.scan_changed_skill(str(path))
    item = selected(path, state)
    assert state.skills[str(path)]["scan_complete"] is False
    payload.write_text("# file corrected before watcher refresh", encoding="utf-8")
    result = run(bridge, "rescan", [item])["results"][0]
    assert (result["status"], result["code"]) == ("success", "ok")
    assert result["version"] != item["version"]
    assert result["attempt_complete"] is True
    assert result["scan_complete"] is True
    assert result["check_status"] == "healthy"
    assert result["scanned_at"]
    row = state.snapshot()["skills"][str(path)]
    assert row["version"] == result["version"]
    assert row["scan_complete"] is True


def test_recheck_reports_finished_attempt_with_remaining_coverage_gap(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    (path / "too-large.txt").write_bytes(b"x" * (sg.MAX_FILE_BYTES + 1))
    daemon.scan_changed_skill(str(path))
    result = run(bridge, "rescan", [selected(path, state)])["results"][0]
    assert (result["status"], result["code"]) == ("success", "scan_incomplete")
    assert result["attempt_complete"] is True
    assert result["scan_complete"] is False
    assert result["check_status"] == "incomplete"
    assert result["scan_issues"] == ["text_size_limit:too-large.txt"]
    assert result["scan_coverage"]["limits"]["text_bytes"] == sg.MAX_FILE_BYTES


def test_recheck_cannot_scan_unregistered_path_even_with_stale_version(client, tmp_path):
    _, (_, _, bridge) = client
    other = tmp_path / "outside"
    other.mkdir()
    (other / "SKILL.md").write_text("# outside", encoding="utf-8")
    result = run(bridge, "rescan", [{"path": str(other), "version": "old"}])["results"][0]
    assert (result["status"], result["code"]) == ("skipped", "not_registered")


def test_recheck_result_keeps_check_evidence_after_automatic_isolation(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    item = selected(path, state)
    (path / "SKILL.md").write_text("cat ~/.ssh/id_rsa\n", encoding="utf-8")
    cfg = sg.load_config()
    cfg["consent"] = {"quarantine": True}
    sg.save_config(cfg)
    daemon.mode = "block"
    result = run(bridge, "rescan", [item])["results"][0]
    assert result["status"] == "success"
    assert result["code"] == "ok"
    assert result["isolated"] is True
    assert result["quarantine_id"]
    assert result["scan_complete"] is True
    assert result["check_status"] == "attention"
    assert result["version"] != item["version"]
    assert result["scanned_at"]
    assert str(path) not in state.snapshot()["skills"]


def test_quarantine_refresh_preserves_asset_coverage_and_reports_decode_gap(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    (path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00image")
    daemon.scan_changed_skill(str(path))
    run(bridge, "quarantine", [selected(path, state)])
    ready_quarantine(bridge)
    bridge.processing.join(timeout=2)
    actual = sg.run_engine

    def limited(*args, **kwargs):
        report = actual(*args, **kwargs)
        report.findings.append(sg.Finding("SR-OBFUS-004", "OBFUS", "MEDIUM", "SKILL.md", 1,
                                         "limited", "decode candidates omitted", []))
        return report

    monkeypatch.setattr(sg, "run_engine", limited)
    bridge.processing._quarantine_checks.clear()
    bridge.processing._quarantine_views.clear()
    record = ready_quarantine(bridge)[0]
    assert record["scan_coverage"]["assets_without_text_check"] == ["image.png"]
    assert record["scan_complete"] is False
    assert "decode_limit" in record["scan_issues"]
    assert record["scanned_at"]


def test_incomplete_scan_cannot_create_trust_exception(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    item = selected(path, state)
    import tray.processing as processing
    actual = processing.capture_version
    monkeypatch.setattr(processing, "capture_version", lambda *a, **kw: dict(actual(*a, **kw), complete=False))
    result = run(bridge, "trust", [item])["results"][0]
    assert (result["status"], result["code"]) == ("failed", "scan_incomplete")


def test_one_running_job_rejects_duplicate_submission(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    entered, release = threading.Event(), threading.Event()
    original = daemon.scan_changed_skill

    def delayed(path):
        entered.set()
        assert release.wait(3)
        return original(path)

    monkeypatch.setattr(daemon, "scan_changed_skill", delayed)
    first = bridge.act("batch_action", {"action": "rescan", "items": [selected(path, state)]})
    assert first.get("ok")
    assert entered.wait(3)
    second = bridge.act("batch_action", {"action": "rescan", "items": [selected(path, state)]})
    assert second.get("ok") is False and second["code"] == "job_running"
    release.set()
    assert finished(bridge)["results"][0]["status"] == "success"


def test_quarantine_persists_metadata_before_moving_and_removes_active_row(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    item = selected(path, state)
    import tray.processing as processing
    original = processing._rename_no_replace

    def move(source, destination):
        payload = json.loads((pool.parent / "data/processing.json").read_text(encoding="utf-8"))
        assert payload["quarantine"][0]["original_path"] == str(path)
        assert payload["quarantine"][0]["version"] == item["version"]
        return original(source, destination)

    monkeypatch.setattr(processing, "_rename_no_replace", move)
    assert run(bridge, "quarantine", [item])["results"][0]["status"] == "success"
    record = ready_quarantine(bridge)[0]
    assert not path.exists() and (Path(record["path"]) / "SKILL.md").is_file()
    assert str(path) not in state.snapshot()["skills"]
    assert record["version"] == item["version"]
    _, _, restarted = build_runtime([str(pool)], "warn")
    restored_record = ready_quarantine(restarted)[0]
    # 重启会重新核查隔离内容，检查时间应更新；持久身份、证据与版本应保持。
    assert {k: v for k, v in restored_record.items() if k != "scanned_at"} == \
        {k: v for k, v in record.items() if k != "scanned_at"}
    assert restored_record["scanned_at"]
    assert restarted.get_state()["batch_job"]["results"][0]["status"] == "success"


def test_partial_failure_retry_only_failed_preserves_already_moved_skill(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    a, b = skill(pool, daemon, "a"), skill(pool, daemon, "b")
    items = [selected(a, state), selected(b, state)]
    import tray.processing as processing
    original = processing._rename_no_replace

    def sometimes(source, destination):
        if source == str(b):
            raise PermissionError("busy")
        return original(source, destination)

    monkeypatch.setattr(processing, "_rename_no_replace", sometimes)
    job = run(bridge, "quarantine", items)
    assert [r["status"] for r in job["results"]] == ["success", "failed"]
    first_record = ready_quarantine(bridge)[0]
    monkeypatch.setattr(processing, "_rename_no_replace", original)
    failures = [{"path": r["path"], "version": r["version"]}
                for r in job["results"] if r["status"] == "failed"]
    assert run(bridge, "quarantine", failures)["results"][0]["status"] == "success"
    records = ready_quarantine(bridge)
    assert len(records) == 2 and first_record in records


def test_quarantine_metadata_failure_leaves_original_untouched(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    item = selected(path, state)
    service = bridge.processing
    original = service._flush

    def cannot_save_metadata():
        if service._quarantine:
            raise PermissionError("read only data folder")
        return original()

    monkeypatch.setattr(service, "_flush", cannot_save_metadata)
    result = run(bridge, "quarantine", [item])["results"][0]
    assert result["status"] == "failed" and result["code"] == "metadata_failed"
    assert path.exists() and bridge.get_state()["quarantine"] == []


def test_quarantine_note_failure_reports_actual_moved_location(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    import tray.processing as processing
    monkeypatch.setattr(processing.ProcessingService, "_write_note",
                        lambda *a: (_ for _ in ()).throw(OSError("note denied")))
    result = run(bridge, "quarantine", [selected(path, state)])["results"][0]
    assert result["status"] == "success" and result["code"] == "note_failed"
    assert not path.exists() and len(bridge.get_state()["quarantine"]) == 1


def test_restore_conflict_does_not_overwrite_existing_folder(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    path.mkdir()
    (path / "keep.txt").write_text("keep", encoding="utf-8")
    item = {"path": record["original_path"], "id": record["id"], "version": record["version"]}
    result = run(bridge, "restore", [item])["results"][0]
    assert result["status"] == "failed" and result["code"] == "restore_conflict"
    assert (path / "keep.txt").read_text() == "keep"
    assert len(bridge.get_state()["quarantine"]) == 1


def test_restore_trust_preserves_critical_evidence_without_requarantine(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon, body="cat ~/.ssh/id_rsa\n")
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    cfg = sg.load_config()
    cfg["consent"] = {"quarantine": True, "add_block": True}
    sg.save_config(cfg)
    daemon.mode = "block"
    item = {"path": record["original_path"], "id": record["id"], "version": record["version"]}
    result = run(bridge, "restore_trust", [item])["results"][0]
    assert result["status"] == "success"
    row = state.snapshot()["skills"][str(path)]
    assert row["review_status"] == "trusted"
    assert any(f["severity"] == "CRITICAL" for f in row["findings"])
    assert path.exists() and bridge.get_state()["quarantine"] == []


def test_ordinary_restore_rechecks_without_implicit_trust(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    item = {"path": str(path), "id": record["id"], "version": record["version"]}
    assert run(bridge, "restore", [item])["results"][0]["status"] == "success"
    assert state.snapshot()["skills"][str(path)]["review_status"] != "trusted"


def test_restore_rejects_removed_original_registration(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    daemon.roots = []
    item = {"path": str(path), "id": record["id"], "version": record["version"]}
    result = run(bridge, "restore", [item])["results"][0]
    assert result["status"] == "skipped" and result["code"] == "not_registered"


def test_unregistered_absolute_path_cannot_be_reviewed(client, tmp_path):
    _, (daemon, _, bridge) = client
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("# safe", encoding="utf-8")
    result = run(bridge, "review", [{"path": str(outside), "version": "anything"}])["results"][0]
    assert result["status"] == "skipped" and result["code"] == "not_registered"


def test_revoke_trust_rechecks_and_restores_automatic_quarantine(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon, body="cat ~/.ssh/id_rsa\n")
    item = selected(path, state)
    assert run(bridge, "trust", [item])["results"][0]["status"] == "success"
    cfg = sg.load_config()
    cfg["consent"] = {"quarantine": True}
    sg.save_config(cfg)
    daemon.mode = "block"
    assert run(bridge, "revoke_trust", [item])["results"][0]["status"] == "success"
    assert not path.exists() and len(bridge.get_state()["quarantine"]) == 1


def test_legacy_accept_drift_closes_review_without_resetting_to_pending(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    (path / "SKILL.md").write_text("# changed", encoding="utf-8")
    daemon.scan_changed_skill(str(path))
    assert bridge.act("accept_drift", {"skill": str(path)})["ok"]
    row = state.snapshot()["skills"][str(path)]
    assert row["review_status"] == "reviewed"
    daemon.scan_changed_skill(str(path))
    assert state.snapshot()["skills"][str(path)]["review_status"] == "reviewed"


def test_restart_marks_unfinished_job_failed_without_repeating_moves(client):
    pool, (_, _, bridge) = client
    job = {"id": "interrupted-job", "action": "review", "status": "running",
           "items": [{"path": str(pool / "missing"), "version": "v"}],
           "total": 1, "completed": 0, "results": [], "started_at": "before"}
    file = pool.parent / "data/processing.json"
    file.write_text(json.dumps({"version": 1, "batch_job": job, "quarantine": []}), encoding="utf-8")
    _, _, restarted = build_runtime([str(pool)], "warn")
    restored = restarted.get_state()["batch_job"]
    assert restored["status"] == "done" and restored["completed"] == 1
    assert restored["results"][0]["code"] == "interrupted"


def test_restart_recovers_moved_skill_when_last_metadata_write_failed(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    item = selected(path, state)
    run(bridge, "quarantine", [item])
    file = pool.parent / "data/processing.json"
    payload = json.loads(file.read_text(encoding="utf-8"))
    payload["quarantine"][0]["status"] = "pending"
    payload["batch_job"].update(status="running", completed=0, results=[])
    file.write_text(json.dumps(payload), encoding="utf-8")
    _, _, restarted = build_runtime([str(pool)], "warn")
    recovered = restarted.get_state()
    assert recovered["quarantine"][0]["status"] == "isolated"
    assert recovered["batch_job"]["results"][0]["status"] == "success"


def test_changed_quarantine_payload_cannot_be_restored_or_trusted(client):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    from pathlib import Path
    Path(record["destination"], "SKILL.md").write_text("# replacement", encoding="utf-8")
    item = {"path": str(path), "version": record["version"], "id": record["id"]}
    for action in ("restore", "restore_trust"):
        result = run(bridge, action, [item])["results"][0]
        assert result["status"] == "skipped" and result["code"] == "version_changed"
    assert not path.exists()
    assert daemon.review_store.decision_for(str(path), record["version"]) != "trusted"


def test_active_junction_cannot_move_referenced_real_skill(client, tmp_path):
    pool, (daemon, state, bridge) = client
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("# outside", encoding="utf-8")
    link = pool / "alias"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    state.record_skill(str(link), "alias", 0, "scanned", version="v")
    result = run(bridge, "quarantine", [{"path": str(link), "version": "v"}])["results"][0]
    assert result["status"] == "skipped" and result["code"] in ("unsafe_path", "not_registered")
    assert outside.exists() and link.exists()


def test_restore_rejects_replaced_original_parent_link(client, tmp_path):
    pool, (daemon, state, bridge) = client
    nested = pool / "nested"
    nested.mkdir()
    path = skill(nested, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    nested.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        nested.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    result = run(bridge, "restore", [{"path": str(path), "version": record["version"],
                                     "id": record["id"]}])["results"][0]
    assert result["status"] == "skipped" and result["code"] in ("unsafe_path", "not_registered")
    assert not (outside / "demo").exists()


def test_restore_trust_failure_does_not_leave_new_trust_exception(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon, body="cat ~/.ssh/id_rsa\n")
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    monkeypatch.setattr("tray.processing._rename_no_replace",
                        lambda *a: (_ for _ in ()).throw(PermissionError("busy")))
    item = {"path": str(path), "version": record["version"], "id": record["id"]}
    result = run(bridge, "restore_trust", [item])["results"][0]
    assert result["status"] == "failed" and result["code"] == "move_failed"
    assert daemon.review_store.decision_for(str(path), record["version"]) != "trusted"


def test_restore_race_does_not_nest_into_new_same_name_folder(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    import tray.processing as processing
    original_move = processing._rename_no_replace

    def competing_move(source, destination):
        path.mkdir()
        (path / "keep.txt").write_text("existing", encoding="utf-8")
        return original_move(source, destination)

    monkeypatch.setattr(processing, "_rename_no_replace", competing_move)
    item = {"path": str(path), "version": record["version"], "id": record["id"]}
    result = run(bridge, "restore", [item])["results"][0]
    assert result["status"] == "failed" and result["code"] == "restore_conflict"
    assert (path / "keep.txt").read_text() == "existing"
    assert not (path / "skill").exists()


def test_corrupted_processing_storage_is_reported_and_not_overwritten(client):
    pool, (_, _, bridge) = client
    file = pool.parent / "data/processing.json"
    file.write_text("{broken metadata", encoding="utf-8")
    _, _, restarted = build_runtime([str(pool)], "warn")
    assert restarted.get_state()["processing_error"]
    response = restarted.act("batch_action", {"action": "review", "items": [
        {"path": str(pool / "any"), "version": "v"}]})
    assert response["code"] == "metadata_failed"
    assert file.read_text(encoding="utf-8") == "{broken metadata"


def test_shutdown_stops_new_jobs_and_skips_unstarted_items(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    a, b = skill(pool, daemon, "a"), skill(pool, daemon, "b")
    entered, release = threading.Event(), threading.Event()
    original = daemon.scan_changed_skill

    def delayed(path):
        entered.set()
        assert release.wait(3)
        return original(path)

    monkeypatch.setattr(daemon, "scan_changed_skill", delayed)
    response = bridge.act("batch_action", {"action": "rescan", "items": [selected(a, state), selected(b, state)]})
    assert response["ok"] and entered.wait(3)
    bridge.processing.stop()
    assert bridge.act("batch_action", {"action": "review", "items": [selected(b, state)]})["code"] == "stopping"
    release.set()
    bridge.processing.join(timeout=3)
    assert [row["status"] for row in finished(bridge)["results"]] == ["success", "skipped"]


def test_restore_completed_but_record_write_failure_warns_actual_success(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    original = bridge.processing._flush

    def no_final_save():
        if any(row["status"] == "restored" for row in bridge.processing._quarantine):
            raise OSError("final save denied")
        return original()

    monkeypatch.setattr(bridge.processing, "_flush", no_final_save)
    result = run(bridge, "restore", [{"path": str(path), "version": record["version"],
                                     "id": record["id"]}])["results"][0]
    assert result["status"] == "success" and result["code"] == "record_update_failed"
    assert path.exists() and bridge.get_state()["quarantine"] == []
    assert bridge.get_state()["batch_job"]["persistence_error"]


def test_cross_drive_quarantine_and_restore_keep_payload_version(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    import errno
    import tray.processing as processing
    rename = processing._rename_no_replace

    def cross_drive(source, destination):
        if ".copy-" not in str(source):
            raise OSError(errno.EXDEV, "different drive")
        return rename(source, destination)

    monkeypatch.setattr(processing, "_rename_no_replace", cross_drive)
    item = selected(path, state)
    assert run(bridge, "quarantine", [item])["results"][0]["status"] == "success"
    record = bridge.get_state()["quarantine"][0]
    assert record["version"] == item["version"] and not path.exists()
    result = run(bridge, "restore", [{"path": str(path), "version": record["version"],
                                     "id": record["id"]}])["results"][0]
    assert result["status"] == "success" and path.exists()


def test_restore_rejects_tampered_metadata_destination_outside_quarantine(client, tmp_path):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "SKILL.md").write_text("# unrelated", encoding="utf-8")
    bridge.processing._quarantine[0]["destination"] = str(outside)
    item = {"path": str(path), "version": record["version"], "id": record["id"]}
    result = run(bridge, "restore", [item])["results"][0]
    assert result["status"] == "skipped" and result["code"] == "unsafe_path"
    assert outside.exists() and not path.exists()


def test_restore_trust_rechecks_engine_coverage_before_creating_exception(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon, body="cat ~/.ssh/id_rsa\n")
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    actual = sg.run_engine

    def limited(*args, **kwargs):
        report = actual(*args, **kwargs)
        report.findings.append(sg.Finding("SR-OBFUS-004", "OBFUS", "MEDIUM", "SKILL.md", 1,
                                         "limited", "decode candidates omitted", []))
        return report

    monkeypatch.setattr(sg, "run_engine", limited)
    item = {"path": str(path), "version": record["version"], "id": record["id"]}
    result = run(bridge, "restore_trust", [item])["results"][0]
    assert result["status"] == "failed" and result["code"] == "scan_incomplete"
    assert not path.exists() and daemon.review_store.decision_for(str(path), record["version"]) != "trusted"


def test_invalid_processing_storage_shape_is_reported_before_overwrite(client):
    pool, (_, _, _) = client
    file = pool.parent / "data/processing.json"
    file.write_text("[]", encoding="utf-8")
    _, _, restarted = build_runtime([str(pool)], "warn")
    assert restarted.get_state()["processing_error"]
    assert file.read_text(encoding="utf-8") == "[]"


def test_action_objects_are_rejected_without_crashing_bridge(client):
    _, (_, _, bridge) = client
    for action in ([], {}, True):
        response = bridge.act("batch_action", {"action": action, "items": []})
        assert response.get("ok") is False and response.get("code") == "invalid_action"


@pytest.mark.parametrize("action", ["restore", "restore_trust"])
def test_rules_update_keeps_quarantined_skill_restorable_under_current_rules(client, action):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon, body="cat ~/.ssh/id_rsa\n")
    run(bridge, "quarantine", [selected(path, state)])
    old = bridge.get_state()["quarantine"][0]
    daemon.rules_text += "\n# newer checking rules\n"
    current = ready_quarantine(bridge)[0]
    assert current["isolation_version"] == old["version"]
    assert current["version"] != old["version"]
    item = {"path": str(path), "version": current["version"], "id": current["id"]}
    result = run(bridge, action, [item])["results"][0]
    assert result["status"] == "success" and path.exists()
    if action == "restore_trust":
        assert state.snapshot()["skills"][str(path)]["review_status"] == "trusted"


def test_malformed_saved_job_does_not_crash_startup_or_erase_storage(client):
    pool, (_, _, _) = client
    file = pool.parent / "data/processing.json"
    payload = {"version": 1, "quarantine": [], "batch_job": {"status": "running",
        "action": "review", "items": [None], "results": [], "total": 1, "completed": 0}}
    file.write_text(json.dumps(payload), encoding="utf-8")
    _, _, restarted = build_runtime([str(pool)], "warn")
    assert restarted.get_state()["processing_error"]
    assert json.loads(file.read_text(encoding="utf-8")) == payload


def test_cross_drive_delete_failure_reports_both_locations_and_retry_finishes(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    import errno
    import tray.processing as processing
    rename, delete = processing._rename_no_replace, processing.shutil.rmtree

    def cross_drive(source, destination):
        if ".copy-" not in str(source):
            raise OSError(errno.EXDEV, "different drive")
        return rename(source, destination)

    def busy_original(target, *args, **kwargs):
        if target == str(path):
            raise PermissionError("original folder busy")
        return delete(target, *args, **kwargs)

    monkeypatch.setattr(processing, "_rename_no_replace", cross_drive)
    monkeypatch.setattr(processing.shutil, "rmtree", busy_original)
    item = selected(path, state)
    result = run(bridge, "quarantine", [item])["results"][0]
    assert result["status"] == "failed" and result["code"] == "copy_pending"
    records = bridge.get_state()["quarantine"]
    assert len(records) == 1 and records[0]["status"] == "copy_pending"
    assert records[0]["original_exists"] is True and records[0]["destination_exists"] is True
    monkeypatch.setattr(processing.shutil, "rmtree", delete)
    assert run(bridge, "quarantine", [item])["results"][0]["status"] == "success"
    assert not path.exists() and len(bridge.get_state()["quarantine"]) == 1


def test_cross_drive_restore_delete_failure_reports_actual_duplicate_locations(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    record = bridge.get_state()["quarantine"][0]
    import errno
    import tray.processing as processing
    rename, delete = processing._rename_no_replace, processing.shutil.rmtree

    def cross_drive(source, destination):
        if ".copy-" not in str(source):
            raise OSError(errno.EXDEV, "different drive")
        return rename(source, destination)

    def busy_quarantine(target, *args, **kwargs):
        if target == record["destination"]:
            raise PermissionError("isolated copy busy")
        return delete(target, *args, **kwargs)

    monkeypatch.setattr(processing, "_rename_no_replace", cross_drive)
    monkeypatch.setattr(processing.shutil, "rmtree", busy_quarantine)
    result = run(bridge, "restore", [{"path": str(path), "version": record["version"],
                                     "id": record["id"]}])["results"][0]
    assert result["status"] == "success" and result["code"] == "restore_copy_pending"
    actual = bridge.get_state()["quarantine"][0]
    assert actual["status"] == "restore_copy_pending"
    assert actual["original_exists"] and actual["destination_exists"] and path.exists()
    assert str(path) in state.snapshot()["skills"]


def test_quarantine_refresh_never_holds_job_lock_or_blocks_state_poll(client, monkeypatch):
    pool, (daemon, state, bridge) = client
    path = skill(pool, daemon)
    run(bridge, "quarantine", [selected(path, state)])
    ready_quarantine(bridge)
    other = skill(pool, daemon, "other")
    import tray.processing as processing
    captured, release = threading.Event(), threading.Event()
    actual = processing.capture_version

    def delayed(path, *args, **kwargs):
        if os.sep + "quarantine" + os.sep in path:
            captured.set()
            assert release.wait(3)
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(processing, "capture_version", delayed)
    daemon.rules_text += "\n# updated rules\n"
    daemon.scan_changed_skill(str(other))
    before = time.monotonic()
    snapshot = bridge.get_state()
    assert time.monotonic() - before < .5
    assert snapshot["quarantine"][0]["refreshing"] is True
    assert captured.wait(1)
    response = bridge.act("batch_action", {"action": "rescan", "items": [selected(other, state)]})
    assert response["ok"]
    # 独立的技能检查仍能完成与保存结果，不等待隔离目录刷新磁盘读取。
    assert finished(bridge)["results"][0]["status"] == "success"
    release.set()
    ready_quarantine(bridge)
