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
