"""skill-radar guard: deterministic offline security scanner for agent skills.

load_yaml 仅支持一个迷你 YAML 子集（规则与配置文件都用它）：
- 顶层为列表或映射；
- 嵌套映射一层；
- 标量 str/int/bool；
- 行内列表 ``[a, b]``；
- 块列表 ``- item``（仅二级）；
- ``#`` 注释与空行。
明确不支持多行字符串、锚点、深层嵌套——超出子集请用 PyYAML 自行转换。
"""
import base64, codecs, hashlib, json, math, os, re, shutil, stat, subprocess, sys, tempfile
from dataclasses import dataclass, field

def _scalar(s):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(p) for p in inner.split(",")] if inner else []
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s

def load_yaml(text):
    """解析受支持的 YAML 子集（见模块 docstring）。"""
    lines = []
    for raw in text.splitlines():
        stripped = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if stripped.strip():
            lines.append(stripped)
    if not lines:
        return {}
    top_is_list = lines[0].lstrip().startswith("- ")
    if top_is_list:
        out, cur = [], None
        for ln in lines:
            if ln.lstrip().startswith("- "):
                cur = {}
                out.append(cur)
                ln = ln.lstrip()[2:]
                if ln.strip():
                    k, v = ln.split(":", 1)
                    cur[k.strip()] = _scalar(v)
            else:
                k, v = ln.split(":", 1)
                cur[k.strip()] = _scalar(v)
        return out
    out, cur_key = {}, None
    for ln in lines:
        indented = ln.startswith("  ") or ln.startswith("\t")
        body = ln.strip()
        if indented and body.startswith("- "):
            if not isinstance(out.get(cur_key), list):
                out[cur_key] = []
            out[cur_key].append(_scalar(body[2:]))
        elif indented:
            k, v = body.split(":", 1)
            out[cur_key][k.strip()] = _scalar(v)
        else:
            cur_key, val = body.split(":", 1)
            out[cur_key] = {} if val.strip() == "" else None
            if val.strip():
                out[cur_key] = _scalar(val)
    return out

CATEGORIES = {"THEFT", "EXEC", "PERSIST", "EXFIL", "INJ", "ABUSE", "DECEP", "SUPPLY"}
SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
SEVERITY_WEIGHT = {"CRITICAL": 40, "HIGH": 25, "MEDIUM": 10, "LOW": 3, "INFO": 0}
EXCLUDED_DIRS = {"node_modules", ".git", "__pycache__", "AppData", "Library",
                 "site-packages", ".venv", "venv", ".cargo", "target", ".pytest_cache"}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_HASH_FILE_BYTES = 8 * 1024 * 1024   # 单文件哈希上限：超大文件不拖慢引擎（不哈希、不匹配）

@dataclass
class Rule:
    id: str
    category: str
    severity: str
    description: str
    patterns: list = field(default_factory=list)
    source: list = field(default_factory=list)
    sink: list = field(default_factory=list)
    pairing: str = None          # None | "same_file" | "cross_file"
    refs: list = field(default_factory=list)
    file_globs: list = field(default_factory=list)
    enabled: bool = True

def parse_rules(text_or_path):
    text = text_or_path
    if "\n" not in text_or_path and os.path.isfile(text_or_path):
        text = open(text_or_path, encoding="utf-8", errors="ignore").read()
    rules = []
    for item in load_yaml(text):
        for key in ("id", "category", "severity"):
            if key not in item:
                raise ValueError(f"rule missing '{key}': {item}")
        if item["category"] not in CATEGORIES:
            raise ValueError(f"unknown category: {item['category']}")
        if item["severity"] not in SEVERITIES:
            raise ValueError(f"unknown severity: {item['severity']}")
        src, snk = item.get("pattern_source"), item.get("pattern_sink")
        pairing = item.get("pairing")
        if (src or snk) and not (src and snk and pairing):
            raise ValueError(f"rule {item['id']}: pairing rules need pattern_source+pattern_sink+pairing")
        for p in item.get("patterns", []) + list(src or []) + list(snk or []):
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"rule {item['id']}: bad regex {p!r}: {e}")
        r = Rule(id=item["id"], category=item["category"], severity=item["severity"],
                 description=item.get("description", ""),
                 patterns=item.get("patterns", []),
                 source=src, sink=snk, pairing=pairing,
                 refs=item.get("refs", []), file_globs=item.get("file_globs", []),
                 enabled=item.get("enabled", True))
        rules.append(r)
    return rules

def collect_text_files(root):
    """返回 [(relpath, text)]；空字节嗅探排除二进制，>2MB 跳过，排除目录整支剪枝。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            try:
                if os.path.getsize(p) > MAX_FILE_BYTES:
                    continue
                with open(p, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            out.append((os.path.relpath(p, root), raw.decode("utf-8", errors="replace")))
    return out

def _file_hashes(root):
    """对 root 下全部文件的原始字节计算 SHA-256（relpath → hexdigest）。

    与 collect_text_files 同样的 EXCLUDED_DIRS 剪枝；但**不**做文本筛选——
    被空字节嗅探跳过的二进制与 >2MB 超限文件也参与哈希（二进制载荷正是
    ClawHavoc 型 IOC 场景）。单文件超过 MAX_HASH_FILE_BYTES 跳过。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            try:
                if os.path.getsize(p) > MAX_HASH_FILE_BYTES:
                    continue
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                out[os.path.relpath(p, root)] = h.hexdigest()
            except OSError:
                continue
    return out

@dataclass
class Finding:
    rule_id: str
    category: str
    severity: str
    file: str
    line: int
    excerpt: str
    message: str
    refs: list

def _match_line(line, regexes):
    return all(re.search(rx, line) for rx in regexes)

def run_l1(rule, files):
    """L1 模式引擎：patterns 全部命中同一行才算命中（行级 AND）。"""
    if not rule.enabled or not rule.patterns:
        return []
    out = []
    for rel, text in files:
        for i, line in enumerate(text.splitlines(), 1):
            if _match_line(line, rule.patterns):
                out.append(Finding(rule.id, rule.category, rule.severity, rel, i,
                                   line.strip()[:200], rule.description, rule.refs))
    return out

def _hit_lines(regexes, text):
    return [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if _match_line(ln, regexes)]

def run_pairing(rule, files):
    """source 行与 sink 行按 pairing 语义配对；finding 报在 sink 行，消息含 source 位置。"""
    if not rule.enabled or not (rule.source and rule.sink):
        return []
    src_hits = [(rel, i, ln) for rel, text in files for (i, ln) in _hit_lines(rule.source, text)]
    if not src_hits:
        return []
    out = []
    for rel, text in files:
        for i, ln in _hit_lines(rule.sink, text):
            partners = [s for s in src_hits if rule.pairing == "cross_file" or s[0] == rel]
            if partners:
                where = ", ".join(f"{p[0]}:{p[1]}" for p in partners[:3])
                out.append(Finding(rule.id, rule.category, rule.severity, rel, i,
                                   ln.strip()[:200],
                                   f"{rule.description} (source: {where})", rule.refs))
    return out

# ---------------------------------------------------------------- L3 混淆检测

ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"
ENTROPY_THRESHOLD, ENTROPY_MIN_LEN = 4.5, 32
BLOB_MIN_LEN = 24
# 膨胀上限（规格 §8.3）：rot13 自逆等会让候选链组合爆炸（实测 2000 token/54KB
# → 12000 候选），候选总数与解码产出总字节数双封顶，超限停止新增并报告截断。
MAX_DECODE_CANDIDATES = 200
MAX_DECODE_TOTAL_BYTES = 512 * 1024

# CJK 表意/音节文字与全形标点区间：熵检测的剔除对象。混淆 blob 本质是
# base64/hex 类 ASCII 串（剔除语义不变）；而语义文字按全字符多重集算熵天然
# 偏高——32 个互异汉字 H=5.0 即过阈值，曾致真实中文技能文档 99%+ 行命中 HIGH
# （dogfood 误报洪泛根因）。区间含统一表意主区/扩展 A–F/兼容区、CJK 符号与
# 部首、假名与谚文（同为音节文字，同谬）、全形标点。
_CJK_RANGES = ((0x3000, 0x303F), (0x3040, 0x30FF), (0x3400, 0x4DBF),
               (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0xF900, 0xFAFF),
               (0xFF00, 0xFFEF), (0x20000, 0x2FA1F))

def _non_cjk(s):
    """剔除 _CJK_RANGES 字符后的剩余串——熵检测只评估这部分。"""
    return "".join(ch for ch in s
                   if not any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES))

def _entropy(s):
    """Shannon 熵（多重集口径），只对非 CJK 剩余部分计算（_non_cjk）。"""
    s = _non_cjk(s)
    if not s: return 0.0
    counts = {}
    for ch in s: counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())

def _decode_candidates(text):
    """产出 (候选列表, 是否截断)，候选为 (层数, 解码文本)。base64/hex/rot13；
    递归上限 5 层；膨胀上限（候选总数 / 解码总字节）超限即停止新增并置截断标记。"""
    found = []
    total = 0
    truncated = False
    def _dec(t, depth):
        nonlocal total, truncated
        if truncated or depth > 5: return
        for tok in re.findall(r"[A-Za-z0-9+/=]{%d,}" % BLOB_MIN_LEN, t):
            for cand in _try_b64(tok) + _try_hex(tok) + _try_rot13(tok):
                if len(found) >= MAX_DECODE_CANDIDATES or \
                        total + len(cand.encode("utf-8")) > MAX_DECODE_TOTAL_BYTES:
                    truncated = True
                    return
                found.append((depth + 1, cand))
                total += len(cand.encode("utf-8"))
                _dec(cand, depth + 1)
    _dec(text, 0)
    return found, truncated

def _try_b64(tok):
    try:
        pad = tok + "=" * (-len(tok) % 4)
        raw = base64.b64decode(pad, validate=True)
        txt = raw.decode("utf-8")
        return [txt] if sum(c.isprintable() for c in txt) / max(len(txt), 1) > 0.85 else []
    except Exception: return []

def _try_hex(tok):
    try:
        txt = bytes.fromhex(tok).decode("utf-8")
        return [txt] if sum(c.isprintable() for c in txt) / max(len(txt), 1) > 0.85 else []
    except Exception: return []

def _try_rot13(tok):
    t = codecs.decode(tok, "rot_13")
    return [t] if t != tok and re.search(r"[a-z]{4}", t) else []

def run_l3(files, rules, max_depth=5):
    findings = []
    for rel, text in files:
        for i, line in enumerate(text.splitlines(), 1):
            non_cjk = _non_cjk(line)
            # 评估门在非 CJK 部分上：纯中文行（剩余为空）永不触发；中英混合行
            # 按 ASCII 部分（含数字符号）判定——混淆 blob 本质是 ASCII 串。
            h = _entropy(non_cjk)
            if len(non_cjk.strip()) >= ENTROPY_MIN_LEN and h > ENTROPY_THRESHOLD:
                findings.append(Finding("SR-OBFUS-001", "OBFUS", "HIGH", rel, i,
                    line.strip()[:200], f"高熵内容 (entropy={h:.2f}, 非 CJK 部分)", []))
            if any(ch in line for ch in ZERO_WIDTH):
                findings.append(Finding("SR-OBFUS-002", "OBFUS", "HIGH", rel, i,
                    line.strip()[:200], "隐藏字符（零宽/ homoglyph 标记）", []))
        cands, truncated = _decode_candidates(text)
        if truncated:
            findings.append(Finding("SR-OBFUS-004", "OBFUS", "LOW", rel, 1, "",
                                    "解码候选超限截断（膨胀上限 MAX_DECODE_CANDIDATES/"
                                    "MAX_DECODE_TOTAL_BYTES），解码重扫可能不完整", []))
        for depth, decoded in cands:
            if depth > max_depth:
                continue
            sub_files = [(f"{rel} (decoded L{depth})", decoded)]
            hits = []
            for rule in rules:
                hits.extend(run_l1(rule, sub_files))
                hits.extend(run_pairing(rule, sub_files))
            for h in hits:   # 解码后命中：镜像一条固定 rule_id 的 OBFUS finding
                findings.append(Finding("SR-OBFUS-003", "OBFUS", "HIGH", h.file, h.line,
                    h.excerpt, f"解码内容命中规则 {h.rule_id}: {h.message}", []))
            findings.extend(hits)
    return findings

# ---------------------------------------------------------------- 评分与报告

@dataclass
class ScanReport:
    skill_name: str; root: str; findings: list; score: int; files_scanned: int; ok: bool

def score_findings(findings):
    return min(100, sum(SEVERITY_WEIGHT.get(f.severity, 0) for f in findings))

def _sanitize(s):
    """输出侧清洗：移除零宽字符、U+FFFD 替换为 "?"、移除 C0 控制字符（保留 \t）。

    被扫内容会原样进入报告/JSON（excerpt/file/message），在 GBK（cp936）stdout 下
    print 会因无法编码而 UnicodeEncodeError 打崩渲染。只清洗输出，不碰检测输入
    （SR-OBFUS-002 等在 run_l3 输入侧判定，不受影响）。"""
    return "".join("?" if ch == "\ufffd" else ch
                   for ch in s
                   if ch not in ZERO_WIDTH and (ch >= " " or ch == "\t"))

def _sanitize_json(obj):
    """_sanitize 的递归版：清洗 JSON 输出树中的全部字符串（含 Finding 字段）。"""
    if isinstance(obj, str):
        return _sanitize(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(v) for v in obj]
    d = getattr(obj, "__dict__", None)
    return _sanitize_json(d) if d is not None else obj

def render_report(rep):
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    fs = sorted(rep.findings, key=lambda f: (order.get(f.severity, 9), f.file, f.line))
    lines = [f"skill: {rep.skill_name}   score: {rep.score}/100   files: {rep.files_scanned}"
             f"   verdict: {'PASS' if rep.ok else 'FAIL'}"]
    for f in fs:
        lines.append(f"  {f.severity:<8} {f.rule_id:<14} {f.file}:{f.line}  {f.message}")
        lines.append(f"           {f.excerpt}")
    if not rep.ok:
        # 拒绝安装的归因按实际 CRITICAL 存在与否分支：ok=False 且无 CRITICAL 只可能
        # 是 audit 侧的 drifted 裁定（scan 侧 ok=False 蕴含必有 CRITICAL）。
        if any(f.severity == "CRITICAL" for f in rep.findings):
            lines.append("建议: 拒绝安装（存在 CRITICAL）。人工 inspect 后可用 --accept-drift 重新基线化已装技能。")
        else:
            lines.append("建议: 拒绝安装（内容漂移，无 CRITICAL 命中）。人工 inspect 后可用 --accept-drift 重新基线化已装技能。")
    elif fs:
        lines.append("建议: inspect 命中项；MEDIUM 及以下可接受时照常安装。")
    else:
        lines.append("建议: 未命中任何规则。")
    return "\n".join(_sanitize(ln) for ln in lines)

# ---------------------------------------------------------------- 供应链 blocklist

def check_blocklist(yaml_text, name, repo, hashes):
    """命中技能名 / 仓库 / 任一文件哈希 → CRITICAL finding（category SUPPLY）。"""
    if isinstance(yaml_text, str):
        body = "\n".join(ln.strip() for ln in yaml_text.splitlines()
                         if ln.strip() and not ln.lstrip().startswith("#"))
        if body in ("[]", ""):   # 空黑名单 / 字面量 []（load_yaml 不支持）→ 无条目
            return []
    entries = load_yaml(yaml_text) if isinstance(yaml_text, str) else yaml_text
    out = []
    for e in entries:
        if e.get("name") and e["name"] == name:
            out.append(Finding("SR-BLOCK-001", "SUPPLY", "CRITICAL", "SKILL.md", 1,
                               f"skill name {name}", f"blocklist 命中 name（{e.get('source','')}）", []))
        if e.get("repo") and e["repo"] == repo:
            out.append(Finding("SR-BLOCK-001", "SUPPLY", "CRITICAL", "SKILL.md", 1,
                               f"repo {repo}", f"blocklist 命中 repo（{e.get('source','')}）", []))
        h = e.get("hash")
        if h:
            for rel, sha in (hashes or {}).items():
                if sha == h:
                    out.append(Finding("SR-BLOCK-001", "SUPPLY", "CRITICAL", rel, 1,
                                       sha[:16], f"blocklist 命中 hash {rel}（{e.get('source','')}）", []))
    return out

# ---------------------------------------------------------------- git URL 支持

def is_git_url(t):
    """http(s)://、git@ 开头或 .git 结尾视为 git 源；其余按本地路径处理。"""
    return t.startswith(("http://", "https://", "git@")) or t.endswith(".git")

def _repo_from_git_url(url):
    """从 git URL 提取 owner/repo（路径后两段，形如 github.com/owner/repo 的
    后两段；scp-like 冒号与 Windows 反斜杠视作斜杠，尾部 .git 剥离）。
    提取不到（不足两段）返回 ""。"""
    path = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", url)   # 剥 scheme
    path = path.split("@")[-1].replace(":", "/").replace("\\", "/")
    parts = [p for p in path.split("/") if p]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][:-4]
    if len(parts) < 2:
        return ""
    return f"{parts[-2]}/{parts[-1]}"

def resolve_target(target, timeout=120):
    """本地路径原样返回；git URL 浅克隆到临时目录（调用方负责在扫描后 shutil.rmtree）。
    clone 失败/超时：就地清理临时目录后原样抛出，不留 %TEMP% 残留。"""
    if not is_git_url(target):
        return target
    base = tempfile.mkdtemp(prefix="skill-radar-scan-")
    try:
        # protocol.ext.allow=never：.git 后缀启发式会放行 ext::<command>，
        # 该传输会被 git 经 shell 执行——必须在 clone 前显式封禁。
        subprocess.run(["git", "-c", "protocol.ext.allow=never",
                        "clone", "--depth", "1", "-q", target, base],
                       check=True, timeout=timeout,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except BaseException:
        _force_rmtree(base)   # 失败的 clone 可能留下只读 .git 对象，须强制删
        raise
    return base

def _force_rmtree(path):
    """Windows 上 git 对象文件带只读属性，rmtree(ignore_errors=True) 会静默残留——
    先逐条目按「原 mode | 写位」清出写位再删。必须按位或而非替换成裸 S_IWRITE：
    POSIX 上替换会让目录丢失 r/x 位，os.walk 与 rmtree 随即双双静默失效（整树残留）。"""
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            try:
                os.chmod(p, os.lstat(p).st_mode | stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)

# ---------------------------------------------------------------- 引擎编排与 CLI

def run_engine(root, rules, blocklist_text="[]", max_depth=5, repo=""):
    """编排全引擎：收集文件 → L1+L2（逐规则）+ L3（一次）→ blocklist → 评分。

    ok 语义：无 CRITICAL 即 PASS；blocklist hash IOC 对文件**原始字节**算
    SHA-256（含被跳过的二进制/超限文件，单文件 8MB 哈希上限）。
    repo：git 源扫描时传入的 owner/repo 标识（供 blocklist repo IOC 匹配），本地路径默认 ""。
    """
    files = collect_text_files(root)
    findings = []
    for r in rules:
        findings.extend(run_l1(r, files))
        findings.extend(run_pairing(r, files))
    findings.extend(run_l3(files, rules, max_depth=max_depth))
    name = os.path.basename(os.path.normpath(root))
    hashes = _file_hashes(root)
    findings.extend(check_blocklist(blocklist_text, name=name, repo=repo, hashes=hashes))
    score = score_findings(findings)
    return ScanReport(name, root, findings, score, len(files),
                      ok=not any(f.severity == "CRITICAL" for f in findings))

def main(argv=None):
    """scan/discover/audit 子命令 CLI。scan/audit 统一返回退出码（scan --strict 且有
    CRITICAL → 1；audit --strict 且 summary 有 DRIFT/NEW 行 → 1），不内部 raise
    SystemExit；用户错误路径除外（与 argparse 口径一致）：discover --deep / audit
    --watch 未授权 consent、audit --show-diff/--accept-drift 的技能不在快照中，
    均 raise SystemExit。"""
    import argparse
    # 输出口径一次性封死整类问题：_sanitize 只清零宽/C0/U+FFFD，被扫内容的
    # emoji 等 cp936 不可编码字符会原样进入报告，GBK 控制台下 print 仍会
    # UnicodeEncodeError 打崩渲染。入口对 stdout/stderr 统一 errors="replace"
    # （保编码不改——CJK 控制台正常输出，不可编码字符降级 "?"）。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass   # 非 TextIOWrapper（pytest 捕获桩/已重定向）则不处理
    ap = argparse.ArgumentParser(prog="skill-radar guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("target")
    p_scan.add_argument("--rules", default=os.path.join(os.path.dirname(__file__), "rules", "defaults.yaml"))
    p_scan.add_argument("-f", "--rules-inline", dest="rules_inline")
    p_scan.add_argument("--blocklist", default=os.path.join(os.path.dirname(__file__), "rules", "blocklist.yaml"))
    p_scan.add_argument("--strict", action="store_true")
    p_scan.add_argument("--json", action="store_true")
    p_scan.add_argument("--yes", action="store_true")
    p_disc = sub.add_parser("discover")
    p_disc.add_argument("--deep", action="store_true",
                        help="全盘扫描起点（需 deep_scan 授权：交互确认或 --yes）")
    p_disc.add_argument("--yes", action="store_true",
                        help="非交互脚本中显式授权 deep_scan（写入 config 持久化）")
    p_audit = sub.add_parser("audit")
    # defaults.yaml 由计划二任务 15 填充，当前仓库尚未落地：默认路径存在则用之，
    # 否则回退空规则集文本（parse_rules("") → 无规则；漂移/基线/blocklist 不受影响）。
    # 显式传入的 --rules 始终严格读取（文件缺失即刻报错，不吞拼写错误）。
    _audit_rules_default = os.path.join(os.path.dirname(__file__), "rules", "defaults.yaml")
    p_audit.add_argument("--rules",
                         default=_audit_rules_default if os.path.isfile(_audit_rules_default) else "")
    p_audit.add_argument("--blocklist", default=os.path.join(os.path.dirname(__file__), "rules", "blocklist.yaml"))
    p_audit.add_argument("--strict", action="store_true")
    p_audit.add_argument("--show-diff", metavar="SKILL",
                         help="打印该技能当前内容与最近基线的 diff（漂移确认后对上个基线；不落盘）")
    p_audit.add_argument("--accept-drift", metavar="SKILL",
                         help="人工 inspect 后重建该技能的哈希基线（status 重置 baseline-unreviewed）")
    p_audit.add_argument("--watch", type=int, metavar="SEC",
                         help="轮询模式：每 SEC 秒审计一轮并落盘，只打印 NEW/DRIFT/CRITICAL 命中行"
                              "（需 watch 授权：交互确认或 --yes；Ctrl+C 退出）")
    p_audit.add_argument("--json", action="store_true")
    p_audit.add_argument("--yes", action="store_true",
                         help="非交互授权（--watch 轮询模式的 watch 授权；首次授权落盘）")
    args = ap.parse_args(argv)
    if args.cmd == "scan":
        tmp = None   # URL 分支克隆出的临时目录；本地路径保持 None，绝不被 rmtree
        repo = ""    # git 源时从 URL 提取 owner/repo 供 blocklist repo IOC 匹配
        target = resolve_target(args.target)
        if is_git_url(args.target):
            tmp = target
            repo = _repo_from_git_url(args.target)
        try:
            rules = parse_rules(args.rules_inline) if args.rules_inline else parse_rules(args.rules)
            rep = run_engine(target, rules, open(args.blocklist, encoding="utf-8").read(), repo=repo)
            print(render_report(rep) if not args.json else
                  json.dumps(_sanitize_json(rep.__dict__), ensure_ascii=False, indent=1))
        finally:
            if tmp is not None:
                _force_rmtree(tmp)
        if args.strict and not rep.ok:
            return 1
    elif args.cmd == "discover":
        cfg = load_config()
        if args.deep:
            cfg = gate_consent(cfg, "deep_scan", args.yes)
            save_config(cfg)   # 授权状态持久化（已授权时幂等重写，无损回读）
            roots = discover_roots(deep=True)
        else:
            roots = discover_roots()   # 有界扫描，不涉 consent
        registered = {_canon_path(r.get("path", ""))
                      for r in cfg.get("roots", []) if r.get("path")}
        tagged = [(r, _canon_path(r) in registered) for r in roots]
        fresh = [r for r, known in tagged if not known]
        print(f"discover: {len(roots)} 个技能根（deep={args.deep}），其中新根 {len(fresh)} 个")
        for r, known in tagged:
            print(f"  [{'已注册' if known else '新'}] {r}")
        if fresh:
            print(f"新根不自动写入：确认后手动加入 {_config_path()} 的 roots 节"
                  "（- section: roots / path: <路径> / builtin: false）。")
    elif args.cmd == "audit":
        return cmd_audit(args)
    return 0

# ---------------------------------------------------------------- 配置存储（roots 注册表 / consent / trust）

HOME = os.path.expanduser("~")
GUARD_DIR = os.path.join(HOME, ".skill-radar")
CONFIG_NAME = os.path.join(GUARD_DIR, "config.yaml")
SNAPSHOTS_NAME = os.path.join(GUARD_DIR, "snapshots.json")

def _config_path():
    """运行时从 GUARD_DIR 派生 config 路径。

    模块级 CONFIG_NAME 是导入期常量（绑定当时的 HOME）；若函数直接引用它，
    测试 monkeypatch skill_guard.GUARD_DIR 后读写仍落在真实用户目录——
    既有污染真实配置的风险，也会让重定向失效。故运行时一律经此派生。"""
    return os.path.join(GUARD_DIR, "config.yaml")

def builtin_roots():
    """内置技能根目录注册表（audit/discover 的扫描基线）。

    路径统一正斜杠规范化：os.path.join 在 Windows 产生反斜杠，会使
    path 形态依赖平台且 YAML 中易混淆；Python 的 os 函数在 Windows
    上同样接受正斜杠。"""
    return [{"path": os.path.join(HOME, d, "skills").replace(os.sep, "/"),
             "builtin": True}
            for d in (".agents", ".claude", ".codex", ".cursor", ".qoder-cn", ".zcode")]

def _default_config():
    return {"consent": {"deep_scan": False, "watch": False},
            "roots": builtin_roots(),
            "trust": {"owners": [], "repos": [], "hashes": []}}

def load_config():
    """读 config.yaml：文件缺失 → 默认配置；存在 → 默认值打底、文件值覆盖。

    文件为 save_config 的对称格式：顶层列表，每个条目形如
    ``- section: <名>`` 加一层键值（roots 每条记录一个条目；无 section 前缀的
    单键条目是顶层标量键，如 usage_file）——这正是 load_yaml 子集原生支持的
    「顶层映射列表」语法（与规则/黑名单文件同构）。手写映射形态（consent: ...
    等）也接受。已知三节（consent/roots/trust）按既有逻辑合并；**未知顶层键
    原样透传**（如 §9 联动消费的 usage_file——若丢弃，消费端 cfg.get
    ("usage_file") 恒 None，联动成端到端死代码）。"""
    path = _config_path()
    if not os.path.isfile(path):
        return _default_config()
    data = load_yaml(open(path, encoding="utf-8").read())
    cfg = {"consent": {}, "roots": [], "trust": {}}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            sec = item.get("section")
            body = {k: v for k, v in item.items() if k != "section"}
            if sec == "roots":
                cfg["roots"].append(body)
            elif sec in cfg and isinstance(cfg[sec], dict):
                cfg[sec].update(body)
            elif sec is None:
                # 无 section 前缀的单键条目 = save_config 的顶层标量写法；
                # 已知三节不在此覆盖（仍走上方既有分支）。
                cfg.update({k: v for k, v in body.items() if k not in cfg})
    elif isinstance(data, dict):
        for k, v in data.items():
            if v is not None:
                cfg[k] = v
    merged = _default_config()
    # 以真值过滤：文件中缺失/为空的 section 不覆盖对应默认值
    #（save_config 恒写全三节，故对其产物与「非 None 即覆盖」等价）。
    merged.update({k: v for k, v in cfg.items() if v})
    return merged

def save_config(cfg):
    """将 config 结构序列化为 load_yaml 可无损回读的对称子集并落盘。

    结构契约：consent/trust 为一层映射（各写一个 section 条目）；
    roots 为 {path, builtin} 记录列表（每条一个 section 条目）；
    顶层标量键（如 §9 联动的 usage_file）写为无 section 前缀的单键条目
    ``- key: value``——load_config 列表形态按未知顶层键透传回读。
    只服务该结构，不是通用 YAML 序列化器。"""
    os.makedirs(GUARD_DIR, exist_ok=True)
    def dump(v):   # 标量/内联列表 → load_yaml._scalar 可回读的形态
        if isinstance(v, bool): return "true" if v else "false"
        if isinstance(v, int): return str(v)
        if isinstance(v, list): return "[" + ", ".join(dump(x) for x in v) + "]"
        return str(v)
    lines = []
    for section, val in cfg.items():
        if isinstance(val, dict):
            lines.append(f"- section: {section}")
            for k2, v2 in val.items():
                lines.append(f"  {k2}: {dump(v2)}")
        elif isinstance(val, list):
            for item in val:
                if not isinstance(item, dict):
                    continue
                lines.append(f"- section: {section}")
                for k2, v2 in item.items():
                    lines.append(f"  {k2}: {dump(v2)}")
        elif val is not None:
            # 顶层标量（str/int/bool）：无 section 前缀的单键条目（dump 已
            # 支持标量；此前该分支缺失，标量被静默丢弃 → usage_file 无写入路径）
            lines.append(f"- {section}: {dump(val)}")
    open(_config_path(), "w", encoding="utf-8").write("\n".join(lines) + "\n")

# ---------------------------------------------------------------- 快照存储与漂移比对

def load_snapshots():
    """读 snapshots.json：结构 ``{"version": 1, "skills": {绝对路径: 快照}}``。

    文件缺失 → 空基线 ``{"version": 1, "skills": {}}``。SNAPSHOTS_NAME 在函数体内
    按全局名**运行时**查找（非导入期绑定到局部），故测试 monkeypatch
    ``skill_guard.SNAPSHOTS_NAME`` 即可整体重定向读写（与 _config_path 的
    派生模式等义的隔离约定）。"""
    if not os.path.isfile(SNAPSHOTS_NAME):
        return {"version": 1, "skills": {}}
    return json.load(open(SNAPSHOTS_NAME, encoding="utf-8"))

def save_snapshots(data):
    """将快照结构落盘（load_snapshots 的对称格式；ensure_ascii=False 保非 ASCII 原样）。

    建目录基于 SNAPSHOTS_NAME 的 dirname 而非 GUARD_DIR：SNAPSHOTS_NAME 被
    monkeypatch 到任意路径时（如测试临时文件 s.json），目录创建随之重定向，
    不会触碰真实 ~/.skill-radar。"""
    os.makedirs(os.path.dirname(SNAPSHOTS_NAME), exist_ok=True)
    json.dump(data, open(SNAPSHOTS_NAME, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

def snapshot_dir(root, status, score):
    """对单个技能目录做漂移基线快照：collect_text_files 收集文本，逐文件
    SHA-256（utf-8 errors=replace 编码后哈希）。

    与 run_engine 的 _file_hashes（原始字节，blocklist IOC 用）语义**刻意不同**：
    这里是漂移基线，只需对同一 collect_text_files 文本口径可复现比对，
    不要求与 blocklist 的字节级哈希一致；二进制（空字节嗅探排除）与
    >2MB 超限文件不进入基线。

    快照存储文件自身（SNAPSHOTS_NAME）不入基线：存储落在被扫目录之下时，
    每次保存都会改写其内容，若入基线则每次比对必把存储自身报为 changed
    （自引用漂移）。按绝对路径精确排除，不影响恰好同名的其他文件。"""
    from datetime import datetime
    store = os.path.abspath(SNAPSHOTS_NAME)
    files = [(rel, t) for rel, t in collect_text_files(root)
             if os.path.abspath(os.path.join(root, rel)) != store]
    return {"name": os.path.basename(os.path.normpath(root)), "status": status,
            "score": score, "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "hashes": {rel: hashlib.sha256(t.encode("utf-8", errors="replace")).hexdigest()
                       for rel, t in files}}

def diff_snapshot(old, new):
    """两份 hashes（relpath → sha256）比对：added/removed/changed 各为排序 relpath 列表。

    changed = 双方都有但哈希不同。"""
    old_k, new_k = set(old), set(new)
    return {"added": sorted(new_k - old_k), "removed": sorted(old_k - new_k),
            "changed": sorted(k for k in old_k & new_k if old[k] != new[k])}

# ---------------------------------------------------------------- discover 有界搜索

def _is_skill_dir(path):
    return os.path.isfile(os.path.join(path, "SKILL.md"))

def discover_roots(deep=False, max_depth=4):
    """有界搜索技能根：起点 HOME 与当前工作目录，深度按**起点**区分——
    HOME 起点限深 max_depth 层（避免整棵用户目录下探）；cwd（当前项目）起点
    **不限深**（规格 §5「当前项目全深度」）；deep=True 的全盘起点同样不限深。
    两起点为同一路径时（HOME == cwd）按 HOME 有界处理（此时「当前项目」即
    HOME，全深度语义让位于有界保护，且与既有测试口径一致）。

    目录含 SKILL.md 即技能根（_is_skill_dir）：收入 found 并从 dirnames 移除
    （不再向技能内部下探）；每层剪枝 EXCLUDED_DIRS（不限深的 cwd/deep walk
    同一剪枝，非附加逻辑）。深度按 os.sep 计数且相对各自起点；起点先 normpath
    归一——HOME 若为正斜杠形态（如 C:/Users/x），按反斜杠 os.sep 计数会得 0，
    限深静默失效。deep=True 为全盘扫描骨架：起点换成 _deep_starts 的跨平台
    集合（Windows=HOME 与 cwd 所在盘符根的去重集合，可能跨盘；POSIX="/"），
    正式放开须经 consent 门（gate_consent）。HOME 在函数体内按模块全局
    **运行时**查找，测试 monkeypatch skill_guard.HOME 即可重定向。
    起点不存在时 os.walk 静默产出空序列（不报错、不崩溃）。"""
    starts = [HOME, os.getcwd()]
    if deep:
        starts = _deep_starts(HOME, os.getcwd())
    home_norm = os.path.normcase(os.path.normpath(HOME))
    found = set()
    for start in starts:
        start = os.path.normpath(start)
        limit = max_depth if os.path.normcase(start) == home_norm else None
        start_depth = start.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, _ in os.walk(start):
            if limit is not None:
                depth = dirpath.rstrip(os.sep).count(os.sep) - start_depth
                if depth >= limit:
                    dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            for d in list(dirnames):
                p = os.path.join(dirpath, d)
                if _is_skill_dir(p):
                    found.add(p); dirnames.remove(d)
    return sorted(found)

def _deep_starts(home, cwd):
    """deep 全盘扫描起点（跨平台）：Windows 取 home 与 cwd 所在**盘符根**的
    去重集合（可跨盘，如 C:\\ 与 F:\\ 各一个）；POSIX（splitdrive 恒空串）
    取 "/"。独立成纯函数以便语义测试：在任一平台打桩 os.path.splitdrive
    即可验证另一分支。修复自任务 3 遗留的 os.getcwd()[:3]——它在 POSIX
    产生 "/ho" 式垃圾起点（且 Windows 上遗漏 HOME 盘时无兜底）。"""
    drives = {os.path.splitdrive(p)[0] for p in (home, cwd)} - {""}
    return sorted(d + os.sep for d in drives) if drives else ["/"]

# ---------------------------------------------------------------- consent 授权门

def _canon_path(p):
    """跨平台路径比较规范化：normpath → normcase（仅 Windows 小写化）→ 正斜杠。
    discover 输出据此把发现的根对照 config roots 注册表标注「已注册 / 新」。"""
    return os.path.normcase(os.path.normpath(p)).replace(os.sep, "/")

def check_consent(cfg, action):
    """cfg["consent"][action] 真值即已授权；键/节缺失一律视为未授权。"""
    return bool(cfg.get("consent", {}).get(action))

def interactive_consent(action):
    """交互确认：打印成本说明后要求**完整**输入 yes（y/no/空/Enter 均拒绝）。"""
    print(f"[skill-radar] 该动作（{action}）读取面较大，需要明确授权。")
    print("  了解成本说明见 docs/threat-model.md。输入完整 yes 继续。")
    return input("confirm> ").strip() == "yes"

def gate_consent(cfg, action, yes_flag):
    """consent 三路门，返回（可能补写授权的）cfg：
    已授权 → 原样返回；--yes → 写入授权并返回；交互 TTY → 询问，同意则写入
    返回；未授权且无法交互确认（管道/CI）→ raise SystemExit（提示传 --yes）。
    TTY 分支中 input() 的 EOF（Ctrl-D，或 /dev/null 等被误判 TTY 的环境）与
    stdin 不可读（OSError/已关闭）一律视为拒绝，走 SystemExit 而非裸 traceback。
    授权落盘由调用方在返回后 save_config(cfg) 完成（main 的 discover --deep 已接）。"""
    if check_consent(cfg, action):
        return cfg
    if yes_flag:
        cfg.setdefault("consent", {})[action] = True
        return cfg
    try:
        agreed = sys.stdin.isatty() and interactive_consent(action)
    except (EOFError, OSError, ValueError):
        agreed = False
    if agreed:
        cfg.setdefault("consent", {})[action] = True
        return cfg
    raise SystemExit(f"[skill-radar] 动作 {action} 未授权：交互确认或在脚本中传 --yes")

# ---------------------------------------------------------------- audit 主流程（基线 / 漂移 / 信任降级）

def apply_trust(findings, trusted):
    """信任降级（规格 §4 信任链）：trusted 时 HIGH→MEDIUM、MEDIUM→LOW、LOW→INFO
    各降一级，message 追加 " [trusted, downgraded]"；CRITICAL 永不降级（底线），
    不在映射中的未知等级原样保留。trusted=False 原样返回同一列表。"""
    if not trusted: return findings
    down = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "INFO"}
    return [f if f.severity == "CRITICAL" or f.severity not in down
            else Finding(f.rule_id, f.category, down[f.severity], f.file, f.line,
                         f.excerpt, f.message + " [trusted, downgraded]", f.refs)
            for f in findings]

def _is_trusted(cfg, root, skill_hash=""):
    """信任根判定（规格 §4 信任链，三级任一命中即 trusted）：
    - owners / repos：任一子串命中 root 归一路径（反斜杠→正斜杠，大小写敏感）；
    - hashes：skill_hash（技能 SKILL.md **原始字节** SHA-256，与 _file_hashes
      同口径，open("rb") 读取）与 cfg["trust"]["hashes"] 任一条目相等（双侧
      lower() 比较）。
    （旧 docstring 曾称"第三级内容 hash 信任由 blocklist/IOC 侧承接"——不成立：
    blocklist 是黑名单 IOC 匹配，与白名单信任分级无关；hash 级在此实现。）"""
    t = cfg.get("trust", {})
    root_norm = root.replace("\\", "/")
    if any(o in root_norm for o in t.get("owners", [])) or \
       any(r in root_norm for r in t.get("repos", [])):
        return True
    return bool(skill_hash) and skill_hash.lower() in \
        {str(h).lower() for h in t.get("hashes", [])}

def audit_roots(roots, rules_text, blocklist_text, snapshots, cfg, max_depth=5):
    """audit 主流程（纯函数层；CLI 接入在后续任务）：逐根逐技能 run_engine →
    apply_trust → 快照比对，返回 summary 字符串列表——**每个技能恰一条**：
    状态行（NEW/DRIFT/OK 前缀 + 技能名 + score + 状态）+ render_report 块。

    状态机（规格 §4）：快照无此技能 → NEW，建基线 status="baseline-unreviewed"；
    哈希有变化（added/removed/changed 任一非空）→ DRIFT，status="drifted"；
    无变化 → 保留原状态 + OK。快照键 = 技能目录路径，值五键齐全；DRIFT 条目
    额外携带 prev_hashes=被覆写前的旧基线 hashes——否则 --show-diff 在漂移被
    audit 确认的瞬间就退化为空 diff（s["hashes"] 已是当前内容），状态行指路的
    inspect 通道随之关闭（规格 §4「人工 inspect 后裁定」闭环缺眼）；NEW/
    无变化条目不携带该键。哈希口径用
    snapshot_dir（文本哈希语义，与 run_engine 的字节级 IOC 哈希刻意不同）。
    前序裁定：render_report 的「拒绝安装」文案前提是 ok 与 CRITICAL 绑定——
    audit 侧以 ``rep.ok = rep.ok and new["status"] != "drifted"`` 补全，drifted
    报告即使无 CRITICAL 也视为 FAIL；此时在报告块尾附一行漂移归因纠正说明
    （render_report 既有文案不动，文案分支化留给 CLI 任务），drifted 且真有
    CRITICAL 时不附（既有文案本就准确）。信任降级后重算 rep.score：报告头、
    状态行、快照与 §9 高危门控均按降级后分数自洽。
    规格 §3 共存计数行仅在池内多技能且确有 EXFIL 命中时附加（summary 每技能
    恰一条是测试契约，空计数行不附加）；§9 联动提示需 cfg["usage_file"] 指向
    v1 monitor 用量 JSON（缺文件/坏 JSON 静默跳过，浅耦合）。"""
    from datetime import datetime
    rules = parse_rules(rules_text)
    summary = []
    last_findings = {}   # 技能路径 → 本次 audit 的 findings（供 §3 共存计数，调用结束即弃）
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            skill = os.path.join(root, entry)
            if not _is_skill_dir(skill):
                continue
            try:   # 信任第三级：SKILL.md 原始字节 SHA-256（与 _file_hashes 同口径）
                with open(os.path.join(skill, "SKILL.md"), "rb") as f:
                    skill_hash = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                skill_hash = ""
            trusted = _is_trusted(cfg, root, skill_hash)   # owners/repos 为路径级，逐技能重算结果不变
            rep = run_engine(skill, rules, blocklist_text, max_depth=max_depth)
            rep.findings = apply_trust(rep.findings, trusted)
            rep.score = score_findings(rep.findings)   # 信任降级后重算：报告头/状态行/快照/§9 门控按降级后分数自洽
            last_findings[skill] = rep.findings
            new = {"name": entry, "status": "baseline-unreviewed", "score": rep.score,
                   "scanned_at": datetime.now().isoformat(timespec="seconds"),
                   "hashes": snapshot_dir(skill, "x", rep.score)["hashes"]}
            old = snapshots["skills"].get(skill)
            if old is None:
                head = f"NEW       {entry}  score={rep.score}  → baseline-unreviewed"
            else:
                d = diff_snapshot(old["hashes"], new["hashes"])
                if d["added"] or d["removed"] or d["changed"]:
                    new["status"] = "drifted"
                    # 留住被覆写的旧基线：--show-diff 据此在 drift 确认后仍能
                    # 回答"自上次基线以来改了什么"（状态行指路的 inspect 通道）
                    new["prev_hashes"] = old["hashes"]
                    head = (f"DRIFT     {entry}  +{len(d['added'])} -{len(d['removed'])} ~{len(d['changed'])}"
                            f"（用 --show-diff {entry} 查看）")
                else:
                    new["status"] = old["status"]   # 未变化，保留原状态
                    # prev_hashes 随保留的状态一并透传：DRIFT 确认后的一轮无变化
                    # re-audit（watch 模式一轮轮询即触发）若不透传，prev_hashes
                    # 随条目覆写丢失——状态仍 drifted、报告仍指路 --show-diff，
                    # diff 却已退化为空（inspect 窗口被无变化轮询关闭）。
                    if "prev_hashes" in old:
                        new["prev_hashes"] = old["prev_hashes"]
                    head = f"OK        {entry}  score={rep.score}"
            rep.ok = rep.ok and new["status"] != "drifted"
            snapshots["skills"][skill] = new
            rep.skill_name = entry; rep.root = skill
            block = _sanitize(head) + "\n" + render_report(rep)
            if new["status"] == "drifted" and not any(f.severity == "CRITICAL" for f in rep.findings):
                # 「拒绝安装」既有文案在纯漂移场景归因失真；附加一行纠正说明
                #（追加-only；render_report 侧的文案分支化已由 CLI 任务落地）
                block += "\n" + _sanitize("  注: 该技能 verdict FAIL 由内容漂移引起，非 CRITICAL 命中；用 --show-diff 查看")
            summary.append(block)
    # 规格 §3：报告上下文——共存计数（与网络外发模式工具共存的技能数）
    netcap = {n for n, s in snapshots["skills"].items()
              if any(f.category == "EXFIL" for f in last_findings.get(n, []))}
    if len(snapshots["skills"]) > 1 and netcap:
        summary.append(_sanitize(f"上下文: 池内 {len(netcap)} 个技能含网络外发模式（跨技能运行时关联在 v3）"))
    # 规格 §9：与 v1 用量数据联动提示（可选、浅耦合）
    usage_path = cfg.get("usage_file")
    if usage_path and os.path.isfile(usage_path):
        try:
            usage = json.load(open(usage_path, encoding="utf-8")).get("skills", {})
            for name, s in snapshots["skills"].items():
                u = usage.get(s["name"], {})
                # 零使用判定对 usage dict 的全部数值键求和：不写死 agent 名
                # （规格 §1「agent 名只允许两处」约束）；v1 schema 各键全是
                # int 计数，对既有三键语义等价，新增 agent 键自动纳入。
                if s["score"] >= 25 and \
                        sum(v for v in u.values() if isinstance(v, int)) == 0:
                    summary.append(_sanitize(f"联动提示: {s['name']} 风险分 {s['score']} 且从未被使用 → 优先删除候选"))
        except (OSError, ValueError):
            pass
    return summary

# ---------------------------------------------------------------- audit CLI 接入

def _find_skill_entry(snaps, needle):
    """在快照中按名称或路径找技能条目，返回 (存储键, 条目)。

    路径比对双侧 os.path.normpath 归一（快照键为 os.path.join 裸形态，跨平台
    分隔符差异由 normpath 吸收；名称比对优先）。未命中属用户错误路径，
    raise SystemExit（与 argparse / discover consent 门口径一致）。"""
    for path, s in snaps["skills"].items():
        if s["name"] == needle or os.path.normpath(path) == os.path.normpath(needle):
            return path, s
    raise SystemExit(f"skill not in snapshots: {needle}")

def _watch_once(args, cfg, rules_text, bl_text, snaps):
    """watch 轮询的单轮体（自 cmd_audit 循环抽出以便测试，无行为差异）：
    当前注册根全量审计 → 快照与配置落盘（配置每轮幂等重写，口径同 discover
    --deep 的授权落盘）→ 返回命中行：summary 展平为行后只保留 NEW/DRIFT 状态
    行与 "  CRITICAL" finding 行（render_report 的 finding 行为两空格缩进）。
    行级而非块级过滤是刻意的：summary 元素是多行报告块，块头只会是 NEW/DRIFT/OK，
    块级 startswith("  CRITICAL") 恒不命中——未变化技能（OK 头）内的 CRITICAL
    命中在轮询中将永久不可见，与「watch 只报变化与高危」语义相悖；展平成行后
    CRITICAL 行、NEW/DRIFT 状态行一并进入输出（上限切片由调用方负责）。
    args 形参当前未消费，保留以维持调用点签名稳定（watch 侧 --json 等输出
    分支后续接入时无需改动调用方）。"""
    roots = [r["path"] for r in cfg["roots"] if os.path.isdir(r["path"])]
    summary = audit_roots(roots, rules_text, bl_text, snaps, cfg)
    save_snapshots(snaps); save_config(cfg)
    return [ln for ln in "\n".join(summary).splitlines()
            if ln.startswith(("NEW", "DRIFT", "  CRITICAL"))]

def cmd_audit(args):
    """audit 子命令分支：
    - --show-diff SKILL：当前内容快照哈希 vs 基线 → diff JSON 打印，**不落盘**。
      基线取 prev_hashes（漂移已被 audit 确认落盘时，s["hashes"] 是确认后的
      当前内容，须对上个基线比对才能回答"改了什么"）；条目无 prev_hashes
      （NEW/无变化/accept 后）时对存储基线 s["hashes"]（原语义不变）；
    - --accept-drift SKILL：重建该技能哈希基线（哈希/scanned_at 取当前内容，
      status 重置 baseline-unreviewed——否则 drift 状态在 accept 后永久滞留，
      每次 audit 继续报 drifted，违背规格 §4「合法更新噪音不淹没」的初衷），
      落盘后返回 0；
    - --watch SEC：consent 门控轮询——先 gate_consent("watch") + save_config
      （首次授权落盘，已授权时幂等重写；未授权且非交互 → SystemExit），随后
      每 SEC 秒执行一轮 _watch_once（审计 + 快照/配置落盘），只打印 NEW/DRIFT/
      CRITICAL 命中行（[HH:MM:SS] 前缀，最多 5 条）；Ctrl+C 退出（KeyboardInterrupt
      不捕获、不被 gate 的 (EOFError, OSError, ValueError) 异常网吞掉，原样穿透）；
    - 默认：audit_roots 全量审计 + save_snapshots + 打印 summary；--strict 且
      summary 有 DRIFT/NEW 行 → 1。"""
    cfg = load_config()
    rules_text = open(args.rules, encoding="utf-8").read() if args.rules else ""
    bl_text = open(args.blocklist, encoding="utf-8").read()
    snaps = load_snapshots()
    if args.show_diff:
        path, s = _find_skill_entry(snaps, args.show_diff)
        cur = snapshot_dir(path, s["status"], s["score"])["hashes"]
        base = s["prev_hashes"] if "prev_hashes" in s else s["hashes"]
        d = diff_snapshot(base, cur)
        print(json.dumps(d, indent=1))   # ensure_ascii 默认 True：GBK 控制台安全
        return 0
    if args.accept_drift:
        path, s = _find_skill_entry(snaps, args.accept_drift)
        cur = snapshot_dir(path, s["status"], s["score"])
        entry = {**s, "hashes": cur["hashes"],
                 "scanned_at": cur["scanned_at"],
                 "status": "baseline-unreviewed"}
        entry.pop("prev_hashes", None)   # 接受后不再有"上个基线"（--show-diff 回落存储基线）
        snaps["skills"][path] = entry
        save_snapshots(snaps)
        print(f"re-baselined: {s['name']}")
        return 0
    if args.watch:
        cfg = gate_consent(cfg, "watch", args.yes)
        save_config(cfg)   # 首次授权落盘（已授权时幂等重写，无损回读）
        import time as _time
        from datetime import datetime as _dt
        print(f"watching every {args.watch}s, Ctrl+C to exit…", flush=True)
        while True:
            hits = _watch_once(args, cfg, rules_text, bl_text, snaps)
            if hits:
                print(f"[{_dt.now():%H:%M:%S}] " + " | ".join(hits[:5]), flush=True)
            _time.sleep(args.watch)
    roots = [r["path"] for r in cfg["roots"] if os.path.isdir(r["path"])]
    summary = audit_roots(roots, rules_text, bl_text, snaps, cfg)
    save_snapshots(snaps)
    print("\n".join(summary) if not args.json else
          json.dumps(summary, ensure_ascii=False, indent=1))
    if args.strict and any(l.startswith(("DRIFT", "NEW")) for l in summary):
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
