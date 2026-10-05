/* Reports, Markdown copying and optional session-log discovery. */
(function () {
  'use strict';
  window.SkillRadarFeatures = function (ctx) {
    const {tr,escapeHtml:esc,act,toast,updateHTML,connected,getState,getUsageStatus}=ctx;
    const $=id=>document.getElementById(id), MD=window.SkillRadarMarkdown;
    let report=null, reports=[], busy=false, historyBusy=false, directoryBusy=false, skillToken=0;
    const locationRequests=new Set();
    const nav=document.createElement('button');nav.type='button';nav.className='nav-btn';nav.dataset.screen='reports';
    nav.innerHTML='<svg class="icon" aria-hidden="true"><use href="#icon-file-code-2"/></svg><span data-i18n="检查报告">'+esc(tr('检查报告','Check reports'))+'</span><span class="nav-dot"></span>';
    document.querySelector('[data-screen="settings"]').before(nav);
    const section=document.createElement('section');section.id='screen-reports';section.setAttribute('aria-labelledby','reports-title');
    section.innerHTML='<div class="page-heading"><div><h1 id="reports-title" data-i18n="检查报告">'+esc(tr('检查报告','Check reports'))+'</h1><p class="sub" data-i18n="生成闲置技能、调用排行与安全检查报告。">'+esc(tr('生成闲置技能、调用排行与安全检查报告。','Generate a report of idle skills, usage rankings and security checks.'))+'</p></div><button class="btn primary" id="btn-generate-report" data-i18n="生成检查报告">'+esc(tr('生成检查报告','Generate report'))+'</button></div><p class="note" id="report-help"></p><p class="inline-status" id="report-status" role="status" hidden></p><div class="card report-card"><div class="card-head"><label for="report-history" data-i18n="历史报告">'+esc(tr('历史报告','Report history'))+'</label><select id="report-history" class="btn"><option value="">—</option></select></div><div id="report-meta" class="note"></div><div class="report-actions"><button class="btn" id="btn-copy-report" data-i18n="复制 Markdown">'+esc(tr('复制 Markdown','Copy Markdown'))+'</button><button class="btn" id="btn-export-report-md" data-i18n="下载 Markdown">'+esc(tr('下载 Markdown','Download Markdown'))+'</button><button class="btn" id="btn-export-report-html" data-i18n="下载 HTML">'+esc(tr('下载 HTML','Download HTML'))+'</button></div><div id="report-preview" class="markdown-body report-preview"></div><details class="markdown-source"><summary data-i18n="Markdown 原文">'+esc(tr('Markdown 原文','Markdown source'))+'</summary><textarea id="report-source" readonly aria-label="Markdown source"></textarea></details></div>';
    $('screen-settings').before(section);
    const guidancePanel=document.createElement('div');guidancePanel.id='report-guidance';
    $('report-preview').before(guidancePanel);
    const evidencePanel=document.createElement('details');evidencePanel.id='report-evidence';evidencePanel.className='report-evidence';
    evidencePanel.innerHTML='<summary></summary><div id="report-evidence-body" class="markdown-body"></div>';
    $('report-preview').after(evidencePanel);
    const explanation=document.createElement('p');explanation.id='attention-help';explanation.className='note attention-help';
    $('skill-search').closest('.security-toolbar').after(explanation);
    const discovery=document.createElement('div');discovery.id='usage-discovery';discovery.className='card usage-discovery';
    discovery.innerHTML='<details id="usage-log-details"><summary><strong id="usage-discovery-title"></strong><span id="usage-scan-status" role="status"></span></summary><p class="note" id="usage-log-help"></p><div id="usage-log-roots"></div><details><summary id="usage-optional-title"></summary><p class="note" id="usage-optional-help"></p><div class="report-actions"><button class="btn" id="btn-select-usage-dir"></button></div><form id="usage-path-form" class="input-row"><input id="usage-log-path" aria-label="Session-log directory or drive root" placeholder="F:\\Logs or F:\\"><button class="btn" id="btn-scan-usage-path" type="submit"></button></form><p class="note" id="usage-scan-warning"></p></details></details>';
    $('usage-bars').before(discovery);
    const copySkill=document.createElement('button');copySkill.type='button';copySkill.id='btn-copy-skill';copySkill.className='btn';copySkill.dataset.i18n='复制检查 Markdown';copySkill.textContent=tr('复制检查 Markdown','Copy check as Markdown');
    const copyLine=document.createElement('div');copyLine.className='report-actions';copyLine.append(copySkill);$('drawer-body').before(copyLine);
    const openLocation=document.createElement('button');openLocation.type='button';openLocation.id='btn-open-skill-location';openLocation.className='btn';openLocation.dataset.i18n='打开所在文件夹';openLocation.textContent=tr('打开所在文件夹','Open skill folder');copyLine.prepend(openLocation);
    const markdownPanel=document.createElement('details');markdownPanel.id='skill-markdown';markdownPanel.className='markdown-source';
    markdownPanel.innerHTML='<summary data-i18n="Markdown 检查结果">'+esc(tr('Markdown 检查结果','Check results in Markdown'))+'</summary><div id="skill-markdown-preview" class="markdown-body"></div><textarea id="skill-markdown-source" readonly aria-label="Check Markdown source"></textarea>';
    $('drawer-body').after(markdownPanel);
    function availability() {
      $('btn-generate-report').disabled=!connected()||busy;
      for(const id of ['btn-copy-report','btn-export-report-md','btn-export-report-html'])$(id).disabled=!connected()||busy||!report;
      $('report-history').disabled=!connected()||busy||historyBusy||!reports.length;
      $('btn-copy-skill').disabled=!connected()||!ctx.currentSkill();
      openLocation.disabled=!connected()||!ctx.currentSkill()||openLocation.dataset.busy==='true';
      for(const button of guidancePanel.querySelectorAll('button'))button.disabled=!connected()||busy||locationRequests.has(button.dataset.path);
      for(const id of ['btn-select-usage-dir','btn-scan-usage-path'])$(id).disabled=!connected()||directoryBusy||!!getUsageStatus()?.scanning;
    }
    function paint() {
      $('attention-help').textContent=tr('需要关注：有风险提示、待确认变化、检查未完成或失败的技能。已查看或已信任的风险仍保留；若全部技能符合条件，数量会与全部技能相同。','Needs attention includes warnings, pending changes, incomplete checks and failures. Reviewed or trusted warnings stay visible; if every skill qualifies, both lists have the same count.');
      document.querySelector('#screen-reports .page-heading .sub').removeAttribute('data-i18n');
      document.querySelector('#screen-reports .page-heading .sub').textContent=tr('看懂哪些技能要先关注，哪些可以考虑清理，以及下一步怎么做。','See which skills need attention, which to consider removing, and what to do next.');
      $('report-help').textContent=tr('先检查、再生成报告，建议会保留生成时的记录。下面的按钮帮你查看与定位，处理由你决定。','Run checks before generating a report. Suggestions use the saved records; the buttons help you review and locate skills. You decide how to proceed.');
      for(const [id,label,en,help,helpEn] of [
        ['btn-copy-report','复制报告','Copy report','复制完整报告，包含结论、教程和技术附录。','Copy the complete report, including conclusions, steps, and evidence.'],
        ['btn-export-report-md','下载文字版','Download text version','保存为 Markdown 文件，适合阅读或分享文字内容。','Save a Markdown file for reading or sharing the text.'],
        ['btn-export-report-html','下载网页版','Download web version','保存为 HTML 文件，双击即可用浏览器阅读。','Save an HTML file that opens in your browser.']]) {
        const button=$(id);button.removeAttribute('data-i18n');button.textContent=tr(label,en);button.title=tr(help,helpEn);
      }
      $('usage-discovery-title').textContent=tr('自动查找调用记录','Automatic log discovery');
      $('usage-log-help').textContent=tr('客户端自动查找并扫描 AI 工具的会话日志，无需选择目录。扫描的是调用记录，不是技能文件夹；不会修改原日志。','The app automatically finds and scans AI session logs. No folder selection is needed. It reads invocation records, not skill folders, and keeps the original logs intact.');
      $('usage-optional-title').textContent=tr('补扫其他日志位置（可选）','Scan another log location (optional)');
      $('usage-optional-help').textContent=tr('仅当日志放在其他位置时使用。选择 Codex 的 sessions / archived_sessions、zcode 的 cli/rollout / cli/agents、Claude 的 projects，或它们的上级目录。下面已显示自动找到的路径。','Use this only for logs stored elsewhere. Choose Codex sessions / archived_sessions, zcode cli/rollout / cli/agents, Claude projects, or their parent folder. Automatically found paths appear above.');
      $('btn-select-usage-dir').textContent=tr('选择会话日志目录','Choose session-log folder');
      $('btn-scan-usage-path').textContent=tr('扫描此位置','Scan this location');
      $('usage-scan-warning').textContent=tr('也可填写整个盘的根目录，例如 F:\\。全盘补扫可能较慢，无法读取的位置会显示在扫描结果中。','You may also enter a drive root such as F:\\. A whole-drive scan can take time; unreadable locations appear in the results.');
      const status=getUsageStatus() || {}, roots=Array.isArray(status.roots)?status.roots:[];
      $('usage-scan-status').textContent=(status.scanning?tr('正在后台扫描历史日志','Scanning history in the background'):status.phase==='error'?tr('扫描完成，部分位置未能读取','Scan finished with unreadable locations'):status.phase==='ready'?tr('历史日志扫描完成','History scan complete'):tr('等待首次扫描','Waiting for the first scan'))+' · '+tr('已扫描','Files scanned')+' '+(status.files_scanned || 0)+(status.last_scan?' · '+window.SkillRadarI18n.formatDateTime(status.last_scan):'')+' · '+tr('新增记录','New records')+' '+(status.new_records || 0);
      updateHTML('usage-log-roots',roots.map(r=>'<p class="skill-path"><strong>'+esc(r.source)+'</strong> · '+esc(r.path)+' · '+esc(r.exists?tr('已找到','Found'):tr('未找到','Not found'))+'</p>').join('')+(status.errors || []).map(e=>'<p class="form-error">'+esc(e.path)+' · '+esc(e.error)+'</p>').join(''));
    $('skill-markdown').hidden=!ctx.currentSkill();copyLine.hidden=!ctx.currentSkill();displayReport();
    }
    async function copy(value,textarea) {
      try { if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(value);toast(tr('已复制 Markdown','Markdown copied'));return;} } catch (_) {}
      const result=await act('copy_markdown',{text:value});
      if(result?.ok)toast(tr('已复制 Markdown','Markdown copied'));
      else { if(textarea){textarea.closest('details').open=true;textarea.focus();textarea.select();}toast(tr('复制失败，请从 Markdown 原文复制','Copy failed. Copy from the Markdown source.'),true); }
    }
    function reportButton(action,label,path='') {
      return '<button type="button" class="btn" data-report-action="'+action+'"'+(path?' data-path="'+esc(path)+'"':'')+'>'+esc(label)+'</button>';
    }
    function reportGroups() {
      const value=report?.guidance;
      if(value?.version!==1 || !value.groups || typeof value.groups!=='object')return null;
      return Object.fromEntries(['priority','confirm','cleanup','observe','handled'].map(key=>[key,Array.isArray(value.groups[key])?value.groups[key].filter(item=>item && typeof item==='object'):[]]));
    }
    function textList(items) { return Array.isArray(items)?items.filter(item=>typeof item==='string'):[]; }
    function guidanceHTML(groups) {
      const total=groups.priority.length+groups.confirm.length;
      const coverage=report.guidance.coverage, enoughChecks=coverage?.checks_available===true && coverage?.unchecked_installed===0;
      const conclusion=total?tr('先关注 '+total+' 个技能，再考虑清理。','Review '+total+' skills first, then consider cleanup.'):!enoughChecks?tr('先补齐检查信息，再决定要不要清理。','Complete the check information before deciding what to remove.'):groups.cleanup.length?tr('当前没有待确认的检查问题，可以先评估清理候选。','There are no pending check issues in these records. Review cleanup candidates next.'):tr('当前记录没有待确认的检查问题，其余技能先保留。','These records have no pending check issues. Keep the remaining skills for now.');
      let output='<div class="report-conclusion"><span class="eyebrow">'+esc(tr('一眼结论','At a glance'))+'</span><h2>'+esc(conclusion)+'</h2><p class="note">'+esc(tr('根据报告生成时的记录整理；打开详情看到的是当前检查结果。','Based on records at generation; skill details show the current check results.'))+'</p><div class="report-counts">'+[['priority',tr('优先处理','Review first')],['confirm',tr('需要确认','Needs confirmation')],['cleanup',tr('可考虑清理','Cleanup candidates')],['observe',tr('继续观察','Keep for now')]].map(([key,label])=>'<span><strong>'+groups[key].length+'</strong> '+esc(label)+'</span>').join('')+'</div><div class="report-item-actions">'+reportButton('attention',tr('查看需要关注的技能','View skills needing attention'))+reportButton('usage',tr('查看闲置技能','View idle skills'))+'</div></div>';
      const notes=textList(report.guidance.overview_notes);
      if(notes.length)output+='<ul class="report-coverage-notes">'+notes.map(note=>'<li>'+esc(note)+'</li>').join('')+'</ul>';
      const definitions=[['priority',tr('优先处理','Review first'),tr('确认前先别主动使用，先查看原因和证据。','Review the reason and evidence before actively using these skills.')],['confirm',tr('需要确认','Needs confirmation'),tr('核对用途；检查没有完成的，先重新检查。','Review their purpose; run unfinished checks again.')],['cleanup',tr('可考虑清理','Cleanup candidates'),tr('确认以后不再需要，再按教程备份并手动清理。','Confirm you no longer need them, then back up and follow the manual steps.')],['observe',tr('继续观察','Keep for now'),tr('新安装、记录不足、同名多份或仍在使用的技能先保留。','Keep recent installations, incomplete or shared records, and recently used skills for now.')],['handled',tr('你已处理','Already reviewed'),tr('保留你之前的决定；内容变化后需要重新确认。','Your previous decisions are kept; review again when the content changes.')]];
      for(const [key,title,help] of definitions) {
        const folded=['observe','handled'].includes(key), items=groups[key];
        output+=folded?'<details class="report-group" data-report-group="'+key+'"><summary>'+esc(title)+' <span class="badge">'+items.length+'</span></summary>':'<section class="report-group" data-report-group="'+key+'"><div class="report-group-heading"><h3>'+esc(title)+'</h3><span class="badge">'+items.length+'</span></div>';
        output+='<p class="note report-group-help">'+esc(help)+'</p>';
        if(!items.length)output+='<p class="note report-empty">'+esc(tr('当前没有这一类建议。','No suggestions in this group.'))+'</p>';
        for(const item of items) {
          const path=typeof item.path==='string'?item.path:'', steps=textList(item.steps);
          output+='<article class="report-item"><h4>'+esc(item.name || tr('未知技能','Unknown skill'))+'</h4><p class="report-reason">'+esc(item.reason || tr('请核对这项记录。','Review this record.'))+'</p><p class="skill-path"><span class="report-path-label">'+esc(tr('完整位置','Full folder path'))+'</span>'+esc(path || tr('位置未记录','Location not recorded'))+'</p><div class="report-item-actions">';
          if(path && ['priority','confirm','handled'].includes(key))output+=reportButton('detail',tr('查看检查详情','View check details'),path);
          if(path)output+=reportButton('location',tr('打开所在文件夹','Open skill folder'),path);
          output+='</div>';
          if(steps.length)output+='<details class="report-steps" data-report-steps><summary>'+esc(tr('下一步怎么做','What to do next'))+'</summary><ol>'+steps.map(step=>'<li>'+esc(step)+'</li>').join('')+'</ol></details>';
          output+='</article>';
        }
        output+=folded?'</details>':'</section>';
      }
      output+='<section class="report-tutorial"><h3>'+esc(tr('清理教程','Manual cleanup guide'))+'</h3><p class="note">'+esc(tr('每次只核对一个技能；备份和回收站方便恢复。','Review one skill at a time. Backups and the Recycle Bin let you restore it.'))+'</p><ol>'+textList(report.guidance.cleanup_steps).map(step=>'<li>'+esc(step)+'</li>').join('')+'</ol><h4>'+esc(tr('清理前请留意','Before removing a folder'))+'</h4><ul>'+textList(report.guidance.cleanup_cautions).map(step=>'<li>'+esc(step)+'</li>').join('')+'</ul></section>';
      return output;
    }
    function reportAppendix(markdown) {
      // 仅按围栏之外的生成器标题拆分附录，原始代码不能伪造分界。
      const lines=String(markdown || '').replace(/\r\n/g,'\n').split('\n');let fence=null;
      for(let i=0;i<lines.length;i++) {
        const marker=lines[i].match(/^(`{3,}|~{3,})/);
        if(marker) { if(fence && marker[1][0]===fence[0] && marker[1].length>=fence.length)fence=null;else if(!fence)fence=marker[1];continue; }
        if(!fence && lines[i]==='## 技术附录')return lines.slice(i+1).join('\n');
      }
      return '';
    }
    function displayReport() {
      $('report-meta').textContent=report?tr('生成时间','Generated at')+' · '+window.SkillRadarI18n.formatDateTime(report.generated_at):'';
      for(const option of $('report-history').options) {
        if(!option.value) { option.textContent=tr('选择历史报告','Choose a report');continue; }
        const saved=reports.find(item=>item.id===option.value);
        if(saved) option.textContent=window.SkillRadarI18n.formatDateTime(saved.generated_at);
      }
      const groups=reportGroups(), guided=!!groups;
      const sameReport=guidancePanel.dataset.reportId===(report?.id || '');
      const expanded=sameReport?[...guidancePanel.querySelectorAll('details[open]')].map(details=>detailsIdentity(details)):[];
      const focus=document.activeElement, owned=sameReport && guidancePanel.contains(focus), focusPath=owned?focus.dataset?.path:'', focusAction=owned?focus.dataset?.reportAction:'', focusDetails=owned && focus.tagName==='SUMMARY'?detailsIdentity(focus.parentElement):null;
      guidancePanel.hidden=!report;
      if(guided) updateHTML('report-guidance',guidanceHTML(groups));
      else updateHTML('report-guidance',report?'<div class="report-legacy note">'+esc(tr('这是旧版报告。生成新报告，即可看到结论和下一步建议。','This is an older report. Generate a new report for conclusions and next steps.'))+'</div>':'');
      if(!sameReport) evidencePanel.open=false;
      for(const details of guidancePanel.querySelectorAll('details'))details.open=expanded.includes(detailsIdentity(details));
      guidancePanel.dataset.reportId=report?.id || '';
      if(owned && !focus.isConnected && !document.getElementById('app-layout').inert) {
        const target=focusDetails?[...guidancePanel.querySelectorAll('details')].find(details=>detailsIdentity(details)===focusDetails)?.querySelector('summary'):[...guidancePanel.querySelectorAll('button')].find(button=>button.dataset.reportAction===focusAction && (button.dataset.path || '')===focusPath);
        target?.focus({preventScroll:true});
      }
      $('report-preview').hidden=guided;
      updateHTML('report-preview',report && !guided?MD.render(report.markdown):report?'':'<div class="report-empty-start"><h2>'+esc(tr('生成一份报告，看看下一步能做什么','Generate a report to see what to do next'))+'</h2><p>'+esc(tr('先完成安全检查，再点击右上角“生成检查报告”。报告会告诉你优先关注哪些技能、哪些可以考虑清理，并附具体步骤。','Run security checks, then use Generate report above. The report identifies skills to review or consider removing, with practical steps.'))+'</p></div>');
      const appendix=guided?reportAppendix(report.markdown):'';
      evidencePanel.hidden=!appendix;evidencePanel.querySelector('summary').textContent=tr('技术附录 · 完整统计与检查证据','Technical appendix · statistics and evidence');
      updateHTML('report-evidence-body',MD.render(appendix));
      $('report-source').value=report?.markdown || '';availability();
    }
    function detailsIdentity(details) {
      if(details.matches('[data-report-group]'))return 'group:'+details.dataset.reportGroup;
      const item=details.closest('.report-item');
      return JSON.stringify([details.closest('[data-report-group]')?.dataset.reportGroup,item?.querySelector('[data-path]')?.dataset.path || item?.querySelector('h4')?.textContent]);
    }
    guidancePanel.addEventListener('click',async event=>{
      const button=event.target.closest('button[data-report-action]');if(!button || button.disabled)return;
      const action=button.dataset.reportAction, path=button.dataset.path || '';
      if(action==='attention') { ctx.reportAttention(); return; }
      if(action==='usage') { ctx.reportUsage(); return; }
      // 路径必须来自正在查看的报告，正文中的任意文本没有动作权限。
      const groups=reportGroups();if(!groups || !Object.values(groups).flat().some(item=>item.path===path))return;
      if(action==='detail') {
        if(!Object.hasOwn(getState()?.skills || {},path)) { toast(tr('当前检查记录中找不到这个技能，请重新检查并生成新报告。','This skill is no longer in the current check records. Run checks and generate a new report.'),true);return; }
        ctx.openReportSkill(path,button);
      } else if(action==='location' && !locationRequests.has(path)) {
        locationRequests.add(path);availability();
        try { const result=await act('open_skill_location',{skill:path});toast(result?.ok?tr('已打开技能所在文件夹','Skill folder opened'):tr('无法打开这个技能文件夹，请核对是否已经移动或移除；也可复制完整位置手动打开。','Cannot open this skill folder. Check whether it was moved or removed, or copy its full path into your file manager.'),!result?.ok); }
        finally { locationRequests.delete(path);availability(); }
      }
    });
    async function loadReports() {
      if(historyBusy || !connected())return;historyBusy=true;availability();
      try { const result=await act('list_reports');if(result?.ok){reports=result.reports || [];updateHTML('report-history','<option value="">'+esc(tr('选择历史报告','Choose a report'))+'</option>'+reports.map(r=>'<option value="'+esc(r.id)+'">'+esc(window.SkillRadarI18n.formatDateTime(r.generated_at))+'</option>').join(''));if(report)$('report-history').value=report.id;}else statusError(result); }
      finally{historyBusy=false;displayReport();}
    }
    function statusError(result){$('report-status').hidden=false;$('report-status').className='inline-status error';$('report-status').textContent=tr('报告操作失败：','Report action failed: ')+(result?.error || tr('未返回结果','No result returned'));}
    async function generate(){if(busy||!connected())return;busy=true;availability();$('report-status').hidden=false;$('report-status').className='inline-status';$('report-status').textContent=tr('正在生成报告…','Generating report…');try{const result=await act('generate_report');if(result?.ok&&result.report){report=result.report;displayReport();$('report-status').hidden=true;await loadReports();}else statusError(result);}finally{busy=false;availability();}}
    async function exportReport(format){if(!report||busy)return;busy=true;availability();try{const result=await act('export_report',{id:report.id,format});if(result?.ok&&!result.cancelled){if(result.content && !result.saved && !window.pywebview?.api){const blob=new Blob([result.content],{type:result.mime}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=result.filename;a.click();URL.revokeObjectURL(url);}toast(tr('报告已保存','Report saved'));}else if(!result?.ok)statusError(result);}finally{busy=false;availability();}}
    async function skillMarkdown(path) {
      const token=++skillToken;if(!path)return;
      markdownPanel.hidden=false;copyLine.hidden=false;availability();
      const result=await act('get_markdown',{subject:'skill',skill:path});if(token!==skillToken||ctx.currentSkill()!==path)return;
      if(result?.ok){updateHTML('skill-markdown-preview',MD.render(result.markdown));$('skill-markdown-source').value=result.markdown;}
      else {updateHTML('skill-markdown-preview','<p>'+esc(result?.error || tr('尚未读取','Not loaded'))+'</p>');$('skill-markdown-source').value='';}
    }
    async function scanPath(path){if(!path)return;const result=await act('scan_usage',{path,source:'auto'});if(!result?.ok)toast(result?.error || tr('扫描失败','Scan failed'),true);else ctx.loadUsage(false);}
    $('btn-generate-report').addEventListener('click',generate);
    $('btn-export-report-md').addEventListener('click',()=>exportReport('md'));$('btn-export-report-html').addEventListener('click',()=>exportReport('html'));
    $('btn-copy-report').addEventListener('click',()=>report&&copy(report.markdown,$('report-source')));
    $('report-history').addEventListener('change',async()=>{const id=$('report-history').value;if(!id)return;busy=true;availability();try{const result=await act('get_report',{id});if(result?.ok){report=result.report;displayReport();}else statusError(result);}finally{busy=false;availability();}});
    $('btn-copy-skill').addEventListener('click',async()=>{await skillMarkdown(ctx.currentSkill());copy($('skill-markdown-source').value,$('skill-markdown-source'));});
    openLocation.addEventListener('click',async()=>{const skill=ctx.currentSkill();if(!skill || openLocation.dataset.busy==='true')return;openLocation.dataset.busy='true';availability();try{const result=await act('open_skill_location',{skill});toast(result?.ok?tr('已打开技能所在文件夹','Skill folder opened'):result?.error || tr('无法打开文件夹','Could not open the folder'),!result?.ok);}finally{delete openLocation.dataset.busy;availability();}});
    $('btn-select-usage-dir').addEventListener('click',async()=>{directoryBusy=true;availability();try{const result=await act('select_usage_directory');if(result?.ok&&!result.cancelled)await scanPath(result.path);else if(!result?.ok)toast(result?.error,true);}finally{directoryBusy=false;availability();}});
    $('usage-path-form').addEventListener('submit',async e=>{e.preventDefault();directoryBusy=true;availability();try{await scanPath($('usage-log-path').value.trim());}finally{directoryBusy=false;availability();}});
    displayReport();paint();return{paint,availability,loadReports,skillMarkdown};
  };
})();
