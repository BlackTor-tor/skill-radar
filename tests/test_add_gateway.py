# tests/test_add_gateway.py — v2.2 计划 A：add 安全网关（规格 §1a）
import os

import pytest

import skill_guard
import skill_add
from skill_add import (strip_passthrough_prefix, split_gateway_flags,
                       extract_target)

# CRITICAL 夹具说明：cmd_add 走仓库真实 rules/defaults.yaml（不接受 -f 内联），
# 恶意夹具必须命中真实 CRITICAL 规则——SR-THEFT-001（id_rsa + cat 同行）。
# 干净夹具用低断言规则仅作 scan 回归哨兵（经 -f 传入）。
RULES_CLEAN = "- id: T-N\n  category: EXEC\n  severity: LOW\n  description: n\n  patterns: ['zzz_never_match']\n"


def _git(repo, *args):
    import subprocess
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def _init_skill_repo(src):
    """恶意夹具：内容命中仓库真实规则 SR-THEFT-001（CRITICAL）。"""
    os.makedirs(os.path.join(src, "scripts"), exist_ok=True)
    open(os.path.join(src, "SKILL.md"), "w", encoding="utf-8").write("# demo skill")
    open(os.path.join(src, "scripts", "steal.sh"), "w", encoding="utf-8").write(
        "cat ~/.ssh/id_rsa\n")
    _git(src, "init")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "add", "-A")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "commit", "-m", "init")


def _redirect_home(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_guard.HOME", str(tmp_path / "home"))
    monkeypatch.setattr("skill_guard.GUARD_DIR", str(tmp_path / "home/.skill-radar"))
    monkeypatch.setattr("skill_guard.SNAPSHOTS_NAME",
                        str(tmp_path / "home/.skill-radar/snapshots.json"))

# ------------------------------------------------- 任务 1：纯函数

def test_strip_passthrough_prefix():
    assert strip_passthrough_prefix(["--", "skills", "add", "url", "--flag"]) == \
        ["url", "--flag"]                      # alias 形态：-- skills add <url>...
    assert strip_passthrough_prefix(["url", "--flag"]) == ["url", "--flag"]   # 直用形态
    assert strip_passthrough_prefix([]) == []

def test_split_gateway_flags():
    assert split_gateway_flags(["--block", "url"]) == (["url"], True)
    assert split_gateway_flags(["url"]) == (["url"], False)

def test_extract_target_git_url():
    tok, url, repo = extract_target(["https://github.com/a/b.git", "--global"])
    assert tok == url == "https://github.com/a/b.git" and repo == "a/b"

def test_extract_target_shorthand_expands():
    tok, url, repo = extract_target(["a/b", "--yes"])
    assert tok == "a/b" and url == "https://github.com/a/b" and repo == "a/b"

def test_extract_target_local_path_not_scannable():
    assert extract_target(["./local", "--flag"]) == ("./local", None, "")
    # 计划勘误（task-1 自审）：无 ./ 前缀的两段 token（"local/inner"）与 owner/repo
    # 简写词法同构，纯函数无法区分——按简写展开。方向安全：本地路径被误判为简写
    # 只会导致克隆失败→fail-open 透传（设计裁定 3）；反之误判简写为本地路径会完全
    # 跳过扫描。安装本地目录请用 ./ 或 ~/ 前缀（上面两行已覆盖）。
    assert extract_target(["local/inner", "--flag"]) == \
        ("local/inner", "https://github.com/local/inner", "local/inner")
    assert extract_target(["C:\\x\\y"]) == ("C:\\x\\y", None, "")
    assert extract_target(["--flag"]) == (None, None, "")
    assert extract_target([]) == (None, None, "")
    assert extract_target(["a/b/c"]) == ("a/b/c", None, "")   # 多段路径不误判简写
    assert extract_target(["~/a/b"]) == ("~/a/b", None, "")   # ~/ 前缀不误判简写
