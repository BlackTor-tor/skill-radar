# 离线界面的结构和无障碍约束；真实交互另由 test_ui_interactions.py 验证。
from html.parser import HTMLParser
import os
from pathlib import Path
import re
from urllib.parse import urlparse

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tray", "web", "index.html")


def _read():
    return open(UI, encoding="utf-8").read()


class _Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def _sources(html):
    """Read locally packaged JavaScript so translated copy can live outside HTML."""
    sources = [html]
    for tag, attrs in _Markup(html).elements:
        if tag == "script" and attrs.get("src"):
            source = (Path(UI).parent / attrs["src"]).resolve()
            assert source.is_file(), f"Missing packaged UI script: {source}"
            sources.append(source.read_text(encoding="utf-8"))
    return "\n".join(sources)


def test_ui_file_exists_and_selfcontained():
    html = _read()
    assert "<!DOCTYPE html>" in html
    for tag, attrs in _Markup(html).elements:
        for attribute in ("src", "href", "action"):
            value = attrs.get(attribute, "")
            assert not value.startswith("//"), f"Remote UI resource: {value}"
            parsed = urlparse(value)
            assert parsed.scheme in ("", "data"), f"Remote UI resource: {tag} {value}"
            if attribute == "src" and value and not parsed.scheme:
                assert (Path(UI).parent / value).is_file(), f"Unpackaged UI resource: {value}"
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    assert not re.search(r"@import|url\(\s*['\"]?(?:https?:)?//", css), "Remote CSS dependencies"


def test_cold_minimal_tokens_present():
    html = _read()
    for tok in ("--accent", "--surface", "--fg", "--danger", "--warning", "--border", "--muted"):
        assert tok in html
    assert "oklch(" in html


def test_four_screens_present():
    html = _sources(_read())
    for sid in ("screen-overview", "screen-security", "screen-usage", "screen-settings"):
        assert f'id="{sid}"' in html
    for nav in ("Overview", "Security", "Usage", "Settings"):
        assert nav in html
    for cn in ("概览", "安全", "使用统计", "设置"):
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
    assert re.search(r"min-height\s*:\s*44px", html)   # 主要按钮触摸目标
    assert ":focus-visible" in html                    # 键盘焦点可见
    assert 'id="btn-close-drawer"' in html
    assert 'id="drawer-backdrop"' in html
    # 允许颜色和透明度等动效，但禁止改变布局尺寸或位置的过渡。
    for m in re.findall(r"transition:\s*([^;]+);", html):
        for prop in m.split(","):
            p = prop.strip().split(" ")[0]
            if p:
                assert p in ("transform", "opacity", "color", "background", "background-color",
                             "border-color", "box-shadow", "filter", "none"), p


def test_data_table_and_drawer_used():
    html = _sources(_read())
    assert "<table" in html and "drawer" in html.lower()
    assert "role=\"dialog\"" in html or "role='dialog'" in html


def test_final_b2_wiring_present():
    # 终审 B2 静态断言：I-2 恢复控件 + I-3 数据源接线（Security ← snapshot.skills、
    # Usage 明细 ← act('get_usage')）
    html = _sources(_read())
    assert 'id="ov-resume"' in html                       # Overview 恢复按钮
    assert re.search(r"['\"]ov-resume['\"]\)\.hidden\s*=\s*!paused", html)
    assert re.search(r"(?:act|runAction)\(['\"]resume['\"]", html)
    assert "st.skills" in html                             # Security 从 snapshot.skills 渲染
    assert "暂无检查记录" in html                        # 明确区分待检查与无风险
    assert "'get_usage'" in html                           # Usage 明细动作
    assert 'id="usage-detail"' in html                     # 明细容器
    assert "loadUsage()" in html                           # 切屏拉取
