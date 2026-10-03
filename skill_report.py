#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill_report.py —— skill-radar 报告生成器（HTML + PNG）
=======================================================
把两个工具的数据渲染成带排版的检查/统计报告：

  usage 报告：用量雷达四层计数（来自 skill_monitor.py 的 skill_usage.json）
  guard 报告：安全闸门扫描（详细模式重扫全部技能聚合 findings；--fast 用快照分数）

PNG 导出原理：生成自包含 HTML（内联 CSS、离线可用），再用本机已有的
Edge/Chrome 无头模式截图。skill-radar 本体保持零第三方依赖——浏览器由
操作系统提供（Windows 10/11 必有 Edge），中文排版走系统字体。

用法:
    python skill_report.py                     # 两份报告都生成（HTML+PNG）
    python skill_report.py usage|guard         # 只生成一份
    python skill_report.py guard --fast        # 安全报告用快照分数，不重扫
    python skill_report.py --html-only         # 只出 HTML，不调浏览器
    python skill_report.py --out DIR --top 30
输出: <out>/usage-report-<时间戳>.html/.png 与 guard-report-<时间戳>.html/.png
"""
import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import skill_guard as sg  # noqa: E402  (same directory)

SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SEV_COLOR = {"CRITICAL": "#dc2626", "HIGH": "#ea580c", "MEDIUM": "#ca8a04",
             "LOW": "#2563eb", "INFO": "#64748b"}
STATUS_COLOR = {"drifted": "#dc2626", "scanned": "#059669",
                "baseline-unreviewed": "#ca8a04", "unscanned": "#64748b"}
CAT_CN = {"THEFT": "凭证窃取", "EXEC": "恶意执行", "PERSIST": "持久化",
          "EXFIL": "数据外发", "INJ": "提示注入", "ABUSE": "配置滥用",
          "DECEP": "话术欺骗", "SUPPLY": "来源伪装", "OBFUS": "内容混淆",
          "BLOCK": "黑名单命中"}


def install_verdict(rep, status):
    """按发现构成给出安装建议（推荐/谨慎/不推荐）与中文理由。"""
    crit = [f for f in rep.findings if f.severity == "CRITICAL"]
    high = [f for f in rep.findings if f.severity == "HIGH"]
    med = [f for f in rep.findings if f.severity == "MEDIUM"]
    def cats(fs):
        return "、".join(sorted({CAT_CN.get(f.category, f.category) for f in fs}))
    if status == "drifted":
        return "不推荐", "#dc2626", "内容已偏离安全基线（疑似篡改或未审查的更新），需先 --show-diff 人工审查"
    if crit:
        return "不推荐", "#dc2626", f"存在 CRITICAL 级发现：{cats(crit)}；安装前必须逐条人工审查"
    if rep.score >= 40:
        return "不推荐", "#dc2626", f"综合风险分 {rep.score}/100 过高（{cats(high or med)}）"
    if high:
        return "谨慎", "#ea580c", f"存在 {len(high)} 条 HIGH 级发现（{cats(high)}），多为运维/脚本类技能的正常形态，建议过目后再装"
    if med:
        return "谨慎", "#ca8a04", f"存在 {len(med)} 条 MEDIUM 级发现（{cats(med)}），影响有限"
    return "推荐", "#059669", "未命中任何风险规则"
PAGE_W = 1440
BROWSERS = ["msedge", "chrome", "chromium", "google-chrome", "chrome.exe", "msedge.exe"]
BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium", "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


# ------------------------------------------------------------------ browser
def find_browser():
    for name in BROWSERS:
        p = shutil.which(name)
        if p:
            return p
    for p in BROWSER_PATHS:
        if os.path.isfile(p):
            return p
    return None


def html_to_png(html_text, out_png, width=PAGE_W, height=2200, browser=None):
    """无头浏览器截图。返回 True=成功；无浏览器/失败返回 False（HTML 已落盘不受影响）。"""
    browser = browser or find_browser()
    if not browser:
        return False
    html_path = out_png[:-4] + ".html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    url = "file:///" + html_path.replace("\\", "/").lstrip("/")
    for headless in ("--headless=new", "--headless"):
        try:
            subprocess.run(
                [browser, headless, "--disable-gpu", "--hide-scrollbars",
                 "--default-background-color=FFFFFF",
                 f"--screenshot={out_png}", f"--window-size={width},{height}", url],
                timeout=120, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.isfile(out_png) and os.path.getsize(out_png) > 1000:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


# ------------------------------------------------------------------ html bits
CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:1440px; font-family:'Segoe UI','Microsoft YaHei','PingFang SC',sans-serif;
       background:#f1f5f9; color:#0f172a; padding:28px 32px; }
header { display:flex; justify-content:space-between; align-items:flex-end;
         border-bottom:3px solid #0f172a; padding-bottom:14px; margin-bottom:22px; }
header h1 { font-size:26px; letter-spacing:.5px; }
header h1 .logo { background:#0f172a; color:#fff; padding:2px 10px; border-radius:6px; margin-right:8px; }
header .meta { font-size:12px; color:#64748b; text-align:right; line-height:1.6; }
h2 { font-size:15px; margin:26px 0 10px; color:#334155; text-transform:uppercase; letter-spacing:1px; }
.cards { display:flex; gap:14px; }
.card { flex:1; background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:14px 18px; }
.card .num { font-size:30px; font-weight:700; }
.card .lbl { font-size:12px; color:#64748b; margin-top:2px; }
.bar-row { display:flex; align-items:center; margin:5px 0; }
.bar-name { width:300px; font-size:12.5px; text-align:right; padding-right:10px;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:#334155; }
.bar-track { flex:1; background:#e2e8f0; border-radius:4px; height:16px; position:relative; }
.bar-fill { height:16px; border-radius:4px; background:#0f172a; min-width:2px; }
.bar-val { width:52px; font-size:12px; font-weight:600; padding-left:8px; }
table { width:100%; border-collapse:collapse; background:#fff; border:1px solid #e2e8f0;
        border-radius:8px; overflow:hidden; font-size:12.5px; }
th { background:#0f172a; color:#fff; text-align:left; padding:7px 10px; font-size:11.5px; }
td { padding:6px 10px; border-top:1px solid #e2e8f0; }
tr:nth-child(even) td { background:#f8fafc; }
.badge { color:#fff; border-radius:4px; padding:1px 7px; font-size:11px; font-weight:600; display:inline-block; }
.muted { color:#64748b; font-size:11px; }
footer { margin-top:24px; font-size:11px; color:#94a3b8; text-align:center; }
"""


def _page(title, meta_lines, body):
    meta = "<br>".join(html.escape(m) for m in meta_lines)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<header><h1><span class="logo">skill-radar</span>{html.escape(title)}</h1>
<div class="meta">{meta}</div></header>
{body}
<footer>skill-radar · 确定性离线报告 deterministic offline reporting · {html.escape(datetime.now().strftime('%Y-%m-%d %H:%M'))}</footer>
</body></html>"""


def _cards(items):
    cells = "".join(f'<div class="card"><div class="num">{v}</div><div class="lbl">{html.escape(lbl)}</div></div>'
                    for v, lbl in items)
    return f'<div class="cards">{cells}</div>'


def _bars(rows, color="#0f172a"):
    """rows: [(label, value, max_value)]"""
    if not rows:
        return '<p class="muted">（无记录）</p>'
    out = []
    for name, val, mx in rows:
        pct = max(1, int(val * 100 / mx)) if mx else 1
        out.append(f'<div class="bar-row"><div class="bar-name" title="{html.escape(name)}">{html.escape(name)}'
                   f'</div><div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>'
                   f'<div class="bar-val">{val}</div></div>')
    return "".join(out)


def _badge(text, color):
    return f'<span class="badge" style="background:{color}">{html.escape(text)}</span>'


# ------------------------------------------------------------------ usage
def render_usage_html(data, top=25):
    skills = data.get("skills", {})
    rows = []
    for n, s in skills.items():
        total = s.get("zcode", 0) + s.get("claude", 0) + s.get("marker", 0)
        rows.append((total, n, s))
    rows.sort(key=lambda r: (-r[0], r[1]))
    invocations = sum(r[0] for r in rows)
    active = sum(1 for r in rows if r[0] > 0)
    zc = sum(s["zcode"] for _, _, s in rows)
    cc = sum(s["claude"] for _, _, s in rows)
    mk = sum(s["marker"] for _, _, s in rows)
    at = sum(s.get("atime", 0) for _, _, s in rows)
    meta = [f"generated 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"skills tracked 追踪技能: {len(rows)}"]
    body = _cards([(invocations, "total invocations · 总调用"), (active, "active skills · 活跃技能"),
                   (len(rows), "skills tracked · 追踪技能"), (mk, "marker session hits · 会话命中"),
                   (at, "atime reads · atime 读取")])
    body += "<h2>TOP SKILLS BY USAGE · 技能调用排行</h2>"
    mx = rows[0][0] if rows and rows[0][0] else 1
    body += _bars([(n, t, mx) for t, n, _ in rows[:top]], "#0f172a")
    body += "<h2>LAYER DISTRIBUTION · 四层来源分布</h2>"
    body += _cards([(zc, "zcode precise · zcode 精确层"), (cc, "claude precise · claude 精确层"),
                    (mk, "marker universal · 标记通用层"), (at, "atime fallback · atime 兜底层")])
    body += ('<h2>ALL SKILLS · 全部技能明细</h2><table><tr><th>skill 技能</th><th>zcode</th>'
             '<th>claude</th><th>sessions 会话</th><th>atime</th><th>total 合计</th>'
             '<th>last used 最近使用</th></tr>')
    for t, n, s in rows[:top * 2] if top < 40 else rows:
        last = (s.get("last_tool_use") or "")[:10] or "—"
        body += (f"<tr><td>{html.escape(n)}</td><td>{s['zcode']}</td><td>{s['claude']}</td>"
                 f"<td>{s['marker']}</td><td>{s.get('atime', 0)}</td><td><b>{t}</b></td><td>{last}</td></tr>")
    body += "</table>"
    return _page("Usage Radar — 技能用量统计报告", meta, body)


# ------------------------------------------------------------------ guard
def _guard_collect(cfg, rules_text, blocklist_text, max_depth=5):
    """重扫全部注册 roots 聚合 findings（内存，不写快照）。返回 [(name, path, rep)]。
    按 realpath 去重——.zcode/skills 等符号链接指向 .agents/skills，同一技能
    会在多个 root 下重复出现，报告只保留一份。"""
    import hashlib
    out = []
    seen = {}
    seen_names = {}
    rules = sg.parse_rules(rules_text)
    for r in cfg.get("roots", []):
        root = r.get("path", "")
        if not os.path.isdir(root):
            continue
        trusted_root = sg._is_trusted(cfg, root)
        for entry in sorted(os.listdir(root)):
            skill = os.path.join(root, entry)
            if not sg._is_skill_dir(skill):
                continue
            real = os.path.realpath(skill).lower()
            if real in seen:
                continue
            seen[real] = True
            rep = sg.run_engine(skill, rules, blocklist_text, max_depth=max_depth)
            rep.findings = sg.apply_trust(rep.findings, trusted_root)
            rep.score = sg.score_findings(rep.findings)
            md = os.path.join(skill, "SKILL.md")
            if os.path.isfile(md):
                try:
                    h = hashlib.sha256(open(md, "rb").read()).hexdigest()
                    if sg._is_trusted(cfg, skill, skill_hash=h):
                        rep.findings = sg.apply_trust(rep.findings, True)
                        rep.score = sg.score_findings(rep.findings)
                except OSError:
                    pass
            rep.skill_name = entry
            # 同名技能去重：不同 agent 目录常有真实内容副本，报告保留风险分最高的实例
            prior = seen_names.get(entry)
            if prior is not None:
                _, _, prev_rep = out[prior]
                if rep.score <= prev_rep.score:
                    continue
                out[prior] = (entry, skill, rep)
                continue
            seen_names[entry] = len(out)
            out.append((entry, skill, rep))
    return out


def render_guard_html(skills_info, snapshots, top=30):
    """skills_info: [(name, path, rep)]（详细模式）或空（fast 模式，用 snapshots）。"""
    all_findings = [f for _, _, rep in skills_info for f in rep.findings]
    sev_count = {s: sum(1 for f in all_findings if f.severity == s) for s in SEV_ORDER}
    status_count = {}
    for _, s in (snapshots.get("skills", {}) or {}).items():
        status_count[s.get("status", "?")] = status_count.get(s.get("status", "?"), 0) + 1
    if not status_count and skills_info:
        status_count = {"rescanned": len(skills_info)}
    crit_skills = sum(1 for _, _, rep in skills_info
                      if any(f.severity == "CRITICAL" for f in rep.findings))
    meta = [f"generated 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"skills scanned 扫描技能: {len(skills_info) if skills_info else sum(status_count.values())}",
            "mode 模式: " + ("detailed rescan 详细重扫" if skills_info else "fast 快照模式")]
    body = _cards([(sum(status_count.values()), "skills · 技能"),
                   (sev_count["CRITICAL"], "critical findings · CRITICAL 发现"),
                   (sev_count["HIGH"], "high findings · HIGH 发现"),
                   (status_count.get("drifted", 0), "drifted · 已漂移"),
                   (status_count.get("baseline-unreviewed", 0), "baseline-unreviewed · 基线未审查")])
    body += "<h2>FINDINGS BY SEVERITY · 发现按严重度</h2>"
    mx = max(sev_count.values()) if any(sev_count.values()) else 1
    body += _bars([(s, sev_count[s], mx) for s in SEV_ORDER if sev_count[s]],
                  "#dc2626") if any(sev_count.values()) else '<p class="muted">（无发现）</p>'
    body += "<h2>STATUS DISTRIBUTION · 状态分布</h2>"
    body += _bars([(k, v, max(status_count.values())) for k, v in
                   sorted(status_count.items(), key=lambda x: -x[1])], "#334155")
    verdicts = {}
    for name, path, rep in skills_info:
        st = (snapshots.get("skills", {}).get(path, {}) or {}).get("status", "rescanned")
        v, _, _ = install_verdict(rep, st)
        verdicts[v] = verdicts.get(v, 0) + 1
    body += "<h2>INSTALL ADVICE · 安装建议汇总</h2><p>"
    body += (_badge(f"推荐安装 {verdicts.get('推荐', 0)}", "#059669") + "  " +
             _badge(f"谨慎评估 {verdicts.get('谨慎', 0)}", "#ca8a04") + "  " +
             _badge(f"不推荐 {verdicts.get('不推荐', 0)}", "#dc2626") +
             '</p><p class="muted">判定依据：CRITICAL 发现或内容漂移 → 不推荐；HIGH/MEDIUM 发现 → 谨慎（多为运维技能的正常形态，附理由逐条判断）；'
             '无命中 → 推荐。基线未经人工审查的技能会附加提醒。</p>')
    body += "<h2>TOP SKILLS BY RISK SCORE · 风险分排行与安装建议</h2><table>" \
            "<tr><th>skill 技能</th><th>score 风险分</th><th>status 状态</th>" \
            "<th>findings 发现 (C/H/M/L)</th><th>install advice 安装建议</th>" \
            "<th>top finding 首要发现</th></tr>"
    ranked = sorted(skills_info, key=lambda x: -x[2].score)[:top]
    for name, path, rep in ranked:
        c = {s: sum(1 for f in rep.findings if f.severity == s) for s in SEV_ORDER}
        badge = _badge(str(rep.score), SEV_COLOR["CRITICAL"] if rep.score >= 40 else
                       SEV_COLOR["HIGH"] if rep.score >= 25 else
                       SEV_COLOR["MEDIUM"] if rep.score >= 10 else "#64748b")
        st = (snapshots.get("skills", {}).get(path, {}) or {}).get("status", "rescanned")
        v, vcolor, reason = install_verdict(rep, st)
        top_f = max(rep.findings, key=lambda f: SEV_ORDER.index(f.severity)) if rep.findings else None
        snippet = sg._sanitize(f"{top_f.rule_id} {top_f.file}:{top_f.line} {top_f.excerpt}")[:90] if top_f else "—"
        body += (f"<tr><td>{html.escape(name)}</td><td>{badge}</td>"
                 f"<td>{_badge(st, STATUS_COLOR.get(st, '#64748b'))}</td>"
                 f"<td>{c['CRITICAL']}/{c['HIGH']}/{c['MEDIUM']}/{c['LOW']}</td>"
                 f"<td>{_badge(v, vcolor)}<br><span class='muted'>{html.escape(sg._sanitize(reason))}</span></td>"
                 f"<td class='muted'>{html.escape(snippet)}</td></tr>")
    body += "</table>"
    if skills_info:
        body += ("<p class='muted' style='margin-top:10px'>full findings detail: re-run with "
                 "--json, or check per-skill scan output. drift evidence: skill_guard.py audit --show-diff &lt;skill&gt;</p>")
    return _page("Security Guard — 技能安全检查报告", meta, body)


# ------------------------------------------------------------------ cli
def _est_height(html_text, fallback=2200):
    rows = html_text.count("<tr>")
    bars = html_text.count("bar-row")
    return min(6000, max(1400, 560 + rows * 30 + bars * 22))


def main(argv=None):
    ap = argparse.ArgumentParser(description="skill-radar report generator (HTML + PNG)")
    ap.add_argument("target", nargs="?", choices=["usage", "guard"], help="which report (default: both)")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "skill-radar-reports"))
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--html-only", action="store_true", help="skip PNG (no browser)")
    ap.add_argument("--fast", action="store_true", help="guard report from snapshot scores, no rescan")
    ap.add_argument("--counters", help="path to skill_usage.json (default: next to skill_monitor.py)")
    ap.add_argument("--rules", help="path to rules yaml (default: repo rules/defaults.yaml)")
    ap.add_argument("--blocklist", help="path to blocklist yaml")
    args = ap.parse_args(argv)

    import skill_monitor as sm
    counters_path = args.counters or sm.DATA_FILE
    try:
        counters = json.load(open(counters_path, encoding="utf-8"))
    except (OSError, ValueError):
        counters = {"skills": {}}

    out = args.out
    os.makedirs(out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    browser = find_browser()
    if not args.html_only and not browser:
        print("[skill-radar] 未找到 Edge/Chrome，PNG 跳过（HTML 已生成）；--html-only 可静音此提示")

    targets = [args.target] if args.target else ["usage", "guard"]
    made = []
    for t in targets:
        if t == "usage":
            html_text = render_usage_html(counters, top=args.top)
            est = _est_height(html_text)
        else:
            snaps = sg.load_snapshots()
            if args.fast:
                info = []
            else:
                cfg = sg.load_config()
                rules_path = args.rules or os.path.join(BASE_DIR, "rules", "defaults.yaml")
                bl_path = args.blocklist or os.path.join(BASE_DIR, "rules", "blocklist.yaml")
                rules_text = open(rules_path, encoding="utf-8").read() if os.path.isfile(rules_path) else "[]"
                bl_text = open(bl_path, encoding="utf-8").read() if os.path.isfile(bl_path) else "[]"
                print(f"[skill-radar] guard 详细模式：重扫全部注册技能（约 1-3 分钟）…")
                info = _guard_collect(cfg, rules_text, bl_text)
            html_text = render_guard_html(info, snaps, top=args.top)
            est = _est_height(html_text)
        base = os.path.join(out, f"{t}-report-{stamp}")
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(html_text)
        png_ok = False
        if not args.html_only and browser:
            png_ok = html_to_png(html_text, base + ".png", height=est, browser=browser)
        made.append((t, base + ".html", base + ".png" if png_ok else None))

    for t, h, p in made:
        print(f"[skill-radar] {t}: {h}" + (f"  +  {p}" if p else "  (PNG 跳过：无可用浏览器或渲染失败)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
