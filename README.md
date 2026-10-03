# skill-radar

**One repo, two instruments, formatted reports — know what your AI agents'
skills are doing, and whether they are safe.**

> 📊 **Usage Radar** (`skill_monitor.py`) — counts which of your installed
> skills actually get used, across every agent on the machine (zcode, Claude
> Code, Codex, Cursor, ...).
>
> 🛡️ **Security Guard** (`skill_guard.py`) — scans installed skills for
> supply-chain risks: credential theft, malicious execution, data exfiltration,
> prompt injection and more.
>
> 🖼️ **Report generator** (`skill_report.py`) — turns both into formatted,
> shareable **HTML + PNG** reports.

中文说明见下方 [中文文档](#中文文档)。

## Why

Every skill an agent knows adds its name + description to every session's
context, and skills get auto-triggered based on those descriptions. Once you
pass a hundred skills, two questions nobody can answer:

1. **Which skills ever fire?** (dead weight, greedy trigger descriptions)
2. **Are the skills I installed safe?** (SKILL.md can carry shell commands;
   malicious skills have stolen credentials at scale — the ClawHavoc /
   skills.sh campaigns)

skill-radar answers both with real numbers, offline, zero dependencies.

## The two instruments

| | 📊 Usage Radar | 🛡️ Security Guard |
|---|---|---|
| File | `skill_monitor.py` | `skill_guard.py` |
| Question | *which skills get used?* | *which skills are dangerous?* |
| Method | 4-layer log analysis (below) | 3-layer static scan (8 risk categories) |
| Output | `skill_usage.json` counters | risk findings + SHA-256 drift baselines |
| Needs | nothing after one-time setup | `rules/defaults.yaml` (bundled) |

**Neither tool is a daemon.** Your agents write their session logs
continuously; skill-radar only *reads* those logs when you run it. Not running
it for three months loses nothing — the next run catches up on everything
logged in between.

## How the Usage Radar works

Four counting layers, from most precise to most universal:

| Layer | Coverage | Granularity | How |
|---|---|---|---|
| **precise (zcode)** | zcode | exact invocations | parses `~/.zcode/cli/rollout/*.jsonl` `response.toolCalls` |
| **precise (claude)** | Claude Code | exact invocations | parses `~/.claude/projects/**/*.jsonl` `tool_use` events |
| **marker (universal)** | *any* agent — Codex, Cursor, anything that logs session content | one "session hit" per (transcript file, skill) | `apply_skill_markers.py` injects a unique `<!-- skill-marker:NAME -->` at the end of every SKILL.md copy; the radar greps **all** agents' session logs for that string. If an agent loaded the skill content into context, the marker rode along into its transcript. |
| **atime (fallback)** | *any* tool, even ones without logs | coarse | watches each SKILL.md's last-access time (NTFS yes, Linux relatime rarely) |

Codex and Cursor are counted by the **marker layer** (session-level
granularity); exact per-invocation counts require a structured Skill-tool event,
which only the zcode/Claude Code lineage exposes.

## Quick start

Python 3.8+, no third-party packages.

```bash
# 1. one-time setup: inject markers into every SKILL.md copy
python apply_skill_markers.py

# 2. first scan (precise layers backfill history; marker/atime start from today)
python skill_monitor.py

# 3. baseline your installed skills for the guard
python skill_guard.py audit

# 4. generate formatted reports (HTML + PNG) for both instruments
python skill_report.py
```

Reports land in `./skill-radar-reports/`. Windows users can double-click
`monitor.bat` for the text report.

## Reports (HTML + PNG)

`skill_report.py` renders two formatted reports — layout via inline CSS, PNG
export via your system's Edge/Chrome in headless mode (no dependencies added;
CJK text uses system fonts).

```bash
python skill_report.py                  # both reports: usage + security
python skill_report.py usage            # usage report only
python skill_report.py guard            # security report only
python skill_report.py guard --fast     # from snapshot scores, skip the rescan
python skill_report.py --html-only      # skip PNG (no browser needed)
python skill_report.py --out DIR        # custom output directory
```

- **Usage report**: total invocations, active skills, per-layer distribution
  (zcode/claude/marker/atime), top-skills bar chart, full per-skill table.
- **Security report**: skills scanned, findings by severity (CRITICAL → INFO),
  status distribution, and a **top-30 risk table with an install-advice verdict
  per skill** (推荐 install / 谨慎 evaluate / 不推荐 reject, each with a plain
  Chinese reason — CRITICAL findings or drift → reject; HIGH/MEDIUM → evaluate
  with the reason spelled out; clean → recommend). Default mode **rescans** all
  registered roots for full findings detail; `--fast` renders from the last
  `audit` baselines. All headings and labels are bilingual (EN · 中文).

## Usage patterns

### Usage radar — three ways to run it

| Pattern | Command | When |
|---|---|---|
| **On demand** (default) | `python skill_monitor.py` | whenever you want a report; exits when done |
| **Scheduled report** | Task Scheduler / cron: `python /path/to/skill_monitor.py --top 20 >> usage-report.log` | a weekly digest, fully unattended |
| **Live watch** (foreground, optional) | `python skill_monitor.py --watch 60` | watch deltas in real time; Ctrl+C to stop — stopping loses nothing |

### Security guard — where it fits your workflow

```bash
# BEFORE installing a skill you found online — the pre-install gate
python skill_guard.py scan https://github.com/someone/some-skills --strict

# right after installing: baseline it (first audit baselines everything)
python skill_guard.py audit

# after `npx skills update` — legit updates also raise DRIFT, so:
python skill_guard.py audit --show-diff <skill>     # what actually changed?
python skill_guard.py audit --accept-drift <skill>  # diff looked fine → re-baseline

# unattended sweep: catches NEW skills, DRIFT, blocklist hits; exit 1 on problems
python skill_guard.py audit --strict                # hook into Task Scheduler / cron

# discover skill roots on this machine; --deep is full-disk, consent-gated
python skill_guard.py discover [--deep] [--yes]
```

A typical rhythm: `scan` before every install, `audit --strict` on a weekly
schedule, `--accept-drift` only after you have eyeballed the diff, and
`skill_report.py` whenever you want the picture in one image.

### Pre-install gateway: `add` (v2.2)

`python skill_guard.py add [--block] <npx skills add args...>` gates every
install: it shallow-clones the source, runs the full 3-layer engine, prints the
report plus the three-way install verdict (推荐/谨慎/不推荐), then delegates to
`npx skills add` with your original arguments (exit code passed through).
After a successful install it automatically baselines the new skills (audit
increment). `--block` (persisted to `consent.add_block` in config.yaml on
first use) refuses 不推荐-verdict installs with exit code 1 — non-interactive
and fail-closed, same contract as `scan --strict`. Clone/scan failure fails
open with a loud warning. One-line accelerator:

    # bash
    alias skills='python /path/to/skill_guard.py add -- skills'
    # PowerShell
    function skills { python F:\path\to\skill_guard.py add -- skills @args }

### SkillRadar Tray (v2.2, Windows + macOS)

The optional tray daemon watches all registered skill roots in real time
(pure-ctypes ReadDirectoryChangesW on Windows, FSEvents on macOS — no daemon
framework, no new core dependencies) and reacts to changes in the skill pool:
new skill or content drift → toast + tray badge; in block mode a CRITICAL
verdict additionally quarantines the skill directory (opt-in, moved to
`~/.skill-radar/quarantine/` with a RESTORE.txt note). The bundled UI
(pywebview, offline single-file HTML) shows Overview / Security / Usage /
Settings. Build it with:

    pip install -r requirements-gui.txt
    python build_tray.py          # Windows: dist/SkillRadarTray.exe (onefile)
                                   # macOS: dist/SkillRadarTray.app + .dmg

Event watching and `audit --watch` are complementary layers (spec §4):
watching = second-level discovery + alerting; `--watch` polling = deep sweep
for non-graphical environments. The tray client is GUI-optional — the five
core .py files stay pure stdlib.

macOS note: the bundle is unsigned / not notarized — on first launch,
right-click the app and choose Open (roadmap: signing & notarization).

## Security Guard details

The guard scans **all** text files inside a skill directory (attached scripts
included, not just SKILL.md) for 8 categories of risk: credential theft (THEFT),
malicious execution (EXEC), persistence (PERSIST), data exfiltration (EXFIL),
prompt injection (INJ), agent-config abuse (ABUSE), social-engineering phrasing
(DECEP), and typosquat / source spoofing (SUPPLY) — via a 3-layer engine
(pattern matching, cross-file source→sink pairing, entropy/hidden-character/
multi-layer decode detection). Deterministic, offline, pure stdlib, and it
**never executes** scanned content.

The ruleset and IOC blocklist live in [`rules/`](rules/) — shareable and
overridable per rule.

**Positioning — complementary, not a replacement:**

- **Snyk (embedded scanning)** covers the package/dependency ecosystem; skill-radar covers the SKILL.md directory convention.
- **LLM review skills** give semantic depth but are non-deterministic; skill-radar is a reproducible, offline first-pass filter.
- **Behavioral sandboxes** give runtime ground truth at high cost; skill-radar is the cheap deterministic gate to run before and after install.

Threat model, known limits (SkillCloak-class semantic evasion is out of scope),
and consent gates for `discover --deep` / `audit --watch`:
[docs/threat-model.md](docs/threat-model.md).

## Notes & caveats

- **Re-run `apply_skill_markers.py` after `npx skills update`** — updating
  skills overwrites SKILL.md and removes the markers.
- The marker layer cannot backfill history: transcripts written before the
  markers were injected don't contain them. The precise layers do.
- "session hits" counts a skill once per transcript file. Use the precise
  layers for exact counts where available.
- All paths resolve from your home directory; the radar's state lives next to
  the scripts, the guard's data in `~/.skill-radar/`.
- Reports need Edge or Chrome for PNG export; `--html-only` works without.
- Tested on Windows (NTFS). macOS/Linux work for the precise + marker layers;
  the atime layer depends on your filesystem's access-time policy.

## Files

| File | Purpose |
|---|---|
| `apply_skill_markers.py` | inject/remove the universal markers (idempotent) |
| `skill_monitor.py` | 📊 Usage Radar: incremental scan + counters |
| `skill_guard.py` | 🛡️ Security Guard: `scan` / `audit` / `discover` |
| `skill_report.py` | 🖼️ report generator: HTML + PNG for both instruments |
| `rules/` | default ruleset (8 categories) + IOC blocklist seed |
| `monitor.bat` | double-click text-report launcher (Windows) |

## License

MIT

---

## 中文文档

**一套工具，两种仪器，一份可视化报告——看清你 agent 的技能在做什么、是否安全。**

> 📊 **用量雷达**（`skill_monitor.py`）——统计装了的技能哪些真的被调用，
> 覆盖机器上的所有 agent（zcode、Claude Code、Codex、Cursor……）
>
> 🛡️ **安全闸门**（`skill_guard.py`）——扫描已装技能的供应链风险：
> 凭证窃取、恶意执行、数据外发、提示注入……
>
> 🖼️ **报告生成器**（`skill_report.py`）——把两者渲染成带排版的 **HTML + PNG** 报告

### 为什么需要

每个技能的名字和描述都会进入每个会话的上下文，agent 靠描述决定要不要自动触发。
技能过百之后两个问题没人答得上来：**哪些技能真的在用？装的技能安全吗？**
（SKILL.md 里能写 shell 命令，恶意技能曾大规模窃取凭证——ClawHavoc / skills.sh 事件。）
skill-radar 用真实数据回答这两问：离线、零依赖。

### 两个仪器

| | 📊 用量雷达 | 🛡️ 安全闸门 |
|---|---|---|
| 文件 | `skill_monitor.py` | `skill_guard.py` |
| 回答 | 哪些技能在被用？ | 哪些技能危险？ |
| 方法 | 四层日志分析 | 三层静态扫描（8 类风险） |
| 产出 | `skill_usage.json` 计数器 | 风险发现 + SHA-256 漂移基线 |

**两个工具都不是 daemon**：agent 持续把会话写进各自日志，skill-radar 只在你
运行时**读取**。三个月不跑也不丢数据，下次运行全部补上——没有后台服务、
没有自启进程、没有需要维活的常驻东西。

### 用量雷达的四层计数

1. **zcode 精确层**：解析 zcode 模型 I/O 日志的 `toolCalls`，精确计数，可回填历史
2. **Claude Code 精确层**：解析 `~/.claude/projects` 的 `tool_use` 事件，可回填历史
3. **标记通用层**：给每个 SKILL.md 末尾注入唯一标记，grep 所有 agent 的会话日志
   （Codex、Cursor 走这层——会话命中粒度；内容被载入上下文标记就必然留痕）
4. **atime 兜底层**：文件访问时间变化 = 被某工具读过（NTFS 可靠；Linux relatime 下无效）

### 快速开始

```bash
python apply_skill_markers.py   # ① 一次性：注入标记（幂等）
python skill_monitor.py         # ② 首次全量扫描用量
python skill_guard.py audit     # ③ 给已装技能建安全基线
python skill_report.py          # ④ 生成两份 HTML+PNG 报告
```

报告落在 `./skill-radar-reports/`。Windows 用户可双击 `monitor.bat` 看文本报告。

### 报告（HTML + PNG）

`skill_report.py` 渲染两份带排版的报告——内联 CSS 排版，PNG 由系统自带
Edge/Chrome 无头截图导出（不加依赖，中文走系统字体）：

- **用量报告**：总调用、活跃技能、四层来源分布、Top 技能条形图、全量明细表
- **安全报告**：扫描技能数、按严重度分布（CRITICAL→INFO）、状态分布、
  **风险分 Top 30 及每技能安装建议**（推荐安装 / 谨慎评估 / 不推荐——
  CRITICAL 发现或内容漂移 → 不推荐；HIGH/MEDIUM → 谨慎并附中文理由；
  无命中 → 推荐；另有三色汇总徽章）。默认**重扫**全部注册根以获得完整
  发现明细；`--fast` 直接用上次 audit 的基线分数（秒出）。
  所有标题与标签均为中英双语（EN · 中文）

常用：`python skill_report.py`（全出）、`--fast`、`--html-only`、`--out DIR`。

### 使用方式

**用量雷达三种跑法**：随取随用（默认，跑完即退）｜定时报告（计划任务/cron：
`python skill_monitor.py --top 20 >> usage-report.log`）｜实时 watch
（`--watch 60`，前台可选，Ctrl+C 即停，停了不丢数据）。

**安全闸门嵌进工作流**：装前 `scan <git-url> --strict` → 装后 `audit` 建基线 →
`npx skills update` 后 `audit --show-diff <skill>` 看 diff、`--accept-drift <skill>`
重建基线 → 每周定时 `audit --strict` 无人值守巡检（抓新装技能/漂移/黑名单命中，
退出码可接 CI）→ 随时 `skill_report.py` 出图。

典型节奏：**装前 scan、每周 audit --strict、accept-drift 只在亲眼看 diff 之后、
想要一图流就跑 skill_report.py**。

### 装前网关：`add`（v2.2）

`python skill_guard.py add [--block] <npx skills add 的参数...>` 把住每一次
安装：浅克隆安装源 → 跑完整三层引擎 → 打印报告与三分法安装判定
（推荐/谨慎/不推荐）→ 携原参数转调 `npx skills add`（退出码透传）。
安装成功后自动为新技能建基线（audit 增量）。`--block`（首次使用即持久化到
config.yaml 的 `consent.add_block`）对不推荐判定的安装以退出码 1 拒绝——
非交互、fail-closed，与 `scan --strict` 同一口径。克隆/扫描失败则高声告警、
fail-open 放行。一行加速器：

    # bash
    alias skills='python /path/to/skill_guard.py add -- skills'
    # PowerShell
    function skills { python F:\path\to\skill_guard.py add -- skills @args }

### SkillRadar 托盘（v2.2，Windows + macOS）

可选的托盘守护进程实时监听所有已登记的技能根目录（Windows 用纯 ctypes 的
ReadDirectoryChangesW，macOS 用 FSEvents——无守护框架、核心零新增依赖），
并对技能池变化作出反应：新技能或内容漂移 → 气泡通知 + 托盘角标；阻断模式下
CRITICAL 判定还会隔离技能目录（opt-in，移入
`~/.skill-radar/quarantine/` 并留 RESTORE.txt 说明）。内置界面（pywebview，
离线单文件 HTML）提供 总览 / 安全 / 用量 / 设置 四屏。构建方式：

    pip install -r requirements-gui.txt
    python build_tray.py          # Windows: dist/SkillRadarTray.exe（onefile）
                                   # macOS: dist/SkillRadarTray.app + .dmg

事件监听与 `audit --watch` 是互补的两层（规格 §4）：监听 = 秒级发现 + 告警；
`--watch` 轮询 = 无图形环境下的深度巡检。托盘客户端的 GUI 是可选层——
五个核心 .py 文件保持纯标准库。

macOS 说明：产物未签名/未公证——首次启动请右键应用选「打开」
（roadmap：签名与公证）。

### 安全闸门细节

扫描技能目录内**全部文本文件**（含附带脚本，不只 SKILL.md），8 类风险：
凭证窃取（THEFT）、恶意执行（EXEC）、持久化（PERSIST）、数据外发（EXFIL）、
提示注入（INJ）、配置滥用（ABUSE）、话术欺骗（DECEP）、来源伪装（SUPPLY）——
三层引擎：模式匹配 / 跨文件 source→sink 配对 / 熵+隐藏字符+多层解码重扫。
确定性、离线、纯标准库，**永不执行被扫内容**。

规则集与 IOC 黑名单在 [`rules/`](rules/)，可分享、可逐条覆盖。

**定位——互补不替代**：Snyk 内嵌扫描管依赖生态，skill-radar 管 SKILL.md 目录
约定；LLM 审查 skill 语义深但不可复现，skill-radar 是可复现的离线第一道筛；
行为沙箱是运行时真值但重装备，skill-radar 是安装前后随手可跑的廉价闸门。
威胁模型、已知局限（SkillCloak 级语义规避不在 v2 范围）与授权门见
[docs/threat-model.md](docs/threat-model.md)。

### 注意

- `npx skills update` 会覆盖 SKILL.md，**更新后重跑 `apply_skill_markers.py`** 补回标记
- 标记层无法回填历史（从部署当天起计数）；精确层可回填
- "会话命中"每会话文件计 1 次；精确次数看精确层
- 路径全部从家目录解析；雷达状态在脚本旁，闸门数据在 `~/.skill-radar/`
- PNG 导出需要 Edge 或 Chrome；没有就用 `--html-only`
- Windows（NTFS）为主测平台；macOS/Linux 的精确+标记层可用，atime 层视文件系统而定

### 文件

| 文件 | 用途 |
|---|---|
| `apply_skill_markers.py` | 注入/移除通用标记（幂等） |
| `skill_monitor.py` | 📊 用量雷达：增量扫描 + 计数器 |
| `skill_guard.py` | 🛡️ 安全闸门：`scan` / `audit` / `discover` |
| `skill_report.py` | 🖼️ 报告生成器：两大仪器的 HTML + PNG |
| `rules/` | 默认规则集（8 类）+ IOC 黑名单种子 |
| `monitor.bat` | 双击看文本报告（Windows） |

## License

MIT
