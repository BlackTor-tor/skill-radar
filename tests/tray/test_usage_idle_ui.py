"""Usage inventory and file-time behavior in real Chromium with an isolated bridge."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def idle_results():
    candidates = [os.environ.get("SKILL_RADAR_BROWSER"), shutil.which("chromium"),
                  "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"]
    browser = next((p for p in candidates if p and Path(p).is_file()), None)
    node = shutil.which("node")
    if not node or not browser:
        pytest.skip("Node 22+ and Chromium required")
    completed = subprocess.run([node, str(Path(__file__).with_name("usage_idle_checks.mjs")),
                                "--browser", browser], capture_output=True,
                               encoding="utf-8", timeout=90)
    assert completed.stdout.strip(), completed.stderr
    results = {r["name"]: r for r in json.loads(completed.stdout)["results"]}
    assert "harness" not in results, results.get("harness")
    return results


@pytest.mark.parametrize("behavior", ["default_idle_complete_groups", "ranking_all_rows",
    "selection_survives_refresh_and_language", "zero_usage_installed_skill", "empty_inventory",
    "partial_coverage", "legacy_inventory_unavailable", "loading_and_error", "safe_user_data",
    "security_file_times", "unknown_file_times", "english_file_times_and_report", "small_window",
    "discovery_collapsed_with_status", "unavailable_inventory_root", "english_coverage_notes",
    "direct_status_over_stale_state", "observation_and_duplicate_explanations"])
def test_usage_idle_behavior(idle_results, behavior):
    assert idle_results[behavior]["ok"], idle_results[behavior].get("error")
