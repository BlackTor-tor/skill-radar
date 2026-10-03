# skill-radar

**You installed 180 skills. How many does your agent actually use?**

skill-radar answers that question. It is a zero-dependency monitor that counts how
often your AI agents (zcode, Claude Code, Codex, Cursor, ...) actually load and invoke
the skills you installed — across every agent on the machine, not just one.

中文说明见下方 [中文文档](#中文文档)。

## Why

Every skill an agent knows adds its name + description to every session's context,
and skills get auto-triggered based on those descriptions. But once you pass a
hundred skills, nobody knows which ones ever fire. skill-radar gives you the real
numbers so you can prune dead weight and tame greedy trigger descriptions.

## How it works

Four counting layers, from most precise to most universal:

| Layer | Coverage | Granularity | How |
|---|---|---|---|
| **precise (zcode)** | zcode | exact invocations | parses `~/.zcode/cli/rollout/*.jsonl` `response.toolCalls` |
| **precise (claude)** | Claude Code | exact invocations | parses `~/.claude/projects/**/*.jsonl` `tool_use` events |
| **marker (universal)** | *any* agent — Codex, Cursor, anything that logs session content | one "session hit" per (transcript file, skill) | `apply_skill_markers.py` injects a unique `<!-- skill-marker:NAME -->` at the end of every SKILL.md copy; the monitor greps **all** agents' session logs for that string — no format parsing needed. If an agent loaded the skill content into context, the marker rode along into its transcript. |
| **atime (fallback)** | *any* tool, even ones without logs | coarse (may count antivirus/indexer reads) | watches each SKILL.md's last-access time; works only where the filesystem updates atime (NTFS yes, Linux relatime rarely) |

The marker layer is the key trick: a content marker by itself cannot detect
"was read" — but the marker travels with the skill content into whatever transcript
the agent writes, so *grepping for the marker string* works on every harness,
regardless of its log format.

## Quick start

Python 3.8+, no third-party packages.

```bash
# 1. inject usage markers into every SKILL.md under ~/.agents/skills,
#    ~/.codex/skills, ~/.claude/skills, ~/.cursor/skills, ~/.qoder-cn/skills
python apply_skill_markers.py

# 2. first run does a full historical scan (precise layers backfill history;
#    marker/atime layers start from today), later runs are incremental and fast
python skill_monitor.py

# top 30 report
python skill_monitor.py --top 30

# live mode: rescan every 60s and print deltas
python skill_monitor.py --watch 60

# raw counters as JSON
python skill_monitor.py --json

# wipe counters and rescan from scratch
python skill_monitor.py --reset
```

Windows users can double-click `monitor.bat` for the report.

### Sample report

```
========================================================================
Skill usage report   last scan: 2026-10-02T18:31:16
========================================================================

Skill                                 zcode  claude  session hits  atime   last used
wechatide-skill                          24       3             0      0   2026-09-30
systematic-debugging                     16       0             0      0   2026-09-29
using-superpowers                         0       5             0      0   2026-09-25
```

## Notes & caveats

- **Re-run `apply_skill_markers.py` after `npx skills update`** — updating skills
  overwrites SKILL.md and removes the markers.
- The marker layer cannot backfill history: transcripts written before the markers
  were injected don't contain them. The precise layers (zcode, Claude Code) do
  backfill history.
- "session hits" counts a skill once per transcript file, so replayed history inside
  one session never inflates the number — but multiple invocations inside one session
  count once. Use the precise layers for exact counts where available.
- All paths are resolved from your home directory; state lives next to the scripts
  (`skill_usage_state.json`), counters in `skill_usage.json`.
- Tested on Windows (NTFS). macOS/Linux work for the precise + marker layers;
  the atime layer depends on your filesystem's access-time policy.

## Files

| File | Purpose |
|---|---|
| `apply_skill_markers.py` | inject/remove the universal markers (idempotent) |
| `skill_monitor.py` | the monitor: incremental scan + report |
| `monitor.bat` | double-click report launcher (Windows) |

## Security module (v2): skill_guard.py

v2 adds a supply-chain guard for AI agent skills. A skill is any directory containing
`SKILL.md`; the guard scans **all** text files inside it (attached scripts included,
not just SKILL.md) for 8 categories of risk: credential theft (THEFT), malicious
execution (EXEC), persistence (PERSIST), data exfiltration (EXFIL), prompt injection
(INJ), agent-config abuse (ABUSE), social-engineering phrasing (DECEP), and
typosquat / source spoofing (SUPPLY). Deterministic, offline, pure stdlib.

```bash
# scan one skill directory (or git URL): human report, --json for CI;
# --strict exits 1 on CRITICAL findings
python skill_guard.py scan <path|git-url> [--strict] [--json]

# audit every registered root: SHA-256 baseline, drift diff, trust downgrades
python skill_guard.py audit [--strict] [--watch N] [--show-diff <skill>] [--accept-drift <skill>] [--json]

# discover skill roots on this machine; --deep is full-disk, consent-gated
python skill_guard.py discover [--deep] [--yes]
```

The built-in ruleset (8 categories) and the IOC blocklist seed live in
[`rules/`](rules/) — shareable and overridable per rule.

**Positioning — complementary, not a replacement:**

- **Snyk (embedded scanning)** covers the package/dependency ecosystem; skill-radar covers the SKILL.md directory convention.
- **LLM review skills** give semantic depth but are non-deterministic; skill-radar is a reproducible, offline first-pass filter.
- **Behavioral sandboxes** give runtime ground truth at high cost; skill-radar is the cheap deterministic gate to run before and after install.

Threat model, known limits (SkillCloak-class semantic evasion is out of scope for v2),
and consent gates for `discover --deep` / `audit --watch`:
[docs/threat-model.md](docs/threat-model.md).

## License

MIT

---

## 中文文档

**你装了 180 个技能，agent 真正用过几个？**

skill-radar 是一个零依赖的技能调用监控器，统计你的 AI agent（zcode、Claude Code、
Codex、Cursor……）实际加载和调用每个技能的次数——覆盖机器上的所有 agent，而不是只看一家。

**为什么需要**：每个技能的名字和描述都会进入每个会话的上下文，agent 靠描述决定要不要
自动触发它。技能过百之后没人知道哪些真的在用——skill-radar 给你真实数据，方便清理
从不调用的死重、约束写得太贪婪的触发描述。

**四层计数**：

1. **zcode 精确层**：解析 zcode 的模型 I/O 日志，按 `toolCalls` 精确计数，可回填历史
2. **Claude Code 精确层**：解析 `~/.claude/projects` 的 `tool_use` 事件，可回填历史
3. **标记通用层**：给每个 SKILL.md 末尾注入唯一标记，然后 grep 所有 agent 的会话日志。
   内容被载入上下文标记就必然留痕，不依赖任何日志格式——这是跨 agent 通用的关键
4. **atime 兜底层**：文件访问时间变化 = 被某工具读过（NTFS 可靠；Linux relatime 下基本无效）

**快速开始**：

```bash
python apply_skill_markers.py   # 注入标记（幂等）
python skill_monitor.py         # 首次全量扫描，之后增量秒级
python skill_monitor.py --top 20
```

**注意**：`npx skills update` 更新技能会覆盖 SKILL.md，更新后重跑
`apply_skill_markers.py` 补回标记；标记层无法回填历史，从部署当天开始计数。

**安全模块（v2）**：`skill_guard.py` 为技能供应链加一道闸——扫描技能目录
（含 SKILL.md 的目录，含附带脚本）内的 8 类风险：凭证窃取、恶意执行、持久化、
数据外发、提示注入、配置滥用、话术欺骗、来源伪装/typosquat。确定性、离线、纯标准库。

```bash
python skill_guard.py scan <path|git-url> [--strict] [--json]                        # 扫单个技能目录或 git URL；--strict 有 CRITICAL 即退出码 1
python skill_guard.py audit [--strict] [--watch N] [--show-diff <skill>] [--accept-drift <skill>] [--json]   # 全注册表：基线/漂移/信任降级
python skill_guard.py discover [--deep] [--yes]                                      # 发现本机技能根；--deep 全盘、需显式授权
```

**定位——互补不替代**：

- Snyk 内嵌扫描管依赖生态；skill-radar 管 SKILL.md 目录约定
- LLM 审查 skill 语义深审但不可复现；skill-radar 是可复现、可离线的第一道筛
- 行为沙箱是运行时真值但重装备；skill-radar 是安装前后随手可跑的廉价闸门

内置规则集（8 类）与 IOC 黑名单在 [`rules/`](rules/)；威胁模型、已知局限与
授权门见 [docs/threat-model.md](docs/threat-model.md)。
