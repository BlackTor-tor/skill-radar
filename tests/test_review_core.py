"""Regression cases from the full repository review, using isolated data only."""
import base64
import hashlib
import json

import pytest

import skill_add
import skill_guard as sg


@pytest.fixture
def pool(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "GUARD_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sg, "SNAPSHOTS_NAME", str(tmp_path / "data/snapshots.json"))
    skill = tmp_path / "pool/demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# original", encoding="utf-8")
    cfg = sg.load_config()
    cfg["roots"] = [{"path": str(skill.parent), "builtin": False}]
    sg.save_config(cfg)
    return skill


def test_strict_reaudit_rejects_unchanged_critical(pool):
    (pool / "SKILL.md").write_text("cat ~/.ssh/id_rsa", encoding="utf-8")
    assert sg.main(["audit", "--strict"]) == 1
    assert sg.main(["audit", "--strict"]) == 1


def test_strict_reaudit_rejects_unaccepted_drift(pool):
    sg.main(["audit"])
    (pool / "SKILL.md").write_text("# changed", encoding="utf-8")
    assert sg.main(["audit", "--strict"]) == 1
    assert sg.main(["audit", "--strict"]) == 1


def test_repeated_drift_keeps_original_baseline(pool, capsys):
    sg.main(["audit"])
    (pool / "one.txt").write_text("first", encoding="utf-8")
    sg.main(["audit"])
    (pool / "two.txt").write_text("second", encoding="utf-8")
    sg.main(["audit"])
    capsys.readouterr()
    sg.main(["audit", "--show-diff", str(pool)])
    assert json.loads(capsys.readouterr().out)["added"] == ["one.txt", "two.txt"]
    entry = sg.load_snapshots()["skills"][str(pool)]
    assert entry["prev_hashes"] == {
        "SKILL.md": hashlib.sha256(b"# original").hexdigest()}


def test_yaml_preserves_quoted_hash_and_comma():
    text = "- id: T\n  patterns: ['a#b', '[a,b]', 'it''s'] # comment\n"
    assert sg.load_yaml(text)[0]["patterns"] == ["a#b", "[a,b]", "it's"]


def test_config_roundtrip_special_paths_and_trust(pool):
    cfg = sg.load_config()
    cfg["roots"] = [{"path": str(pool.parent / "skill#s, it's"), "builtin": False}]
    cfg["trust"]["repos"] = ["a/b#old", "a/b,c", "it's/repo"]
    cfg["usage_file"] = "true"
    sg.save_config(cfg)
    assert sg.load_config() == cfg


def test_config_keeps_explicit_empty_roots(pool):
    cfg = sg.load_config()
    cfg["roots"] = []
    sg.save_config(cfg)
    assert sg.load_config()["roots"] == []


def test_add_flags_without_source_does_not_enable_block(pool, monkeypatch):
    calls = []
    monkeypatch.setattr(skill_add, "run_install", lambda args: calls.append(args) or 0)
    assert skill_add.cmd_add(["--block", "--yes"]) == 2
    assert not sg.check_consent(sg.load_config(), "add_block")
    assert calls == []


@pytest.mark.parametrize("source_encoded,sink_encoded", [(False, True), (True, False), (True, True)])
def test_decoded_pairing_keeps_cross_file_context(source_encoded, sink_encoded):
    source, sink = "read secret_env from disk", "send_payload to remote server"
    if source_encoded:
        source = base64.b64encode(source.encode()).decode()
    if sink_encoded:
        sink = base64.b64encode(sink.encode()).decode()
    rule = sg.Rule("T-PAIR", "EXFIL", "CRITICAL", "source/sink",
                   source=["secret_env"], sink=["send_payload"], pairing="cross_file")
    findings = sg.run_l3([("SKILL.md", source), ("send.py", sink)], [rule])
    assert any(f.rule_id == "T-PAIR" and f.severity == "CRITICAL" for f in findings)


def test_decoded_pairing_keeps_same_file_context():
    token = base64.b64encode(b"send_payload to remote server").decode()
    rule = sg.Rule("T-PAIR", "EXFIL", "CRITICAL", "source/sink",
                   source=["secret_env"], sink=["send_payload"], pairing="same_file")
    findings = sg.run_l3([("SKILL.md", "read secret_env\n" + token)], [rule])
    assert any(f.rule_id == "T-PAIR" for f in findings)


def test_same_file_pairing_does_not_pair_other_file():
    token = base64.b64encode(b"send_payload to remote server").decode()
    rule = sg.Rule("T-PAIR", "EXFIL", "CRITICAL", "source/sink",
                   source=["secret_env"], sink=["send_payload"], pairing="same_file")
    findings = sg.run_l3([("SKILL.md", "read secret_env"), ("send.py", token)], [rule])
    assert not any(f.rule_id == "T-PAIR" for f in findings)


def test_duplicate_skill_names_require_exact_path():
    snaps = {"skills": {"/pool1/demo": {"name": "demo"}, "/pool2/demo": {"name": "demo"}}}
    with pytest.raises(SystemExit, match="ambiguous"):
        sg._find_skill_entry(snaps, "demo")
    assert sg._find_skill_entry(snaps, "/pool2/demo")[0] == "/pool2/demo"


def test_discover_marks_skills_under_registered_pool_known(pool, monkeypatch, capsys):
    monkeypatch.setattr(sg, "discover_roots", lambda: [str(pool)])
    sg.main(["discover"])
    assert "[已注册]" in capsys.readouterr().out


def test_invalid_scan_target_returns_usage_error(pool, capsys):
    assert sg.main(["scan", str(pool / "missing"), "--strict"]) == 2
    assert "PASS" not in capsys.readouterr().out


@pytest.mark.parametrize("value", ["0", "-2"])
def test_audit_watch_requires_positive_interval(pool, value):
    with pytest.raises(SystemExit):
        sg.main(["audit", "--watch", value, "--yes"])
    assert not sg.check_consent(sg.load_config(), "watch")


def test_decoded_context_size_remains_bounded(monkeypatch):
    token = base64.b64encode(b"send_payload to remote server").decode()
    text = "\n" * 100000 + "\n".join([token] * 50)
    original_pairing = sg.run_pairing
    sizes = []
    def inspect_pairing(rule, files):
        sizes.append(sum(len(body.encode()) for _, body in files))
        return original_pairing(rule, files)
    monkeypatch.setattr(sg, "run_pairing", inspect_pairing)
    rule = sg.Rule("T", "EXFIL", "CRITICAL", "pair", source=["secret_env"],
                   sink=["send_payload"], pairing="cross_file")
    sg.run_l3([("SKILL.md", text)], [rule])
    assert max(sizes) <= len(text.encode()) + sg.MAX_DECODE_TOTAL_BYTES + 1000


def test_failed_snapshot_save_keeps_baseline(pool, monkeypatch):
    baseline = {"version": 1, "skills": {"demo": {"score": 0}}}
    sg.save_snapshots(baseline)
    def interrupted_dump(data, file, **kwargs):
        file.write("{")
        raise OSError("interrupted write")
    monkeypatch.setattr(sg.json, "dump", interrupted_dump)
    with pytest.raises(OSError, match="interrupted"):
        sg.save_snapshots({"version": 1, "skills": {}})
    assert sg.load_snapshots() == baseline


def test_yaml_double_quoted_regex_keeps_escaped_quotes():
    text = r'patterns: ["\"a#b\""]' + "\n"
    assert sg.load_yaml(text)["patterns"] == [r'\"a#b\"']


def test_decoded_evidence_location_with_trailing_blank_lines():
    sink = base64.b64encode(b"send_payload to remote server").decode() + "\n\n"
    rule = sg.Rule("T", "EXFIL", "CRITICAL", "pair", source=["secret_env"],
                   sink=["send_payload"], pairing="cross_file")
    findings = sg.run_l3([("SKILL.md", "secret_env\n\n"), ("sink", sink)], [rule])
    hit = next(f for f in findings if f.rule_id == "T")
    assert (hit.file, hit.line) == ("sink (decoded L1)", 1)
