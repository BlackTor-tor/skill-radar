/* 离线语言资源。只翻译明确的界面文案，不改技能名、路径或代码。 */
(function () {
  'use strict';
  const COPY = {
    'SkillRadar · 技能守护':'SkillRadar · Skill checks',
    '技能守护工作台':'Your skill workspace', '工作区':'Workspace', '主导航':'Main navigation',
    '概览':'Overview', '安全检查':'Security', '使用统计':'Usage', '用量统计':'Usage', '设置':'Settings',
    '正在连接客户端':'Connecting to the app', '文件有变化时自动检查':'Checks when files change',
    '检查记录保存在本机':'Records stay on this device', '本机工作区':'This device', '等待连接':'Waiting for connection',
    '暂时无法读取客户端状态':'Unable to read app status', '正在重试，请稍候。':'Trying again. Please wait.', '重试':'Retry',
    '技能守护状态与最近的检查记录。':'See automatic checks and recent activity.', '重新检查':'Check again',
    '当前守护状态':'Automatic checks', '正在读取状态':'Loading status', '连接客户端后显示真实的守护状态。':'Connect to the app to see its current status.',
    '暂停守护':'Pause checks', '恢复守护':'Resume checks', '最近事件':'Recent activity', '等待检查记录':'Waiting for records',
    '最近事件列表，可滚动':'Recent activity, scrollable', '最近检查事件':'Recent checks', '时间':'Time', '类型':'Type', '事件内容':'What happened',
    '正在读取检查记录':'Loading check records', '请稍候。':'Please wait.',
    '可打开的事件支持点击，或使用 Enter / 空格查看详情。':'Click an available activity, or press Enter or Space to open its details.',
    '规则检查用于发现已知风险，未命中规则不代表绝对安全。使用前仍需核对技能来源与内容。':'Checks look for known warning signs. Review where a skill comes from and what it does before using it.',
    '先看需要关注的技能，再核对文件与代码。':'Find skills that need attention, then review their files and content.',
    '搜索技能或文件位置':'Search skills or file locations', '筛选检查结果':'Filter check results', '全部技能':'All skills', '需要关注':'Needs attention',
    '技能安全检查结果':'Skill check results', '技能 / 文件位置':'Skill / file location', '风险分':'Risk score', '检查状态':'Check status', '处理建议':'Suggested next step',
    '正在读取检查结果':'Loading check results', '检查结果来自客户端最近保存的记录。':'Results come from the latest records saved by the app.',
    '风险分用于辅助判断；内容有变化不等于有恶意。请打开详情，结合来源、文件位置与代码确认。':'Scores help you decide what to review. A content change may be a normal update. Open details and check its source and content.',
    '查看已记录的技能调用与来源。':'See recorded skill uses and where they came from.', '刷新记录':'Refresh', '技能调用排行':'Most used skills',
    '按已记录的调用次数排序':'Sorted by recorded uses', '正在读取调用记录':'Loading usage records',
    '仅展示调用记录中的前 25 项。合计为这些技能的记录之和，不含未被记录的调用；扫描事件不计入调用次数。':'Shows up to 25 skills. Totals cover these records only; file checks do not count as skill uses.',
    '管理守护方式、技能文件夹与本机数据。':'Choose how checks work and manage skill folders and local records.',
    '更改后自动保存':'Changes save automatically', '守护方式':'How checks work', '作用于后续检查':'Applies to future checks',
    '高风险处理':'Serious risk handling',
    '开启后，将命中严重风险的技能标记并提醒。内容变化仅作提示；此设置本身不会阻止 AI 使用技能。':'Marks skills with serious warning signs and notifies you. Content changes are reported for review. This setting alone does not stop AI tools from using a skill.',
    '隔离严重风险技能':'Move serious-risk skills aside',
    '需同时开启高风险处理；检测到严重风险时尝试将技能移出原文件夹，保存到本机隔离文件夹。失败时会提醒。':'Requires serious risk handling. Tries to move a flagged skill out of its original folder into a separate local folder, and reports if the move fails.',
    '技能文件夹':'Skill folders', '等待读取':'Waiting for records', '正在读取技能文件夹':'Loading skill folders', '添加包含技能的文件夹':'Add a folder containing skills',
    '例如 C:\\Users\\你的用户名\\.agents\\skills':'For example C:\\Users\\YourName\\.agents\\skills', '添加':'Add',
    '请填写完整路径；移除只停止监听，不删除文件。':'Enter the full folder location. Removing it from this list stops checks and keeps your files.',
    '本机数据':'Local records', '存放配置、检查记录和隔离文件；~ 表示当前用户的主文件夹。':'Stores settings, check records and moved files. ~ means your user folder.', '打开文件夹':'Open folder',
    '技能检查详情':'Skill check details', '关闭技能详情':'Close skill details', '关闭':'Close',
    '确认更新记录前，请先查看变化。确认只更新比较记录，不会忽略现有风险提示。':'Review the changes first. Confirming updates the comparison record and keeps any existing risk warnings.',
    '查看内容变更':'View changes', '确认本次变化':'Confirm update',
    '界面语言':'Language', '语言已保存':'Language saved', '语言保存失败：':'Could not save language: ',
    '严重风险':'Serious risk', '高风险':'Higher risk', '中等风险':'Needs review', '低风险':'Minor warning', '提示':'Note', '风险提示':'Warning',
    '新技能待确认':'New skill to review', '内容有变化':'Content changed', '新增技能':'New skill', '高风险提醒':'Serious risk warning', '隔离处理':'Moved aside',
    '处理失败':'Action failed', '已跳过':'Skipped', '发现严重风险':'Serious risk found', '已检查':'Checked', '已重新检查':'Checked again', '尚未检查':'Not checked yet',
    '已确认来源':'Source reviewed', '已确认变化':'Update confirmed', '已确认':'Reviewed', '状态待确认':'Status needs review',
    '暂勿使用':'Avoid using for now', '核对内容变化':'Review the changes', '确认来源与内容':'Review source and content', '检查风险详情':'Review warnings', '使用前核对来源':'Review source before use',
    '其他事件':'Other activity', '查看':'View ', '的检查详情':' check details', '时间未记录':'Time unavailable',
    '没有符合条件的技能':'No matching skills', '暂无检查记录':'No check records yet', '换个关键词，或查看全部技能。':'Try another search or show all skills.',
    '添加技能文件夹后，客户端会在文件变化时检查；也可点击重新检查。':'Add a skill folder to check changes automatically, or choose Check again.',
    '显示 ':'Showing ', ' 个技能 · 点击行查看完整检查详情':' skills · Open a row for details',
    '守护已暂停':'Checks paused', '有技能需要你关注':'Some skills need your attention', '技能守护运行中':'Automatic checks are running', '守护状态待确认':'Check status unavailable',
    '暂停期间的文件变化会被跳过；恢复后可重新检查。':'Changes made while paused are skipped. Run a check after resuming.',
    '请查看检查详情；是否已隔离以事件记录为准。':'Review the details. Activity records show whether a skill was moved aside.',
    '查看最近事件，核对新增技能、风险提示与内容变化。':'Review recent activity for new skills, warnings and content changes.',
    '监测技能文件夹变化，检查结果会记录在这里。':'Checks skill folders when files change and saves the results here.',
    '有待关注事项':'Needs attention', '守护运行中':'Checks are running', '今日新增技能':'New skills today', '今日内容有变化':'Changed today', '今日高风险提醒':'Risk warnings today',
    '暂无检查事件':'No recent activity', '文件有变化时会留下记录；也可以主动重新检查。':'File changes create activity records. You can also run a check now.',
    '最近 ':'Latest ', ' 条':' records', '已记录技能':'Recorded skills', '有严重风险':'Serious warnings',
    '已同步 ':'Updated ', '状态同步中断':'Status updates stopped', '未连接客户端':'App disconnected', '显示上次记录':'Showing saved records', '未连接':'Disconnected',
    '状态同步失败':'Unable to update status', '尚未连接 SkillRadar 客户端':'Not connected to SkillRadar',
    '保留上次读取的记录，当前守护状态未确认。正在重试。':'Showing the last records. Current check status is unknown; trying again.',
    '请在 SkillRadar 桌面窗口中使用；本页面不会生成模拟检查结果。':'Open this page in the SkillRadar desktop app to see your real check results.',
    '暂时无法读取状态，正在重试。':'Unable to read status. Trying again.', '等待客户端连接':'Waiting for the app',
    '连接后显示真实的守护状态与检查记录。':'Connect to see the current check status and saved records.', '尚未读取检查记录':'Check records are unavailable',
    '请先连接 SkillRadar 客户端。':'Connect to the SkillRadar app first.', '尚未读取检查结果':'Check results are unavailable', '尚未读取技能文件夹':'Skill folders are unavailable',
    '连接后显示已登记的文件夹。':'Connect to see your saved folders.', '尚未读取':'Not loaded', '状态同步失败。当前详情为上次读取的记录。':'Unable to update status. These details are from the last saved records.',
    ' 个文件夹':' folders', '内置':'Included', '停止监听 ':'Stop checking ', '移除':'Remove', '尚未添加技能文件夹':'No skill folders added',
    '添加一个包含技能的文件夹，开始监听变化。':'Add a folder containing skills to check its changes.', '已开启':' enabled', '已关闭':' disabled', '设置失败：':'Could not save settings: ',
    '已展示技能的调用合计':'Recorded uses shown', '有调用记录的技能':'Skills with recorded uses', '最近记录日期':'Latest record', '调用记录读取失败':'Unable to load usage', '暂无调用记录':'No usage records yet',
    '当前客户端未读取到记录。已有调用记录需要由本机的调用监测功能提供。':'No usage records are available. Usage is collected by the local activity tracker.',
    '次':' uses', '个':' skills', '未记录':'Not recorded', ' 已记录 ':' has ', ' 次调用':' recorded uses', '各技能调用明细':'Usage by skill', '技能':'Skill', '合计':'Total', '会话标记':'Session records', '最近记录':'Last recorded',
    '检查记录已更新':'Check records changed', '该技能已不在当前记录中':'This skill is no longer in the current records', '关闭详情后刷新检查结果。':'Close details and refresh the checks.',
    '规则 ':'Check ', '未提供编号':'No reference', '文件位置未提供':'File location unavailable', ' · 第 ':' · Line ', ' 行':'', '该条检查未提供代码片段。':'No content excerpt was provided.',
    '未命中当前风险规则':'No known warning signs found', '本次检查未命中已知规则，不代表绝对安全。请继续核对技能来源、文件内容和所需权限。':'No known warning signs were found. Review its source, content and requested permissions before using it.',
    '内容变更':'Content changes', '文件位置与代码保留客户端原始输出。':'File locations and content are shown as provided by the app.', '客户端未返回变更内容。':'The app returned no change details.',
    '风险分 ':'Risk score ', '技能文件夹 · 完整位置':'Skill folder · Full location', '请根据下面的检查结果核对技能内容。':'Use the check details below to review this skill.', '规则检查结果':'Check details', ' 项提示':' warnings',
    '确认会更新内容检查记录；不会忽略现有风险提示。':'Confirming updates the comparison record and keeps existing warnings.',
    '检查只覆盖当前规则；使用前仍需确认来源、内容与权限。':'Checks cover known warning signs. Review the source, content and permissions before use.',
    '处理中，请稍候。':' is in progress. Please wait.', '完成。':' completed.', '完成':' completed', '处理失败：':'Action failed: ', '失败：':' failed: ', '处理中…':'Working…',
    '已安排 ':'Queued ', ' 个技能重新检查；结果稍后更新':' skills for checking. Results will update shortly.', '操作失败：':'Action failed: ',
    '已打开本机数据文件夹':'Opened local records folder', '守护已恢复':'Checks resumed', '高风险处理':'Serious risk handling',
    '请填写完整的文件夹路径，例如 C:\\Users\\你的用户名\\.agents\\skills':'Enter the full folder location, for example C:\\Users\\YourName\\.agents\\skills',
    '已添加技能文件夹':'Skill folder added', '添加失败：':'Could not add folder: ', '已停止监听该文件夹，文件保留':'Stopped checking this folder. Files were kept.',
    '客户端未返回成功结果':'The app did not confirm success', '未连接客户端，请在 SkillRadar 桌面窗口中操作':'App disconnected. Open the SkillRadar desktop window.',
    '处理时间较长，请稍后核对检查记录':'This is taking longer than expected. Check the records again shortly.', '请填写技能文件夹的完整路径':'Enter the full skill folder location.',
    '该技能已不在当前检查记录中，请刷新后重试':'This skill is no longer in the current records. Refresh and try again.',
    '新增文件':'Added files', '已移除文件':'Removed files', '已修改文件':'Changed files', '没有文件变化':'No file changes', '已确认这次更新：':'Update confirmed: ',
    '出处：':'Reference: ', '对照内容：':'Matching content: ', '原始说明':'Original check note'
  };
  let language = 'zh-CN';
  try { if (localStorage.getItem('skillradar-language') === 'en') language = 'en'; } catch (_) {}
  const tr = (text, english) => language === 'en' ? (english ?? COPY[text] ?? text) : text;
  // 只处理代码里明确调用的静态 HTML 模板；用户数据始终另行转义后拼接。
  const markup = value => String(value).replace(/>([^<>]*)(?=<|$)/g, (all, text) => '>' + tr(text))
    .replace(/((?:aria-label|placeholder|title)=")([^"]+)(")/g, (all, start, text, end) => start + tr(text) + end);
  const set = value => {
    language = value === 'en' ? 'en' : 'zh-CN';
    document.documentElement.lang = language;
    try { localStorage.setItem('skillradar-language', language); } catch (_) {}
  };
  const apply = () => {
    document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = tr(el.dataset.i18n); });
    for (const attr of ['aria-label', 'placeholder', 'title']) {
      document.querySelectorAll('[data-i18n-' + attr + ']').forEach(el => {
        el.setAttribute(attr, tr(el.getAttribute('data-i18n-' + attr)));
      });
    }
    document.title = tr('SkillRadar · 技能守护');
    const select = document.getElementById('ui-language'); if (select) select.value = language;
  };
  function eventText(event) {
    const raw = String(event?.text ?? '');
    const templates = {
      new: [/^新技能\s+(.+)$/, '发现新技能：', 'New skill: '],
      drift: [/^(?:内容漂移|内容有变化)\s+(.+)$/, '内容有变化：', 'Content changed: '],
      block: [/^(?:CRITICAL|严重风险)\s*拦截\s+(.+)$/, '发现严重风险：', 'Serious risk found: '],
      skip: [/^守护暂停，跳过\s+(.+)$/, '已暂停检查，暂未检查：', 'Checks paused; skipped: '],
    };
    const template = templates[event?.kind], match = template?.[0].exec(raw);
    if (match) return tr(template[1], template[2]) + match[1];
    if (event?.kind === 'quarantine') {
      if (raw.startsWith('已隔离 ')) return tr('已移至隔离文件夹：', 'Moved aside: ') + raw.slice('已隔离 '.length);
      if (raw.startsWith('隔离失败，请人工处理 ')) return tr('移动失败，请手动处理：', 'Could not move skill; review manually: ') + raw.slice('隔离失败，请人工处理 '.length);
    }
    const errors = [
      ['扫描异常 ', '检查失败：', 'Check failed: '],
      ['目录补扫异常 ', '文件夹检查失败：', 'Folder check failed: '],
      ['目录监听启动失败: ', '无法自动检查文件夹：', 'Could not watch folder changes: '],
      ['通知失败: ', '通知发送失败：', 'Notification failed: '],
    ];
    if (event?.kind === 'error') for (const [prefix, cn, en] of errors) {
      if (raw.startsWith(prefix)) return tr(cn, en) + raw.slice(prefix.length);
    }
    return raw;
  }
  function reasonText(value, row) {
    const raw = String(value ?? '');
    if (raw === '内容已偏离安全基线（疑似篡改或未审查的更新），需先 --show-diff 人工审查')
      return tr('内容与之前的记录不同，可能是更新或修改。请先查看变化，再确认这次更新。', 'Content differs from the previous record. It may be an update or another change. Review it before confirming.');
    if (/^存在 CRITICAL 级发现：/.test(raw))
      return tr('发现严重风险提示，请逐条查看详情，暂不建议使用。', 'Serious warning signs were found. Review each detail before using this skill.');
    const score = /^综合风险分 (\d+)\/100 过高（/.exec(raw);
    if (score) return tr('累计风险评分较高：', 'Combined risk score is high: ') + score[1] + '/100' + tr('。请先查看详情。', '. Review the details before use.');
    const high = /^存在 (\d+) 条 HIGH 级发现/.exec(raw);
    if (high) return tr('发现 ', 'Found ') + high[1] + tr(' 条较高风险提示。部分操作可能是技能的正常用途，请先查看详情。', ' higher-risk warnings. Some operations may be expected for this skill; review the details first.');
    const med = /^存在 (\d+) 条 MEDIUM 级发现/.exec(raw);
    if (med) return tr('发现 ', 'Found ') + med[1] + tr(' 条需要留意的提示，请查看详情。', ' warnings to review. Open the details.');
    if (raw === '未命中任何风险规则') return tr('本次未发现已知风险提示，使用前仍需核对来源与内容。', 'No known warning signs were found. Review the source and content before use.');
    if (/^技能说明已更新，请确认新增的下载操作是否符合你的预期。$/.test(raw)) return tr(raw, 'The skill instructions changed. Check whether the added download steps match what you expect.');
    if (/^发现可能读取并上传本机私密文件的指令。$/.test(raw)) return tr(raw, 'Instructions may read and upload private files from this device.');
    if (raw === '请根据下面的检查结果核对技能内容。') return tr(raw);
    return raw || tr('请根据下面的检查结果核对技能内容。');
  }
  const RULES = {
    'SR-THEFT-001':['可能读取登录用的私密密钥。','May read private sign-in keys.'],
    'SR-THEFT-002':['可能读取账号凭据或应用配置中的秘密。','May read account credentials or secrets from app settings.'],
    'SR-THEFT-003':['可能访问浏览器保存的账号或浏览记录。','May access account or browsing data saved by your browser.'],
    'SR-EXEC-001':['包含下载后立即运行内容的操作，请核对下载来源。','Downloads content and runs it immediately. Review the download source.'],
    'SR-EXEC-002':['包含把文本作为指令运行的操作。','May run text as commands.'],
    'SR-EXEC-003':['可能允许外部设备远程控制本机。','May allow another device to control this one remotely.'],
    'SR-PERSIST-001':['可能添加开机或定时运行的操作。','May add startup or scheduled operations.'],
    'SR-PERSIST-002':['可能修改自动执行操作或远程登录设置。','May change automatic actions or remote sign-in settings.'],
    'SR-EXFIL-001':['同一文件中包含读取私密信息和向外部发送数据的操作。','Contains steps to read private information and send data elsewhere in the same file.'],
    'SR-EXFIL-002':['多个文件一起包含读取私密信息和向外部发送数据的操作。','Contains steps across files to read private information and send data elsewhere.'],
    'SR-INJ-001':['可能要求 AI 忽略已有要求，改为服从本技能。','May tell an AI tool to ignore existing instructions and follow this skill instead.'],
    'SR-INJ-002':['可能要求 AI 在不告知你的情况下操作。','May tell an AI tool to act without letting you know.'],
    'SR-INJ-003':['可能冒用系统或官方身份发出要求。','May present instructions as if they came from the system or an official source.'],
    'SR-ABUSE-001':['可能修改 AI 工具的设置。','May change your AI tool settings.'],
    'SR-ABUSE-002':['可能修改其他技能的文件。','May change files belonging to other skills.'],
    'SR-DECEP-001':['可能使用催促或恐吓用语推动你操作。','May use pressure or threats to make you act.'],
    'SR-DECEP-002':['可能冒用知名技能或官方来源的身份。','May imitate a known skill or claim an official source.'],
    'SR-SUPPLY-001':['可能在安装时自动运行额外操作。','May run additional actions automatically during installation.'],
    'SR-SUPPLY-002':['包含与知名软件包非常相似的名字，请核对来源。','Contains a name similar to a known package. Check its source.'],
    'SR-SUPPLY-003':['可能从短链接或陌生地址下载代码。','May download code from shortened links or unfamiliar addresses.'],
    'SR-OBFUS-001':['包含难以直接读懂的编码内容，请核对其用途。','Contains encoded content that is hard to read. Review what it does.'],
    'SR-OBFUS-002':['包含不易察觉的隐藏字符。','Contains hidden characters that are hard to notice.'],
    'SR-OBFUS-003':['还原隐藏内容后发现风险提示。','Warning signs were found after reading encoded content.'],
    'SR-OBFUS-004':['隐藏内容较多，本次未能完整检查。','There is too much encoded content to check completely.'],
    'SR-BLOCK-001':['与已知风险清单中的技能、来源或文件相符。','Matches a skill, source or file on a known risk list.'],
    'DATA_EXFILTRATION':['可能把私密文件发送到外部地址。','May send private files to an external address.'],
  };
  function findingText(finding) {
    const known = RULES[finding?.rule_id];
    if (known) return tr(known[0], known[1]);
    const raw = String(finding?.message ?? '');
    const descriptions = {
      '包含访问外部地址的指令。':'Contains instructions to access an external address.',
      '可能把私密文件发送到外部地址。':'May send private files to an external address.',
    };
    return tr(raw, descriptions[raw] ?? (language === 'en' && /[\u4e00-\u9fff]/.test(raw) ? 'Review the matching content below. The original note is available under its reference.' : raw));
  }
  function outputText(value) {
    const raw = String(value ?? '');
    if (raw.startsWith('re-baselined: ')) return tr('已确认这次更新：') + raw.slice(14);
    try {
      const data = JSON.parse(raw);
      if (data && ['added','removed','changed'].every(key => Array.isArray(data[key]))) {
        const groups = [['added','新增文件'],['removed','已移除文件'],['changed','已修改文件']];
        if (!groups.some(([key]) => data[key].length)) return tr('没有文件变化');
        return groups.filter(([key]) => data[key].length).map(([key,label]) => tr(label) + '\n' + data[key].map(file => '  ' + String(file)).join('\n')).join('\n\n');
      }
    } catch (_) {}
    return raw || tr('客户端未返回变更内容。');
  }
  window.SkillRadarI18n = {tr, markup, set, apply, eventText, reasonText, findingText, outputText, get language() { return language; }};
})();
