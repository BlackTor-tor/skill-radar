"""Background history collection is useful on first launch and persists safely."""
import json
import os
import importlib.util
from pathlib import Path
import skill_monitor as sm


def service_class():
    assert importlib.util.find_spec("tray.usage") is not None, "client usage collector is missing"
    from tray.usage import UsageService
    return UsageService


def test_latest_use_compares_marker_and_tool_actual_instants_without_refresh(tmp_path):
    service = service_class()(str(tmp_path / "data"), history_roots=[], auto_start=False)
    service._data = {"skills": {"time": {"codex": 1, "marker": 1,
        "last_tool_use": "2026-09-01T23:00:00+14:00", "last_marker": "2026-09-01T10:00:00Z"}}}
    result = service.get_usage(refresh=False)
    assert result["rows"][0]["last"] == "2026-09-01T10:00:00Z"
    assert result["rows"][0]["total"] == 2
    assert service._thread is None


def test_usage_rows_sort_by_most_recent_record_before_invocation_total(tmp_path):
    service = service_class()(str(tmp_path / "data"), history_roots=[], auto_start=False)
    service._data = {"skills": {
        "many-old": {"codex": 9, "last_tool_use": "2026-09-01T00:00:00Z"},
        "one-new": {"codex": 1, "last_tool_use": "2026-10-04T00:00:00Z"},
        "no-date": {"codex": 20},
    }}
    rows = service.get_usage(refresh=False)["rows"]
    assert [row["name"] for row in rows] == ["one-new", "many-old", "no-date"]


def load(log, name, call="call", session="sess"):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"sessionId": session, "completedAt": "2026-10-04T15:11:12Z", "response": {
            "toolCalls": [{"id": call, "name": "Skill", "input": {"skill": name}}]}}) + "\n")


def test_initial_background_scan_refresh_and_restart_dedup(tmp_path):
    log = tmp_path / "history" / "rollout.jsonl"
    load(log, "first")
    klass = service_class()
    service = klass(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}])
    service._thread.join(timeout=10)
    result = service.get_usage(refresh=False)
    assert result["status"]["phase"] == "ready"
    assert result["rows"][0]["name"] == "first"
    assert result["rows"][0]["last"] == "2026-10-04T15:11:12Z"
    load(log, "second", call="new")
    service.scan(background=False)
    assert sum(row["total"] for row in service.get_usage(refresh=False)["rows"]) == 2
    restarted = klass(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    restarted.scan(background=False)
    assert sum(row["total"] for row in restarted.get_usage(refresh=False)["rows"]) == 2
    assert (tmp_path / "data" / "skill_usage_state.json").exists()


def test_explicit_directory_scan_autodetects_and_dedups_overlapping_roots(tmp_path):
    log = tmp_path / "drive" / "old-project" / "history.jsonl"
    load(log, "found")
    service = service_class()(str(tmp_path / "data"), history_roots=[], auto_start=False)
    service.scan(path=str(tmp_path / "drive"), source="auto", background=False)
    result = service.get_usage(refresh=False)
    assert result["rows"][0]["total"] == 1
    assert result["status"]["files_scanned"] == 1
    service.scan(path=str(log.parent), source="auto", background=False)
    assert service.get_usage(refresh=False)["rows"][0]["total"] == 1
    assert str(tmp_path / "drive") in [r["path"] for r in service.snapshot()["roots"]]


def test_scan_validation_and_missing_optional_roots_are_visible(tmp_path):
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "codex", "path": str(tmp_path / "missing")}], auto_start=False)
    assert service.scan(path="relative/logs", background=False)["ok"] is False
    service.scan(background=False)
    status = service.snapshot()
    assert status["roots"][0]["exists"] is False
    assert status["phase"] == "ready"
    assert status["coverage"]
    assert service.get_usage(refresh=False)["rows"] == []


def test_default_roots_use_codex_home_and_dedup_realpaths(tmp_path, monkeypatch):
    custom = tmp_path / "codexhome"
    custom.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(custom))
    klass = service_class()
    from tray.usage import discover_history_roots
    roots = discover_history_roots()
    assert os.path.realpath(custom / "sessions") in [r["path"] for r in roots if r["source"] == "codex"]
    assert len({(r["source"], os.path.normcase(os.path.realpath(r["path"]))) for r in roots}) == len(roots)


def test_persist_error_is_reported_without_exposing_log_content(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "log.jsonl"
    load(log, "available")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    def denied(*args, **kwargs):
        raise PermissionError("PRIVATE SECRET log body")
    monkeypatch.setattr(sm, "_save", denied)
    service.scan(background=False)
    result = service.get_usage(refresh=False)
    assert result["status"]["phase"] == "error"
    assert "PRIVATE SECRET" not in json.dumps(result)
    assert result["status"]["errors"]


def test_default_state_paths_are_stable_for_frozen_client(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "BASE_DIR", str(tmp_path / "temporary-extraction"))
    monkeypatch.setenv("SKILL_RADAR_DATA_DIR", str(tmp_path / "stable"))
    assert getattr(sm, "storage_paths", lambda: ("", ""))() == (
        str(tmp_path / "stable" / "skill_usage_state.json"), str(tmp_path / "stable" / "skill_usage.json"))


def test_interrupted_counter_export_does_not_recount_on_retry(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "session.jsonl"
    load(log, "once")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    save = sm._save
    def fail_export(path, data):
        if path == service.data_file:
            raise OSError("interrupted export")
        save(path, data)
    monkeypatch.setattr(sm, "_save", fail_export)
    service.scan(background=False)
    assert service.snapshot()["phase"] == "error"
    assert service.get_usage(refresh=False)["rows"][0]["total"] == 1
    restarted = service_class()(str(tmp_path / "data"), history_roots=[], auto_start=False)
    assert restarted.snapshot()["phase"] == "error"
    monkeypatch.setattr(sm, "_save", save)
    service.scan(background=False)
    assert service.get_usage(refresh=False)["rows"][0]["total"] == 1
    assert service.snapshot()["new_records"] == 0


def test_progress_exposes_collected_rows_before_backfill_completes(tmp_path, monkeypatch):
    import threading
    log = tmp_path / "logs" / "session.jsonl"
    load(log, "visible")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    progress_done, release = threading.Event(), threading.Event()
    scan = sm.scan_history
    def paused_after_progress(*args, **kwargs):
        result = scan(*args, **kwargs)
        progress_done.set()
        release.wait(5)
        return result
    monkeypatch.setattr(sm, "scan_history", paused_after_progress)
    service.scan()
    assert progress_done.wait(5)
    try:
        result = service.get_usage(refresh=False)
        assert result["status"]["scanning"] is True
        assert result["rows"][0]["name"] == "visible"
        assert Path(service.state_file).exists()
    finally:
        release.set()
        service._thread.join(5)


def test_stop_mid_history_scan_is_resumable_and_deduplicated(tmp_path, monkeypatch):
    import threading
    log = tmp_path / "logs" / "session.jsonl"
    load(log, "first", call="1")
    load(log, "second", call="2")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    started, release = threading.Event(), threading.Event()
    record_calls = sm._record_calls
    def pause(record, source):
        if (record.get("response") or {}).get("toolCalls", [{}])[0].get("id") == "1":
            started.set()
            release.wait(5)
        return record_calls(record, source)
    monkeypatch.setattr(sm, "_record_calls", pause)
    service.scan()
    assert started.wait(5)
    assert callable(getattr(service, "stop", None)), "usage scanner needs graceful shutdown"
    service.stop(timeout=0)
    release.set()
    assert service.stop(timeout=5)
    assert service.snapshot()["scanning"] is False
    assert service.get_usage(refresh=False)["rows"][0]["total"] == 1
    restarted = service_class()(str(tmp_path / "data"), history_roots=[], auto_start=False)
    restarted.scan(background=False)
    assert sum(r["total"] for r in restarted.get_usage(refresh=False)["rows"]) == 2


def test_progress_checkpoint_failure_does_not_lose_first_load_on_retry(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "log.jsonl"
    load(log, "once")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    save = sm._save
    def denied_state(path, data):
        if path == service.state_file:
            raise PermissionError("not writable")
        return save(path, data)
    monkeypatch.setattr(sm, "_save", denied_state)
    service.scan(background=False)
    assert service.snapshot()["phase"] == "error"
    monkeypatch.setattr(sm, "_save", save)
    service.scan(background=False)
    assert service.get_usage(refresh=False)["rows"][0]["total"] == 1


def test_explicit_refresh_forces_incremental_scan_without_waiting_for_cadence(tmp_path):
    log = tmp_path / "logs" / "session.jsonl"
    load(log, "old", call="1")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False, refresh_seconds=3600)
    service.scan(background=False)
    load(log, "new", call="2")
    assert len(service.get_usage(refresh=True)["rows"]) == 1
    try:
        service.get_usage(refresh=True, force=True)
    except TypeError:
        pass
    if service._thread:
        service._thread.join(5)
    assert len(service.get_usage(refresh=False)["rows"]) == 2
    service.stop()


def test_completed_scan_persists_status_for_read_only_reports_and_restart(tmp_path):
    log = tmp_path / "logs" / "session.jsonl"
    load(log, "used")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    service.scan(background=False)
    exported = json.loads(Path(service.data_file).read_text(encoding="utf-8"))
    status = exported.get("meta", {}).get("usage_status", {})
    assert status.get("phase") == "ready"
    assert status["scanning"] is False and status["files_scanned"] == 1
    assert status["roots"] == [{"source": "zcode", "path": str(log.parent), "exists": True}]
    restarted = service_class()(str(tmp_path / "data"), history_roots=[], auto_start=False)
    assert restarted.snapshot()["phase"] == "ready"
    assert restarted.snapshot()["files_scanned"] == 1
    assert restarted._thread is None
    log.unlink()
    log.parent.rmdir()
    assert restarted.snapshot()["roots"][0]["exists"] is False


def test_mid_scan_checkpoint_cannot_reuse_previously_completed_status(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "session.jsonl"
    load(log, "first")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    service.scan(background=False)
    load(log, "second", call="new")
    observed = []
    save = sm._save
    def inspect_checkpoint(path, value):
        if path == service.data_file:
            observed.append(value.get("meta", {}).get("usage_status", {}).get("phase"))
        save(path, value)
    monkeypatch.setattr(sm, "_save", inspect_checkpoint)
    service.scan(background=False)
    assert observed[0] == "scanning"
    assert observed[-1] == "ready"


def test_stopped_scan_persists_stopped_phase_without_claiming_ready(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "session.jsonl"
    load(log, "first")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    scan = sm.scan_history
    def stop_after_one_file(*args, **kwargs):
        result = scan(*args, **kwargs)
        service._stop_event.set()
        return result
    monkeypatch.setattr(sm, "scan_history", stop_after_one_file)
    service.scan(background=False)
    exported = json.loads(Path(service.data_file).read_text(encoding="utf-8"))
    assert exported.get("meta", {}).get("usage_status", {}).get("phase") == "stopped"
    restarted = service_class()(str(tmp_path / "data"), history_roots=[], auto_start=False)
    assert restarted.snapshot()["phase"] == "stopped"


def test_usage_rows_and_scan_status_are_one_matching_snapshot(tmp_path, monkeypatch):
    import threading
    import tray.usage as usage_module
    from skill_inventory import merge_usage

    log = tmp_path / "logs" / "session.jsonl"
    load(log, "first")
    service = service_class()(str(tmp_path / "data"), history_roots=[{"source": "zcode", "path": str(log.parent)}], auto_start=False)
    service.scan(background=False)
    load(log, "late", call="second")
    scan_paused, resume_scan, rows_copied, resume_reader = (threading.Event() for _ in range(4))
    scan = sm.scan_history
    latest = usage_module.latest_time
    def pause_scan(*args, **kwargs):
        scan_paused.set()
        assert resume_scan.wait(5)
        return scan(*args, **kwargs)
    def pause_after_rows_copy(*values):
        rows_copied.set()
        assert resume_reader.wait(5)
        return latest(*values)
    monkeypatch.setattr(sm, "scan_history", pause_scan)
    monkeypatch.setattr(usage_module, "latest_time", pause_after_rows_copy)
    result, errors = {}, []
    def read_usage():
        try:
            result.update(service.get_usage(refresh=False))
        except Exception as error:
            errors.append(error)
    service.scan()
    reader = threading.Thread(target=read_usage)
    try:
        assert scan_paused.wait(5)
        reader.start()
        assert rows_copied.wait(5)
        resume_scan.set()
        service._thread.join(5)
        assert not service._thread.is_alive()
        assert service.snapshot()["phase"] == "ready"
        resume_reader.set()
        reader.join(5)
        assert not reader.is_alive() and not errors
        assert [row["name"] for row in result["rows"]] == ["first"]
        assert result["status"]["phase"] == "scanning"
        assert result["status"]["scanning"] is True
        merged = merge_usage(result["rows"], [{"name": "late", "path": str(tmp_path / "late")}], result["status"])
        assert merged["installed_rows"][0]["usage_state"] == "unknown"
    finally:
        resume_scan.set()
        resume_reader.set()
        if reader.ident is not None:
            reader.join(5)
        service.stop()
