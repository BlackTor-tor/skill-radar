"""从客户端当前快照生成可复制、可下载的本地检查报告。"""
import html
import hashlib
import json
import os
import re
import stat
import threading
import uuid
from datetime import datetime
from pathlib import Path

import skill_guard as sg
from tray.report_guidance import build_guidance


REPORT_VERSION = 1
REPORT_ID = re.compile(r"check-report-\d{8}-\d{6}-\d{6}-[a-f0-9]{32}\Z")
CHECK_LABELS = {"healthy": "本次检查未发现风险", "attention": "需要关注",
                "incomplete": "检查未完成", "error": "检查失败"}
REVIEW_LABELS = {"pending": "待人工确认", "reviewed": "已查看原始风险",
                 "trusted": "已人工信任当前版本", "not_required": "无需人工决定"}


def _text(value):
    """保留多行正文，同时移除终端控制字符和孤立代理字符。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value = str(value)
    return "".join(ch for ch in value if (ch >= " " or ch in "\n\t")
                   and ch not in "\x7f\u200b\u200c\u200d\u2060\ufeff"
                   and not 0xD800 <= ord(ch) <= 0xDFFF)


def _inline(value):
    """动态名称、路径和说明作为代码内容展示，不解释为 Markdown 或 HTML。"""
    text = html.escape(_text(value).replace("\n", " "), quote=False)
    size = max((len(run) for run in re.findall(r"`+", text)), default=0) + 1
    marker = "`" * size
    return f"{marker} {text} {marker}"


def _block(value):
    """动态多行证据使用比正文更长的围栏，防止伪造报告章节。"""
    text = _text(value)
    size = max(3, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    marker = "`" * size
    return f"{marker}text\n{text}\n{marker}"


def _list(value):
    return value if isinstance(value, (list, tuple)) else [value] if value else []


def _is_link(path):
    """待创建文件可以不存在，已有链接和重解析点均不允许用于报告。"""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) &
                                            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _aware(value):
    """未带时区的旧时间属于客户端本地时间，展示时补充明确的 UTC 偏移。"""
    return value.astimezone() if value.tzinfo is None else value


def _timestamp(value):
    raw = _text(value)
    if not raw:
        return "未记录检查时间"
    try:
        return _aware(datetime.fromisoformat(raw.replace("Z", "+00:00"))).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return raw + "（时间格式不完整）"


def _file_timestamp(value, unknown):
    """文件时间缺失时保持未知，不把检查时间当作安装时间。"""
    return _timestamp(value) if value else unknown


def _classification(row):
    """缺少覆盖证据或原始发现时，不从风险分为零推断检查健康。"""
    raw = row.get("raw_findings")
    complete = row.get("scan_complete") is True
    issues = _list(row.get("scan_issues"))
    if not complete or issues or not isinstance(raw, list) or \
            any(not isinstance(item, dict) for item in raw):
        return "incomplete"
    if raw:
        return "attention"
    return "healthy" if row.get("check_status") == "healthy" else "incomplete"


def render_skill_markdown(path, row):
    """单项检查正文与完整报告共用同一事实和原始风险字段。"""
    row = row if isinstance(row, dict) else {}
    kind = _classification(row)
    name = row.get("name") or os.path.basename(_text(path).replace("\\", "/")) or path
    raw = row.get("raw_findings")
    raw_saved = isinstance(raw, list) and all(isinstance(item, dict) for item in raw)
    lines = [f"### {_inline(name)}", "", f"- **技能路径：** {_inline(path)}",
             f"- **检查结果：** {CHECK_LABELS['error'] if row.get('check_status') == 'error' else CHECK_LABELS[kind]}",
             f"- **人工决定：** {_inline(REVIEW_LABELS.get(_text(row.get('review_status')), '未记录人工决定'))}",
             f"- **检查时间：** {_inline(_timestamp(row.get('scanned_at')))}",
             f"- **安装时间（估算）：** {_inline(_file_timestamp(row.get('installed_at'), '安装时间未知'))}",
             f"- **最近更新时间：** {_inline(_file_timestamp(row.get('updated_at'), '更新时间未知'))}",
             "- **时间来源：** SKILL.md 文件创建时间用于估算安装时间，修改时间用于最近更新时间；复制、恢复或重建文件可能改变创建时间。",
             f"- **内容版本：** {_inline(row.get('version') or '未记录')}",
             f"- **基线状态：** {_inline(row.get('status') or '未记录')}",
             f"- **原始风险分：** {_inline(row.get('raw_score') if 'raw_score' in row else '未保存')}",
             f"- **应用例外后风险分：** {_inline(row.get('score') if 'score' in row else '未保存')}",
             f"- **检查覆盖：** {'完整' if row.get('scan_complete') is True and not _list(row.get('scan_issues')) else '未完成'}"]
    if kind == "incomplete" and ('scan_complete' not in row or 'check_status' not in row):
        lines.append("- **覆盖提示：** 缺少完整检查元数据，需要重新检查。")
    if row.get("review_status") == "trusted":
        lines.append("- **决定说明：** 人工信任仅适用于当前内容版本，原始风险仍保留在报告中。")
    for label, field in (("未完成原因", "scan_issues"), ("覆盖说明", "coverage_notes")):
        values = _list(row.get(field))
        if values:
            lines.extend(["", f"**{label}：**", ""])
            lines.extend(f"- {_inline(value)}" for value in values)
    coverage = row.get("scan_coverage")
    if isinstance(coverage, dict):
        lines.extend(["", "**实际检查范围：**", ""])
        for label, key in (("文字检查文件", "text_files_checked"),
                           ("素材哈希", "asset_files_hashed"),
                           ("原始哈希文件", "hashed_files")):
            if key in coverage:
                lines.append(f"- **{label}：** {_inline(coverage[key])}")
        limits = coverage.get("limits")
        if isinstance(limits, dict):
            for label, key in (("单文件文字检查上限（字节）", "text_bytes"),
                               ("单文件原始哈希上限（字节）", "hash_bytes")):
                if key in limits:
                    lines.append(f"- **{label}：** {_inline(limits[key])}")
        assets = _list(coverage.get("assets_without_text_check"))
        if assets:
            lines.extend(["", "素材仅做格式识别和原始哈希核对；不表示素材内容已通过安全审查。",
                          "以下素材未进行文字规则检查：", ""])
            lines.extend(f"- {_inline(value)}" for value in assets)
        excluded = _list(coverage.get("excluded_directories"))
        if excluded:
            lines.extend(["", "配置排除的目录未纳入本次检查：", ""])
            lines.extend(f"- {_inline(value)}" for value in excluded)
    lines.extend(["", "**原始检查发现：**", ""])
    if not raw_saved:
        lines.append("原始发现未保存；不能由风险分或旧基线判定安全。")
    elif not raw:
        lines.append("本次保存的原始发现为空。" if kind != "incomplete" else
                     "原始发现为空，但检查覆盖未完成，不能判定全部内容安全。")
    else:
        for index, item in enumerate(raw, 1):
            lines.extend([f"#### 发现 {index} · {_inline(item.get('severity') or '未记录严重度')}", "",
                          f"- **规则：** {_inline(item.get('rule_id') or '未记录')}",
                          f"- **类别：** {_inline(item.get('category') or '未记录')}",
                          f"- **位置：** {_inline(item.get('file') or '未记录')}，行 {_inline(item.get('line') if 'line' in item else '未记录')}",
                          f"- **说明：** {_inline(item.get('message') or '未记录')}"])
            if item.get("tags"):
                lines.append(f"- **标签：** {_inline(item.get('tags'))}")
            lines.extend(["", "**原始证据：**", "", _block(item.get("excerpt") or "未记录证据"), ""])
    return "\n".join(lines).rstrip() + "\n"


def render_events_markdown(events):
    """事件时间原样保留；只有时分秒的历史记录不编造事件日期。"""
    rows = [row for row in _list(events) if isinstance(row, dict)]
    lines = ["## 事件记录", "", "仅包含客户端保留的最近事件，不代表完整历史。", ""]
    if not rows:
        lines.append("当前没有保存的事件。")
    else:
        for index, row in enumerate(rows, 1):
            raw_ts = _text(row.get("ts"))
            time_text = raw_ts + "（原事件未保存日期）" if re.fullmatch(r"\d{2}:\d{2}:\d{2}", raw_ts) else \
                _timestamp(raw_ts) if raw_ts else "原事件未保存日期时间"
            lines.extend([f"### 事件 {index}", "", f"- **时间：** {_inline(time_text)}",
                          f"- **类型：** {_inline(row.get('kind') or '未记录')}",
                          "", "**事件内容：**", "", _block(row.get("text")), ""])
    return "\n".join(lines).rstrip() + "\n"


def _summary(skills):
    result = dict(total=len(skills), healthy=0, attention=0, incomplete=0, findings=0)
    for _, row in skills:
        result[_classification(row)] += 1
        raw = row.get("raw_findings")
        result["findings"] += sum(isinstance(item, dict) for item in raw) if isinstance(raw, list) else 0
    return result


def render_usage_markdown(usage):
    """使用已采集快照生成完整闲置清单，不重新扫描日志。"""
    usage = usage if isinstance(usage, dict) else {}
    summary = usage.get("inventory_summary")
    summary = summary if isinstance(summary, dict) else {}
    groups = usage.get("idle_groups")
    groups = groups if isinstance(groups, dict) else {}
    lines = ["## 使用概览", "", "使用记录只反映已采集日志中的技能加载，不代表全部实际使用。",
             "同名技能共用名称层面的调用记录，完整路径用于区分安装副本。", ""]
    for label, key in (("已安装技能", "total_installed"), ("无调用记录", "never"),
                       ("近 30 天未调用", "inactive"), ("记录不足", "unknown"),
                       ("近期活跃技能", "active"), ("已安装技能调用合计", "total_invocations")):
        lines.append(f"- **{label}：** {_inline(summary.get(key, '未知'))}")
    note = summary.get("coverage_note") or "未保存完整的日志采集范围信息。"
    lines.extend([f"- **覆盖说明：** {_inline(note)}", ""])
    inventory_incomplete = summary.get("inventory_complete") is False
    if inventory_incomplete:
        lines.extend(["**安装清单未完整读取。以下清单仅包含当前可读取的安装目录，不能据此判断其他目录没有闲置技能。**", ""])
        lines.extend(f"- **不可用安装目录：** {_inline(path)}"
                     for path in _list(summary.get("unavailable_inventory_roots")))
        lines.append("")
    lines.extend(["安装时间（估算）来自 SKILL.md 文件创建时间；最近更新时间来自修改时间。复制、恢复或重建可能改变创建时间。", ""])
    for label, key, explanation in (("无调用记录", "never", "在已采集日志中没有发现调用。"),
            ("近 30 天未调用", "inactive", "以前有调用记录，但最近 30 天未调用。"),
            ("记录不足", "unknown", "日志或最近使用时间不足，暂时不能判断是否闲置。")):
        rows = [row for row in _list(groups.get(key)) if isinstance(row, dict)]
        lines.extend([f"### {label}", "", explanation, ""])
        if not rows:
            lines.append("当前可读取的安装目录中没有符合该口径的技能。" if inventory_incomplete else
                         "当前没有符合该口径的技能。")
        for row in rows:
            flags = " · 观察中" if row.get("observing") else ""
            flags += " · 同名记录共享" if row.get("shared_name") else ""
            lines.append(f"- {_inline(row.get('name') or '未知技能')} · {_inline(row.get('path') or '路径未知')}"
                         f" · 调用 {_inline(row.get('total', 0))} · 最近调用 {_inline(row.get('last') or '无记录')}{flags}"
                         f" · 安装时间（估算） {_inline(_file_timestamp(row.get('installed_at'), '安装时间未知'))}"
                         f" · 最近更新时间 {_inline(_file_timestamp(row.get('updated_at'), '更新时间未知'))}")
        lines.append("")
    rows = [row for row in _list(usage.get("rows")) if isinstance(row, dict)]
    lines.extend(["### 调用排行", "", "保留全部已采集技能记录，包含已移除或未注册的技能。", ""])
    if not rows:
        lines.append("当前没有保存的调用记录。")
    for index, row in enumerate(rows, 1):
        lines.append(f"- {index}. {_inline(row.get('name') or '未知技能')} · 调用 {_inline(row.get('total', 0))}"
                     f" · 最近调用 {_inline(row.get('last') or '无记录')}")
    return "\n".join(lines).rstrip() + "\n"


def _render_guidance_markdown(guidance):
    """先给出结论和人工操作步骤，技术字段留在完整附录。"""
    groups, counts = guidance["groups"], guidance["counts"]
    lines = ["## 一眼结论", "",
             f"优先处理 {counts['priority']} 个，需要确认 {counts['confirm']} 个；可考虑清理 {counts['cleanup']} 个，继续观察 {counts['observe']} 个。",
             "建议依据生成时的记录，不代表已经发生损害，也不会自动批准、隔离或删除技能。", ""]
    lines.extend("- **记录范围：** " + note for note in guidance.get("overview_notes", []))
    lines.append("")
    definitions = [("priority", "优先处理", "确认前先别主动使用这些技能，先核对证据；拿不准时可先隔离。"),
                   ("confirm", "需要确认", "核对技能是否确实需要这些行为；检查未完成的先重新检查。"),
                   ("cleanup", "可考虑清理", "这里是手动清理候选，不代表删除后一定没有影响。"),
                   ("observe", "继续观察", "信息不足、近期安装、仍有调用或共享记录的技能先保留。"),
                   ("handled", "你已处理", "你之前的决定仍保留，原始风险不会因此消失。")]
    for key, title, explanation in definitions:
        lines.extend(["## " + title, "", explanation, ""])
        if not groups[key]:
            lines.extend(["当前没有这一类建议。", ""])
        for item in groups[key]:
            lines.extend(["### " + _inline(item["name"]), "", "- **完整位置：** " + _inline(item["path"] or "位置未记录"),
                          "- **为什么：** " + item["reason"]])
            lines.extend("- **下一步：** " + step for step in item["steps"])
            lines.append("")
    lines.extend(["## 清理教程", "", "按完整位置逐个手动操作；SkillRadar 不会替你删除。", ""])
    lines.extend(f"- **第 {index} 步：** {step}" for index, step in enumerate(guidance["cleanup_steps"], 1))
    lines.extend(["", "**清理前请留意：**", ""])
    lines.extend("- " + caution for caution in guidance["cleanup_cautions"])
    return "\n".join(lines).rstrip() + "\n"


def _render_snapshot(snapshot, generated_at, usage=None, guidance=None):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    data = snapshot.get("skills")
    skills = sorted(((_text(path), row if isinstance(row, dict) else {})
                     for path, row in data.items()), key=lambda item: item[0]) if isinstance(data, dict) else []
    summary = _summary(skills)
    lines = ["# SkillRadar 技能检查报告", "", f"- **报告生成时间：** {_inline(generated_at)}",
             "", _render_guidance_markdown(guidance or build_guidance(snapshot, usage, generated_at)),
             "", "## 技术附录", "", "以下保留生成时的完整使用统计、检查证据和事件，供进一步核对。", "",
             f"- **守护状态：** {_inline(snapshot.get('guard') or '未记录')}",
             f"- **监听目录数：** {_inline(snapshot.get('watched_roots') if 'watched_roots' in snapshot else '未记录')}",
             "", "本报告仅反映生成时保存的检查结果；生成报告不会重新扫描技能或改变人工决定。",
             "规则命中是核查线索，未发现风险也不保证所有行为安全。检查未完成的内容需要继续核查。",
             ""]
    if usage is not None:
        lines.extend([render_usage_markdown(usage), ""])
    lines.extend(["## 检查概览", "", "| 项目 | 数量 |", "| --- | ---: |",
             f"| 保存的技能检查 | {summary['total']} |",
             f"| 本次检查未发现风险 | {summary['healthy']} |",
             f"| 需要关注 | {summary['attention']} |",
             f"| 检查未完成 | {summary['incomplete']} |",
             f"| 保存的原始发现 | {summary['findings']} |", "", "## 需要关注列表的用途", "",
             "需要关注表示规则发现了需要核查的行为，例如脚本执行、敏感文件访问或外部发送。",
             "查看规则、证据、路径和内容版本后，可以重新检查、记录已查看、信任当前版本或隔离技能。",
             "该列表保留原始风险；已查看和人工信任均不表示规则发现已经消失。", "", "## 技能检查明细", ""])
    if not skills:
        lines.append("当前没有保存的技能检查结果。请先检查技能，再生成报告。")
    else:
        for path, row in skills:
            lines.extend([render_skill_markdown(path, row), ""])
    lines.extend([render_events_markdown(snapshot.get("events"))])
    return "\n".join(lines).rstrip() + "\n", summary


def _render_inline(value):
    """只识别生成器自身的代码片段和粗体，所有内容始终经过 HTML 转义。"""
    tokens = re.split(r"(`+ .+? `+|\*\*[^*]+\*\*)", value)
    out = []
    for token in tokens:
        code = re.fullmatch(r"(`+) (.*?) \1", token)
        if code:
            out.append("<code>" + html.escape(html.unescape(code.group(2))) + "</code>")
        elif token.startswith("**") and token.endswith("**"):
            out.append("<strong>" + html.escape(token[2:-2]) + "</strong>")
        else:
            out.append(html.escape(token))
    return "".join(out)


def _render_html(markdown, fold_appendix=False):
    """离线、无脚本的报告版式；不加载图片、脚本或第三方 Markdown 服务。"""
    body, code, fence, in_table, in_list = [], [], None, False, False
    appendix_open = False
    for line in markdown.splitlines():
        if fence:
            if line == fence:
                body.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                fence, code = None, []
            else:
                code.append(line)
            continue
        match = re.fullmatch(r"(`{3,})text", line)
        if match:
            if in_list:
                body.append("</ul>")
                in_list = False
            fence = match.group(1)
            continue
        is_table = line.startswith("| ") and line.endswith(" |")
        is_list = line.startswith("- ")
        if in_table and not is_table:
            body.append("</tbody></table>")
            in_table = False
        if in_list and not is_list:
            body.append("</ul>")
            in_list = False
        if fold_appendix and line == "## 技术附录" and not appendix_open:
            body.append('<details class="technical-appendix"><summary>技术附录 · 完整统计与检查证据</summary>')
            appendix_open = True
            continue
        if is_table:
            cells = line.strip("|").split("|")
            if all(re.fullmatch(r"\s*:?-+:?\s*", cell) for cell in cells):
                continue
            if not in_table:
                body.append("<table><tbody>")
                in_table = True
            body.append("<tr>" + "".join("<td>" + _render_inline(cell.strip()) + "</td>" for cell in cells) + "</tr>")
        elif is_list:
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append("<li>" + _render_inline(line[2:]) + "</li>")
        elif re.match(r"^#{1,4} ", line):
            level = len(line) - len(line.lstrip("#"))
            body.append(f"<h{level}>" + _render_inline(line[level + 1:]) + f"</h{level}>")
        elif line:
            body.append("<p>" + _render_inline(line) + "</p>")
    if in_list:
        body.append("</ul>")
    if in_table:
        body.append("</tbody></table>")
    if appendix_open:
        body.append("</details>")
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>SkillRadar 技能检查报告</title><style>
body{max-width:960px;margin:32px auto;padding:0 24px;font:15px/1.7 system-ui,'Microsoft YaHei',sans-serif;color:#1f2937;background:#fff}
h1,h2,h3,h4{line-height:1.35}h1{border-bottom:3px solid #2563eb;padding-bottom:16px}h2{margin-top:36px}h3{border-top:1px solid #e5e7eb;padding-top:20px}
table{border-collapse:collapse;width:100%;margin:16px 0}td{padding:8px 12px;border:1px solid #e5e7eb}tr:first-child{font-weight:700;background:#f3f4f6}
code{font-size:13px;overflow-wrap:anywhere}pre{padding:16px;background:#f3f4f6;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere}li{margin:4px 0}
details.technical-appendix{margin-top:36px;border:1px solid #d1d5db;border-radius:8px;padding:16px}details.technical-appendix>summary{cursor:pointer;font-weight:700}
@media print{body{margin:0;max-width:none}pre,table{break-inside:avoid}}
</style></head><body>""" + "\n".join(body) + "</body></html>\n"


class ReportService:
    """时间戳报告归档；按受限 ID 读取，不接受任意本地读取路径。"""

    def __init__(self, state, report_dir=None, clock=None, usage_provider=None):
        self.state = state
        self.report_dir = Path(report_dir if report_dir is not None else os.path.join(sg.GUARD_DIR, "reports"))
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.usage_provider = usage_provider
        self._lock = threading.Lock()

    def _artifact(self, report_id, extension):
        if not isinstance(report_id, str) or not REPORT_ID.fullmatch(report_id):
            raise ValueError("报告标识无效")
        root = self.report_dir.resolve()
        target = self.report_dir / (report_id + "." + extension)
        if target.resolve().parent != root or _is_link(target):
            raise ValueError("报告路径无效")
        return target

    def _read(self, report_id):
        metadata_path = self._artifact(report_id, "json")
        with metadata_path.open(encoding="utf-8") as source:
            metadata = json.load(source)
        if not isinstance(metadata, dict) or metadata.get("version") != REPORT_VERSION or \
                metadata.get("id") != report_id or not isinstance(metadata.get("generated_at"), str) or \
                not isinstance(metadata.get("summary"), dict) or \
                not isinstance(metadata.get("checksums"), dict):
            raise ValueError("报告元数据无效")
        paths = {ext: self._artifact(report_id, ext) for ext in ("md", "html")}
        contents = {}
        if "guidance" in metadata:
            guidance = metadata["guidance"]
            if not isinstance(guidance, dict) or self._guidance_checksum(guidance) != metadata["checksums"].get("guidance"):
                raise ValueError("报告建议已改变，请重新生成报告")
        for extension, path in paths.items():
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("报告文件无效")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != metadata["checksums"].get(extension):
                raise ValueError("报告文件已改变，请重新生成报告")
            contents[extension] = content.decode("utf-8")
        report = dict(id=report_id, generated_at=metadata["generated_at"],
                    title="SkillRadar 技能检查报告", summary=metadata["summary"],
                    markdown_path=os.fspath(paths["md"]), html_path=os.fspath(paths["html"]),
                    markdown=contents["md"], html=contents["html"])
        if isinstance(metadata.get("guidance"), dict):
            report["guidance"] = metadata["guidance"]
        return report

    @staticmethod
    def _guidance_checksum(guidance):
        """建议与身份使用稳定 JSON 摘要校验，保持桌面建议与归档正文一致。"""
        content = json.dumps(guidance, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _error(error):
        return {"ok": False, "error": _text(error)}

    def generate_report(self):
        """生成时只取一次快照；独占创建文件，旧报告不被同秒生成覆盖。"""
        created = []
        try:
            now = _aware(self.clock())
            generated_at = now.isoformat(timespec="seconds")
            usage = self.usage_provider() if self.usage_provider is not None else None
            snapshot = self.state.snapshot()
            guidance = build_guidance(snapshot, usage, generated_at)
            markdown, summary = _render_snapshot(snapshot, generated_at, usage, guidance)
            rendered = _render_html(markdown, fold_appendix=True)
            with self._lock:
                self.report_dir.mkdir(parents=True, exist_ok=True)
                report_id = "check-report-" + now.strftime("%Y%m%d-%H%M%S-%f-") + uuid.uuid4().hex
                metadata = dict(version=REPORT_VERSION, id=report_id,
                                generated_at=generated_at, summary=summary, guidance=guidance,
                                checksums={"md": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                                           "html": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                                           "guidance": self._guidance_checksum(guidance)})
                for extension, content in (("md", markdown), ("html", rendered),
                        ("json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")):
                    path = self._artifact(report_id, extension)
                    with path.open("x", encoding="utf-8", newline="\n") as destination:
                        created.append(path)
                        destination.write(content)
            return {"ok": True, "report": self._read(report_id)}
        except (OSError, ValueError, TypeError, OverflowError) as error:
            # 仅清理由本次独占创建的临时不完整归档，不触及已有报告。
            for path in created:
                try:
                    path.unlink()
                except OSError:
                    pass
            return self._error(error)

    def list_reports(self):
        """损坏或缺文件的报告跳过，列表不重新生成报告。"""
        reports = []
        try:
            for path in self.report_dir.glob("check-report-*.json"):
                try:
                    report = self._read(path.stem)
                    reports.append({key: value for key, value in report.items() if key not in ("markdown", "html")})
                except (OSError, ValueError, TypeError, UnicodeError):
                    continue
            reports.sort(key=lambda row: (row["generated_at"], row["id"]), reverse=True)
            return {"ok": True, "reports": reports}
        except (OSError, ValueError) as error:
            return self._error(error)

    def get_report(self, report_id):
        try:
            return {"ok": True, "report": self._read(report_id)}
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            return self._error(error)

    def export_report(self, report_id, format="md", destination=None):
        """下载已归档的 Markdown 或 HTML；可写入原生对话框选定的目标。"""
        try:
            if format not in ("md", "html"):
                raise ValueError("仅支持 Markdown 和 HTML 报告")
            report = self._read(report_id)
            content = report["markdown" if format == "md" else "html"]
            archive_path = self._artifact(report_id, format)
            path = os.fspath(archive_path)
            if destination is not None:
                if not isinstance(destination, (str, os.PathLike)) or not os.fspath(destination):
                    raise ValueError("导出位置无效")
                selected = Path(destination)
                root = self.report_dir.resolve()
                resolved = selected.resolve()
                if resolved == root or root in resolved.parents:
                    raise ValueError("不能覆盖报告归档，请选择其他下载位置")
                if _is_link(selected) or \
                        (selected.exists() and not selected.is_file()):
                    raise ValueError("导出位置无效")
                if selected.exists() and any(selected.samefile(artifact)
                        for artifact in self.report_dir.glob("check-report-*") if artifact.is_file()):
                    raise ValueError("不能覆盖报告归档，请选择其他下载位置")
                with selected.open("w", encoding="utf-8", newline="\n") as target:
                    target.write(content)
                path = os.fspath(selected)
            return {"ok": True, "filename": report_id + "." + format, "path": path,
                    "mime": "text/markdown; charset=utf-8" if format == "md" else "text/html; charset=utf-8",
                    "content": content}
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            return self._error(error)
