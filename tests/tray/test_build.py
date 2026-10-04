# tests/tray/test_build.py — 任务 7：打包驱动（命令拼装，不真打包）
import sys
from pathlib import Path

import pytest

import build_tray


@pytest.mark.parametrize("platform, suffix, separator", [
    ("win32", ".ico", ";"),
    ("darwin", ".icns", ":"),
])
def test_packaged_client_contains_brand_assets(monkeypatch, platform, suffix, separator):
    # 漏掉 --icon / assets 或混用平台分隔符会让客户端回退默认图标或找不到资源。
    monkeypatch.setattr(build_tray.sys, "platform", platform)
    cmd = build_tray.pyinstaller_cmd()
    assert "--icon" in cmd
    icon_path = Path(cmd[cmd.index("--icon") + 1])
    assert icon_path.suffix == suffix
    assert icon_path.is_file()
    data_paths = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--add-data"]
    assert any(value.endswith(separator + "tray/assets") for value in data_paths)
    assert all(separator in value for value in data_paths)


def test_windows_icon_has_small_and_high_dpi_sizes():
    from PIL import Image

    icon_path = Path(build_tray.BASE) / "tray" / "assets" / "skillradar.ico"
    assert icon_path.is_file()
    with Image.open(icon_path) as icon:
        assert {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                (128, 128), (256, 256)} <= icon.ico.sizes()
        for size in (16, 24, 32):
            small = icon.ico.getimage((size, size)).convert("RGBA")
            assert small.getpixel((0, 0))[3] == 0
            assert len(small.getcolors(size * size)) > 10


def test_tray_status_keeps_brand_shape_with_distinct_indicators(monkeypatch):
    from tray import branding

    images = [branding.tray_icon(state) for state in
              ("running", "alert", "quarantine", "paused")]
    assert all(image.size == (64, 64) and image.mode == "RGBA" for image in images)
    assert all(image.getpixel((0, 0))[3] == 0 for image in images)
    assert len({image.tobytes() for image in images}) == 4
    assert branding.tray_icon("unrecognized").tobytes() == images[0].tobytes()


def test_brand_asset_loading_uses_frozen_resource_location(monkeypatch, tmp_path):
    from tray import branding

    bundled_assets = tmp_path / "tray" / "assets"
    bundled_assets.mkdir(parents=True)
    from PIL import Image
    Image.new("RGBA", (64, 64), (15, 23, 42, 255)).save(
        bundled_assets / "skillradar.png")
    monkeypatch.setattr(branding.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert branding.asset_path("skillradar.png") == str(bundled_assets / "skillradar.png")
    assert branding.tray_icon().getpixel((8, 8)) == (15, 23, 42, 255)


def test_pyinstaller_args_windows(monkeypatch):
    monkeypatch.setattr(build_tray.sys, "platform", "win32")
    cmd = build_tray.pyinstaller_cmd()
    joined = " ".join(cmd)
    assert "--onefile" in cmd and "--windowed" in cmd
    assert "tray/app.py" in joined.replace("\\", "/")
    # add-data 两个数据目录都在场（web/index.html 与 rules/ 必须随包）
    assert "--add-data" in joined and "tray/web" in joined and "rules" in joined


def test_pyinstaller_args_macos(monkeypatch):
    monkeypatch.setattr(build_tray.sys, "platform", "darwin")
    cmd = build_tray.pyinstaller_cmd()
    joined = " ".join(cmd)
    assert "--windowed" in joined and "--onefile" not in joined   # .app 形态
    # add-data 双数据目录（win 分支同口径）
    assert "--add-data" in joined and "tray/web" in joined and "rules" in joined


def test_dmg_command_macos(monkeypatch):
    monkeypatch.setattr(build_tray.sys, "platform", "darwin")
    cmd = build_tray.dmg_cmd()
    assert "hdiutil" in cmd


def test_dmg_command_windows_raises(monkeypatch):
    monkeypatch.setattr(build_tray.sys, "platform", "win32")
    with pytest.raises(SystemExit):
        build_tray.dmg_cmd()
