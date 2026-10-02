# tests/test_collect.py
import os
from skill_guard import collect_text_files

def make(root, rel, content, binary=False):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(content if binary else content.encode())

def test_collect_skips_binary_oversized_and_excluded(tmp_path):
    make(tmp_path, "SKILL.md", "# ok")
    make(tmp_path, "scripts/run.sh", "echo hi")
    make(tmp_path, "bin/blob.dat", b"\x00\x01abc", binary=True)      # 空字节 -> 跳过
    make(tmp_path, "big.md", "x" * (2 * 1024 * 1024 + 1))            # >2MB -> 跳过
    make(tmp_path, "node_modules/x/SKILL.md", "# excluded")          # 排除目录
    files = collect_text_files(str(tmp_path))
    rels = [f[0] for f in files]
    assert rels == ["SKILL.md", os.path.normpath("scripts/run.sh")] or \
           [r.replace("\\", "/") for r in rels] == ["SKILL.md", "scripts/run.sh"]

def test_nested_depth_unlimited_inside_skill(tmp_path):
    make(tmp_path, "a/b/c/deep.md", "deep")
    assert any(f[0].endswith("deep.md") for f in collect_text_files(str(tmp_path)))
