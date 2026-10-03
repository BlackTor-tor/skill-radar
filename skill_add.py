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


def resolve_mode(argv, cfg):
    """拦截/警告模式（设计裁定 2）：--block 或已持久化 consent.add_block → block。
    --block 首次出现即经 gate_consent(yes_flag=True) 写入并落盘（gate_consent 复用，
    规格 §1a）；警告模式永不经过 consent 门。返回 (透传参数, mode)。"""
    rest, block = split_gateway_flags(argv)
    if block and not sg.check_consent(cfg, "add_block"):
        cfg = sg.gate_consent(cfg, "add_block", True)
        sg.save_config(cfg)
    mode = "block" if (block or sg.check_consent(cfg, "add_block")) else "warn"
    return rest, mode


def run_install(user_args):
    """转调 `npx skills add <user_args>`（subprocess，退出码透传，规格 §1a 第 4 步）。
    npx 未安装 → 127（与 shell 找不到命令的惯例一致）。"""
    npx = shutil.which("npx")
    if not npx:
        print("[skill-radar] 未找到 npx（skills CLI 不可用），无法转调安装",
              file=sys.stderr)
        return 127
    return subprocess.run([npx, "skills", "add", *user_args]).returncode


def _repo_file(name):
    """rules/ 下文件路径（网关与 CLI 同仓，规则口径一致）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", name)


def baseline_new_skills():
    """装后自动建快照基线（规格 §1a 第 5 步）：对注册根全量 audit_roots（NEW 即建
    基线，已有技能维持漂移语义）→ 落盘 → 只打印 NEW 状态行（完整 audit 输出会
    淹没安装命令的回显）。返回 NEW 行数。"""
    cfg = sg.load_config()
    rules_text = (open(_repo_file("defaults.yaml"), encoding="utf-8").read()
                  if os.path.isfile(_repo_file("defaults.yaml")) else "")
    bl_text = (open(_repo_file("blocklist.yaml"), encoding="utf-8").read()
               if os.path.isfile(_repo_file("blocklist.yaml")) else "[]")
    snaps = sg.load_snapshots()
    roots = [r["path"] for r in cfg["roots"] if os.path.isdir(r["path"])]
    summary = sg.audit_roots(roots, rules_text, bl_text, snaps, cfg)
    sg.save_snapshots(snaps)
    news = [ln for ln in summary if ln.startswith("NEW")]
    for ln in news:
        print(sg._sanitize(ln.split("\n")[0]))
    return len(news)


def cmd_add(argv):
    """网关编排（规格 §1a 1-5 步）。返回退出码：拦截拒绝=1；其余=npx 透传码。"""
    cfg = sg.load_config()
    rest, mode = resolve_mode(argv, cfg)
    rest = strip_passthrough_prefix(rest)
    token, url, repo = extract_target(rest)
    if url is None:
        if not rest:
            print("usage: skill_guard.py add [--block] -- <npx skills add 参数...>",
                  file=sys.stderr)
            return 2
        print("[skill-radar] 未识别到可扫描的安装源，直接透传（不扫描）")
        return run_install(rest)

    tmp = None
    try:
        target = sg.resolve_target(url)
        tmp = target if target != url else None
        rules_text = (open(_repo_file("defaults.yaml"), encoding="utf-8").read()
                      if os.path.isfile(_repo_file("defaults.yaml")) else "")
        bl_text = (open(_repo_file("blocklist.yaml"), encoding="utf-8").read()
                   if os.path.isfile(_repo_file("blocklist.yaml")) else "[]")
        rep = sg.run_engine(target, sg.parse_rules(rules_text), bl_text, repo=repo)
    except Exception as e:   # 设计裁定 3：fail-open 透传，醒目告警。
        # 只捕 Exception：KeyboardInterrupt/SystemExit 必须原样穿透（CLI 惯例，
        # 与 skill_guard.main 的 gate_consent 异常网口径一致）
        print(f"[skill-radar] 装前扫描失败（{e}），按无扫描透传安装", file=sys.stderr)
        if tmp is not None:
            sg._force_rmtree(tmp)
        return run_install(rest)

    print(sg.render_report(rep))
    verdict, _color, reason = sr.install_verdict(rep, "scanned")
    print(f"安装建议: {verdict} —— {sg._sanitize(reason)}")
    if tmp is not None:
        sg._force_rmtree(tmp)

    if verdict == "不推荐":
        if mode == "block":
            print("[skill-radar] 拦截模式（--block）：拒绝安装，不转调 skills CLI。",
                  file=sys.stderr)
            return 1
        print("[skill-radar] 警告模式：按建议继续安装（--block 可拦截此安装）。")

    rc = run_install(rest)
    if rc == 0:
        try:
            n = baseline_new_skills()
            print(f"[skill-radar] 装后基线完成：新增 {n} 个技能快照")
        except Exception as e:   # 基线失败不影响安装结果，只告警（KeyboardInterrupt 穿透）
            print(f"[skill-radar] 装后基线失败（{e}），可手动运行 skill_guard.py audit 补建",
                  file=sys.stderr)
    return rc
