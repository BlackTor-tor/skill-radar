"""skill-radar add 网关：装前拦截（v2.2 规格 §1a）。

用法（无独立二进制，入口即 skill_guard.py）：
    python skill_guard.py add [--block] <npx skills add 的参数...>
    python skill_guard.py add [--block] -- skills add <参数...>   # alias 形态
alias 加速器（README 同步给出）：
    alias skills='python /path/to/skill_guard.py add -- skills'
"""
import os
import re
import shutil
import subprocess
import sys

import skill_guard as sg
import skill_report as sr

REPO_SHORTHAND = re.compile(r"^[\w.-]+/[\w.-]+$")


def strip_passthrough_prefix(argv):
    """alias 形态透传去重：剥掉开头的 "--"、"skills"、"add"（各至多一个，规格 §1a）。

    alias 展开后网关收到 ["--","skills","add",<用户参数>...]；本函数归一为
    与直用形态（["<url>", ...]）一致的用户参数列表。"""
    out = list(argv)
    for tok in ("--", "skills", "add"):
        if out and out[0] == tok:
            out.pop(0)
    return out


def split_gateway_flags(argv):
    """手工解析网关旗标（绕开 argparse REMAINDER 的旗标先后怪癖）：
    返回 (透传参数, block)。"--block" 从透传参数中剔除（若与 skills CLI 的
    未来旗标撞名，以网关语义优先，README 已注明）。"""
    block = "--block" in argv
    return [a for a in argv if a != "--block"], block


def extract_target(argv):
    """从透传参数提取扫描目标：第一个非 "-" 开头位置参数。
    git URL → (token, url, owner/repo)；owner/repo 简写 → 展开为 github URL；
    其余（本地路径等）→ (token, None, "")，调用方按「不可扫描，直接透传」处理。
    简写判定附加排除：./ ../ ~/ 开头与含反斜杠的 token 一律按本地路径
    （"./local" 单斜杠形态与简写同构，必须显式排除）。"""
    for tok in argv:
        if tok.startswith("-"):
            continue
        if sg.is_git_url(tok):
            return tok, tok, sg._repo_from_git_url(tok)
        if (REPO_SHORTHAND.match(tok) and not tok.startswith((".", "~"))
                and "\\" not in tok):
            return tok, f"https://github.com/{tok}", tok
        return tok, None, ""
    return None, None, ""
