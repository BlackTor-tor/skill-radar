"""生产模块只保留 src 中的一份，并验证脱离仓库工作目录的启动入口。"""
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


PRODUCTION = (
    "skill_guard.py", "skill_add.py", "skill_inventory.py", "skill_monitor.py",
    "skill_report.py", "apply_skill_markers.py",
)
TRAY_PRODUCTION = (
    "alerts.py", "app.py", "branding.py", "daemon.py", "processing.py",
    "reports.py", "report_guidance.py", "review.py", "state.py", "usage.py",
    "watchers.py",
)


def test_production_python_sources_live_under_src():
    assert (SRC / "tray" / "__init__.py").is_file()
    for name in PRODUCTION:
        assert (SRC / name).is_file(), name
        assert not (ROOT / name).exists(), f"obsolete root entry: {name}"
    for name in TRAY_PRODUCTION:
        assert (SRC / "tray" / name).is_file(), name
        assert not (ROOT / "tray" / name).exists(), f"obsolete tray entry: {name}"
    assert not (ROOT / "tray" / "__init__.py").exists()


def test_imports_resolve_to_the_only_production_sources():
    for name in PRODUCTION:
        module = importlib.import_module(Path(name).stem)
        assert Path(module.__file__).resolve() == SRC / name
    for name in TRAY_PRODUCTION:
        module = importlib.import_module(f"tray.{Path(name).stem}")
        assert Path(module.__file__).resolve() == SRC / "tray" / name


@pytest.mark.parametrize("entry", ["skill_guard.py", "skill_monitor.py", "skill_report.py"])
def test_cli_help_runs_without_root_wrappers(entry, tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-B", str(SRC / entry), "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()


def test_tray_script_imports_and_finds_resources_from_another_directory(tmp_path):
    # run_path 只加载入口，不启动 GUI，也不读取真实用户数据。
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    code = (
        "import runpy, sys; from pathlib import Path; "
        "app = runpy.run_path(sys.argv[1]); "
        "from tray.branding import asset_path; import skill_guard; "
        "assert (Path(app['BASE']) / 'tray/web/index.html').is_file(); "
        "assert Path(asset_path('skillradar.png')).is_file(); "
        "assert (Path(skill_guard.RULES_ROOT) / 'defaults.yaml').is_file()"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(SRC / "tray" / "app.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
