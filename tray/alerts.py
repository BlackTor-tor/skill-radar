# tray/alerts.py — Toast 告警与隔离动作（规格 §1b/§5；隔离是对被监听技能目录的
# 唯一写盘动作（快照/配置写入均落在 ~/.skill-radar 数据目录））
import os
import shutil
import shlex
import subprocess
import sys
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

import skill_guard as sg


def _qdir():
    # 运行时从 GUARD_DIR 派生（与 _config_path 同一隔离约定，测试可重定向）
    return os.path.join(sg.GUARD_DIR, "quarantine")


def _ps_quote(s):
    # PowerShell 单引号字符串内的单引号转义为两个单引号（防注入；s 已过 _sanitize）
    return "'" + s.replace("'", "''") + "'"


def _win_toast_ps(t, m):
    """完整可用的 WinRT toast 脚本（无 burntToast 依赖）。四要素：
    WinRT 类型加载、XmlDocument+LoadXml 装载含 t/m 的 XML、
    ToastNotification::new、CreateToastNotifier('SkillRadarTray').Show。
    整段 XML 作为一个 PowerShell 单引号字符串传入——XML 内出现的所有单引号
    （含 t/m 携带的）统一双写转义，既防注入也防提前闭合字符串。
    t/m 过 XML 实体转义（升级 T3）：目录名含 & < > 时 LoadXml 解析失败
    → Toast 永久静默失效；转义只覆盖这三实体，引号已在属性外文本节点、
    无需转义（PS 单引号层另管）。"""
    xml = (
        "<toast><visual><binding template=\"ToastGeneric\">"
        f"<text id=\"1\">{xml_escape(t)}</text>"
        f"<text id=\"2\">{xml_escape(m)}</text>"
        "</binding></visual></toast>"
    )
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] | Out-Null;"
        f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml({_ps_quote(xml)});"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($x);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'SkillRadarTray').Show($toast)"
    )


def _applescript_quote(s):
    # AppleScript 字符串转义：反斜杠与双引号都要转义
    return s.replace("\\", "\\\\").replace('"', '\\"')


def toast(title, msg, _capture=False, notify=None):
    """跨平台 Toast：Windows 走 PowerShell WinRT 兼容层，macOS 走 osascript；
    平台子进程路径整体包 try——OSError/SubprocessError 或非零返回码时降级：
    notify 可调用则以 (title, msg) 回调（壳层接 pystray 图标气泡，终审 M-4），
    之后兜底静默（托盘徽标照常兜底），绝不向调用方抛。文案过 _sanitize（规格 §5）。
    _capture=True 仅供测试：返回清洗后的 (title, msg) 拼接，不真弹。"""
    t = sg._sanitize(title)
    m = sg._sanitize(msg)
    if _capture:
        return t + m
    try:
        proc = None
        if sys.platform == "win32":
            proc = subprocess.run(["powershell", "-NoProfile", "-Command",
                                   _win_toast_ps(t, m)],
                                  timeout=10, capture_output=True,
                                  # 捕获输出不会隐藏控制台；GUI 客户端必须禁止子进程开窗。
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        elif sys.platform == "darwin":
            proc = subprocess.run(["osascript", "-e",
                                   f'display notification "{_applescript_quote(m)}"'
                                   f' with title "{_applescript_quote(t)}"'],
                                  timeout=10, capture_output=True)
        if proc is not None and proc.returncode != 0:
            raise subprocess.SubprocessError(
                f"toast subprocess exit {proc.returncode}")
    except (OSError, subprocess.SubprocessError):
        if callable(notify):
            try:
                notify(t, m)
            except Exception:
                pass   # 降级通道再失败：静默（托盘徽标兜底）
    return t + m


def restore_command(dest, original):
    # 纯命令行（cmd.exe 可直接粘贴执行）；macOS 形态见 RESTORE.txt 内说明
    return f'move "{dest}" "{original}"'


def quarantine_skill(skill_path, allowed_roots):
    """隔离（规格 §5 唯一写盘动作）：三重校验后移动到
    ~/.skill-radar/quarantine/<name>-<ts>/ 并写 RESTORE.txt。
    任何校验不过 → 返回 None 且原样不动：
    1. realpath 后必须仍在某个 allowed_root 之下（防 ../ 与中间符号链接逃逸；
       双侧 normcase + normpath 后前缀比较——Windows 大小写不敏感 + 分隔符归一）；
    2. 必须是技能目录（含 SKILL.md）；
    3. 目录名不含路径分隔符（防拼接注入）。"""
    # 不跟随根内链接或 junction：否则隔离 alias 会搬走被引用的真实技能。
    path = os.path.abspath(skill_path)
    chain = path
    while True:
        if sg._is_reparse(chain):
            return None
        parent = os.path.dirname(chain)
        if parent == chain:
            break
        chain = parent
    real = os.path.normcase(os.path.normpath(os.path.realpath(path)))
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
    # 同名技能同秒二次隔离：追加 -2、-3… 直到不冲突（防嵌套进已存在 dest）
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(_qdir(), f"{name}-{ts}-{n}")
        n += 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(real, dest)
    note = os.path.join(dest, "RESTORE.txt")
    with open(note, "w", encoding="utf-8") as f:
        f.write(f"该技能已被 skill-radar 托盘守护隔离。\n"
                f"原路径: {skill_path}\n"
                f"隔离时间: {ts}\n"
                f"恢复方法（确认安全后）: {restore_command(dest, skill_path)}\n"
                f"（Windows cmd 恢复命令如上；macOS/Linux 请用: "
                f'mv -- {shlex.quote(dest)} {shlex.quote(skill_path)}）\n'
                f"审查建议: python skill_guard.py scan \"{skill_path}\"\n")
    return dest
