# build_tray.py — SkillRadar Tray 打包驱动（入口位于 src/tray/app.py）：
# Windows=onefile exe；macOS=windowed .app + hdiutil 出 dmg）
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "src")
ENTRY = os.path.join(SRC, "tray", "app.py")
WEB = os.path.join(BASE, "tray", "web")
ASSETS = os.path.join(BASE, "tray", "assets")


def pyinstaller_cmd():
    sep = ";" if sys.platform == "win32" else ":"
    add_data_web = f"{WEB}{sep}tray/web"
    add_data_rules = f"{os.path.join(BASE, 'rules')}{sep}rules"
    add_data_assets = f"{ASSETS}{sep}tray/assets"
    extension = ".icns" if sys.platform == "darwin" else ".ico"
    icon = os.path.join(ASSETS, "skillradar" + extension)
    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--windowed", "--name", "SkillRadarTray",
           "--icon", icon,
           "--add-data", add_data_web,
           # rules/ 与核心 .py 打进包：守护在无源码环境也要能扫
           "--add-data", add_data_rules,
           "--add-data", add_data_assets,
           "--paths", SRC, "--paths", BASE, ENTRY]
    if sys.platform == "win32":
        cmd.insert(cmd.index("--windowed") + 1, "--onefile")
    return cmd   # macOS：windowed 产出 .app（spec §1c：dmg 后续 hdiutil）


def dmg_cmd():
    if sys.platform != "darwin":
        raise SystemExit("dmg 打包仅 macOS（规格 §1c）")
    app = os.path.join(BASE, "dist", "SkillRadarTray.app")
    dmg = os.path.join(BASE, "dist", "SkillRadarTray.dmg")
    return ["hdiutil", "create", "-volname", "SkillRadarTray",
            "-srcfolder", app, "-ov", "-format", "UDZO", dmg]


def main():
    cmd = pyinstaller_cmd()
    print("[build]", " ".join(cmd))
    subprocess.run(cmd, check=True)
    if sys.platform == "darwin":
        c = dmg_cmd()
        print("[build]", " ".join(c))
        subprocess.run(c, check=True)


if __name__ == "__main__":
    main()
