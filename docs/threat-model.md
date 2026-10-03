# skill-radar guard 威胁模型

## 防护对象
AI agent 技能（SKILL.md 目录约定）在安装前与安装后的供应链风险：
凭证窃取（THEFT）、恶意执行（EXEC）、持久化（PERSIST）、数据外发（EXFIL）、
提示注入（INJ）、agent 配置滥用（ABUSE）、社会工程话术（DECEP）、
来源伪装与 typosquat（SUPPLY）。

## 四原则
1. 确定性离线第一道筛：正则/共现/熵/多层解码，纯标准库，可复现，可离线。
   与 Snyk（内嵌扫描）、LLM 审查 skill、行为沙箱互补，不替代。
2. 已知局限——SkillCloak 类语义规避（thehackernews.com 2026-07）：静态引擎
   无法保证检测语义级伪装。对抗路线（沙箱 / LLM 深审）在 roadmap，v2 不承诺。
3. 永不执行被扫描内容：解码例程设 5 层嵌套上限 / 单文件 2MB / 空字节跳过，
   防 zip bomb 与路径穿越；不解析执行规则 YAML 之外的任何代码。
4. 误报优于漏报；报告给证据与上下文，判决权在用户。

## 信任边界
- 本工具读取的输入 = 技能目录文件内容。其中 SKILL.md 是攻击者可控文本，
  解码例程对它做的一切操作都是"只读字符串变换"。
- 被扫内容永不变成 scan target：解码、重扫全部在内存字符串上进行，
  不落盘、不解析为路径、不升级为新的扫描对象——无二次落地，无自扩散。
- rules/ 规则文件是可信输入：`re.compile` 通过 ≠ 无回溯——劣质或恶意规则
  正则可造成灾难性回溯（ReDoS）或漏报。规则文件来源需可信（本仓库内置集
  或用户显式放置），不要加载来历不明的规则 YAML。
- 用户数据（config/snapshots）只写 ~/.skill-radar/，不外发任何数据。
- 授权门：discover --deep 与 audit --watch 需显式同意（完整输入 yes 或 --yes）。

## 非目标（v2）
- 运行时行为监控（v3，与 usage monitor 集成）
- 跨技能 Toxic Flow 的动态判定（v2 只做技能内共现 + 共存计数）
- SkillCloak 级对抗（需沙箱/LLM）
