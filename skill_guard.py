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
