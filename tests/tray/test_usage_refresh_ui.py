"""Background usage polling must preserve visible content and interactions."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def usage_refresh_results():
    candidates = [os.environ.get("SKILL_RADAR_BROWSER"), shutil.which("chromium"),
                  "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    browser = next((path for path in candidates if path and Path(path).is_file()), None)
    node = shutil.which("node")
    if not node or not browser:
        pytest.skip("Node 22+ and Chromium required")
    completed = subprocess.run(
        [node, str(Path(__file__).with_name("usage_refresh_checks.mjs")), "--browser", browser],
        capture_output=True, encoding="utf-8", timeout=60,
    )
    assert completed.stdout.strip(), completed.stderr
    results = {result["name"]: result for result in json.loads(completed.stdout)["results"]}
    assert "harness" not in results, results.get("harness")
    assert "browser_start" not in results, results.get("browser_start")
    return results


@pytest.mark.parametrize("behavior", [
    "automatic_poll_keeps_usage_content", "unchanged_poll_preserves_interaction",
    "background_update_changes_usage_statistics", "one_usage_request_in_flight",
    "automatic_poll_keeps_refresh_button", "failed_background_poll_preserves_content",
    "manual_refresh_during_poll_forces_once",
])
def test_usage_background_refresh(usage_refresh_results, behavior):
    assert usage_refresh_results[behavior]["ok"], usage_refresh_results[behavior].get("error")
