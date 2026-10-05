"""Real Chromium checks for header language and appearance controls."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def header_results():
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
        pytest.skip("Header behavior checks require Node 22+ and an installed Chromium browser")
    websocket = subprocess.run([node, "-p", "typeof WebSocket"], capture_output=True, text=True)
    if websocket.stdout.strip() != "function":
        pytest.skip("Node 22+ is required for the native WebSocket browser driver")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("header_controls_checks.mjs")), "--browser", browser],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.stdout.strip(), result.stderr
    report = json.loads(result.stdout.strip())
    checks = {check["name"]: check for check in report["results"]}
    assert "harness" not in checks, checks.get("harness")
    assert "browser_start" not in checks, checks.get("browser_start")
    return checks


@pytest.mark.parametrize("behavior", [
    "header_controls_are_compact_icons", "appearance_defaults_to_light",
    "appearance_toggle_preserves_screen_filters_and_language",
    "appearance_persists_after_reload_with_language_and_page",
    "language_native_keyboard_and_bridge_persistence", "appearance_keyboard_activation",
    "appearance_ignores_invalid_saved_value", "appearance_works_when_storage_is_unavailable",
    "header_controls_fit_supported_window_sizes",
    "appearance_saved_by_bridge_restores_after_private_restart",
    "appearance_bridge_save_failure_restores_previous_theme",
    "appearance_stale_state_after_save_does_not_reverse_selection",
    "appearance_pending_save_prevents_duplicate_activation",
])
def test_header_control_behavior(header_results, behavior):
    check = header_results[behavior]
    assert check["ok"], check.get("error", behavior)
