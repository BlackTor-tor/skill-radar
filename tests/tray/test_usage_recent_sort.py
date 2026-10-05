"""The usage ranking should be ordered by latest recorded invocation."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def recent_sort_result():
    candidates = [os.environ.get("SKILL_RADAR_BROWSER"), shutil.which("chromium"),
                  "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"]
    browser = next((path for path in candidates if path and Path(path).is_file()), None)
    node = shutil.which("node")
    if not node or not browser:
        pytest.skip("Node 22+ and Chromium required")
    completed = subprocess.run(
        [node, str(Path(__file__).with_name("usage_recent_sort_checks.mjs")),
         "--browser", browser], capture_output=True, encoding="utf-8", timeout=60,
    )
    assert completed.stdout.strip(), completed.stderr
    results = {result["name"]: result for result in json.loads(completed.stdout)["results"]}
    return results["ranking_sorted_by_recent_record"]


def test_usage_ranking_sorts_by_recent_record(recent_sort_result):
    assert recent_sort_result["ok"], recent_sort_result.get("error")
