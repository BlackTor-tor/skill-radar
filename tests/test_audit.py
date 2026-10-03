# tests/test_audit.py — 任务 5：audit 主流程（基线 / 漂移 / 信任降级）
# 简报三测为契约原文；后四测补自审要求：三态之 OK 路径、drifted 即 FAIL
#（无 CRITICAL 也 FAIL——前序裁定）、_is_trusted 子串语义、信任降级的 audit 级集成。
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
