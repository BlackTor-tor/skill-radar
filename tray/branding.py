"""客户端品牌资源：源码和打包运行共用，托盘状态保留相同的雷达标识。"""
import os
import sys


_STATUS_COLORS = {
    "running": "#58D9AF",
    "alert": "#EF5B64",
    "quarantine": "#F5B848",
    "paused": "#94A3B8",
}


def asset_path(name):
    """取得客户端内置图标路径，兼容 PyInstaller 的临时解包目录。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "tray", "assets", name)


def tray_icon(guard="running"):
    """返回带状态圆点的品牌托盘图标，异常状态依次使用红、黄、灰提示。"""
    from PIL import Image, ImageDraw

    with Image.open(asset_path("skillradar.png")) as source:
        image = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    draw.ellipse((46, 2, 62, 18), fill="#102536")
    draw.ellipse((49, 5, 59, 15), fill=_STATUS_COLORS.get(guard, _STATUS_COLORS["running"]))
    return image
