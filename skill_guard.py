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
import base64, codecs, hashlib, json, math, os, re, shutil, subprocess, sys, tempfile
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
                 "site-packages", ".venv", "venv", ".cargo", "target"}
MAX_FILE_BYTES = 2 * 1024 * 1024

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

def _entropy(s):
    if not s: return 0.0
    counts = {}
    for ch in s: counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())

def _decode_candidates(text):
    """产出 (层数, 解码文本)。base64/hex/rot13；递归上限 5 层。"""
    found = []
    def _dec(t, depth):
        if depth > 5: return
        for tok in re.findall(r"[A-Za-z0-9+/=]{%d,}" % BLOB_MIN_LEN, t):
            for cand in _try_b64(tok) + _try_hex(tok) + _try_rot13(tok):
                found.append((depth + 1, cand)); _dec(cand, depth + 1)
    _dec(text, 0)
    return found

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
            if len(line.strip()) >= ENTROPY_MIN_LEN and _entropy(line) > ENTROPY_THRESHOLD:
                findings.append(Finding("SR-OBFUS-001", "OBFUS", "HIGH", rel, i,
                    line.strip()[:200], f"高熵内容 (entropy={_entropy(line):.2f})", []))
            if any(ch in line for ch in ZERO_WIDTH):
                findings.append(Finding("SR-OBFUS-002", "OBFUS", "HIGH", rel, i,
                    line.strip()[:200], "隐藏字符（零宽/ homoglyph 标记）", []))
        for depth, decoded in _decode_candidates(text):
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

def render_report(rep):
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    fs = sorted(rep.findings, key=lambda f: (order.get(f.severity, 9), f.file, f.line))
    lines = [f"skill: {rep.skill_name}   score: {rep.score}/100   files: {rep.files_scanned}"
             f"   verdict: {'PASS' if rep.ok else 'FAIL'}"]
    for f in fs:
        lines.append(f"  {f.severity:<8} {f.rule_id:<14} {f.file}:{f.line}  {f.message}")
        lines.append(f"           {f.excerpt}")
    if not rep.ok:
        lines.append("建议: 拒绝安装（存在 CRITICAL）。人工 inspect 后可用 --accept-drift 重新基线化已装技能。")
    elif fs:
        lines.append("建议: inspect 命中项；MEDIUM 及以下可接受时照常安装。")
    else:
        lines.append("建议: 未命中任何规则。")
    return "\n".join(lines)
