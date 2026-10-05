"""浏览器检查报告的下一步入口、历史兼容与安全展示。"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def guidance_results():
    node = shutil.which("node")
    candidates = [os.environ.get("SKILL_RADAR_BROWSER")] + [
        shutil.which(name) for name in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge")
    ] + [
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    browser = next((path for path in candidates if path and Path(path).is_file()), None)
    if not node or not browser:
        pytest.skip("Report behavior checks require Node 22+ and Chromium")
    websocket = subprocess.run([node, "-p", "typeof WebSocket"], capture_output=True, text=True)
    if websocket.stdout.strip() != "function":
        pytest.skip("Node 22+ is required for the native WebSocket browser driver")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("report_guidance_checks.mjs")), "--browser", browser],
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.stdout.strip(), result.stderr
    data = json.loads(result.stdout.strip())
    checks = {check["name"]: check for check in data["results"]}
    assert "harness" not in checks, checks.get("harness")
    assert "browser_start" not in checks, checks.get("browser_start")
    return checks


@pytest.mark.parametrize("behavior", [
    "conclusions_and_actions_precede_collapsed_evidence",
    "risk_detail_uses_exact_path_among_same_name_copies",
    "attention_action_opens_needs_attention_filter",
    "cleanup_location_is_exact_and_never_deletes",
    "usage_action_selects_idle_after_ranking",
    "manual_steps_expand_and_include_recovery_guidance",
    "technical_appendix_expands_without_running_raw_html",
    "copy_and_download_keep_the_complete_report",
    "legacy_report_still_previews_and_offers_new_report",
    "history_missing_skill_gives_feedback_without_wrong_detail",
    "report_metadata_cannot_inject_markup_links_or_actions",
    "english_action_controls_and_headings_are_translated",
    "expanded_guidance_and_appendix_survive_poll_and_language_change",
    "pending_folder_open_prevents_duplicate_requests_across_poll",
    "missing_check_coverage_does_not_claim_no_pending_problems",
    "unavailable_folder_gives_plain_feedback_and_reenables_action",
    "report_layout_fits_light_dark_and_supported_window_sizes",
])
def test_report_guidance_behavior(guidance_results, behavior):
    check = guidance_results[behavior]
    assert check["ok"], check.get("error", behavior)
