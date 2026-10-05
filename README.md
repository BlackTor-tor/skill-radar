# skill-radar

**Local skill usage history, static security checks, and reports you can copy or download.**

- **Usage Radar** (`src/skill_monitor.py`) reads structured Codex, zcode, and Claude Code session logs, plus optional marker and access-time evidence.
- **Security Guard** (`src/skill_guard.py`) checks credential theft, risky execution, data exfiltration, prompt injection, and other supply-chain risks without executing scanned files.
- **SkillRadar Tray** provides **Overview / Security / Usage / Reports / Settings**, background collection, individual and batch review, and a recoverable quarantine.
- **Reports** include desktop **Markdown + offline HTML** check archives and CLI **HTML + PNG** summaries.

中文说明见下方 [中文文档](#中文文档)。

## Quick start

The CLI uses Python's standard library. Release workflows use Python 3.12; the optional desktop client requires `requirements-gui.txt`.

```bash
# Existing supported logs can be collected without injecting markers.
python src/skill_monitor.py
python src/skill_guard.py audit
python src/skill_report.py --html-only

# Run the optional client from the repository root.
python -m pip install -r requirements-gui.txt
python src/tray/app.py
```

CLI commands exit after a run unless `--watch` is requested. The client stays in the tray, watches registered skill directories, and collects history in the background. Closing the window hides it; the tray's Quit action stops it.

## Usage history

| Evidence | Supported sources | Meaning |
| --- | --- | --- |
| Codex structured calls | `~/.codex/sessions`, `archived_sessions`, and `CODEX_HOME` | Literal `SKILL.md` reads, including static `exec_command` commands inside `functions.exec` wrappers |
| zcode structured calls | `~/.zcode/cli/rollout` and `cli/agents`, or `ZCODE_HOME` | Skill invocations and literal skill-file reads in `response.toolCalls` and scheduled call records |
| Claude Code structured calls | `~/.claude/projects`, or `CLAUDE_CONFIG_DIR` | Skill-tool events and supported literal reads in `tool_use` records |
| Marker session hits | Other configured transcript sources, including Cursor | One marker hit per transcript file and skill |
| Access time (`atime`) | Installed skill documents | Coarse access evidence, dependent on filesystem policy |

Structured counts record **observed load commands, not successful execution or completion**. Catalogs, plans, searches, echoed names, and writes do not count as loads. Dynamic paths, deleted/missing logs, and unsupported schemas cannot be reliably reconstructed. Usage aggregates by skill name; security checks keep full paths separate. Structured calls, session hits, and access-time evidence have different granularity.

Usage provides **Idle skills / Invocation ranking** views, opening Idle skills by default. Invocation ranking is ordered by the most recent valid record first; total calls and skill name break ties, and entries without a valid time stay at the end. The installed inventory comes from registered skill roots and includes skills with no observed calls. Idle groups distinguish no recorded calls from previously called skills whose last valid call is more than 30 days old. Skills with incomplete history or missing/invalid call times show insufficient records; recently installed skills show an observation-period note. These groups describe retained supported logs, not proof that a skill has never been used. Same-name copies keep their full paths but share name-based history, and invocation totals count each name once. Complete lists and totals are not limited to the first 25 skills.

Security lists, details, and reports show **estimated installation time** from the `SKILL.md` filesystem creation time, and **updated time** from its modification time, separately from the check time. Copies, restores, or file recreation can change creation time. Platforms without a filesystem birth time show installation time as unknown; Unix metadata-change time is not substituted for creation time.

The first scan reads retained history, including records from before SkillRadar was installed. Later scans resume from saved offsets and deduplicate observed structured call identifiers. Large histories can take minutes.

### Desktop automatic and manual collection

The client starts a background scan of known agent log locations. The Usage page retains collected rows while scanning and shows progress, checked-file count, new records, completion time, and read errors. While the Usage page is open, an incremental pass is scheduled at most every 30 seconds; Refresh records requests a pass immediately when no scan is already running.

For other locations, select or enter an **absolute log directory or drive root** and scan; the client automatically detects supported Codex / zcode / Claude records. Selected roots persist. Whole-drive scanning is an explicit action; startup does not scan every disk. Collection reads JSONL, avoids recursive directory links, and skips `.git`, `node_modules`, `__pycache__`, the Recycle Bin, and system-volume directories. Agent logs are not modified and transcript bodies are not uploaded.

### CLI collection

```bash
python src/skill_monitor.py --top 20
python src/skill_monitor.py --json
python src/skill_monitor.py --watch 60

# Repeat --log-root for multiple additional directories.
python src/skill_monitor.py --log-root "F:\old-agent-logs" --log-source auto
python src/skill_monitor.py --log-root "F:\codex-logs" --log-source codex --json
python src/skill_monitor.py --log-root "F:\" --log-source auto
```

`--log-source` accepts `auto/codex/zcode/claude` and applies to extra roots. Roots must exist and be absolute directories. `--reset` clears counters/offsets and rebuilds from retained logs; it cannot recover deleted history. Task Scheduler/cron can run the normal command periodically.

### Optional markers

```bash
python src/apply_skill_markers.py           # modifies known SKILL.md copies
python src/apply_skill_markers.py --remove
```

Markers support agents without a structured parser and are not required for supported Codex/zcode/Claude records. The injector handles known stores, not every possible installation path. An idempotent `<!-- skill-marker:NAME -->` appended to a skill can appear in future logged reads. It cannot create evidence in old transcripts. Skill updates may remove markers; rerun the injector if needed. Injection/removal changes files and may trigger drift checks. Access-time evidence also depends on filesystem policy and is not an exact invocation count.

## Desktop security and review

The Security page separates the current check from the user's decision. Same-name skills in different directories retain separate rows and colored location badges; full paths identify actions. Location colors do not indicate severity.

The detail panel's **Open file location** button opens the selected installed skill folder in the system file manager. It verifies the full registered path, permits a declared root's ancestor alias (such as a relocated agent home), and opens the verified real directory. Missing folders and links introduced within the registered root are rejected. Opening does not change files or review decisions, and does not relax checks for trust or file moves.

**Needs attention** includes findings, failed/incomplete checks, or decisions awaiting review. Inspect rule, severity, file/line, original evidence, version, and coverage before choosing an action. This is a review work list, not a claim that every listed skill is malicious. Reviewed/trusted skills retain original warnings and can remain in it.

| Action | Effect |
| --- | --- |
| Recheck | Updates current findings, time, version, and coverage; does not approve the skill |
| Mark reviewed | Records that current findings were viewed; retains warnings and automatic protection |
| Trust current version | Creates an exception for the exact path and complete checked version; stops repeated reminders/automatic isolation for that version while retaining evidence |
| Revoke trust | Removes the current exception and rechecks |
| Quarantine | Moves installed files into the local quarantine with original path/evidence |
| Restore | Restores the original location and rechecks; never overwrites an existing folder and serious risks may cause isolation again |
| Restore and trust | Restores and explicitly trusts the complete current version while retaining warnings |

Trust binds original file bytes, skill identity, and effective policy. Content/policy changes invalidate older exceptions. An incomplete check cannot create complete-version trust. Baseline/drift state, check completeness, and manual review are separate; rebuilding a baseline is not approval.

For a complete, trusted result, the single primary badge includes the decision and highest original severity: **Trusted (serious/high/medium/low risk, information, or no known risks found)**. Original scores and severity colors remain visible; advice states that the skill is trusted and original check details remain. Incomplete/error results keep the coverage/failure badge and make no primary trust claim. A failed trust attempt does not save a trusted decision. Policy changes require rechecking and explicitly deciding whether to trust the new complete version.

Individual and batch actions share this model. Search/filter, select rows or all current filtered results, then review full paths and effects before confirmation. Batches run in the background and show progress plus per-item success/failure/skipped results. Changed versions are skipped for decisions and file moves. Recheck refreshes an active registered path even if its selected result is stale. A **completed attempt can still have incomplete coverage** when a limit/read issue remains.

The last batch and quarantine records persist. Reopen results and retry only failed items. Restore checks the current isolated contents. Interrupted jobs and partial copy/metadata failures remain visible for follow-up.

### Coverage and incomplete checks

Text rules support **2 MiB per file**; original SHA-256 and IOC/hash checking support **8 MiB per file**. Details/reports state text-file count, hashed-file count, asset count, omitted asset paths, excluded directories, and limits.

Recognized image/audio/font assets whose extension and format header agree retain original hash and IOC checks. Paths omitted from text analysis are explicit coverage notes; these assets alone do not make a check incomplete. Format recognition is **not digital-signature verification, malware scanning, or proof that asset contents are safe**.

Unreadable files, links/reparse points, unrecognized binary/executable/archive content, size limits, decode limits, and changes during checking remain incomplete reasons. Decoding deduplicates candidates across bounded breadth-first transformations, so repeated ROT13 results do not exhaust the candidate budget. A genuine decode limit exposes candidate/byte limits and file/line evidence; inspect the source or split a large encoded payload before rechecking. Recheck does not eliminate a still-applicable limit. “No findings” means current rules found none within the stated scope, not guaranteed behavioral safety.

## Desktop check reports and Markdown

Generate on the Reports page to display/archive the **current saved check snapshot**. Generation does not rescan, accept drift, or change trust. Recheck first when updated evidence is needed, then generate a new report.

Reports lead with plain-language conclusions: review first, needs confirmation, cleanup candidates, keep for now, and already reviewed. Each suggestion explains why and what to do; the desktop can open current check details, the attention filter, idle skills, or the exact skill folder. Cleanup is manual: confirm the skill is no longer needed, back up its whole folder, move it to the Recycle Bin/Trash, restart the AI client, and restore if needed. Incomplete history, recent installations, ambiguous copies, or unresolved security issues are kept out of cleanup candidates.

The technical appendix preserves generation date/time, per-skill paths/check times, versions, baselines, original findings/scores, manual decisions, coverage/limits, and retained events. It is collapsed in the desktop and HTML export; Markdown keeps it at the end. Missing/incomplete evidence is labeled, and old events containing only `HH:MM:SS` are marked as lacking the original date. Existing reports remain readable; generate a new one to receive action suggestions.

- Browse archived reports and read the rendered Markdown.
- Copy reports, events, or individual check results as Markdown; source is also available for manual selection.
- Download `.md` or offline `.html` through the native save dialog.

Archive files live in `~/.skill-radar/reports/` as separate timestamped Markdown/HTML/JSON metadata files. New reports do not overwrite older ones; hashes are checked before reading/downloading. HTML escapes untrusted skill/log content, uses inline styles, and loads no remote scripts, fonts, or images.

## CLI reports: HTML + PNG

The CLI generator is separate from desktop archives. It uses inline CSS and installed Edge/Chrome for optional PNG; `--html-only` needs no browser.

```bash
python src/skill_report.py                  # usage + security summaries
python src/skill_report.py usage            # saved counters; no log collection
python src/skill_report.py guard            # rescans registered roots
python src/skill_report.py guard --fast     # saved baseline scores; no rescan
python src/skill_report.py --html-only
python src/skill_report.py --out DIR --top 30
```

Default output: `./skill-radar-reports/`. Usage includes Codex/zcode/Claude counts, marker hits, and access-time evidence. Security shows severity, baseline state, top risk scores, and install advice. Normal security mode rescans; `--fast` lacks full findings and labels that limit. CLI install advice does not record desktop review/trust.

## Security CLI workflow

```bash
# Remote Git sources need network + Git; local directories work offline.
python src/skill_guard.py scan https://github.com/someone/some-skills --strict
python src/skill_guard.py discover
python src/skill_guard.py audit
python src/skill_guard.py audit --show-diff <full-skill-path>
python src/skill_guard.py audit --accept-drift <full-skill-path>
python src/skill_guard.py audit --strict
python src/skill_guard.py audit --watch 60 --yes
python src/skill_guard.py discover --deep --yes
```

Use full paths for ambiguous names. Initial baselines are `baseline-unreviewed`, not approval. Deep skill discovery and usage-log scanning are different operations. `discover --deep` and `audit --watch` require first-use interactive consent or explicit `--yes`, saved in configuration.

### Pre-install gateway

```bash
python src/skill_guard.py add [--block] -- <npx skills add arguments...>
```

The gateway clones/checks a recognized source, prints findings/install advice, delegates to `npx skills add`, passes through its exit code, and baselines successful installs. `--block` persists `consent.add_block` and refuses a scanned “not recommended” verdict. Clone/scan failures warn and delegate to installation: the gateway is not a fail-closed guarantee for every error. Remote cloning/installation needs its normal network/runtime tools.

## Data, build, and verification

| Location | Contents |
| --- | --- |
| `~/.skill-radar/skill_usage_state.json` | Offsets, observed call IDs, counters checkpoint, selected history roots |
| `~/.skill-radar/skill_usage.json` | Default CLI/client exported counters |
| `~/.skill-radar/config.yaml`, `snapshots.json` | Roots, consent/settings, drift baselines |
| `~/.skill-radar/tray_state.json`, `review_decisions.json`, `processing.json` | Recent events/today counts, decisions, last batch/quarantine records |
| `~/.skill-radar/quarantine/` | Recoverable isolated files and restoration notes |
| `~/.skill-radar/reports/` | Desktop check-report archives |
| `./skill-radar-reports/` | Default CLI HTML/PNG output |

CLI usage storage supports `SKILL_RADAR_DATA_DIR`; the client uses the guard data directory. `usage_file` remains a legacy fallback for displaying external counters while automatic collection has no records; collected history takes precedence once available. Normal client use needs no custom counters path. Packaged executables no longer default to temporary extraction folders. Older script-adjacent counters are not the new default: retained supported logs can be rescanned, or an old counters file can supply the fallback.

```bash
python -m pip install -r requirements-gui.txt
python build_tray.py
# Windows: dist/SkillRadarTray.exe
# macOS: dist/SkillRadarTray.app and dist/SkillRadarTray.dmg

# Test dependency is not an application-core dependency.
python -m pip install pytest
python -m pytest tests -q
```

Desktop targets Windows/macOS with ReadDirectoryChangesW/FSEvents and a polling fallback. macOS distribution is unsigned/not notarized: if a downloaded app is blocked, attempt opening once and use Privacy & Security → Open Anyway. Locally built apps normally lack the downloaded quarantine attribute.

## Files and limits

| File | Purpose |
| --- | --- |
| `src/skill_monitor.py` | Structured-log collection and usage CLI |
| `src/skill_guard.py`, `src/skill_add.py` | Static checks, discovery, baselines, install gateway |
| `src/skill_report.py` | CLI HTML/PNG summaries |
| `src/apply_skill_markers.py` | Optional marker injection/removal |
| `src/tray/app.py`, `src/tray/daemon.py`, `src/tray/state.py` | Desktop bridge, live checks, events/state |
| `src/tray/usage.py` | Background historical usage collection |
| `src/tray/review.py`, `src/tray/processing.py` | Version-bound decisions, batches, quarantine/restore |
| `src/tray/`, `tray/web/` | Production client modules, report archive, and local bilingual UI |
| `rules/` | Rules and IOC blocklist |
| `monitor.bat`, `build_tray.py` | Windows text launcher and client build |

The static engine covers pattern matching, cross-file source→sink pairing, entropy/hidden characters, and bounded decoding. Categories include THEFT, EXEC, PERSIST, EXFIL, INJ, ABUSE, DECEP, SUPPLY, plus obfuscation/IOC findings. Rules are editable under [rules/](rules/). Static checks complement semantic review, dependency scans, and behavioral tests; semantic evasion/runtime behavior remain outside the engine. Local checks/history/reports work offline; remote Git sources and package installs use the network.

## 中文文档

**本地统计技能使用记录，检查静态风险，生成可复制、可下载的报告。**

`skill_monitor.py` 负责用量，`skill_guard.py` 负责静态检查，`skill_report.py` 生成命令行 HTML/PNG。托盘客户端提供 **总览 / 安全检查 / 使用统计 / 检查报告 / 设置** 五屏，包含历史补扫、单项/批量处理、可恢复隔离。命令行核心只用标准库；客户端需要 GUI 依赖，发布流水线使用 Python 3.12。

### 快速开始

```bash
python src/skill_monitor.py              # 已有历史日志，无需先加标记
python src/skill_guard.py audit          # 检查登记技能并保存漂移基线
python src/skill_report.py --html-only
python -m pip install -r requirements-gui.txt
python src/tray/app.py                                      # 在仓库根目录启动客户端
```

命令行默认跑完退出，`--watch` 才持续运行。客户端常驻托盘监听技能并收集用量；关闭窗口只是隐藏，托盘「退出」才停止。

### 使用统计与历史补扫

客户端启动后自动读取已知 agent 日志目录的已有历史，再按偏移增量更新：

| 来源 | 默认位置和证据 |
| --- | --- |
| Codex | `~/.codex/sessions`、`archived_sessions`，以及 `CODEX_HOME`；结构化调用中的明确 `SKILL.md` 读取，包括 `functions.exec` 包装内的静态读取命令 |
| zcode | `~/.zcode/cli/rollout`、`cli/agents`，或 `ZCODE_HOME`；Skill 工具/技能文件读取、模型 I/O 和 agent 事件调用记录 |
| Claude Code | `~/.claude/projects`，或 `CLAUDE_CONFIG_DIR`；`tool_use` 中 Skill 调用和支持的文件读取 |
| 标记层 | 其它配置会话来源，如 Cursor；每份会话文件、每个技能记一次命中 |
| atime | 本地文件访问时间，仅粗略证据，取决于文件系统策略 |

统计的是**观察到的技能加载命令**，不代表执行成功或任务完成。目录清单、计划、搜索、echo、写文件不算调用；动态路径、已删除日志、不支持格式无法可靠还原。用量按技能名聚合，安全检查按完整路径分开。结构化次数、会话命中、文件访问不是同一种精确计数。

使用统计提供 **「闲置技能 / 调用排行」** 两个视图，默认打开闲置技能。从登记技能目录补齐完整安装清单，包含零调用技能；分为「无调用记录」和「曾调用、近 30 天未调用」两组。有次数但缺少有效调用时间、日志未采集完或缺失时显示「记录不足」，新安装技能提示仍在观察期。这些结论仅对应已保留、已支持的日志，不能断言技能从未使用。同名副本保留完整路径并注明共享同名调用记录；调用合计按名称去重。完整列表和概览不再限制为前 25 项。

安全检查列表、详情和报告分别展示 **安装时间（估算）/ 更新时间 / 检查时间**。安装估算取 `SKILL.md` 的文件创建时间，更新时间取最后修改时间；复制、恢复、重建文件可能改变创建时间。不支持文件出生时间的平台显示安装时间未知，不将 Unix 元数据变更时间冒充创建时间。

首次可回填安装 SkillRadar 之前仍保留的日志，大量历史可能要几分钟。用量页保留已采集数据，显示进度、文件数、新记录、最近完成时间、读取错误；页面打开时最多每 30 秒安排增量扫描，「刷新记录」在没有扫描任务运行时立即补扫。

其它位置可在用量页**选择文件夹或输入绝对目录/盘符根目录**后扫描，客户端自动识别支持的 Codex/zcode/Claude 记录；选定根目录会保存。全盘扫描由用户发起，启动时不默认扫全部磁盘。只读 `.jsonl`，跳过 Git、node_modules、缓存、回收站及系统卷目录，不递归目录链接、不修改日志、不上传会话正文。

```bash
python src/skill_monitor.py --top 20
python src/skill_monitor.py --json
python src/skill_monitor.py --watch 60
python src/skill_monitor.py --log-root "F:\old-agent-logs" --log-source auto
python src/skill_monitor.py --log-root "F:\codex-logs" --log-source codex --json
python src/skill_monitor.py --log-root "F:\" --log-source auto
```

`--log-root` 可重复，`--log-source` 为 `auto/codex/zcode/claude` 并作用于新增目录；目录必须存在且为绝对路径。`--reset` 清空计数/偏移再从仍存在的日志重建，无法恢复已删除记录。

`src/apply_skill_markers.py` is an optional marker tool. It updates known SKILL.md files idempotently; use `--remove` to remove markers.

### 「需要关注」与人工处理

「需要关注」包含原始风险、检查失败/未完成、待人工确认技能，用于集中核对规则、位置、证据、版本、范围再处理；并不表示每个技能都恶意。**已查看、已信任保留原始警告**，仍有发现的条目可继续出现在关注列表。

同名不同路径技能各有彩色「位置」标识和完整路径。颜色区分位置，不表示风险，每次操作用完整路径为身份。

详情中的「打开所在文件位置」会在系统文件管理器打开选中的已安装技能目录，复核完整登记路径，支持已登记根上级的既有别名（如迁移后的 agent 家目录），打开核实后的真实目录；拒绝已移走的目录或登记根内部新增的链接重定向。不修改文件或决定，也不放宽信任、移动操作的检查。

| 操作 | 含义 |
| --- | --- |
| 重新检查 | 更新当前结果、时间、版本和覆盖，不自动批准/信任 |
| 标记已查看 | 保存已查看当前发现，保留风险和自动保护 |
| 信任当前版本 | 对完整路径和完整检查版本建立人工例外，停止该版本重复提醒/自动隔离，保留证据 |
| 撤销信任 | 移除例外并重查 |
| 隔离 | 移出安装目录，保存在本机隔离区及原位置/证据 |
| 恢复 | 恢复原位置再查，不覆盖现存目录，严重风险可再次隔离 |
| 恢复并信任 | 显式恢复并信任完整当前版本，保留原始风险 |

信任绑定原始字节、技能身份、有效检查策略，内容/策略变化后旧例外失效；检查未完成不能建立完整版本信任。漂移基线、检查结果、人工决定是不同信息，重建基线不等于批准。

完整检查且信任成功后，单一主标识按原始发现最高严重度显示「已信任（严重风险/高风险/中等风险/低风险/提示/未发现已知风险）」，保留原始风险分和严重度颜色；建议注明「已信任；保留原始检查详情」。未完成/失败组合仍显示覆盖或失败，不给主标识「已信任」结论；信任失败不保存决定。策略变化使旧例外失效后，先重查，再按当前完整版本显式决定是否信任。

支持搜索/筛选、多选、全选当前筛选项，先看完整路径清单及操作后果再确认。后台逐项处理，展示进度及成功/失败/跳过；人工决定/移动在版本变化时跳过。**重新检查允许结果版本已经过时的登记路径**，读取当前文件刷新。动作完成和检查覆盖完整是两件事：仍有超限/读取问题会明确保持「未完成」及原因。

最近批次/隔离记录保存，可重新查看、仅重试失败项。隔离区恢复前复核当前内容，不静默覆盖；中断、跨盘复制和记录异常显示逐项提示。

### 检查范围和未完成原因

文字规则上限单文件 **2 MiB**，原始 SHA-256 及 IOC/哈希上限 **8 MiB**。详情/报告显示文字文件、哈希文件、素材数量、未文字检查素材、排除目录、限制。

图片/音频/字体素材在**扩展名与格式头同时匹配**时保留原始哈希/IOC；未文字分析路径给覆盖说明，素材本身不导致未完成。格式识别不是数字签名验证或恶意软件扫描，不能保证素材内部安全。

不可读文件、链接/重解析点、未识别二进制、可执行/压缩包、文字/哈希超限、解码限额、检查时变化仍算未完成。解码以有界广度优先方式去重转换候选，ROT13 循环产生的重复结果不会耗尽候选预算；真实超限显示候选数/字节限制及文件行号证据，可查看源内容或拆分大型编码片段后重查。重查不能消除仍存在的限制。「检查正常/无发现」仅指当前规则在明确范围内未命中，不是运行安全保证。

### 检查报告与 Markdown

「检查报告」生成后在客户端展示并归档**生成时保存的检查快照**，不会隐式重扫、接受漂移、信任技能。需要新证据先重新检查，再生成新报告。

报告先给“一眼结论”，按优先处理、需要确认、可考虑清理、继续观察、你已处理列出理由与下一步。可直接打开当前检查详情、需要关注筛选、闲置技能和完整文件夹。清理仍由用户手动完成，教程包含核对功能、返回上级备份整个文件夹、移入回收站/废纸篓、重启验证和恢复。记录不完整、新安装、同名多份或存在未处理安全问题的技能不会直接进入清理候选。

完整使用统计、原始检查证据和事件放到末尾技术附录，客户端和 HTML 默认折叠；复制和下载保留全部内容。记录不足、同名共享记录和文件时间估算均保留说明；生成报告不触发历史日志补扫，旧报告保持原生成内容，需要新建议时重新生成。

带生成日期、时间、明确 UTC 偏移，以及技能完整路径、检查时间、版本、基线、原始风险/分数、人工决定、覆盖/限制、最近事件。缺失/未完成明确标注；旧事件只有时分秒会注明「原事件未保存日期」，不补造历史。

- 查看历史报告、Markdown 排版预览。
- 复制报告、事件、单项结果为 Markdown，也可展开源码手动选取。
- 系统保存对话框下载 `.md` 或离线 `.html`。

归档 `~/.skill-radar/reports/`，每份带时间戳独立 `.md/.html/.json`，不覆盖旧报告，读取/下载前校验哈希。HTML 转义不可信内容，用本地样式，不加载远程脚本/字体/图片。

### 命令行报告

```bash
python src/skill_report.py                  # 用量+安全，HTML 和可选 PNG
python src/skill_report.py usage            # 已保存计数，不采集新日志
python src/skill_report.py guard            # 重扫登记技能
python src/skill_report.py guard --fast     # 基线分数，不重扫
python src/skill_report.py --html-only
python src/skill_report.py --out DIR --top 30
```

输出默认 `./skill-radar-reports/`；用量包含 Codex/zcode/Claude、标记命中、atime；安全含严重度/基线/风险分/建议。安全默认重扫，`--fast` 缺完整发现并注明限制。CLI 建议不保存客户端人工决定。PNG 需 Edge/Chrome，HTML 不需截图浏览器。

### 安全命令行与装前网关

```bash
python src/skill_guard.py scan <本地目录或Git-URL> --strict
python src/skill_guard.py discover
python src/skill_guard.py audit
python src/skill_guard.py audit --show-diff <技能完整路径>
python src/skill_guard.py audit --accept-drift <技能完整路径>
python src/skill_guard.py audit --strict
python src/skill_guard.py audit --watch 60 --yes
python src/skill_guard.py discover --deep --yes
python src/skill_guard.py add [--block] -- <npx skills add 的参数...>
```

首次基线是 `baseline-unreviewed`，先看变更再显式接受；同名歧义用完整路径。全盘找技能和全盘找日志不同。深度发现/持续审计首次需交互或 `--yes` 并保存授权。

网关扫描已识别源，显示建议，转调 `npx skills add` 并透传退出码，成功后增量基线。`--block` 保存 `consent.add_block` 并拒绝已扫描的「不推荐」安装；克隆/扫描失败会告警继续委托，不是所有异常均拒绝的保证。远程 Git/安装仍需网络及工具。

### 数据、构建、测试

默认 `~/.skill-radar/`：`skill_usage_state.json` 保存偏移、调用去重、选定历史目录；`skill_usage.json` 导出计数；`config.yaml/snapshots.json` 保存设置/基线；`tray_state.json/review_decisions.json/processing.json` 保存最近事件、人工决定、批次/隔离；隔离文件 `quarantine/`，报告 `reports/`。

CLI 可用 `SKILL_RADAR_DATA_DIR` 改用量目录，客户端用 guard 数据目录。`usage_file` 是旧外部计数的兼容兜底：自动采集尚无记录时可显示，采集到历史后优先显示新记录；正常客户端无需自定义计数路径。打包程序不再默认写临时解包目录；旧脚本旁文件不再默认读取，可重扫保留日志或使用旧文件兜底。

```bash
python -m pip install -r requirements-gui.txt
python build_tray.py
# Windows: dist/SkillRadarTray.exe
# macOS: dist/SkillRadarTray.app 和 dist/SkillRadarTray.dmg
python -m pip install pytest
python -m pytest tests -q
```

客户端目标 Windows/macOS：ReadDirectoryChangesW/FSEvents，必要时轮询。macOS 下载产物未签名/公证，被阻止后先尝试打开，再到「隐私与安全性」选择「仍要打开」；本机构建通常无下载隔离标记。

### 规则和边界

三层包括模式匹配、跨文件 source→sink、熵/隐藏字符/有上限解码；类别 THEFT、EXEC、PERSIST、EXFIL、INJ、ABUSE、DECEP、SUPPLY，另有混淆/IOC。规则在 [rules/](rules/)，不执行被扫内容，本地检查/统计/报告可离线。

静态检查与人工语义、依赖扫描、行为测试互补；语义规避、运行行为仍在范围外。文件用途见上方 [Files and limits](#files-and-limits)。缺日志、格式未支持、大小/解码限制会影响结果，零计数/零风险分不代表完整安全。

## License

MIT
