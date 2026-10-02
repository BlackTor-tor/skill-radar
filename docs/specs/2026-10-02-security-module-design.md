# skill-radar v2 安全模块设计（scan + audit）

状态：待审查 · 日期：2026-10-02 · 前置讨论：定位（开源安全模块）、范围（scan+audit 双核）、引擎（A 静态三层 + C 信任链/漂移）、安装时机制（六项推荐）、网络调研（SkillCloak / mcp-scan / skill-security-reviewer / Snyk ToxicSkills / OWASP AST02）

## 1. 核心抽象：skill 为中心，零 agent 概念

安全模块不感知任何 agent（zcode/claude/codex/cursor 等词不出现在引擎代码中）。唯一依赖的生态契约：

```
Skill  := 含 SKILL.md 的目录
Root   := 含一个或多个 Skill 的目录（注册表管理）
scan() := 输入一个文件系统路径（git URL 先浅克隆落地临时目录）→ 风险报告
```

- agent 名字只允许出现在两处：v1 用量监控层（`skill_monitor.py`，天然依赖各家日志格式）与默认 roots 注册表（标注为"便利默认值，非穷尽"，用户可增删）
- `audit` = 遍历注册表全部 roots，逐 Skill 执行 scan + 快照比对，与"哪个工具装的"无关

## 2. 仓库与数据布局

```
skill-radar/
├── skill_monitor.py / apply_skill_markers.py   # v1，不动
├── skill_guard.py        # 新：scan / audit / discover 三个子命令（单文件，零依赖）
├── rules/
│   ├── defaults.yaml     # 内置规则集（8 类，可分享/覆盖/禁用单条）
│   └── blocklist.yaml    # IOC 黑名单种子（ClawHavoc 341 技能、skills.sh 事件公开 IOC）
├── docs/threat-model.md  # 威胁模型（见 §8）
├── tests/                # pytest：每条规则正反样本 + ClawHavoc 回归集
└── README.md             # 定位声明

~/.skill-radar/
├── config.yaml           # roots 注册表、consent 状态、trust.list
└── snapshots.json        # 技能文件 SHA-256 基线 + 状态
```

## 3. 三层检测引擎

扫描对象 = Skill 目录内全部文本文件（不只 SKILL.md——ClawHavoc 型攻击藏身附带脚本）。文本判定：空字节嗅探排除二进制、单文件大小上限默认 2MB、解码嵌套上限 5 层（实现计划细化）。永不执行任何被扫内容。

| 层 | 内容 | 说明 |
|---|---|---|
| L1 模式 | 8 类规则（THEFT/EXEC/PERSIST/EXFIL/INJ/ABUSE/DECEP/SUPPLY），正则 + 组合模式 | 组合优先：敏感路径+网络外发 > 单关键词。INJ/DECEP 权重低于 EXEC/EXFIL（高频低危 vs 低频高危） |
| L2 数据流启发 | 技能内**跨文件** source→sink 共现配对（如 SKILL.md 指示读 `.env` + 同目录脚本 `curl POST`） | 不做真污点分析，共现足够；跨技能运行时流分析（mcp-scan TFA 完整形态）留 v3 |
| L3 混淆 | 熵值、base64/hex/rot13/单字节 XOR 解码后重扫 L1/L2、零宽字符/homoglyph、字符串拆分 | **解码内容重跑全部规则**；混淆本身计 HIGH（解不开≠无罪） |

规则 YAML schema：

```yaml
- id: SR-EXFIL-002
  category: EXFIL
  severity: CRITICAL
  description: 敏感文件内容外发
  file_globs: ["**/*"]
  pattern_source: ["(~|\\.)?(env|ssh|aws|kube)", "credentials", "id_rsa"]
  pattern_sink: ["curl .*-d", "Invoke-WebRequest .*-Body", "requests\\.post", "nc .*-e"]
  pairing: cross_file        # source 与 sink 跨文件共现即命中
  refs: ["OWASP-AST02", "CWE-200"]
```

报告上下文附加一行：技能与其他已安装的网络外发模式工具共存数量（v2 边界，不做运行时关联）。

## 4. audit、快照与信任链

技能状态机：

```
unscanned ──scan──▶ scanned ──内容哈希变化──▶ drifted
首次见到（未 scan）──▶ baseline-unreviewed（建基线但未经人工审查）
```

- 快照 = Skill 目录全部文本文件的 SHA-256 清单，存 `~/.skill-radar/snapshots.json`
- 首次 audit 对存量技能全量建基线（覆盖面优先），状态标 `baseline-unreviewed`；用户可随时对单个技能跑 `scan` 升级
- `trust.list` 三级：owner > repo > 内容 hash，命中可降级告警（写明风险自担）
- 合法更新（`npx skills update`）会触发 DRIFT——提供 `audit --show-diff <skill>` 查看变化、`audit --accept-drift <skill>` 重建基线；没有这两个命令工具会被合法更新噪音淹没

## 5. 发现模型与授权

三层发现（不做定时全盘扫描）：

1. **注册表**：config 内置 6-8 个标准根（便利默认值）
2. **`discover`**：家目录限深 4 层 + 当前项目全深度，只认"目录含 SKILL.md"，排除 node_modules/.git/AppData/Library；新根需用户确认后写入注册表
3. **`discover --deep`**：全盘 opt-in，需交互式完整输入 `yes`（写入 config.consent，删行即撤销；非 TTY 必须显式 `--yes`）

`audit --watch N` 可选轮询模式（纯标准库，复用扫描器，用户自行挂计划任务），首次启用与 `--deep` 同一 consent 流程。没有 skills CLI 安装钩子可用（已核实），"装后尽快发现"（watcher/定时 audit）替代"装时拦截"。

## 6. CLI 全表

```
python skill_guard.py scan <path|git-url> [--strict] [--json]
python skill_guard.py audit [--strict] [--watch N] [--show-diff <skill>] [--accept-drift <skill>] [--json]
python skill_guard.py discover [--deep] [--yes]
```

输出：风险等级 CRITICAL/HIGH/MEDIUM/LOW/INFO + 文件:行 + 上下文摘录 + 行动建议（inspect / quarantine 到项目级 / remove）。`--strict` 时存在 CRITICAL 则退出码 1（CI 可用）。调校哲学：误报优先于漏报，用户按 severity 过滤。

## 7. 测试策略

- 每条规则至少一正一反 pytest 样本
- ClawHavoc 公开 IOC 可还原的恶意样本做回归集
- L3 解码例程的健壮性测试：zip bomb、超长嵌套、路径穿越、二进制垃圾
- 引擎冒烟：对 skill-radar 自身仓库跑 scan 应零 CRITICAL（自检 dogfooding）

## 8. 威胁模型四原则（docs/threat-model.md 展开）

1. **确定性离线第一道筛**：与 Snyk 内嵌扫描、LLM 审查 skill、行为沙箱（97% 检出但重装备）互补，不替代
2. **不能检测 SkillCloak 级语义规避**——需要沙箱/LLM，roadmap 预留 hook（v2 不实现，避免过度承诺）
3. **永不执行被扫内容**：解码例程设嵌套上限与膨胀上限，防 zip bomb；不解析执行 YAML 外的任何代码
4. **误报优于漏报**；报告给证据与上下文，判决权在用户

## 9. 与 v1 的关系与 Roadmap

- v1 `skill_monitor.py`（用量）回答"哪些技能在被用"；v2 `skill_guard.py`（安全）回答"哪些技能危险"。audit 报告末尾一行联动提示：**高危 + 零使用 = 优先删除候选**。不做深度耦合
- v2.1：质量评分卡（触发条件显式度等启发式）；v2.1.x：L3 单字节 XOR 解码层（含 255-key 候选爆炸防护与专属 TDD，2026-10-02 审查裁定的跟踪项）；v2.2：IOC live feed；v3：跨技能运行时 Toxic Flow（与 monitor 集成）、LLM 深审 hook

## 调研来源

- SkillCloak 绕过静态扫描：thehackernews.com（2026-07）
- Snyk ToxicSkills（36% 阳性 / 1467 恶意载荷）：snyk.io/blog
- skill-security-reviewer（熵 + 多层解码 + 8 类 53 检查）：github.com/zast-ai
- MCP-Scan（模式 + Toxic Flow Analysis + 代理监控）：invariantlabs.ai
- ClawHavoc（341 恶意技能投毒 OpenClaw 投放 AMOS）：antiy.net / trellix.com / unit42.paloaltonetworks.com
- skills.sh 凭证窃取（170 万安装）：daily.dev / Zenity Labs
- OWASP Agentic Skills Top 10 AST02：owasp.org
- MalSkills 神经符号框架：arxiv.org/html/2603.27204
