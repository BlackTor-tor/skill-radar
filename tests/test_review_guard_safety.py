"""代码审查回归：扫描边界、名称 IOC、技能发现与临时清理。"""
import os
import stat
import subprocess

import pytest

import skill_guard as sg


def make_skill(path, body="# clean"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(body, encoding="utf-8")
    return path


def test_scan_missing_target_does_not_report_pass(tmp_path):
    with pytest.raises(ValueError, match="target"):
        sg.run_engine(str(tmp_path / "missing"), [])


def test_scan_regular_file_target_does_not_report_pass(tmp_path):
    file = tmp_path / "SKILL.md"
    file.write_text("# clean", encoding="utf-8")
    with pytest.raises(ValueError, match="target"):
        sg.run_engine(str(file), [])


def test_frontmatter_name_ioc_survives_directory_rename(tmp_path):
    skill = make_skill(tmp_path / "renamed", "---\nname: google-k53\n---\n# clean")
    report = sg.run_engine(str(skill), [], "- name: google-k53\n  source: test\n")
    assert not report.ok
    assert any(f.rule_id == "SR-BLOCK-001" for f in report.findings)


def test_frontmatter_name_ioc_allows_quoted_name_with_comment(tmp_path):
    skill = make_skill(tmp_path / "renamed", "---\nname: 'google-k53' # package\n---\n# clean")
    report = sg.run_engine(str(skill), [], "- name: google-k53\n  source: test\n")
    assert not report.ok


def test_repo_scan_checks_nested_skill_names_for_iocs(tmp_path):
    skill = make_skill(tmp_path / "skills" / "innocent", "---\nname: google-k53\n---\n# clean")
    report = sg.run_engine(str(tmp_path), [], "- name: google-k53\n  source: test\n")
    assert not report.ok
    assert any(f.file == os.path.join("skills", "innocent", "SKILL.md")
               for f in report.findings)


def test_repo_scan_checks_nested_skill_directory_name_iocs(tmp_path):
    make_skill(tmp_path / "skills" / "google-k53")
    assert not sg.run_engine(str(tmp_path), [], "- name: google-k53\n  source: test\n").ok


def test_discovered_skill_dirs_can_be_audited(tmp_path, monkeypatch):
    skill = make_skill(tmp_path / ".agents" / "skills" / "demo")
    monkeypatch.setattr(sg, "HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    roots = sg.discover_roots()
    snaps = {"version": 1, "skills": {}}
    sg.audit_roots(roots, "", "[]", snaps, {})
    assert set(snaps["skills"]) == {str(skill)}


def test_audit_finds_nested_skill_stores_and_deduplicates_roots(tmp_path):
    pool = tmp_path / "pool"
    nested = make_skill(pool / ".system" / "nested")
    ordinary = make_skill(pool / "ordinary")
    make_skill(pool / "node_modules" / "skip")
    snaps = {"version": 1, "skills": {}}
    reports = sg.audit_roots([str(pool), str(nested), str(pool)], "", "[]", snaps, {})
    assert set(snaps["skills"]) == {str(nested), str(ordinary)}
    assert len(reports) == 2


def test_discover_includes_cwd_skill_itself(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    skill = make_skill(tmp_path / "current")
    monkeypatch.setattr(sg, "HOME", str(home))
    monkeypatch.chdir(skill)
    assert str(skill) in sg.discover_roots()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink permission and FIFO semantics")
def test_scanning_skips_file_and_directory_symlinks(tmp_path):
    pool = make_skill(tmp_path / "pool")
    outside = make_skill(tmp_path / "outside", "private outside data")
    (pool / "external.txt").symlink_to(outside / "SKILL.md")
    (pool / "external-dir").symlink_to(outside, target_is_directory=True)
    assert dict(sg.collect_text_files(str(pool))) == {"SKILL.md": "# clean"}
    assert set(sg._file_hashes(str(pool))) == {"SKILL.md"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO semantics")
def test_scanning_skips_fifo_without_blocking(tmp_path):
    import signal
    pool = make_skill(tmp_path / "pool")
    os.mkfifo(pool / "stream")
    def timed_out(*_args):
        raise RuntimeError("scanning a FIFO blocked")
    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(2)
    try:
        assert set(dict(sg.collect_text_files(str(pool)))) == {"SKILL.md"}
        assert set(sg._file_hashes(str(pool))) == {"SKILL.md"}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_temp_cleanup_does_not_chmod_external_symlink_target(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("private outside data", encoding="utf-8")
    outside.chmod(0o400)
    (pool / "external.txt").symlink_to(outside)
    sg._force_rmtree(str(pool))
    assert stat.S_IMODE(outside.stat().st_mode) == 0o400
    assert outside.read_text(encoding="utf-8") == "private outside data"
    assert not pool.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_windows_junction_scan_and_cleanup_stay_inside_target(tmp_path):
    pool = make_skill(tmp_path / "pool")
    outside = make_skill(tmp_path / "outside", "private outside data")
    external = outside / "SKILL.md"
    external.chmod(stat.S_IREAD)
    junction = pool / "external-dir"
    created = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
                             capture_output=True)
    if created.returncode:
        external.chmod(stat.S_IREAD | stat.S_IWRITE)
        pytest.skip("junction creation unavailable")
    try:
        assert dict(sg.collect_text_files(str(pool))) == {"SKILL.md": "# clean"}
        assert set(sg._file_hashes(str(pool))) == {"SKILL.md"}
        sg._force_rmtree(str(pool))
        assert external.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        assert external.read_text(encoding="utf-8") == "private outside data"
        assert not pool.exists()
    finally:
        if junction.exists():
            os.rmdir(junction)
        external.chmod(stat.S_IREAD | stat.S_IWRITE)
