// Real Chromium regressions for compact header controls and appearance persistence.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync, readFileSync, writeFileSync, mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename, dirname, resolve, sep} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const args = process.argv.slice(2);
const option = name => args.includes(name) ? args[args.indexOf(name) + 1] : null;
const browserPath = option('--browser') || process.env.SKILL_RADAR_BROWSER;
assert(browserPath && existsSync(browserPath), 'Provide Chromium with --browser or SKILL_RADAR_BROWSER');
assert(typeof WebSocket === 'function', 'Node 22+ is required for its native WebSocket');
const htmlPath = resolve(option('--html') || resolve(repo, 'tray/web/index.html'));
const captureDir = option('--capture-dir');
const profile = mkdtempSync(resolve(tmpdir(), 'skill-radar-header-'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const readPort = async file => {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try { return readFileSync(file, 'utf8').split(/\r?\n/)[0]; }
    catch (error) {
      if (error.code !== 'EBUSY' && error.code !== 'EACCES') throw error;
      await pause(40);
    }
  }
  throw new Error(`Chromium endpoint file remained locked: ${file}`);
};
const state = {
  guard: 'running', paused: false, watched_roots: 1, today: {},
  settings: {language: 'zh-CN', roots: [{path: 'F:/Skills'}]},
  skills: {
    'F:/Skills/frontend-design': {name: 'frontend-design', score: 0, status: 'scanned', findings: []},
    'F:/Skills/code-review': {name: 'code-review', score: 0, status: 'scanned', findings: []},
  },
  events: [{ts: '2026-10-05T10:30:00+08:00', kind: 'new', text: '新技能 frontend-design'}],
};
const bridge = `window.__header_boot=crypto.randomUUID();
window.__header_state=${JSON.stringify(state)};window.__header_actions=[];
window.__header_theme_error=false;window.__header_theme_delay=60;
window.__header_state_delay=0;window.__header_state_captured=false;
try {window.__header_state.settings.language=localStorage.getItem('skillradar-language') || 'zh-CN';}catch{}
window.pywebview={api:{
  get_state:async()=>{
    const snapshot=structuredClone(window.__header_state);window.__header_state_captured=true;
    if(window.__header_state_delay)await new Promise(resolve=>setTimeout(resolve,window.__header_state_delay));
    return snapshot;
  },
  act:async(name,payload)=>{
    window.__header_actions.push({name,payload});
    if(name==='set_language') {
      await new Promise(resolve=>setTimeout(resolve,60));
      window.__header_state.settings.language=payload.language;return{ok:true};
    }
    if(name==='set_theme') {
      await new Promise(resolve=>setTimeout(resolve,window.__header_theme_delay));
      if(window.__header_theme_error)return{error:'theme save failed'};
      window.__header_state.settings.theme=payload.theme;return{ok:true,theme:payload.theme};
    }
    if(name==='get_usage')return{ok:true,rows:[],installed_rows:[],
      idle_groups:{never:[],inactive:[],unknown:[]},inventory_summary:{total_installed:0,coverage_complete:true},
      status:{phase:'ready',scanning:false,files_scanned:0,roots:[],errors:[]}};
    if(name==='list_reports')return{ok:true,reports:[]};
    return{ok:true};
  }
}};`;

let browser, ws, cdp;
const results = [];
try {
  browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  {windowsHide: true, stdio: 'ignore'});
  browser.on('error', error => results.push({name: 'browser_start', ok: false, error: error.message}));
  const portFile = resolve(profile, 'DevToolsActivePort');
  const deadline = Date.now() + 12000;
  while (!existsSync(portFile) && Date.now() < deadline) await pause(40);
  assert(existsSync(portFile), 'Chromium did not expose its temporary endpoint');
  const port = await readPort(portFile);
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = pages.find(page => page.type === 'page');
  assert(target, 'Chromium did not create a page target');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, {once: true});
    ws.addEventListener('error', reject, {once: true});
  });
  let nextId = 0;
  const pending = new Map();
  ws.addEventListener('message', event => {
    const response = JSON.parse(event.data), request = pending.get(response.id);
    if (!request) return;
    pending.delete(response.id); clearTimeout(request.timeout);
    response.error ? request.reject(new Error(JSON.stringify(response.error))) : request.resolve(response.result);
  });
  cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId, timeout = setTimeout(() => {
      pending.delete(id); reject(new Error('CDP timeout: ' + method));
    }, 8000);
    pending.set(id, {resolve, reject, timeout}); ws.send(JSON.stringify({id, method, params}));
  });
  const evaluate = async expression => {
    const response = await cdp('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    assert(!response.exceptionDetails, JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  const waitFor = async expression => {
    const deadline = Date.now() + 3000;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await pause(25);
    }
    throw new Error('UI condition: ' + expression);
  };
  const click = async (selector, {settle = true} = {}) => {
    const point = await evaluate(`(()=>{
      const el=document.querySelector(${JSON.stringify(selector)});if(!el)return null;
      el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();
      return{x:r.x+r.width/2,y:r.y+r.height/2,width:r.width,height:r.height};
    })()`);
    assert(point?.width && point.height, 'Missing visible target: ' + selector);
    for (const type of ['mousePressed', 'mouseReleased']) await cdp('Input.dispatchMouseEvent', {
      type, x: point.x, y: point.y, button: 'left', clickCount: 1,
    });
    if (selector === '#btn-theme' && settle) await waitFor(`typeof THEME_BUSY==='undefined' || !THEME_BUSY`);
  };
  const key = async (key, code, keyCode) => {
    // Chromium uses the translated character to activate native buttons on
    // Enter; custom keydown handlers alone do not exercise that default action.
    const text = key === 'Enter' ? '\r' : key === ' ' ? ' ' : undefined;
    for (const type of ['keyDown', 'keyUp']) await cdp('Input.dispatchKeyEvent', {
      type, key, code, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode,
      ...(type === 'keyDown' && text ? {text, unmodifiedText: text} : {}),
    });
  };
  await cdp('Page.enable'); await cdp('Runtime.enable');
  await cdp('Emulation.setDeviceMetricsOverride', {width: 1280, height: 820, deviceScaleFactor: 1, mobile: false});
  await cdp('Page.addScriptToEvaluateOnNewDocument', {source: bridge});
  const navigate = async () => {
    const previous = await evaluate('window.__header_boot');
    await cdp('Page.navigate', {url: pathToFileURL(htmlPath).href});
    await waitFor(`window.__header_boot!==${JSON.stringify(previous)} && !!document.querySelector('#sec-rows tr[data-skill]') && CONNECTED`);
  };
  const reset = async () => {
    await navigate();
    await evaluate('try{localStorage.clear();}catch{}');
    await navigate();
  };
  const check = async (name, run) => {
    try {await reset(); const evidence = await run(); results.push({name, ok: true, evidence});}
    catch (error) {results.push({name, ok: false, error: error.stack || error.message});}
  };
  const theme = () => evaluate('document.documentElement.dataset.theme');
  const themeAction = async expected => {
    const labels = await evaluate(`(()=>{const b=document.getElementById('btn-theme');return b?{title:b.title,label:b.getAttribute('aria-label')}:null;})()`);
    assert.deepEqual(labels, {title: expected, label: expected}, 'Appearance control must name the action it will perform');
  };
  const changeLanguage = async language => {
    // Native select keyboard events differ between WebKit/Chromium platforms;
    // dispatch the same user-facing change event after setting the selected option.
    await evaluate(`(()=>{const s=document.getElementById('ui-language');s.value=${JSON.stringify(language)};s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await waitFor(`window.__header_state.settings.language===${JSON.stringify(language)} && !LANGUAGE_BUSY`);
  };
  const layout = () => evaluate(`(()=>{
    const errors=[],top=document.querySelector('.topbar'),main=document.getElementById('main-content');
    if(document.documentElement.scrollWidth>innerWidth+1 || main.scrollWidth>main.clientWidth+1)errors.push('Horizontal overflow');
    for(const id of ['btn-theme','ui-language']) {
      const el=document.getElementById(id);if(!el){errors.push('Missing '+id);continue;}
      const r=el.getBoundingClientRect(),t=top.getBoundingClientRect();
      if(r.width<30 || r.height<30)errors.push(id+' target is too small');
      if(r.left<-1 || r.right>innerWidth+1 || r.top<t.top-1 || r.bottom>t.bottom+1)errors.push(id+' exceeds header');
    }
    return{errors,width:innerWidth,height:innerHeight};
  })()`);

  await check('header_controls_are_compact_icons', async () => {
    const evidence = await evaluate(`(()=>{
      const top=document.querySelector('.topbar'),select=document.getElementById('ui-language'),button=document.getElementById('btn-theme');
      return{topText:top.innerText,themeIcon:!!button?.querySelector('svg'),
        languageIcon:!!select?.parentElement.querySelector('svg'),
        languageAccessible:!!select?.getAttribute('aria-label'),
        languages:Array.from(select?.options || []).map(o=>o.value),
        workspaceRemoved:!document.getElementById('environment-label'),syncRemoved:!document.getElementById('sync-time')};
    })()`);
    assert(evidence.themeIcon, 'Provide the appearance icon button');
    assert(evidence.languageIcon, 'Show a translation icon around the native language selector');
    assert(evidence.languageAccessible, 'Name the icon language selector for assistive technology');
    assert.deepEqual(evidence.languages, ['zh-CN', 'en']);
    assert(evidence.workspaceRemoved && evidence.syncRemoved, 'Remove redundant workspace and synchronized labels');
    assert(!/界面语言|Interface language|本机工作区|Local workspace|已同步|Synced/.test(evidence.topText), 'The compact header must not show removed copy');
    return evidence;
  });
  await check('appearance_defaults_to_light', async () => {
    assert.equal(await theme(), 'light', 'A new profile must start with the light appearance');
    await themeAction('切换到深色外观');
  });
  await check('appearance_toggle_preserves_screen_filters_and_language', async () => {
    await click('nav.sidebar button[data-screen="security"]');
    await click('.filter[data-filter="attention"]');
    await evaluate(`(()=>{const input=document.getElementById('skill-search');input.value='frontend';input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
    const before = await evaluate(`({screen:ACTIVE_SCREEN,filter:FILTER,query:document.getElementById('skill-search').value,language:document.documentElement.lang})`);
    const lightColor = await evaluate(`getComputedStyle(document.body).backgroundColor`);
    await click('#btn-theme');
    assert.equal(await theme(), 'dark'); await themeAction('切换到浅色外观');
    assert.notEqual(await evaluate('getComputedStyle(document.body).backgroundColor'), lightColor, 'The appearance toggle must visibly change the palette');
    assert.deepEqual(await evaluate(`({screen:ACTIVE_SCREEN,filter:FILTER,query:document.getElementById('skill-search').value,language:document.documentElement.lang})`), before);
    assert.equal(await evaluate(`localStorage.getItem('skill-radar-theme')`), 'dark');
    await click('#btn-theme'); assert.equal(await theme(), 'light'); await themeAction('切换到深色外观');
  });
  await check('appearance_persists_after_reload_with_language_and_page', async () => {
    await click('nav.sidebar button[data-screen="settings"]');
    await changeLanguage('en'); await click('#btn-theme');
    assert.equal(await theme(), 'dark');
    await navigate();
    assert.equal(await theme(), 'dark', 'Reload must retain the saved dark appearance');
    await themeAction('Switch to light appearance');
    assert.equal(await evaluate('ACTIVE_SCREEN'), 'settings');
    assert.equal(await evaluate('document.documentElement.lang'), 'en');
    assert.equal(await evaluate(`document.getElementById('ui-language').value`), 'en');
  });
  await check('language_native_keyboard_and_bridge_persistence', async () => {
    await click('nav.sidebar button[data-screen="security"]');
    await evaluate(`document.getElementById('ui-language').focus()`);
    await key('ArrowDown', 'ArrowDown', 40); await key('Enter', 'Enter', 13);
    // macOS headless Chromium does not always commit a native select's
    // keyboard choice; exercise the same change event after the native attempt
    // so the bridge persistence assertion remains cross-platform.
    if (!await evaluate(`window.__header_actions.some(a=>a.name==='set_language' && a.payload.language==='en')`)) {
      await evaluate(`(()=>{const select=document.getElementById('ui-language');select.value='en';select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    }
    await waitFor(`window.__header_state.settings.language==='en' && !LANGUAGE_BUSY`);
    assert.equal(await evaluate('document.documentElement.lang'), 'en');
    assert.equal(await evaluate('ACTIVE_SCREEN'), 'security');
    assert(await evaluate(`window.__header_actions.some(a=>a.name==='set_language' && a.payload.language==='en')`));
    await themeAction('Switch to dark appearance');
    await click('#btn-theme'); assert.equal(await theme(), 'dark');
    await changeLanguage('zh-CN'); await themeAction('切换到浅色外观');
    assert.equal(await theme(), 'dark', 'Language changes must retain the selected appearance');
  });
  await check('appearance_keyboard_activation', async () => {
    assert(await evaluate(`!!document.getElementById('btn-theme')`), 'Provide the focusable appearance button');
    await evaluate(`document.getElementById('btn-theme').focus()`);
    await key('Enter', 'Enter', 13); assert.equal(await theme(), 'dark');
    await waitFor(`typeof THEME_BUSY==='undefined' || !THEME_BUSY`);
    assert.equal(await evaluate(`document.activeElement.id`), 'btn-theme',
      'Finishing the appearance save must retain keyboard focus on its button');
    await key(' ', 'Space', 32); assert.equal(await theme(), 'light');
    await waitFor(`typeof THEME_BUSY==='undefined' || !THEME_BUSY`);
  });
  await check('appearance_ignores_invalid_saved_value', async () => {
    await evaluate(`localStorage.setItem('skill-radar-theme','invalid-theme')`); await navigate();
    assert.equal(await theme(), 'light', 'Unknown saved appearances must fall back to light');
    await click('#btn-theme'); assert.equal(await theme(), 'dark');
  });
  await check('appearance_works_when_storage_is_unavailable', async () => {
    const blocked = await cdp('Page.addScriptToEvaluateOnNewDocument', {source: `Object.defineProperty(window,'localStorage',{configurable:true,get(){throw new DOMException('Storage disabled','SecurityError');}});`});
    try {
      await navigate(); assert.equal(await theme(), 'light');
      await click('#btn-theme'); assert.equal(await theme(), 'dark');
      await click('#btn-theme'); assert.equal(await theme(), 'light');
      await changeLanguage('en'); await themeAction('Switch to dark appearance');
      assert.equal(await evaluate('CONNECTED'), true, 'Blocked storage must not break the desktop bridge');
    } finally {await cdp('Page.removeScriptToEvaluateOnNewDocument', {identifier: blocked.identifier});}
  });
  await check('header_controls_fit_supported_window_sizes', async () => {
    const evidence = [];
    for (const [width, height] of [[1280, 820], [960, 720], [700, 650]]) {
      await cdp('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
      const audit = await layout(); evidence.push(audit); assert.deepEqual(audit.errors, [], JSON.stringify(audit));
    }
    return evidence;
  });
  await check('appearance_saved_by_bridge_restores_after_private_restart', async () => {
    await click('#btn-theme');
    assert.equal(await evaluate(`window.__header_state.settings.theme`), 'dark',
      'The desktop bridge must save the appearance independently of private browser storage');
    assert(await evaluate(`window.__header_actions.some(a=>a.name==='set_theme' && a.payload.theme==='dark')`));
    const saved = await evaluate('window.__header_state.settings.theme');
    await evaluate('localStorage.clear()');
    // A new desktop renderer receives persisted settings even though pywebview
    // private mode creates an empty browser storage on the next application run.
    const restored = await cdp('Page.addScriptToEvaluateOnNewDocument', {
      source: `window.__header_state.settings.theme=${JSON.stringify(saved)};`,
    });
    try {
      await navigate();
      assert.equal(await theme(), 'dark', 'The saved desktop appearance must restore after browser storage was cleared');
      await themeAction('切换到浅色外观');
    } finally {await cdp('Page.removeScriptToEvaluateOnNewDocument', {identifier: restored.identifier});}
  });
  await check('appearance_bridge_save_failure_restores_previous_theme', async () => {
    await evaluate(`window.__header_state.settings.theme='light';window.__header_theme_error=true;`);
    await click('#btn-theme');
    assert.equal(await theme(), 'light', 'A failed desktop save must restore the previously saved appearance');
    assert.equal(await evaluate(`window.__header_state.settings.theme`), 'light');
    assert.equal(await evaluate(`localStorage.getItem('skill-radar-theme')`), 'light', 'The local fallback must roll back with the desktop save');
    await themeAction('切换到深色外观');
    assert(await evaluate(`window.__header_actions.some(a=>a.name==='set_theme')`), 'The failure must come from an attempted desktop save');
    assert(await evaluate(`document.getElementById('toast').classList.contains('error')`), 'Explain a failed appearance save to the user');
  });
  await check('appearance_stale_state_after_save_does_not_reverse_selection', async () => {
    await evaluate(`window.__header_state.settings.theme='light';`); await evaluate('tick()');
    await evaluate(`(()=>{
      window.__header_state_delay=180;window.__header_state_captured=false;window.__header_theme_mutations=[];
      new MutationObserver(()=>window.__header_theme_mutations.push(document.documentElement.dataset.theme))
        .observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    })()`);
    await evaluate('void tick()'); await waitFor('window.__header_state_captured');
    await click('#btn-theme');
    assert.equal(await evaluate(`window.__header_state.settings.theme`), 'dark', 'Complete the new desktop save while an older light snapshot is pending');
    await pause(240);
    assert.equal(await theme(), 'dark', 'An older get_state response arriving after saving must not undo the new theme');
    assert(!(await evaluate('window.__header_theme_mutations')).includes('light'), 'The appearance must never flash back to the old theme during saving');
  });
  await check('appearance_pending_save_prevents_duplicate_activation', async () => {
    const initial = await evaluate(`({theme:document.documentElement.dataset.theme,busy:THEME_BUSY,
      saved:window.__header_state.settings.theme,storage:localStorage.getItem('skill-radar-theme')})`);
    assert.equal(initial.theme, 'light', 'Start the duplicate-activation check in the reset light appearance: ' + JSON.stringify(initial));
    await evaluate('window.__header_theme_delay=600');
    await click('#btn-theme', {settle: false});
    assert.equal(await evaluate(`typeof THEME_BUSY!=='undefined' && THEME_BUSY`), true, 'Keep an appearance save busy until the desktop confirms it');
    assert.equal(await theme(), 'dark'); await themeAction('切换到浅色外观');
    assert.equal(await evaluate(`document.getElementById('btn-theme').disabled`), true, 'Prevent repeated theme activations while saving');
    await click('#btn-theme', {settle: false});
    await waitFor(`typeof THEME_BUSY!=='undefined' && !THEME_BUSY`);
    assert.equal(await evaluate(`window.__header_actions.filter(a=>a.name==='set_theme').length`), 1,
      'Repeated activation must not race two saves');
    assert.equal(await theme(), 'dark');
    assert.equal(await evaluate(`document.getElementById('btn-theme').disabled`), false, 'Re-enable the appearance button after saving');
  });

  if (captureDir) {
    mkdirSync(captureDir, {recursive: true});
    for (const [width, height] of [[1280, 820], [960, 720], [700, 650]]) {
      await cdp('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
      await reset();
      for (const language of ['zh-CN', 'en']) {
        await changeLanguage(language);
        for (const appearance of ['light', 'dark']) {
          if (await theme() !== appearance) await click('#btn-theme');
          await pause(180);
          const name = `header-${width}-${language}-${appearance}.png`;
          const audit = await layout(); results.push({name: 'layout_' + name, ok: !audit.errors.length, evidence: audit});
          const screenshot = await cdp('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
          writeFileSync(resolve(captureDir, name), Buffer.from(screenshot.data, 'base64'));
        }
      }
    }
    await cdp('Emulation.setDeviceMetricsOverride', {width: 1280, height: 820, deviceScaleFactor: 1, mobile: false});
    await reset();
    for (const language of ['zh-CN', 'en']) {
      await changeLanguage(language); await click('#ui-language'); await pause(120);
      const screenshot = await cdp('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      writeFileSync(resolve(captureDir, `header-language-menu-${language}.png`), Buffer.from(screenshot.data, 'base64'));
      await key('Escape', 'Escape', 27);
    }
    // Keep the behavior checks above minimal, then add representative records
    // only for visual QA of all pages, action colors, path badges and details.
    await cdp('Emulation.setDeviceMetricsOverride', {width: 1080, height: 720, deviceScaleFactor: 1, mobile: false});
    await reset();
    await evaluate(`(()=>{
      const finding={severity:'WARNING',rule_id:'REMOTE_RESOURCE',file:'SKILL.md',line:18,
        message:'包含访问外部地址的指令。',excerpt:'Download public project assets into the local assets directory.'};
      Object.assign(window.__header_state.skills['F:/Skills/frontend-design'],{
        status:'drifted',score:28,raw_score:28,findings:[finding],raw_findings:[finding],version:'frontend-visual-v2',
        check_status:'attention',review_status:'pending',scan_complete:true,scanned_at:'2026-10-05T10:32:00+08:00',
        installed_at:'2026-08-01T02:03:04Z',updated_at:'2026-10-04T04:05:06Z',
        reason:'技能说明已更新，请确认新增的下载操作是否符合你的预期。'});
      window.__header_state.skills['F:/Other/frontend-design']={name:'frontend-design',score:0,status:'scanned',findings:[]};
      window.__header_state.skills['F:/Skills/database-helper']={name:'database-helper',score:46,status:'blocked',
        version:'database-visual-v1',raw_score:46,check_status:'attention',review_status:'pending',scan_complete:true,
        reason:'发现可能读取并上传本机私密文件的指令。',findings:[{...finding,severity:'CRITICAL',rule_id:'DATA_EXFILTRATION',
        message:'可能把私密文件发送到外部地址。'}]};
      window.__header_state.settings.block=true;window.__header_state.settings.quarantine=false;
      window.__header_state.settings.roots.push({path:'F:/Other'});
      const never={name:'unused-helper',path:'F:/Skills/unused-helper',total:0,last:null,installed_at:'2026-08-01T02:03:04Z',
        updated_at:'2026-09-01T04:05:06Z',usage_state:'never'};
      const active={name:'frontend-design',path:'F:/Skills/frontend-design',total:42,last:'2026-10-05T10:20:00+08:00',usage_state:'active'};
      const usage={ok:true,rows:[{name:active.name,total:42,codex:30,zcode:10,claude:2,marker:0,last:active.last}],
        installed_rows:[never,active],idle_groups:{never:[never],inactive:[],unknown:[]},
        inventory_summary:{total_installed:2,never:1,inactive:0,unknown:0,active:1,total_invocations:42,coverage_complete:true,inventory_complete:true},
        status:{phase:'ready',scanning:false,files_scanned:12,new_records:0,last_scan:'2026-10-05T10:35:00+08:00',errors:[],
          roots:[{source:'codex',path:'F:/CodexData/.codex/sessions',exists:true}]}};
      const report={id:'visual-report',generated_at:'2026-10-05T10:36:00+08:00',
        markdown:${JSON.stringify('# Skill check report\n\n## Idle skills\n\n| Skill | Invocations | Status |\n| --- | --- | --- |\n| unused-helper | 0 | No invocation records |\n\n## Security findings\n\n**frontend-design** has a changed download instruction.\n\n> Verify public asset sources before confirming.\n\n~~~sh\nDownload public project assets.\n~~~')}};
      const original=window.pywebview.api.act;
      window.pywebview.api.act=async(name,payload)=>{
        if(name==='get_usage')return structuredClone(usage);
        if(name==='generate_report'||name==='get_report')return{ok:true,report};
        if(name==='list_reports')return{ok:true,reports:[report]};
        if(name==='get_markdown')return{ok:true,markdown:${JSON.stringify('# Skill results\n\n## Finding\n\n**REMOTE_RESOURCE**: Verify the public asset source.')}};
        return original(name,payload);
      };
    })()`);
    await evaluate('tick()');
    const visualAudit = () => evaluate(`(()=>{
      // Canvas converts both modern oklch tokens and rgb colors into actual
      // sRGB pixels, instead of interpreting oklch components as RGB numbers.
      const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
      const context=canvas.getContext('2d',{willReadFrequently:true}),colors=new Map();
      const rgb=value=>{
        if(colors.has(value))return colors.get(value);
        context.clearRect(0,0,1,1);context.fillStyle=value;context.fillRect(0,0,1,1);
        const data=context.getImageData(0,0,1,1).data,result=[data[0],data[1],data[2],data[3]/255];
        colors.set(value,result);return result;
      };
      const blend=(front,back)=>{const a=front.length>3?front[3]:1;return front.slice(0,3).map((c,i)=>c*a+back[i]*(1-a));};
      const background=el=>{const chain=[];for(let p=el;p;p=p.parentElement)chain.unshift(p);return chain.reduce((b,p)=>blend(rgb(getComputedStyle(p).backgroundColor),b),[255,255,255]);};
      const luminance=color=>color.map(c=>{c/=255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;}).reduce((v,c,i)=>v+c*[.2126,.7152,.0722][i],0);
      const ratio=(a,b)=>{const values=[luminance(a),luminance(b)].sort((a,b)=>b-a);return(values[0]+.05)/(values[1]+.05);};
      const lowContrast=[],samples=[];
      for(const el of document.querySelectorAll('body *')) {
        if(!Array.from(el.childNodes).some(n=>n.nodeType===3&&n.textContent.trim()))continue;
        const r=el.getBoundingClientRect(),css=getComputedStyle(el);
        if(!r.width||!r.height||r.bottom<=0||r.top>=innerHeight||el.closest('[hidden],:disabled')||document.getElementById('drawer').hidden===false&&el.closest('#app-layout'))continue;
        let invisible=false;for(let p=el;p;p=p.parentElement){const s=getComputedStyle(p);if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0){invisible=true;break;}}
        if(invisible)continue;
        const bg=background(el),fg=blend(rgb(css.color),bg),contrast=ratio(fg,bg),large=parseFloat(css.fontSize)>=24||parseFloat(css.fontSize)>=18.667&&Number(css.fontWeight)>=600;
        const item={id:el.id||el.className,text:el.textContent.trim().slice(0,80),contrast:Math.round(contrast*100)/100,fg:css.color,bg};
        if(contrast<(large?3:4.5)-.01)lowContrast.push(item);
        if(el.matches('.btn.primary,.path-badge,.badge.crit,.badge.warn,[data-batch-action]'))samples.push(item);
      }
      return{lowContrast,samples};
    })()`);
    const capturePage = async name => {
      await evaluate(`document.getElementById('main-content').scrollTop=0;document.getElementById('toast').classList.remove('show')`);
      await pause(180);
      const audit = await visualAudit(), bounds = await layout();
      results.push({name: 'visual_' + name, ok: !bounds.errors.length, evidence: {...bounds, ...audit}});
      const screenshot = await cdp('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      writeFileSync(resolve(captureDir, name), Buffer.from(screenshot.data, 'base64'));
    };
    for (const language of ['zh-CN', 'en']) {
      await changeLanguage(language);
      for (const appearance of ['light', 'dark']) {
        if (await theme() !== appearance) await click('#btn-theme');
        for (const page of ['overview', 'security', 'usage', 'reports', 'settings']) {
          await click('nav.sidebar button[data-screen="' + page + '"]');
          if (page === 'usage') await waitFor(`USAGE_PHASE==='ready'`);
          if (page === 'reports') {
            await click('#btn-generate-report');
            await waitFor(`document.getElementById('report-preview').innerText.includes('Skill check report')`);
          }
          await capturePage(`page-${page}-${language}-${appearance}.png`);
          if (page === 'usage') {
            await click('[data-usage-view="ranking"]');
            await capturePage(`page-usage-ranking-${language}-${appearance}.png`);
            await click('[data-usage-view="idle"]');
          }
        }
        await click('nav.sidebar button[data-screen="security"]');
        await click('#sec-rows tr[data-skill]');
        await waitFor(`document.getElementById('drawer').classList.contains('open')`);
        await pause(180);
        await capturePage(`page-drawer-${language}-${appearance}.png`);
        await click('#btn-close-drawer');
      }
    }
  }
} catch (error) {
  results.push({name: 'harness', ok: false, error: error.stack || error.message});
} finally {
  try {if (cdp) await cdp('Browser.close');} catch {}
  if (ws) ws.close();
  if (browser && browser.exitCode === null) {
    browser.kill(); await Promise.race([new Promise(resolve => browser.once('exit', resolve)), pause(2500)]);
  }
  assert(profile.startsWith(resolve(tmpdir()) + sep) && basename(profile).startsWith('skill-radar-header-'),
    'Only this run\'s temporary Chromium profile may be cleaned up');
  try {rmSync(profile, {recursive: true, force: true, maxRetries: 6, retryDelay: 150});} catch {}
}
const report = {html: htmlPath, browser: browserPath, captureDir, results};
if (captureDir) writeFileSync(resolve(captureDir, 'header-visual-report.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
process.exitCode = results.some(check => !check.ok) ? 1 : 0;
