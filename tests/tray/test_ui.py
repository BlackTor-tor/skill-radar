# tests/tray/test_ui.py — 任务 6：界面承诺的静态断言（不渲染，钉规格 §2）
import os

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tray", "web", "index.html")


def _read():
    return open(UI, encoding="utf-8").read()


def test_ui_file_exists_and_selfcontained():
    html = _read()
    assert "<!DOCTYPE html>" in html
    assert "http://" not in html.replace("http://localhost", "")   # 无外链 CDN
    assert "https://" not in html   # 离线自包含


def test_cold_minimal_tokens_present():
    html = _read()
    for tok in ("--color-primary", "--color-surface", "--color-on-surface",
                "--color-error", "--color-success", "--color-warning"):
        assert tok in html
    assert "oklch(" in html


def test_four_screens_present():
    html = _read()
    for sid in ("screen-overview", "screen-security", "screen-usage", "screen-settings"):
        assert f'id="{sid}"' in html
    for nav in ("Overview", "Security", "Usage", "Settings"):
        assert nav in html
    for cn in ("概览", "安全", "用量", "设置"):
        assert cn in html


def test_anti_patterns_absent():
    html = _read()
    assert "backdrop-filter" not in html        # 无玻璃拟态
    assert "background-clip:text" not in html.lower().replace(" ", "")   # 无渐变文字
    assert "text-fill-color" not in html.lower().replace(" ", "")        # 无渐变文字
    assert "linear-gradient" not in html        # 无渐变
    assert "@keyframes" not in html or "prefers-reduced-motion" in html


def test_motion_and_a11y_commitments():
    html = _read()
    assert "prefers-reduced-motion" in html
    assert "min-height: 44px" in html   # 触摸目标（nav 侧栏按钮内联尺寸钉住）
    assert "ease-out" in html
    # transform/opacity-only 动效：transition 里不得出现 left/top/width/height
    import re
    for m in re.findall(r"transition:\s*([^;]+);", html):
        for prop in m.split(","):
            p = prop.strip().split(" ")[0]
            if p:
                assert p in ("transform", "opacity", "color", "background-color",
                             "border-color", "box-shadow", "filter", "all"), p


def test_data_table_and_drawer_used():
    html = _read()
    assert "<table" in html and "drawer" in html.lower()
    assert "role=\"dialog\"" in html or "role='dialog'" in html


def test_final_b2_wiring_present():
    # 终审 B2 静态断言：I-2 恢复控件 + I-3 数据源接线（Security ← snapshot.skills、
    # Usage 明细 ← act('get_usage')）
    html = _read()
    assert 'id="ov-resume"' in html                       # Overview 恢复按钮
    assert "ov-resume').hidden = !st.paused" in html      # 仅 paused 时显示
    assert "act('resume'" in html                          # 与托盘动态项同一通道
    assert "st.skills" in html                             # Security 从 snapshot.skills 渲染
    assert "暂无扫描数据" in html and "awaiting scan" in html   # Security 空态
    assert "'get_usage'" in html                           # Usage 明细动作
    assert 'id="usage-detail"' in html                     # 明细容器
    assert "loadUsage()" in html                           # 切屏拉取
