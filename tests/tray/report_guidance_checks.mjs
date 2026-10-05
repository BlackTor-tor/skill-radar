// Real Chromium behavior checks: reports guide people to reversible, existing actions.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync, readFileSync, writeFileSync, mkdtempSync, rmSync, mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename, dirname, resolve, sep} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const args = process.argv.slice(2);
const option = name => args.includes(name) ? args[args.indexOf(name) + 1] : null;
const browserPath = option('--browser') || process.env.SKILL_RADAR_BROWSER;
assert(browserPath && existsSync(browserPath), 'Provide Chromium with --browser');
const htmlPath = resolve(option('--html') || resolve(repo, 'tray/web/index.html'));
const captureDir = option('--capture'), prefix = option('--prefix') || 'report-guidance';
const externalFixture = option('--report-json') ? JSON.parse(readFileSync(option('--report-json'), 'utf8').replace(/^\uFEFF/, '')) : null;
const language = option('--language') || 'zh-CN', theme = option('--theme') || 'light';
const width = Number(option('--width') || 1080), height = Number(option('--height') || 720);
const profile = mkdtempSync(resolve(tmpdir(), 'skill-radar-report-ui-'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

const firstPath = 'F:/First skills/same-name';
const secondPath = 'F:/Second skills/same-name';
const cleanupPath = 'F:/My skills/unused "demo"';
const cleanupLocation = '#report-guidance [data-report-action="location"][data-path=' + JSON.stringify(cleanupPath) + ']';
const evidence = 'SHA-256 evidence: 92bc / SR-EXFIL-001';
const row = (name, changes = {}) => ({name, version: 'v1', score: 0, raw_score: 0,
  status: 'scanned', findings: [], raw_findings: [], scan_complete: true, scan_issues: [],
  check_status: 'healthy', review_status: 'not_required', ...changes});
const risk = {rule_id: 'SR-EXFIL-001', severity: 'CRITICAL', category: 'EXFIL',
  message: 'Potential upload', file: 'SKILL.md', line: 4};
const guidanceItem = (name, path, reason, steps) => ({name, path, reason, steps, version: 'v1'});
const guidance = {version: 1, counts: {priority: 1, confirm: 1, handled: 1, cleanup: 1, observe: 1}, groups: {
  priority: [guidanceItem('same-name', secondPath, '敏感信息可能被发到外部，请先确认用途。',
    ['打开检查详情，查看具体位置和可能的影响。', '不确定来源时，先备份再考虑隔离。'])],
  confirm: [guidanceItem('check-gap', 'F:/Skills/check-gap', '检查尚未完成，现在无法判断。', ['重新检查后再决定。'])],
  handled: [guidanceItem('reviewed', 'F:/Skills/reviewed', '你已经查看过这项风险。', ['如果用途发生变化，再检查一次。'])],
  cleanup: [guidanceItem('unused "demo"', cleanupPath, '安装已超过 30 天，未发现调用记录。确认以后是否还需要。',
    ['确认以后不再需要这个技能。', '打开指定文件夹，备份整个技能文件夹。', '把这个技能文件夹移入回收站。', '重启对应 AI 客户端并检查；需要恢复时从回收站还原。'])],
  observe: [guidanceItem('fresh', 'F:/Skills/fresh', '新安装的技能还在观察期。', ['继续使用，稍后再看。'])],
}, cleanup_steps: ['确认以后不再需要这个技能。', '打开指定文件夹，备份完整文件夹到非技能目录。',
  '只把这个技能文件夹移入回收站，不要删除根目录。', '重启对应 AI 客户端并检查。', '需要恢复时，从回收站还原或取回备份。'],
cleanup_cautions: ['同名副本、共享位置和链接先核对，确认是普通文件夹。', '停止监听或暂停守护不会阻止 AI 使用技能。']};
const markdown = '# 技能检查报告\n\n生成时间：2026-10-05T12:00:00+08:00\n\n## 一眼结论\n\n先处理 1 项风险，可以考虑清理 1 个技能。\n\n## 清理教程\n\n'
  + guidance.cleanup_steps.map((step, index) => `${index + 1}. ${step}`).join('\n')
  + '\n\n## 技术附录\n\n' + evidence + '\n\n<script>window.__unsafe=true</script>';
const report = {id: 'report-guidance-1', generated_at: '2026-10-05T12:00:00+08:00', markdown,
  html: '<h1>Skill report</h1>', guidance};
const state = {guard: 'running', watched_roots: 2, settings: {language: 'zh-CN', theme: 'light',
  roots: [{path: 'F:/Skills'}]}, events: [], skills: {
  [firstPath]: row('same-name'),
  [secondPath]: row('same-name', {check_status: 'attention', review_status: 'pending', raw_findings: [risk]}),
  'F:/Skills/check-gap': row('check-gap', {scan_complete: false, check_status: 'incomplete', review_status: 'pending'}),
  'F:/Skills/reviewed': row('reviewed', {check_status: 'attention', review_status: 'reviewed', raw_findings: [risk]}),
  [cleanupPath]: row('unused "demo"'),
}};
const installed = {name: 'unused "demo"', path: cleanupPath, total: 0, codex: 0, zcode: 0,
  claude: 0, marker: 0, last: null, installed_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z', usage_state: 'never', shared_name: false, observing: false};
const usage = {ok: true, rows: [], installed_rows: [installed], idle_groups: {never: [installed], inactive: [], unknown: []},
  inventory_summary: {total_installed: 1, never: 1, inactive: 0, unknown: 0, active: 0,
    total_invocations: 0, coverage_complete: true, inventory_complete: true},
  status: {phase: 'ready', scanning: false, files_scanned: 5, last_scan: '2026-10-05T11:30:00Z', roots: [], errors: []}};
const bridge = `Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});
window.__report_boot=crypto.randomUUID();window.__report_state=${JSON.stringify(externalFixture?.state || state)};
window.__report_data=${JSON.stringify(externalFixture?.report || externalFixture || report)};window.__report_actions=[];window.__copied='';window.__location_pending=false;window.__location_error=false;
try{localStorage.clear();}catch{}
window.pywebview={api:{get_state:async()=>structuredClone(window.__report_state),act:async(name,payload)=>{
window.__report_actions.push({name,payload});
if(name==='set_language'){window.__report_state.settings.language=payload.language;return{ok:true};}
if(name==='set_theme'){window.__report_state.settings.theme=payload.theme;return{ok:true};}
if(name==='generate_report'||name==='get_report')return{ok:true,report:structuredClone(window.__report_data)};
if(name==='list_reports')return{ok:true,reports:[{id:window.__report_data.id,generated_at:window.__report_data.generated_at}]};
if(name==='export_report')return{ok:true,path:'C:/Downloads/report.'+payload.format};
if(name==='copy_markdown'){window.__copied=payload.text;return{ok:true};}
if(name==='get_usage')return ${JSON.stringify(externalFixture?.usage || usage)};
if(name==='get_markdown')return{ok:true,markdown:'# Current skill\\n\\n'+payload.skill};
if(name==='open_skill_location'&&window.__location_error)return{error:'skill location does not exist'};
if(name==='open_skill_location'&&window.__location_pending)return new Promise(resolve=>{window.__resolve_location=()=>resolve({ok:true});});
return{ok:true};
}}};`;

let browser, ws, closeBrowser;
const results = [];
try {
  browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], {windowsHide: true, stdio: 'ignore'});
  browser.on('error', error => results.push({name: 'browser_start', ok: false, error: error.message}));
  const portFile = resolve(profile, 'DevToolsActivePort'), startup = Date.now() + 12000;
  while (!existsSync(portFile) && Date.now() < startup) await pause(40);
  assert(existsSync(portFile), 'Chromium did not expose its temporary endpoint');
  const port = readFileSync(portFile, 'utf8').split(/\r?\n/)[0];
  const target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(page => page.type === 'page');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, fail) => {ws.addEventListener('open', ok, {once: true}); ws.addEventListener('error', fail, {once: true});});
  let id = 0;
  const pending = new Map(), runtimeErrors = [];
  ws.addEventListener('message', event => {
    const data = JSON.parse(event.data);
    if (data.method === 'Runtime.exceptionThrown') runtimeErrors.push(data.params.exceptionDetails);
    const request = pending.get(data.id);
    if (!request) return;
    pending.delete(data.id); clearTimeout(request.timeout);
    data.error ? request.reject(new Error(JSON.stringify(data.error))) : request.resolve(data.result);
  });
  const cdp = (method, params = {}) => new Promise((ok, fail) => {
    const current = ++id, timeout = setTimeout(() => {pending.delete(current); fail(new Error('CDP timeout ' + method));}, 8000);
    pending.set(current, {resolve: ok, reject: fail, timeout}); ws.send(JSON.stringify({id: current, method, params}));
  });
  closeBrowser = () => cdp('Browser.close');
  const evaluate = async expression => {
    const value = await cdp('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    assert(!value.exceptionDetails, JSON.stringify(value.exceptionDetails)); return value.result.value;
  };
  const waitFor = async expression => {
    const deadline = Date.now() + 2500;
    while (Date.now() < deadline) {if (await evaluate(expression)) return; await pause(25);}
    throw new Error('UI condition: ' + expression + (runtimeErrors.length ? '\nRuntime exceptions: ' + JSON.stringify(runtimeErrors) : ''));
  };
  const click = async selector => {
    const point = await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)return null;
      el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2,width:r.width,height:r.height};})()`);
    assert(point?.width && point.height, 'Missing visible target: ' + selector);
    for (const type of ['mousePressed', 'mouseReleased']) await cdp('Input.dispatchMouseEvent', {type, x: point.x, y: point.y, button: 'left', clickCount: 1});
  };
  const content = id => evaluate(`document.getElementById(${JSON.stringify(id)})?.innerText || ''`);
  const screenshot = async (name, selector) => {
    if (!captureDir) return;
    await pause(200);
    await evaluate(`document.getElementById('toast').classList.remove('show');
      ${selector ? `document.querySelector(${JSON.stringify(selector)})?.scrollIntoView({block:'center'});` : "document.getElementById('main-content').scrollTop=0;"}`);
    await pause(180);
    mkdirSync(captureDir, {recursive: true});
    const overflow = await evaluate(`(()=>{const main=document.getElementById('main-content');
      return document.documentElement.scrollWidth>innerWidth+1||main.scrollWidth>main.clientWidth+1;})()`);
    results.push({name: 'layout_' + name, ok: !overflow, overflow});
    const data = await cdp('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
    writeFileSync(resolve(captureDir, prefix + '-' + name + '.png'), Buffer.from(data.data, 'base64'));
  };
  await cdp('Page.enable'); await cdp('Runtime.enable');
  await cdp('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
  await cdp('Page.addScriptToEvaluateOnNewDocument', {source: bridge});
  const reset = async () => {
    runtimeErrors.length = 0;
    const previous = await evaluate('window.__report_boot');
    await cdp('Page.navigate', {url: pathToFileURL(htmlPath).href});
    await waitFor(`window.__report_boot!==${JSON.stringify(previous)} && !!document.querySelector('#sec-rows tr[data-skill]')`);
    await click('nav.sidebar button[data-screen="reports"]');
  };
  const generate = async () => {
    await click('#btn-generate-report');
    await waitFor(`document.getElementById('report-source').value===window.__report_data.markdown && !document.getElementById('btn-generate-report').disabled`);
  };
  const configureAppearance = async () => {
    await evaluate(`changeLanguage(${JSON.stringify(language)})`);
    if (await evaluate('document.documentElement.dataset.theme') !== theme) await click('#btn-theme');
  };
  const check = async (name, fn) => {
    try {await reset(); await fn(); results.push({name, ok: true});}
    catch (error) {results.push({name, ok: false, error: error.stack || error.message});}
  };

  if (externalFixture) {
    await check('backend_generated_report_is_actionable_in_the_client', async () => {
      await generate(); await configureAppearance();
      const reportValue = externalFixture.report || externalFixture;
      assert.equal(await evaluate(`document.querySelectorAll('#report-guidance .report-item').length`),
        Object.values(reportValue.guidance.groups).reduce((total, items) => total + items.length, 0));
      assert.equal(await evaluate(`document.getElementById('report-evidence').open`), false);
      assert.equal(await evaluate('window.__unsafe'), undefined);
      assert((await content('report-guidance')).length > 150);
      await screenshot('conclusions');
      if (await evaluate(`!!document.querySelector('#report-guidance [data-report-steps] > summary')`)) {
        await click('#report-guidance [data-report-steps] > summary');
        await screenshot('steps', '#report-guidance [data-report-steps]');
      }
      await screenshot('cleanup', '#report-guidance [data-report-group="cleanup"]');
      await screenshot('cleanup-tutorial', '#report-guidance .report-tutorial');
      await click('#report-evidence > summary');
      assert.equal(await evaluate(`document.getElementById('report-evidence').open`), true);
      await screenshot('appendix', '#report-evidence > summary');
      await cdp('Emulation.setDeviceMetricsOverride', {width: 860, height: 560, deviceScaleFactor: 1, mobile: false});
      await screenshot('small');
      const priority = reportValue.guidance.groups.priority[0], cleanup = reportValue.guidance.groups.cleanup[0];
      if (priority) {
        await click('#report-guidance [data-report-action="detail"][data-path=' + JSON.stringify(priority.path) + ']');
        await waitFor(`!document.getElementById('drawer').hidden`);
        assert.equal(await evaluate('DRAWER_SKILL'), priority.path);
        await click('#btn-close-drawer');
      }
      if (cleanup) {
        await click('#report-guidance [data-report-action="location"][data-path=' + JSON.stringify(cleanup.path) + ']');
        await waitFor(`window.__report_actions.some(a=>a.name==='open_skill_location')`);
        assert.equal(await evaluate(`window.__report_actions.find(a=>a.name==='open_skill_location').payload.skill`), cleanup.path);
      }
      await click('#btn-copy-report');
      await waitFor(`window.__copied===window.__report_data.markdown`);
      assert.equal(await evaluate('window.__copied'), reportValue.markdown);
      await click('#btn-export-report-md'); await waitFor(`!document.getElementById('btn-export-report-md').disabled`);
      await click('#btn-export-report-html'); await waitFor(`!document.getElementById('btn-export-report-html').disabled`);
      assert.deepEqual(await evaluate(`window.__report_actions.filter(a=>a.name==='export_report').map(a=>a.payload)`),
        [{id: reportValue.id, format: 'md'}, {id: reportValue.id, format: 'html'}]);
      assert(!await evaluate(`window.__report_actions.some(a=>['delete','remove_skill','quarantine','batch_action'].includes(a.name))`));
    });
  } else {
  await check('conclusions_and_actions_precede_collapsed_evidence', async () => {
    await generate();
    assert((await content('report-guidance')).includes('敏感信息可能被发到外部'));
    const structure = await evaluate(`(()=>{const guidance=document.getElementById('report-guidance'),evidence=document.getElementById('report-evidence');
      return{visible:!!guidance&&!guidance.hidden,details:evidence?.tagName,open:evidence?.open,
      precedes:!!(guidance?.compareDocumentPosition(evidence)&Node.DOCUMENT_POSITION_FOLLOWING),
      actionable:!!guidance?.querySelector('[data-report-action="detail"]')};})()`);
    assert.deepEqual(structure, {visible: true, details: 'DETAILS', open: false, precedes: true, actionable: true});
    assert(!(await content('report-preview')).includes(evidence), 'Technical evidence should be in the collapsed appendix');
    await configureAppearance(); await screenshot('conclusions');
  });
  await check('risk_detail_uses_exact_path_among_same_name_copies', async () => {
    await generate(); await click('#report-guidance [data-report-action="detail"]');
    await waitFor(`!document.getElementById('drawer').hidden`);
    assert.equal(await evaluate('DRAWER_SKILL'), secondPath);
    assert((await content('drawer-body')).includes(secondPath));
    assert(!await evaluate(`window.__report_actions.some(a=>['trust','quarantine','review','batch_action'].includes(a.name))`));
  });
  await check('attention_action_opens_needs_attention_filter', async () => {
    await evaluate(`document.getElementById('skill-search').value='nonmatching old filter';document.getElementById('skill-search').dispatchEvent(new Event('input',{bubbles:true}))`);
    await generate(); await click('#report-guidance [data-report-action="attention"]');
    assert.equal(await evaluate('ACTIVE_SCREEN'), 'security');
    assert.equal(await evaluate(`document.querySelector('[data-filter="attention"]').getAttribute('aria-pressed')`), 'true');
    assert.equal(await evaluate(`document.getElementById('skill-search').value`), '');
    assert.equal(await evaluate(`document.getElementById('drawer').hidden`), true);
    assert.equal(await evaluate(`document.querySelectorAll('#sec-rows tr[data-skill]').length`), 3);
  });
  await check('cleanup_location_is_exact_and_never_deletes', async () => {
    await generate(); await click(cleanupLocation);
    await waitFor(`window.__report_actions.some(a=>a.name==='open_skill_location')`);
    assert.equal(await evaluate(`window.__report_actions.find(a=>a.name==='open_skill_location').payload.skill`), cleanupPath);
    assert(!await evaluate(`window.__report_actions.some(a=>['delete','remove_skill','quarantine','batch_action'].includes(a.name))`));
  });
  await check('usage_action_selects_idle_after_ranking', async () => {
    await click('nav.sidebar button[data-screen="usage"]'); await waitFor(`USAGE_PHASE==='ready'`);
    await click('[data-usage-view="ranking"]'); await click('nav.sidebar button[data-screen="reports"]');
    await generate(); await click('#report-guidance [data-report-action="usage"]');
    assert.equal(await evaluate('ACTIVE_SCREEN'), 'usage');
    assert.equal(await evaluate(`document.querySelector('[data-usage-view="idle"]').getAttribute('aria-pressed')`), 'true');
    await waitFor(`document.getElementById('usage-detail').innerText.includes('unused')`);
  });
  await check('manual_steps_expand_and_include_recovery_guidance', async () => {
    await generate();
    assert.equal(await evaluate(`document.querySelector('#report-guidance [data-report-steps]').tagName`), 'DETAILS');
    assert.equal(await evaluate(`document.querySelector('#report-guidance [data-report-steps]').open`), false);
    await click('#report-guidance [data-report-steps] > summary');
    assert.equal(await evaluate(`document.querySelector('#report-guidance [data-report-steps]').open`), true);
    assert((await evaluate(`document.querySelector('#report-guidance [data-report-steps]').innerText`)).includes('打开检查详情'));
    const full = await content('report-guidance');
    for (const word of ['备份', '回收站', '重启', '恢复', '共享', '停止监听']) assert(full.includes(word), 'Missing manual guidance: ' + word);
    await configureAppearance(); await screenshot('steps', '#report-guidance [data-report-steps]');
    await screenshot('cleanup', cleanupLocation);
    await screenshot('cleanup-tutorial', '#report-guidance > :last-child');
  });
  await check('technical_appendix_expands_without_running_raw_html', async () => {
    await generate(); await click('#report-evidence > summary');
    assert.equal(await evaluate(`document.getElementById('report-evidence').open`), true);
    assert((await content('report-evidence')).includes(evidence));
    assert.equal(await evaluate('window.__unsafe'), undefined);
    assert.equal(await evaluate(`document.querySelector('#screen-reports script,#screen-reports [onerror]')`), null);
    await configureAppearance(); await screenshot('appendix', '#report-evidence > summary');
  });
  await check('copy_and_download_keep_the_complete_report', async () => {
    await generate(); await click('#btn-copy-report'); await waitFor(`window.__copied.includes('SHA-256 evidence')`);
    assert.equal(await evaluate('window.__copied'), markdown);
    await click('#btn-export-report-md'); await waitFor(`!document.getElementById('btn-export-report-md').disabled`);
    await click('#btn-export-report-html'); await waitFor(`!document.getElementById('btn-export-report-html').disabled`);
    assert.deepEqual(await evaluate(`window.__report_actions.filter(a=>a.name==='export_report').map(a=>a.payload)`),
      [{id: 'report-guidance-1', format: 'md'}, {id: 'report-guidance-1', format: 'html'}]);
  });
  await check('legacy_report_still_previews_and_offers_new_report', async () => {
    await evaluate(`delete window.__report_data.guidance;window.__report_data.markdown='# Legacy report\\n\\nSaved technical evidence.'`);
    await evaluate(`document.getElementById('report-history').value='report-guidance-1';document.getElementById('report-history').dispatchEvent(new Event('change',{bubbles:true}))`);
    await waitFor(`document.getElementById('report-preview').innerText.includes('Saved technical evidence')`);
    assert(/旧|历史|legacy|older/i.test(await content('report-guidance')));
    assert(/生成|generate/i.test(await content('report-guidance')));
    assert.equal(await evaluate(`document.getElementById('btn-copy-report').disabled`), false);
    assert.equal(await evaluate(`document.querySelectorAll('#report-guidance [data-report-action="detail"]').length`), 0);
  });
  await check('history_missing_skill_gives_feedback_without_wrong_detail', async () => {
    await evaluate(`window.__report_data.guidance.groups.priority[0].path='F:/Gone/same-name'`);
    await generate(); await click('#report-guidance [data-report-action="detail"]');
    await waitFor(`document.getElementById('toast').classList.contains('show') || !document.getElementById('report-status').hidden`);
    assert.equal(await evaluate(`document.getElementById('drawer').hidden`), true);
    const feedback = (await content('toast')) + (await content('report-status'));
    assert(/不存在|找不到|未找到|当前|not found|no longer|current/i.test(feedback), feedback);
    assert(!await evaluate(`window.__report_actions.some(a=>a.name==='get_markdown')`));
  });
  await check('report_metadata_cannot_inject_markup_links_or_actions', async () => {
    await evaluate(`const item=window.__report_data.guidance.groups.priority[0];
      item.name='<img src=x onerror="window.__unsafe=true">';
      item.reason='<a href="https://example.invalid" data-report-action="location" data-path="C:/Secrets">fake</a><script>window.__unsafe=true</script>';
      item.path='F:/<svg onload="window.__unsafe=true">';
      item.steps=['<button data-report-action="delete">Delete everything</button>'];`);
    await generate();
    assert.equal(await evaluate('window.__unsafe'), undefined);
    assert.equal(await evaluate(`document.querySelector('#report-guidance img,#report-guidance script,#report-guidance a,#report-guidance [onload],#report-guidance [onerror],#report-guidance [data-report-action="delete"]')`), null);
    assert((await content('report-guidance')).includes('<img src=x'));
    await click('#report-guidance [data-report-steps] > summary');
    assert((await content('report-guidance')).includes('Delete everything'));
    assert.equal(await evaluate(`window.__report_actions.some(a=>a.payload?.skill==='C:/Secrets')`), false);
  });
  await check('english_action_controls_and_headings_are_translated', async () => {
    await generate(); await evaluate(`changeLanguage('en')`);
    const labels = await evaluate(`Array.from(document.querySelectorAll('#report-guidance button,#report-guidance h2,#report-guidance h3,#report-guidance summary,#report-evidence > summary'))
      .map(el=>el.innerText+' '+(el.getAttribute('aria-label')||'')+' '+(el.title||'')).join('\\n')`);
    assert(!/[\u4e00-\u9fff]/.test(labels), labels);
    assert(/attention/i.test(labels) && /folder/i.test(labels), labels);
    assert.equal(await evaluate(`document.getElementById('reports-title').innerText`), 'Check reports');
    const history = await evaluate(`document.getElementById('report-history').selectedOptions[0].textContent`);
    assert.equal(history, '2026-10-05 12:00:00');
  });
  await check('expanded_guidance_and_appendix_survive_poll_and_language_change', async () => {
    await generate(); await click('#report-guidance [data-report-steps] > summary');
    await click('#report-guidance [data-report-group="handled"] > summary');
    await click('#report-evidence > summary');
    await evaluate(`tick()`); await evaluate(`tick()`);
    const expanded = () => evaluate(`({steps:document.querySelector('#report-guidance [data-report-steps]').open,
      handled:document.querySelector('#report-guidance [data-report-group="handled"]').open,
      evidence:document.getElementById('report-evidence').open})`);
    assert.deepEqual(await expanded(), {steps: true, handled: true, evidence: true});
    await evaluate(`changeLanguage('en')`);
    assert.deepEqual(await expanded(), {steps: true, handled: true, evidence: true});
    await evaluate(`changeLanguage('zh-CN')`);
    assert.deepEqual(await expanded(), {steps: true, handled: true, evidence: true});
  });
  await check('pending_folder_open_prevents_duplicate_requests_across_poll', async () => {
    await generate(); await evaluate(`window.__location_pending=true`); await click(cleanupLocation);
    await waitFor(`typeof window.__resolve_location==='function'`);
    await evaluate(`tick()`);
    assert.equal(await evaluate(`document.querySelector(${JSON.stringify(cleanupLocation)}).disabled`), true);
    await evaluate(`document.querySelector(${JSON.stringify(cleanupLocation)}).click()`);
    assert.equal(await evaluate(`window.__report_actions.filter(a=>a.name==='open_skill_location').length`), 1);
    await evaluate(`window.__resolve_location()`);
    await waitFor(`!document.querySelector(${JSON.stringify(cleanupLocation)}).disabled`);
  });
  await check('missing_check_coverage_does_not_claim_no_pending_problems', async () => {
    await evaluate(`window.__report_data.guidance.groups.priority=[];window.__report_data.guidance.groups.confirm=[];
      window.__report_data.guidance.coverage={checks_available:false,usage_available:true,usage_complete:true,unchecked_installed:1};
      window.__report_data.guidance.overview_notes=['尚无安全检查记录，请先完成安全检查。'];`);
    await generate();
    const conclusion = await evaluate(`document.querySelector('#report-guidance .report-conclusion h2').innerText`);
    assert(/补齐|检查信息|complete.*check/i.test(conclusion), conclusion);
    assert(!/没有待确认|no pending/i.test(conclusion), conclusion);
    assert((await content('report-guidance')).includes('尚无安全检查记录'));
    await evaluate(`changeLanguage('en')`);
    const english = await evaluate(`document.querySelector('#report-guidance .report-conclusion h2').innerText`);
    assert(/complete.*check/i.test(english), english);
    assert(!/no pending/i.test(english), english);
  });
  await check('unavailable_folder_gives_plain_feedback_and_reenables_action', async () => {
    await generate(); await evaluate(`window.__location_error=true`); await click(cleanupLocation);
    await waitFor(`!document.querySelector(${JSON.stringify(cleanupLocation)}).disabled`);
    const feedback = await content('toast');
    assert(/文件夹|位置|不存在|已移动|已移除/.test(feedback), feedback);
    assert(!feedback.includes('skill location does not exist'), feedback);
    assert.equal(await evaluate(`document.getElementById('drawer').hidden`), true);
  });
  await check('report_layout_fits_light_dark_and_supported_window_sizes', async () => {
    await generate();
    assert.equal(await evaluate(`!!document.getElementById('report-guidance')`), true, 'Actionable report panel must exist');
    const failures = [];
    for (const size of [{width: 1080, height: 720}, {width: 860, height: 560}]) {
      await cdp('Emulation.setDeviceMetricsOverride', {...size, deviceScaleFactor: 1, mobile: false});
      for (const appearance of ['light', 'dark']) {
        if (await evaluate('document.documentElement.dataset.theme') !== appearance) await click('#btn-theme');
        const overflow = await evaluate(`(()=>{const main=document.getElementById('main-content'),panel=document.getElementById('report-guidance');
          return document.documentElement.scrollWidth>innerWidth+1||main.scrollWidth>main.clientWidth+1||panel.scrollWidth>panel.clientWidth+1;})()`);
        if (overflow) failures.push(`${size.width}/${appearance}`);
      }
    }
    assert.deepEqual(failures, []);
    await configureAppearance(); await screenshot('small');
  });
  }
} catch (error) {results.push({name: 'harness', ok: false, error: error.stack || error.message});}
finally {
  try {if (closeBrowser) await closeBrowser();} catch {}
  if (ws) ws.close();
  if (browser && browser.exitCode === null) {browser.kill(); await Promise.race([new Promise(resolve => browser.once('exit', resolve)), pause(2000)]);}
  assert(profile.startsWith(resolve(tmpdir()) + sep) && basename(profile).startsWith('skill-radar-report-ui-'));
  try {rmSync(profile, {recursive: true, force: true, maxRetries: 6, retryDelay: 150});} catch {}
}
console.log(JSON.stringify({html: htmlPath, browser: browserPath, results}));
process.exitCode = results.some(row => !row.ok) ? 1 : 0;
