"""用 Lucide 官方图标源渲染客户端资源；仅制作时需要 Node 的 resvg。"""
import argparse
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw


HERE = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 128, 256)


def icon_paths(filename):
    """保留 Lucide 官方路径，添加品牌颜色与布局。"""
    root = ElementTree.parse(HERE / filename).getroot()
    return "".join(ElementTree.tostring(child, encoding="unicode").replace(
        "ns0:", "").replace(' xmlns:ns0="http://www.w3.org/2000/svg"', "")
        for child in root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resvg-module", required=True,
                        help="已安装 @resvg/resvg-js 的绝对目录")
    args = parser.parse_args()
    radar = icon_paths("lucide-radar.svg")
    shield = icon_paths("lucide-shield-check.svg")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 512 512">
<defs>
  <linearGradient id="tile" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#23435A"/><stop offset="1" stop-color="#102536"/>
  </linearGradient>
  <linearGradient id="scan" x1="0" y1="1" x2="1" y2="0">
    <stop offset="0" stop-color="#3FBF9B"/><stop offset="1" stop-color="#B5FFE0"/>
  </linearGradient>
</defs>
<rect x="8" y="8" width="496" height="496" rx="120" fill="url(#tile)"/>
<rect x="10" y="10" width="492" height="492" rx="118" fill="none" stroke="#416477" stroke-opacity=".48" stroke-width="4"/>
<g transform="translate(72 66) scale(15.15)" fill="none" stroke="url(#scan)" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round">{radar}</g>
<circle cx="382" cy="390" r="79" fill="#102536" stroke="#385B6B" stroke-width="4"/>
<g transform="translate(326 333) scale(4.65)" fill="none" stroke="#B5FFE0" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{shield}</g>
</svg>'''
    (HERE / "skillradar.svg").write_text(svg, encoding="utf-8")
    renderer = r"""const fs = require('fs');
const { Resvg } = require(process.argv[1]);
const svg = fs.readFileSync(process.argv[2], 'utf8');
fs.writeFileSync(process.argv[3], new Resvg(svg).render().asPng());"""
    subprocess.run(["node", "-e", renderer, args.resvg_module,
                    str(HERE / "skillradar.svg"), str(HERE / "skillradar.png")], check=True)
    with Image.open(HERE / "skillradar.png") as original:
        image = original.convert("RGBA")
        image.save(HERE / "skillradar.ico", sizes=[(size, size) for size in SIZES])
        image.save(HERE / "skillradar.icns", format="ICNS")
        preview = Image.new("RGB", (760, 380), "#EDF5F4")
        draw = ImageDraw.Draw(preview)
        draw.text((20, 18), "SkillRadar | Lucide radar + shield-check", fill="#17394D")
        x = 30
        for size in (16, 24, 32, 48, 64, 128, 256):
            small = image.resize((size, size), Image.Resampling.LANCZOS)
            preview.paste(small, (x, 58), small)
            draw.text((x, 58 + size + 12), f"{size}px", fill="#17394D")
            x += max(size + 16, 50)
        preview.save(HERE / "skillradar-preview.png")
    print(json.dumps({"assets": str(HERE), "windows_sizes": SIZES}))


if __name__ == "__main__":
    main()
