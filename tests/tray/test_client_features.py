"""Client bridge and browser behavior for reports and automatic usage scanning."""
import json
from pathlib import Path
import subprocess

import pytest

import os
import shutil


def _browser():
    candidates = [os.environ.get("SKILL_RADAR_BROWSER"), shutil.which("chromium"),
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"]
    return next((p for p in candidates if p and Path(p).is_file()), None)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    import skill_guard as sg
    from tray.app import build_runtime
    monkeypatch.setattr(sg, "HOME", str(tmp_path))
    monkeypatch.setattr(sg, "GUARD_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(tmp_path / "data/snapshots.json"))
    pool = tmp_path / "skills"
    pool.mkdir()
    daemon, state, client = build_runtime([str(pool)], "warn")
    state.record_skill(str(pool / "demo"), "demo", 0, "scanned",
                       check_status="healthy", review_status="not_required",
                       scan_complete=True, version="v1", findings=[])
    yield client
    client.processing.stop()
    if client.usage is not None and hasattr(client.usage, "stop"):
        client.usage.stop()


def test_report_bridge_generates_archive_and_markdown(bridge):
    result = bridge.act("generate_report")
    assert result.get("ok"), result
    report = result["report"]
    assert "demo" in report["markdown"] and report["generated_at"]
    assert bridge.act("get_report", {"id": report["id"]})["report"]["markdown"] == report["markdown"]
    assert len(bridge.act("list_reports")["reports"]) == 1
    assert "demo" in bridge.act("get_markdown", {"subject": "skill", "skill": next(iter(bridge.state.skills))})["markdown"]


def test_report_download_without_window_returns_content(bridge):
    report = bridge.act("generate_report")["report"]
    result = bridge.act("export_report", {"id": report["id"], "format": "md"})
    assert result["ok"] and result["content"] == report["markdown"]


def test_report_download_cancel_is_not_failure(bridge):
    import types
    bridge.runtime.window = types.SimpleNamespace(create_file_dialog=lambda *args, **kwargs: None)
    report = bridge.act("generate_report")["report"]
    assert bridge.act("export_report", {"id": report["id"], "format": "html"}) == {"ok": True, "cancelled": True}


def test_events_keep_full_date_for_markdown(bridge):
    from datetime import datetime
    bridge.state.add_event("scan", "demo checked")
    stamp = bridge.state.snapshot()["events"][0]["ts"]
    assert datetime.fromisoformat(stamp).tzinfo is not None
    assert stamp in bridge.act("get_markdown", {"subject": "events"})["markdown"]


def test_windows_clipboard_command_reads_utf8_without_changing_clipboard(bridge, monkeypatch):
    import tray.app as app
    if app.sys.platform != "win32":
        pytest.skip("Windows clipboard encoding")
    real_run = subprocess.run
    seen = {}
    def run(args, **kwargs):
        seen.update(args=args, **kwargs)
    monkeypatch.setattr(app.subprocess, "run", run)
    assert bridge.act("copy_markdown", {"text": "中文报告\n第二行"})["ok"]
    command = seen["args"][-1]
    assert "Set-Clipboard -Value $markdownText" in command
    result = real_run(seen["args"][:-1] + [command.replace(
        "Set-Clipboard -Value $markdownText", "[Console]::Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($markdownText)))")],
        input=seen["input"], text=True, encoding="utf-8", capture_output=True)
    import base64
    assert base64.b64decode(result.stdout).decode("utf-8").replace("\r\n", "\n") == "中文报告\n第二行"


@pytest.fixture(scope="module")
def feature_results():
    browser = _browser()
    if not browser:
        pytest.skip("Chromium required")
    result = subprocess.run(["node", str(Path(__file__).with_name("client_feature_checks.mjs")),
                             "--browser", browser], capture_output=True, encoding="utf-8", timeout=90)
    assert result.stdout.strip(), result.stderr
    data = json.loads(result.stdout)
    results = {row["name"]: row for row in data["results"]}
    assert "harness" not in results, results.get("harness")
    return results


@pytest.mark.parametrize("behavior", ["attention_filter", "duplicate_path_colors",
    "reports_generate_display_download", "reports_failure_retry", "events_detail_markdown_copy",
    "skill_markdown_safe", "usage_automatic_guidance", "usage_codex_counts",
    "usage_manual_folder_cancel", "recheck_updates_status", "features_english", "incomplete_recheck_warning",
    "detail_time_does_not_duplicate_on_poll", "open_skill_location_exact_path",
    "action_button_backgrounds", "decode_gap_has_remedy", "usage_datetime_follows_language"])
def test_client_feature(feature_results, behavior):
    assert feature_results[behavior]["ok"], feature_results[behavior].get("error")
