# tests/test_report.py
from skill_guard import Finding, ScanReport, score_findings, render_report

def mk(sev):
    return Finding("X-1", "EXEC", sev, "a.md", 1, "excerpt", "msg", [])

def test_score_sums_weights_capped():
    assert score_findings([mk("CRITICAL"), mk("HIGH"), mk("MEDIUM")]) == 75
    assert score_findings([mk("CRITICAL")] * 4) == 100

def test_render_orders_by_severity_and_has_advice():
    rep = ScanReport(skill_name="demo", root="/tmp/demo",
                     findings=[mk("MEDIUM"), mk("CRITICAL")], score=50,
                     files_scanned=3, ok=False)
    text = render_report(rep)
    assert text.index("CRITICAL") < text.index("MEDIUM")
    assert "拒绝安装" in text or "inspect" in text
    assert "demo" in text

def test_json_output_roundtrip():
    import json
    rep = ScanReport("demo", "/r", [mk("HIGH")], 25, 1, True)
    data = json.loads(json.dumps(rep.__dict__, default=lambda o: o.__dict__))
    assert data["findings"][0]["severity"] == "HIGH"
