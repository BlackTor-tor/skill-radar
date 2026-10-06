"""Collapsible desktop sidebar behavior in real Chromium."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def sidebar_results():
    browser = next((p for p in [os.environ.get("SKILL_RADAR_BROWSER"), shutil.which("chromium"),
                    "C:/Program Files/Google/Chrome/Application/chrome.exe",
                    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"]
                   if p and Path(p).is_file()), None)
    node = shutil.which("node")
    if not node or not browser:
        pytest.skip("Node 22+ and Chromium required")
    result = subprocess.run([node, str(Path(__file__).with_name("sidebar_collapse_checks.mjs")),
                             "--browser", browser], capture_output=True, encoding="utf-8", timeout=60)
    assert result.stdout.strip(), result.stderr
    checks = {item["name"]: item for item in json.loads(result.stdout)["results"]}
    assert "harness" not in checks, checks.get("harness")
    return checks


@pytest.mark.parametrize("behavior", ["sidebar_starts_expanded", "sidebar_collapses_and_keeps_navigation",
    "sidebar_collapsed_brand_centered", "sidebar_state_survives_reload", "sidebar_keyboard_toggle",
    "sidebar_fits_narrow_desktop"])
def test_sidebar_collapse(sidebar_results, behavior):
    assert sidebar_results[behavior]["ok"], sidebar_results[behavior].get("error")
