"""Real-browser drawer regressions; no browser automation package is required."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def _browser():
    configured = os.environ.get("SKILL_RADAR_BROWSER")
    if configured and Path(configured).is_file():
        return configured
    candidates = [
        shutil.which(name) for name in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge")
    ] + [
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((p for p in candidates if p and Path(p).is_file()), None)


@pytest.fixture(scope="module")
def drawer_results():
    node, browser = shutil.which("node"), _browser()
    if not node or not browser:
        pytest.skip("UI behavior verification requires Node 22+ and an installed Chromium browser")
    websocket = subprocess.run([node, "-p", "typeof WebSocket"], capture_output=True, text=True)
    if websocket.stdout.strip() != "function":
        pytest.skip("Node 22+ is required for the native WebSocket browser driver")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("ui_interaction_checks.mjs")), "--browser", browser],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.stdout.strip(), result.stderr
    report = json.loads(result.stdout.strip())
    checks = {check["name"]: check for check in report["results"]}
    assert "harness" not in checks, checks.get("harness")
    assert "browser_start" not in checks, checks.get("browser_start")
    return checks


@pytest.mark.parametrize("behavior", [
    "drawer_close_button", "drawer_backdrop", "drawer_escape_dismisses", "drawer_escape_restores_focus",
    "drawer_focus_trap", "drawer_background_locked", "drawer_row_keyboard",
    "drawer_initial_hidden", "drawer_focus_restored_after_refresh",
    "drawer_locks_background_scroll",
    "ui_language_english_and_persisted", "ui_language_chinese_preserves_screen_and_data",
    "ui_language_initial_saved_english", "ui_language_stale_state_after_save",
])
def test_drawer_interaction(drawer_results, behavior):
    check = drawer_results[behavior]
    assert check["ok"], check.get("error", behavior)
