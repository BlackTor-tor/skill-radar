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

# ------------------------------------------------- 任务 2：模式解析

def test_resolve_mode_block_flag_persists_consent(tmp_path, monkeypatch):
    _redirect_home(tmp_path, monkeypatch)
    cfg = skill_guard.load_config()
    assert skill_guard.load_config()["consent"].get("add_block") is not True  # 默认 warn
    rest, mode = skill_add.resolve_mode(["--block", "a/b"], cfg)
    assert mode == "block" and rest == ["a/b"]
    assert skill_guard.load_config()["consent"]["add_block"] is True   # 首次 --block 落盘

def test_resolve_mode_persisted_consent_without_flag(tmp_path, monkeypatch):
    _redirect_home(tmp_path, monkeypatch)
    cfg = skill_guard.load_config()
    skill_add.resolve_mode(["--block", "a/b"], cfg)          # 首次落盘
    cfg2 = skill_guard.load_config()
    rest, mode = skill_add.resolve_mode(["a/b"], cfg2)       # 不带 flag 也拦截
    assert mode == "block" and rest == ["a/b"]

def test_resolve_mode_warn_never_gates(tmp_path, monkeypatch):
    _redirect_home(tmp_path, monkeypatch)
    cfg = skill_guard.load_config()
    rest, mode = skill_add.resolve_mode(["a/b"], cfg)        # 警告模式：无 consent 门
    assert mode == "warn" and rest == ["a/b"]
    assert skill_guard.load_config()["consent"].get("add_block") is not True

# ------------------------------------------------- 任务 3：转调 + 编排
# run_install 全部打桩（不真调 npx）；克隆用本地 .git 目录仓库（同 test_scan_url 手法）

def _stub_install(monkeypatch, rc=0):
    calls = []
    def fake_run(user_args):
        calls.append(list(user_args))
        return rc
    monkeypatch.setattr(skill_add, "run_install", fake_run)
    return calls


def test_cmd_add_clean_warn_installs_and_baselines(tmp_path, monkeypatch, capsys):
    _redirect_home(tmp_path, monkeypatch)
    src = tmp_path / "clean-skill.git"; src.mkdir()
    os.makedirs(os.path.join(src, "sub"), exist_ok=True)
    open(os.path.join(src, "SKILL.md"), "w", encoding="utf-8").write("# clean")
    open(os.path.join(src, "sub/a.txt"), "w", encoding="utf-8").write("hello")
    _git(src, "init")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@e.com",
         "-c", "commit.gpgsign=false", "add", "-A")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@e.com",
         "-c", "commit.gpgsign=false", "commit", "-m", "init")
    calls = _stub_install(monkeypatch, rc=0)
    rc = skill_add.cmd_add([str(src)])
    assert rc == 0 and calls == [[str(src)]]          # 透传原参数、退出码透传
    out = capsys.readouterr().out
    assert "安装建议: 推荐" in out
    # 克隆临时目录已清理：第二轮 spy 本轮新建的 mkdtemp，逐个断言消失
    # （不扫全局 %TEMP%——并发进程的残留会让那种断言假红）
    import tempfile
    real_mkdtemp = tempfile.mkdtemp
    created = []
    def spy_mkdtemp(*a, **kw):
        d = real_mkdtemp(*a, **kw)
        created.append(d)
        return d
    monkeypatch.setattr(tempfile, "mkdtemp", spy_mkdtemp)
    rc2 = skill_add.cmd_add([str(src)])
    assert rc2 == 0
    assert created and not any(os.path.exists(d) for d in created)


def test_cmd_add_critical_block_refuses_no_install(tmp_path, monkeypatch, capsys):
    _redirect_home(tmp_path, monkeypatch)
    src = tmp_path / "evil-skill.git"; src.mkdir()
    _init_skill_repo(str(src))                        # 含 curl|sh → CRITICAL
    calls = _stub_install(monkeypatch)
    rc = skill_add.cmd_add(["--block", str(src)])
    assert rc == 1                                    # 拒绝且退出码 1（规格 §1a）
    assert calls == []                                # 绝不转调安装
    captured = capsys.readouterr()
    assert "不推荐" in captured.out                    # 三分法判定在场
    assert "拦截模式" in captured.err                  # 拦截提示走 stderr


def test_cmd_add_critical_warn_continues(tmp_path, monkeypatch, capsys):
    _redirect_home(tmp_path, monkeypatch)
    src = tmp_path / "evil2-skill.git"; src.mkdir()
    _init_skill_repo(str(src))
    calls = _stub_install(monkeypatch, rc=0)
    rc = skill_add.cmd_add([str(src)])                # 默认警告模式
    assert rc == 0 and calls == [[str(src)]]          # 继续安装
    assert "警告模式" in capsys.readouterr().out


def test_cmd_add_alias_form_dedupes_add(tmp_path, monkeypatch):
    _redirect_home(tmp_path, monkeypatch)
    src = tmp_path / "alias-skill.git"; src.mkdir()
    os.makedirs(os.path.join(src, "sub"), exist_ok=True)
    open(os.path.join(src, "SKILL.md"), "w", encoding="utf-8").write("# x")
    open(os.path.join(src, "sub/a.txt"), "w", encoding="utf-8").write("ok")
    _git(src, "init")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@e.com",
         "-c", "commit.gpgsign=false", "add", "-A")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@e.com",
         "-c", "commit.gpgsign=false", "commit", "-m", "init")
    calls = _stub_install(monkeypatch)
    rc = skill_add.cmd_add(["--", "skills", "add", str(src), "--global"])
    assert rc == 0
    assert calls == [[str(src), "--global"]]          # --/skills/add 已去重，用户旗标保留


def test_cmd_add_unscannable_passthrough_no_scan(tmp_path, monkeypatch, capsys):
    _redirect_home(tmp_path, monkeypatch)
    calls = _stub_install(monkeypatch)
    rc = skill_add.cmd_add(["./local-dir", "--flag"])
    assert rc == 0 and calls == [["./local-dir", "--flag"]]
    assert "未识别到可扫描的安装源" in capsys.readouterr().out


def test_cmd_add_clone_failure_fails_open(tmp_path, monkeypatch, capsys):
    _redirect_home(tmp_path, monkeypatch)
    calls = _stub_install(monkeypatch)
    def boom(url, timeout=120):
        raise RuntimeError("network down")
    monkeypatch.setattr(skill_guard, "resolve_target", boom)
    rc = skill_add.cmd_add(["https://github.com/no/such.git"])
    assert rc == 0 and calls == [["https://github.com/no/such.git"]]   # fail-open 透传
    assert "装前扫描失败" in capsys.readouterr().err


def test_run_install_missing_npx(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_add.shutil, "which", lambda n: None)
    assert skill_add.run_install(["a/b"]) == 127


def test_cmd_add_broken_pipe_still_cleans_tmp(tmp_path, monkeypatch):
    """I-3 回归：渲染/判定段在 try/finally 之内——print 抛 BrokenPipeError
    （管道 head 截断）或 KeyboardInterrupt（Ctrl+C）时临时克隆目录仍被清理，
    异常原样穿透（不被 fail-open 的 except Exception 吞掉）。mkdtemp spy 只盯
    本轮新建目录（不扫全局 %TEMP%，避免并发进程残留假红）。"""
    _redirect_home(tmp_path, monkeypatch)
    src = tmp_path / "pipe-skill.git"; src.mkdir()
    open(os.path.join(src, "SKILL.md"), "w", encoding="utf-8").write("# clean")
    _git(src, "init")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@e.com",
         "-c", "commit.gpgsign=false", "add", "-A")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@e.com",
         "-c", "commit.gpgsign=false", "commit", "-m", "init")
    calls = _stub_install(monkeypatch)
    import tempfile
    real_mkdtemp = tempfile.mkdtemp
    created = []
    def spy_mkdtemp(*a, **kw):
        d = real_mkdtemp(*a, **kw)
        created.append(d)
        return d
    monkeypatch.setattr(tempfile, "mkdtemp", spy_mkdtemp)
    monkeypatch.setattr(skill_add.sg, "render_report",
                        lambda rep: (_ for _ in ()).throw(BrokenPipeError()))
    with pytest.raises(BrokenPipeError):
        skill_add.cmd_add([str(src)])
    assert calls == []                       # 异常在转调安装之前抛出
    assert created and not any(os.path.exists(d) for d in created)


def test_baseline_new_skills_snapshots_and_counts(tmp_path, monkeypatch, capsys):
    _redirect_home(tmp_path, monkeypatch)
    pool = tmp_path / "home/.agents/skills/fresh"
    pool.mkdir(parents=True)
    (pool / "SKILL.md").write_text("# fresh", encoding="utf-8")
    n = skill_add.baseline_new_skills()
    assert n == 1
    snaps = skill_guard.load_snapshots()
    assert str(pool) in snaps["skills"]
    assert snaps["skills"][str(pool)]["status"] == "baseline-unreviewed"
    assert "NEW       fresh" in capsys.readouterr().out

# ------------------------------------------------- 任务 4：CLI 接入（经 skill_guard.main）
# 恶意夹具注意：skill_guard.main 接管 add 后仍走真实 rules/defaults.yaml，
# _init_skill_repo 的内容命中 SR-THEFT-001（CRITICAL）——与任务 3 同口径。

def test_main_intercepts_add_and_delegates(tmp_path, monkeypatch):
    _redirect_home(tmp_path, monkeypatch)
    src = tmp_path / "cli-skill.git"; src.mkdir()
    os.makedirs(os.path.join(src, "sub"), exist_ok=True)
    open(os.path.join(src, "SKILL.md"), "w", encoding="utf-8").write("# x")
    open(os.path.join(src, "sub/a.txt"), "w", encoding="utf-8").write("ok")
    _git(src, "init")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@e.com",
         "-c", "commit.gpgsign=false", "add", "-A")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@e.com",
         "-c", "commit.gpgsign=false", "commit", "-m", "init")
    calls = _stub_install(monkeypatch)
    assert skill_guard.main(["add", str(src)]) == 0      # argparse 之前被拦截
    assert calls == [[str(src)]]
    # 既有子命令不受影响（回归哨兵）
    d = tmp_path / "local-skill"; d.mkdir()
    open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8").write("# clean")
    assert skill_guard.main(["scan", str(d), "-f", RULES_CLEAN]) == 0


def test_main_add_block_end_to_end_fail_closed(tmp_path, monkeypatch, capsys):
    # 非交互（pytest 下 stdin 非 TTY）+ --block + CRITICAL → fail-closed 拒绝：
    # 不询问、不安装、退出码 1，consent.add_block 已落盘（与 scan --strict 口径一致）
    _redirect_home(tmp_path, monkeypatch)
    src = tmp_path / "evil-cli.git"; src.mkdir()
    _init_skill_repo(str(src))
    calls = _stub_install(monkeypatch)
    assert skill_guard.main(["add", "--block", str(src)]) == 1
    assert calls == []
    assert skill_guard.load_config()["consent"]["add_block"] is True
