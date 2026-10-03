# tests/test_audit.py — 任务 5：audit 主流程（基线 / 漂移 / 信任降级）
# 简报三测为契约原文；后四测补自审要求：三态之 OK 路径、drifted 即 FAIL
#（无 CRITICAL 也 FAIL——前序裁定）、_is_trusted 子串语义、信任降级的 audit 级集成。
import os

import skill_guard as sg
from skill_guard import audit_roots, apply_trust

RULES = "- id: T\n  category: EXEC\n  severity: HIGH\n  description: d\n  patterns: ['curl [^\\n]*|sh']\n"

def make_skill(base, rel, body="# s"):
    d = base / rel; d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body)

def _cfg(**trust):
    return {"trust": {"owners": [], "repos": [], "hashes": [], **trust}}

def test_first_audit_baselines_all(tmp_path, monkeypatch):
    make_skill(tmp_path, "a"); make_skill(tmp_path, "b")
    snaps = {"skills": {}}
    reports = audit_roots([str(tmp_path)], rules_text=RULES, blocklist_text="[]",
                          snapshots=snaps, cfg=_cfg())
    statuses = {s["status"] for s in snaps["skills"].values()}
    assert statuses == {"baseline-unreviewed"} and len(reports) == 2

def test_second_audit_reports_drift(tmp_path, monkeypatch):
    make_skill(tmp_path, "a")
    snaps = {"skills": {}}
    audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    (tmp_path / "a/SKILL.md").write_text("# s\ncurl x | sh")
    reports = audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    assert snaps["skills"][str(tmp_path / "a")]["status"] == "drifted"
    assert any("DRIFT" in r for r in reports)

def test_trust_downgrades_but_never_critical(tmp_path):
    from skill_guard import Finding
    fs = [Finding("X", "EXEC", "HIGH", "a", 1, "e", "m", []),
          Finding("Y", "EXFIL", "CRITICAL", "a", 2, "e", "m", [])]
    out = apply_trust(fs, trusted=True)
    sev = {f.rule_id: f.severity for f in out}
    assert sev["X"] == "MEDIUM" and sev["Y"] == "CRITICAL"

def test_unchanged_skill_keeps_status_and_reports_ok(tmp_path):
    make_skill(tmp_path, "a")
    snaps = {"skills": {}}
    audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    reports = audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    assert snaps["skills"][str(tmp_path / "a")]["status"] == "baseline-unreviewed"
    assert len(reports) == 1 and reports[0].startswith("OK        a  ")

def test_drift_fails_even_without_critical(tmp_path):
    # 内容变化但零规则命中：无 CRITICAL 也必须 FAIL（ok 与 drifted 绑定）
    make_skill(tmp_path, "a")
    snaps = {"skills": {}}
    audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    (tmp_path / "a/SKILL.md").write_text("# s\nplain extra line, no rule hit")
    reports = audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    assert any("DRIFT" in r for r in reports)
    assert any("verdict: FAIL" in r for r in reports)

def test_is_trusted_matches_normalized_root():
    from skill_guard import _is_trusted
    assert _is_trusted(_cfg(owners=["alice"]), "C:\\Users\\alice\\skills")
    assert not _is_trusted(_cfg(owners=["alice"]), "C:\\Users\\bob\\skills")
    assert _is_trusted(_cfg(repos=["myrepo"]), "C:/code/myrepo/skills")
    assert not _is_trusted(_cfg(), "C:/code/myrepo/skills")

def test_trusted_root_downgrades_high_findings(tmp_path):
    make_skill(tmp_path, "a", body="# s\ncurl x | sh")
    snaps = {"skills": {}}
    reports = audit_roots([str(tmp_path)], RULES, "[]", snaps,
                          cfg=_cfg(owners=[tmp_path.name]))   # tmp_path 必在 root 归一路径中
    joined = "\n".join(reports)
    assert "MEDIUM" in joined and "HIGH" not in joined
    assert "[trusted, downgraded]" in joined
    # 发现 1 钉死：信任降级后重算 score（HIGH 25 → MEDIUM 10），报告头/状态行/快照一致
    assert "score=10" in joined and "score: 10/100" in joined
    assert snaps["skills"][str(tmp_path / "a")]["score"] == 10

def test_drift_without_critical_gets_correction_note(tmp_path):
    # 发现 2 钉死：纯漂移（零规则命中、无 CRITICAL）的报告块尾附漂移归因纠正行
    make_skill(tmp_path, "a")
    snaps = {"skills": {}}
    audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    (tmp_path / "a/SKILL.md").write_text("# s\nplain extra line, no rule hit")
    reports = audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    assert any("verdict: FAIL" in r for r in reports)
    assert any("由内容漂移引起" in r for r in reports)

def test_drift_with_critical_gets_no_correction_note(tmp_path):
    # 反向钉死：漂移且真有 CRITICAL（blocklist name 命中）→
    # 「拒绝安装（存在 CRITICAL）」文案本就准确，不附纠正行
    make_skill(tmp_path, "a")
    snaps = {"skills": {}}
    audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    (tmp_path / "a/SKILL.md").write_text("# s changed")
    reports = audit_roots([str(tmp_path)], RULES, "- name: a\n  source: t\n", snaps, cfg=_cfg())
    assert any("SR-BLOCK-001" in r for r in reports)   # CRITICAL 在场
    assert not any("由内容漂移引起" in r for r in reports)


def test_audit_replaces_normpath_equivalent_orphan_key(tmp_path):
    """I-2 回归：快照里同技能的旧混合分隔符键（normpath 等价）在写入 normpath
    新键时必须被清除——否则孤儿键残留，--accept-drift 按名称命中插入序在前的
    孤儿键时重基线写进孤儿，存活键永远 drifted（audit 持续报 DRIFT、accept
    清不掉的死循环）。旧键用正斜杠 + 中部双斜杠形态构造：Windows 上与反斜杠
    normpath 键字符串不等而 normpath 等价（真实回归形态）；POSIX 上 normpath
    幂等但中部双斜杠仍被折叠（// 前缀才有实现定义语义），两平台都得到
    「字符串不等、normpath 相等」的孤儿夹具，测试无需按平台分支。"""
    make_skill(tmp_path, "a")
    legacy_root = str(tmp_path).replace(os.sep, "/")        # roots 归一化前的正斜杠形态
    legacy_key = legacy_root + "//a"
    norm_key = os.path.normpath(os.path.join(str(tmp_path), "a"))
    assert legacy_key != norm_key and \
        os.path.normpath(legacy_key) == os.path.normpath(norm_key)   # 夹具自检
    snaps = {"skills": {legacy_key: {"name": "a", "status": "drifted",
                                     "score": 10, "scanned_at": "t",
                                     "hashes": {"SKILL.md": "old"}}}}
    audit_roots([str(tmp_path)], RULES, "[]", snaps, cfg=_cfg())
    # 孤儿键被清除，只剩 normpath 形态键且可被后续流程（--show-diff /
    # --accept-drift 的 _find_skill_entry）按名称寻址——修复前孤儿键插入序在
    # 前，accept 按名称命中孤儿并把重基线写进孤儿，存活键永远 drifted
    assert legacy_key not in snaps["skills"]
    assert list(snaps["skills"].keys()) == [norm_key]
    key, entry = sg._find_skill_entry(snaps, "a")
    assert key == norm_key                     # 名称查找唯一指向存活键（无孤儿歧义）
    # 旧键对 normpath 精确查找不可见 → 本轮按 NEW 重建基线；死循环已断：
    # 孤儿清除后，后续 --accept-drift 的名称命中唯一指向存活键，重基线即生效
    assert entry["status"] == "baseline-unreviewed"
