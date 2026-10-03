# tray/alerts.py — Toast 告警与隔离动作（规格 §1b/§5；隔离是守护唯一写盘动作）
import os
import shutil
import subprocess
import sys
from datetime import datetime

import skill_guard as sg


def _qdir():
    # 运行时从 GUARD_DIR 派生（与 _config_path 同一隔离约定，测试可重定向）
    return os.path.join(sg.GUARD_DIR, "quarantine")


def toast(title, msg, _capture=False):
    """跨平台 Toast：Windows 走 PowerShell WinRT 兼容层，macOS 走 osascript；
    失败静默（调用方有托盘徽标兜底）。文案过 _sanitize（规格 §5）。
    _capture=True 仅供测试：返回清洗后的 (title, msg) 拼接，不真弹。"""
    t = sg._sanitize(title)
    m = sg._sanitize(msg)
    if _capture:
        return t + m
    try:
        if sys.platform == "win32":
            ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI."
                  "Notifications, ContentType = WindowsRuntime] | Out-Null;"
                  "$t=[Windows.UI.Notifications.ToastNotificationManager]::"
                  "GetTemplateContent(1);"
                  "$x=[Windows.UI.Notifications.ToastNotificationManager]::"
                  "GetText($t.ContentXml, 'text', 1);"
                  "echo done")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           timeout=10, capture_output=True)
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            f'display notification "{m}" with title "{t}"'],
                           timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass
    return t + m


def restore_command(dest, original):
    return f'move "{dest}" "{original}"   # Windows（macOS: mv "{dest}" "{original}"）'


def quarantine_skill(skill_path, allowed_roots):
    """隔离（规格 §5 唯一写盘动作）：三重校验后移动到
    ~/.skill-radar/quarantine/<name>-<ts>/ 并写 RESTORE.txt。
    任何校验不过 → 返回 None 且原样不动：
    1. realpath 后必须仍在某个 allowed_root 之下（防 ../ 与中间符号链接逃逸；
       双侧 normcase + normpath 后前缀比较——Windows 大小写不敏感 + 分隔符归一）；
    2. 必须是技能目录（含 SKILL.md）；
    3. 目录名不含路径分隔符（防拼接注入）。"""
    real = os.path.normcase(os.path.normpath(os.path.realpath(skill_path)))
    real_roots = [os.path.normcase(os.path.normpath(os.path.realpath(r)))
                  for r in allowed_roots]
    if not any(real == rr or real.startswith(rr + os.sep) for rr in real_roots):
        return None
    if not os.path.isfile(os.path.join(real, "SKILL.md")):
        return None
    name = os.path.basename(real.rstrip(os.sep))
    if os.sep in name or "/" in name or name in ("", ".", ".."):
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(_qdir(), f"{name}-{ts}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(real, dest)
    note = os.path.join(dest, "RESTORE.txt")
    with open(note, "w", encoding="utf-8") as f:
        f.write(f"该技能已被 skill-radar 托盘守护隔离。\n"
                f"原路径: {skill_path}\n"
                f"隔离时间: {ts}\n"
                f"恢复方法（确认安全后）: {restore_command(dest, skill_path)}\n"
                f"审查建议: python skill_guard.py scan \"{skill_path}\"\n")
    return dest
