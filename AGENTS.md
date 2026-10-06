# AGENTS.md — skill-radar 工作区指南

本地 AI 技能使用历史监控 + 静态安全检查 + 报告导出，三个组成部分：

- **Usage Radar**（`src/skill_monitor.py`）：读取 Codex / zcode / Claude 结构化会话日志，统计技能实际加载次数。
- **Security Guard**（`src/skill_guard.py`）：静态扫描凭据窃取、危险执行、数据外传、提示注入等供应链风险，**永不执行被扫描文件**。
- **SkillRadar Tray**（`src/tray/app.py`）：pywebview 桌面客户端（概览/安全/使用/报告/设置五屏），前端在 `tray/web/`。

## 目录结构

- `src/` — 生产 Python（src-layout，`pytest.ini` 已设 `pythonpath = src`）；`src/tray/` 为客户端后端逻辑。
- `tray/web/` — 客户端前端：`index.html` 单文件应用（CSS/JS 内联）+ `features.js` / `i18n.js` / `processing.js`。打包时经 PyInstaller `--add-data` 嵌入可执行文件。
- `rules/` — 安全规则（`blocklist.yaml`、`defaults.yaml`），同样打进包，保证无源码环境可扫。
- `tests/` 与 `tests/tray/` — pytest 套件；浏览器 UI 检查是真实 Chromium CDP 脚本（`.mjs`）。
- `docs/plans/`、`docs/specs/`、`docs/threat-model.md` — SDD 计划与设计文档，改敏感区域前先读对应 spec。
- `build_tray.py` — 打包驱动（Windows=onefile exe；macOS=.app + dmg）。
- `monitor.bat` — Windows 双击启动 CLI 监控的快捷入口。

## 常用命令

```bash
python -m pytest tests -q        # 全量测试（CI 同款）
python -m pytest tests/tray -q   # 仅客户端
python src/tray/app.py           # 本地运行客户端（先装 requirements-gui.txt）
python build_tray.py             # 打包
```

- 浏览器 UI 检查需 **Node 22+ 和 Chromium**：设 `SKILL_RADAR_BROWSER` 或依赖默认路径（chrome.exe / msedge）；缺失时自动 skip，不算失败。
- `network` 标记的测试默认跳过，`SKILL_RADAR_NETWORK_TESTS=1` 开启。
- 持久化数据在 `~/.skill-radar/`（`SKILL_RADAR_DATA_DIR` 可覆盖）；用户数据绝不入库。

## 架构与编码约定

- **UI 是单文件应用**：`tray/web/index.html` 内联全部样式与脚本；界面文案必须走 `tr()` 并在 `i18n.js` 同时补 zh-CN / en 词条。
- **侧栏折叠态 CSS 有三套选择器要同步**：`.layout.sidebar-collapsed .x` 与 `html[data-sidebar-collapsed="true"] .x` 成对出现，≤700px 移动端还有第三套覆盖规则；改折叠样式三处都要改。
- `.mjs` 检查脚本共享 localStorage 与 JS 全局状态：每个 check 前必须重置相关状态（如 `SIDEBAR_COLLAPSED`）再交互，否则点击的展开/折叠语义会反转。
- 新增安全检查规则先进 `rules/`，同步跑 `tests/test_rules.py`、`tests/test_ruleset.py`。
- 提交信息用 conventional commits（fix/feat/ci/docs/...）。

## 平台与发布

- 开发平台 Windows；macOS 只在 GitHub Actions（macos-14，x64 经 Rosetta）构建验证。
- `macos-tray` CI：push 到 main（按 paths 过滤：`src/**`、`tray/**`、`tests/**`、`rules/**` 等）→ 全量测试 + 打包 .app/.dmg artifact。
- `release` workflow：macos-tray 在 main 成功后**自动发布**下一个 patch 版本，固定使用触发 CI 的 commit；CI 失败或过期不发布。也可手动 `workflow_dispatch` 指定版本号。
- 真机 GUI 验收（托盘/四屏交互）CI 无法执行，需维护者本地按清单验收（见 `docs/plans/*acceptance*`）。

## 已知坑（Windows 托盘）

- 打包后资源路径以 `sys._MEIPASS` 为基准，不要依赖仓库相对路径。
- pywebview 关闭事件有 veto（关窗=隐藏到托盘）；停止后台 IO 线程用 CancelIoEx/CancelSynchronousIo，不要裸 kill。
- Windows toast 通知文本含 XML 特殊字符（`& < > '`）需转义。
- `tests/` 下不要创建与生产包同名的目录/文件（pytest 包遮蔽问题）。
