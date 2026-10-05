"""Cross-platform smoke checks for the frozen macOS application bundle.

The checks are deliberately filesystem based so CI can validate the artifact
without starting the tray GUI.  ``--dmg`` adds a read-only hdiutil mount check
on macOS; Windows/Linux can still validate an extracted ``.app`` or archive.
"""

from __future__ import annotations

import argparse
import hashlib
import plistlib
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


WEB_FILES = (
    "index.html",
    "features.js",
    "i18n.js",
    "processing.js",
    "markdown.js",
    "lucide-license.txt",
    "material-symbols-license.txt",
)
RESOURCE_FILES = tuple(f"tray/web/{name}" for name in WEB_FILES) + (
    "rules/defaults.yaml",
    "tray/assets/skillradar.png",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resource_path(app: Path, relative: str) -> Path:
    """Find a PyInstaller resource under the normal app bundle locations."""
    candidates = (
        app / "Contents" / "Resources" / relative,
        app / relative,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    matches = list(app.rglob(Path(relative).name))
    for candidate in matches:
        if candidate.as_posix().endswith(relative.replace("\\", "/")):
            return candidate
    raise AssertionError(f"missing packaged resource: {relative}")


def validate_app(app: Path, source_root: Path, expected_arch: str | None = None) -> dict[str, object]:
    app = app.resolve()
    assert app.is_dir(), f"missing app bundle: {app}"
    executable = app / "Contents" / "MacOS" / "SkillRadarTray"
    assert executable.is_file(), f"missing app executable: {executable}"

    hashes: dict[str, str] = {}
    for relative in RESOURCE_FILES:
        packaged = _resource_path(app, relative)
        source = source_root / relative
        assert source.is_file(), f"missing source resource: {source}"
        source_hash = _sha256(source)
        packaged_hash = _sha256(packaged)
        assert source_hash == packaged_hash, (
            f"resource hash mismatch for {relative}: {source_hash} != {packaged_hash}"
        )
        hashes[relative] = packaged_hash

    architecture = ""
    if shutil.which("file"):
        architecture = subprocess.check_output(
            ["file", str(executable)], text=True, stderr=subprocess.STDOUT
        ).strip()
        if expected_arch:
            needle = {"arm64": "arm64", "x64": "x86_64", "x86_64": "x86_64"}[expected_arch]
            assert re.search(rf"\b{re.escape(needle)}\b", architecture), (
                f"expected {needle} executable, got: {architecture}"
            )
    return {"app": str(app), "architecture": architecture, "resources": hashes}


def validate_archive(archive: Path, expected_app_name: str = "SkillRadarTray.app") -> None:
    """Ensure the distributable archive contains the complete app/resource set."""
    assert archive.is_file(), f"missing archive: {archive}"
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    prefix = expected_app_name.rstrip("/") + "/"
    for relative in ("Contents/MacOS/SkillRadarTray",) + tuple(
        f"Contents/Resources/{item}" for item in RESOURCE_FILES
    ):
        assert prefix + relative in names, f"missing archive member: {prefix + relative}"


def validate_dmg(dmg: Path, source_root: Path, expected_arch: str | None = None) -> dict[str, object]:
    assert shutil.which("hdiutil"), "hdiutil is required for DMG validation"
    mount_info = plistlib.loads(
        subprocess.check_output(
            ["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(dmg)]
        )
    )
    mountpoint = next(
        (
            entity.get("mount-point")
            for entity in mount_info.get("system-entities", [])
            if entity.get("mount-point")
        ),
        None,
    )
    assert mountpoint, f"DMG mounted without a mount point: {dmg}"
    mount = Path(mountpoint)
    try:
        return validate_app(mount / "SkillRadarTray.app", source_root, expected_arch)
    finally:
        subprocess.run(["hdiutil", "detach", "-force", str(mount)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--dmg", type=Path)
    parser.add_argument("--expected-arch", choices=("arm64", "x64", "x86_64"))
    args = parser.parse_args()
    result = validate_app(args.app, args.source_root, args.expected_arch)
    if args.archive:
        validate_archive(args.archive)
    if args.dmg:
        validate_dmg(args.dmg, args.source_root, args.expected_arch)
    print(result)


if __name__ == "__main__":
    main()
