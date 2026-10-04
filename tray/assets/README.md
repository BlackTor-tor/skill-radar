# SkillRadar 客户端图标

青绿雷达表示技能使用发现，深蓝盾牌表示技能安全检查。图形基于 Lucide 官方 `radar` 与 `shield-check` 组合并离线渲染，不含网络依赖。

- `skillradar.svg`：品牌矢量图，界面使用。
- `skillradar.png`：1024 × 1024 透明背景图，托盘使用。
- `skillradar.ico`：Windows 可执行文件图标，含 16、24、32、48、64、128、256 像素。
- `skillradar.icns`：macOS 应用图标。
- `skillradar-preview.png`：常见大小效果预览。

Lucide 官方来源：

- https://github.com/lucide-icons/lucide/blob/main/icons/radar.svg
- https://github.com/lucide-icons/lucide/blob/main/icons/shield-check.svg
- 许可全文见 `LICENSE-lucide.txt`（ISC）。

制作时安装 `@resvg/resvg-js@2.6.2` 到任意工具目录，再运行：

```powershell
python tray/assets/generate_brand.py --resvg-module C:/path/to/node_modules/@resvg/resvg-js
```

客户端只需要提交的图像文件，渲染工具无需随安装包分发。
