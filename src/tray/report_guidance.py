"""从已保存事实生成日常语言建议，不扫描文件、不替用户批准或删除。"""
from collections import Counter
from datetime import timedelta
import os

from skill_inventory import parse_time


GROUPS = ("priority", "confirm", "handled", "cleanup", "observe")
SEVERITIES = frozenset(("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"))
CATEGORY_REASONS = {
    "THEFT": "可能读取密码、密钥或其他敏感信息，请确认这些信息是否确实需要提供。",
    "EXFIL": "敏感信息可能被发到外部，请确认发送内容和接收方。",
    "EXEC": "可能执行额外程序或命令，请确认程序来源和执行目的。",
    "PERSIST": "可能修改启动设置，让程序以后自动运行，请确认是否需要。",
    "INJ": "包含改变 AI 行为或绕过原有要求的指令，请确认是否符合你的预期。",
    "ABUSE": "可能修改 AI 配置或其他技能文件，请确认修改范围。",
    "DECEP": "包含催促操作或声称官方来源的内容，请核对来源和实际用途。",
    "SUPPLY": "下载、安装或来源信息需要核对，请确认提供者和安装时执行的内容。",
    "OBFUS": "部分内容经过编码或隐藏，请先确认实际内容和用途。",
    "BLOCK": "来源命中已登记的风险名单，请核对来源后再使用。",
}
RULE_REASONS = {
    "SR-BLOCK-001": "来源命中已登记的风险名单，请核对来源后再使用。",
    "SR-ABUSE-002": "可能修改其他技能的文件，请确认是否会影响你仍在使用的技能。",
    "SR-SUPPLY-001": "安装时可能自动执行额外程序，请确认程序来源和目的。",
}
CLEANUP_STEPS = [
    "确认你已不再需要这个技能的功能，并检查是否有任务或其他客户端仍在使用它。",
    "在客户端点击“打开所在文件夹”；读导出报告时按完整路径找到同一位置。核对里面有 SKILL.md 和配套文件；要备份或删除的是这个技能的完整文件夹，不是只删 SKILL.md，也不是 .agents、.codex、skills 等根目录。",
    "返回上一级文件夹（Windows：Alt+↑；macOS：Command+↑），选中与报告名字一致的技能文件夹。复制到自己新建的备份位置，例如桌面的“技能备份”；备份放在非技能目录，不要放回 skills 里。",
    "再次核对选中的是普通文件夹，再右键选择“删除”（Windows，移入回收站）或“移到废纸篓”（macOS）。不要永久删除，也不要用 Shift+Delete。发现文件夹快捷方式、链接或多人共享目录，先停止操作并核对影响。",
    "重启对应 AI 客户端，再检查常用任务是否正常；SkillRadar 本身不会自动删除文件。",
    "需要恢复时，在回收站右键“还原”，或在废纸篓选择“放回原处”；也可以把备份复制回报告里的原完整位置。已有同名文件夹时先核对，避免覆盖，再重启对应 AI 客户端。",
]
CLEANUP_CAUTIONS = [
    "无调用记录只表示已采集日志中没有发现调用，近 30 天未调用也不保证删除后没有影响。",
    "安装时间是文件创建时间的估算，复制、恢复或重建可能改变它；缺失日志和不支持的来源无法还原。",
    "文件夹快捷方式、链接或共享技能目录可能被多个 AI 客户端使用，删除真实目录会影响所有引用它的客户端。",
    "停监听或暂停守护只影响 SkillRadar 的检查，不等于停止 AI 使用技能。",
]


def _text(value):
    """建议字段只接收明确文本，未知值不猜测身份。"""
    return value if isinstance(value, str) else ""


def _path_key(path):
    """匹配登记别名与真实安装位置，只解析身份，不扫描或改变展示路径。"""
    normalized = os.path.normpath(_text(path).replace("\\", "/"))
    try:
        normalized = os.path.realpath(normalized)
    except (OSError, ValueError):
        pass  # 旧记录指向失效位置时仍可按原规范路径核对。
    return os.path.normcase(os.path.normpath(normalized))


def _valid_findings(value):
    """缺少规则身份或严重度的旧记录不能支持已处理结论。"""
    return isinstance(value, list) and all(isinstance(item, dict)
        and bool(_text(item.get("rule_id"))) and _text(item.get("severity")) in SEVERITIES for item in value)


def _risk_reason(findings):
    """只使用固定规则语义，不将任意扫描消息升级为已证实的危害。"""
    reasons = []
    for item in findings:
        rule = _text(item.get("rule_id"))
        category = _text(item.get("category"))
        if not category and rule.startswith("SR-"):
            category = rule.split("-")[1]
        reason = RULE_REASONS.get(rule) or CATEGORY_REASONS.get(category)
        if reason and reason not in reasons:
            reasons.append(reason)
    return " ".join(reasons) or "检查发现了需要核对的内容，请查看证据并确认它是否符合技能的正常用途。"


def _item(path, row, reason, steps):
    """完整路径作为后续定位身份，建议不包含可执行删除命令。"""
    item = {"name": _text(row.get("name")) or _text(path).replace("\\", "/").rstrip("/").split("/")[-1] or "未知技能",
            "path": _text(path), "reason": reason, "steps": list(steps)}
    if _text(row.get("version")):
        item["version"] = row["version"]
    if _text(row.get("usage_state")):
        item["usage_state"] = row["usage_state"]
    return item


def build_guidance(snapshot, usage, generated_at):
    """风险处理先于清理；所有清理证据明确满足条件时才给候选建议。"""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    usage_available = isinstance(usage, dict) and isinstance(usage.get("installed_rows"), list) \
        and isinstance(usage.get("inventory_summary"), dict)
    usage = usage if isinstance(usage, dict) else {}
    groups = {key: [] for key in GROUPS}
    skills = snapshot.get("skills") if isinstance(snapshot.get("skills"), dict) else {}
    security_by_path, unresolved = {}, set()
    for path, original in sorted(skills.items(), key=lambda item: _text(item[0])):
        row = original if isinstance(original, dict) else {}
        key = _path_key(path)
        security_by_path[key] = row
        raw = row.get("raw_findings")
        valid = _valid_findings(raw)
        complete = row.get("scan_complete") is True and not row.get("scan_issues") and valid \
            and row.get("check_status") in ("healthy", "attention") \
            and (bool(raw) or row.get("check_status") == "healthy")
        handled = complete and row.get("review_status") in ("reviewed", "trusted")
        if handled:
            decision = "你已信任当前版本" if row.get("review_status") == "trusted" else "你已记录查看结果"
            groups["handled"].append(_item(path, row, decision + "；原始提示保留在技术附录，内容变化后需要重新确认。", []))
        elif not complete:
            unresolved.add(key)
            groups["confirm"].append(_item(path, row, "检查未读全或检查记录不完整，现在无法确认全部内容。",
                ["打开完整文件夹，核对是否有无法读取或过大的文件。", "在安全检查中重新检查，完成后再决定是否继续使用。", "不确定时可先放入隔离区，随后可以恢复。 ".rstrip()]))
        elif raw:
            unresolved.add(key)
            priority = any(item["severity"] == "CRITICAL" for item in raw)
            steps = ["先在安全检查中查看命中的文件和原始证据。", "不确定是否需要这些行为时，先放入隔离区，随后可以恢复。",
                     "确认符合用途后可记录已查看；只在明确接受当前版本的行为时选择信任。"]
            if priority:
                steps.insert(0, "确认前先停止在 AI 客户端中主动使用这个技能。")
            groups["priority" if priority else "confirm"].append(_item(path, row, _risk_reason(raw), steps))

    installed = usage.get("installed_rows")
    rows = [row for row in installed if isinstance(row, dict)] if isinstance(installed, list) else []
    if not rows and isinstance(usage.get("idle_groups"), dict):
        rows = [row for values in usage["idle_groups"].values() if isinstance(values, list)
                for row in values if isinstance(row, dict)]
    names = Counter(_text(row.get("name")) for row in rows)
    summary = usage.get("inventory_summary") if isinstance(usage.get("inventory_summary"), dict) else {}
    coverage = summary.get("coverage_complete") is True and summary.get("inventory_complete") is True
    unchecked = sum(_path_key(row.get("path")) not in security_by_path for row in rows)
    overview_notes = []
    if not skills:
        overview_notes.append("尚无安全检查记录：还没有保存检查结果，请先检查技能。")
    if unchecked:
        overview_notes.append(f"有 {unchecked} 个安装技能未检查，先确认这些技能的功能；是否清理由你决定。")
    if not usage_available:
        overview_notes.append("尚未读取完整使用记录，先读取使用统计，再评估闲置情况。")
    elif not coverage:
        overview_notes.append("日志或安装清单尚未完整采集，先补齐信息；当前记录不能覆盖全部使用情况。")
    current = parse_time(generated_at)
    cutoff = current - timedelta(days=30) if current else None
    for row in rows:
        path, name = _text(row.get("path")), _text(row.get("name"))
        key = _path_key(path)
        if key in unresolved:
            continue
        installed_at = parse_time(row.get("installed_at"))
        same_name = bool(name) and names[name] > 1
        eligible = coverage and bool(name) and bool(path) and cutoff is not None and installed_at is not None \
            and installed_at <= cutoff and row.get("usage_state") in ("never", "inactive") \
            and row.get("shared_name") is False and not same_name and row.get("observing") is False
        if eligible:
            reason = "已采集日志中没有发现调用记录，估算安装已超过 30 天。" if row["usage_state"] == "never" else \
                "已有调用记录，但最近 30 天没有记录到调用，估算安装已超过 30 天。"
            reason += "如果已不再需要，可以按下方教程手动备份后清理。"
            if key not in security_by_path:
                reason += "尚无对应安全检查记录，删除前请自行确认技能的功能和是否仍被需要。"
            merged = {**security_by_path.get(key, {}), **row}
            groups["cleanup"].append(_item(path, merged, reason,
                ["核对完整位置和技能功能，确认已不再需要。", "按照清理教程备份整个技能文件夹，再手动移入回收站。 ".rstrip()]))
        else:
            if same_name or row.get("shared_name") is True:
                reason = "同名技能共用调用记录，无法确认具体使用了哪个安装位置，先保留并核对。"
            elif row.get("observing") is True or (installed_at is not None and cutoff is not None and installed_at > cutoff):
                reason = "技能安装时间仍在最近 30 天的观察期，先保留并观察是否需要。"
            elif row.get("usage_state") == "active":
                reason = "最近有调用记录，先保留；是否需要仍由你根据实际任务判断。"
            elif not coverage:
                reason = "日志或安装清单尚未完整采集，不能据此判断是否适合清理。"
            else:
                reason = "安装时间、调用时间或共享位置信息不足，先保留并核对。"
            groups["observe"].append(_item(path, row, reason, ["核对技能的完整位置和用途，等待信息补齐后再评估清理。 ".rstrip()]))
    return {"version": 1, "groups": groups, "counts": {key: len(groups[key]) for key in GROUPS},
            "overview_notes": overview_notes, "coverage": {"checks_available": bool(skills),
                "usage_available": usage_available, "usage_complete": coverage, "unchecked_installed": unchecked},
            "cleanup_steps": list(CLEANUP_STEPS), "cleanup_cautions": list(CLEANUP_CAUTIONS)}
