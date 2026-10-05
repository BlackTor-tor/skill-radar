"""Batch actions exercised against the actual offline page in Chromium."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def _browser():
    candidates = [os.environ.get("SKILL_RADAR_BROWSER"), shutil.which("chromium"),
                  "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"]
    return next((p for p in candidates if p and Path(p).is_file()), None)


@pytest.fixture(scope="module")
def batch_results():
    node, browser = shutil.which("node"), _browser()
    if not node or not browser:
        pytest.skip("Batch UI verification requires Node 22+ and Chromium")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("batch_ui_checks.mjs")), "--browser", browser],
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.stdout.strip(), result.stderr
    report = json.loads(result.stdout.strip())
    checks = {row["name"]: row for row in report["results"]}
    assert "harness" not in checks, checks.get("harness")
    assert "browser_start" not in checks, checks.get("browser_start")
    return checks


@pytest.mark.parametrize("behavior", [
    "selection_current_filter", "selection_all_matching_not_viewport",
    "selection_search_clears", "selection_filter_clears", "selection_poll_preserves",
    "selection_version_change", "checkbox_does_not_open_detail", "healthy_without_pending",
    "trusted_risk_stays_red", "trusted_severity_and_incomplete_labels", "incomplete_is_not_healthy", "detail_review_entry",
    "trust_confirm_exact_paths", "confirm_cancel_and_escape", "batch_progress_reopen",
    "batch_mixed_results", "retry_only_failed", "quarantine_restore_choices",
    "restore_conflict_visible", "revoke_trust_entry", "batch_english_copy",
    "unavailable_bridge_disables_actions", "confirmation_keeps_selected_version",
    "success_with_warning_visible", "persistence_error_visible", "trusted_guard_not_pending",
    "detail_confirmation_restores_row_focus", "reviewed_change_not_pending",
    "processing_storage_failure_disables", "rollback_failure_visible",
    "incomplete_detail_not_clean", "confirmation_updated_warning",
    "incomplete_detail_reasons",
    "quarantine_current_risk_summary", "quarantine_copy_pending_retry", "batch_dynamic_text_escaped",
    "quarantine_refreshing_disables_restore", "restore_confirm_refreshing_disables_submit",
    "restore_copy_pending_warning",
])
def test_batch_interaction(batch_results, behavior):
    check = batch_results[behavior]
    assert check["ok"], check.get("error", behavior)
