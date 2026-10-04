// Run the actual offline UI in Chromium with a temporary profile and a fake bridge.
// Node 22+ and an existing Chrome/Edge/Chromium installation are sufficient.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync, mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const args = process.argv.slice(2);
const option = name => args.includes(name) ? args[args.indexOf(name) + 1] : null;
const browserPath = option('--browser') || process.env.SKILL_RADAR_BROWSER;
assert(browserPath && existsSync(browserPath), 'Provide an installed browser with --browser or SKILL_RADAR_BROWSER');
assert(typeof WebSocket === 'function', 'Node 22+ is required for its native WebSocket');
const htmlPath = resolve(option('--html') || resolve(repo, 'tray/web/index.html'));
const captureDir = option('--capture');
const capturePrefix = option('--prefix') || 'before';
const width = Number(option('--width') || 1280);
const height = Number(option('--height') || 820);
const captureOnly = args.includes('--capture-only');
const captureAll = args.includes('--capture-all');
const layoutAudit = args.includes('--audit-layout');
const language = option('--language') || 'zh-CN';
const profile = mkdtempSync(resolve(tmpdir(), 'skill-radar-ui-'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, ws, closeBrowser;

const state = {
  guard: 'running', paused: false, watched_roots: 3,
  today: { '2026-10-04': { new: 3, drift: 2, block: 1 } },
  settings: { block: true, quarantine: false, language, roots: [
    { path: 'F:\\Skills\\常用技能' }, { path: 'F:\\Skills\\项目工具' },
    { path: 'C:\\Users\\Demo\\.codex\\skills' },
  ] },
  skills: {
    'F:\\Skills\\常用技能\\frontend-design': {
      name: 'frontend-design', score: 28, status: 'drifted',
      reason: '技能说明已更新，请确认新增的下载操作是否符合你的预期。',
      findings: [{ severity: 'WARNING', rule_id: 'REMOTE_RESOURCE',
        file: 'SKILL.md', line: 18, message: '包含访问外部地址的指令。',
        excerpt: '下载项目需要的公开图标素材，然后保存到本地 assets 目录。' }],
    },
    'F:\\Skills\\项目工具\\database-helper': {
      name: 'database-helper', score: 46, status: 'blocked',
      reason: '发现可能读取并上传本机私密文件的指令。',
      findings: [{ severity: 'CRITICAL', rule_id: 'DATA_EXFILTRATION',
        file: 'scripts/setup.sh', line: 12, message: '可能把私密文件发送到外部地址。',
        excerpt: '上传环境配置文件的可疑命令已被拦截。' }],
    },
    'F:\\Skills\\常用技能\\writing-helper': {
      name: 'writing-helper', score: 0, status: 'scanned', findings: [],
    },
    'C:\\Users\\Demo\\.codex\\skills\\code-review': {
      name: 'code-review', score: 8, status: 'scanned', findings: [],
    },
  },
  events: [
    { ts: '2026-10-04 16:42:06', kind: 'drift', text: '内容漂移 frontend-design' },
    { ts: '2026-10-04 16:39:18', kind: 'block', text: 'CRITICAL 拦截 F:\\Skills\\项目工具\\database-helper' },
    { ts: '2026-10-04 16:36:22', kind: 'new', text: '新技能 writing-helper' },
    { ts: '2026-10-04 16:31:50', kind: 'new', text: '新技能 code-review' },
  ],
};
const bridgeScript = `window.__ui_test_state = ${JSON.stringify(state)};
window.__ui_test_actions = [];
try {localStorage.clear();} catch {}
window.__ui_test_state_delay = 0;
window.pywebview = { api: {
  get_state: async () => {
    const snapshot = structuredClone(window.__ui_test_state);
    if (window.__ui_test_state_delay) await new Promise(resolve => setTimeout(resolve, window.__ui_test_state_delay));
    return snapshot;
  },
  act: async (name, payload) => {
    window.__ui_test_actions.push({name,payload});
    if (name === 'set_language') {
      await new Promise(resolve => setTimeout(resolve, 80));
      window.__ui_test_state.settings.language = payload.language;
      return {ok:true,language:payload.language};
    }
    return name === 'get_usage' ? {ok:true, rows:[
    {name:'frontend-design', total:42, zcode:30, claude:10, marker:2, last:'2026-10-04 16:41'},
    {name:'writing-helper', total:18, zcode:12, claude:6, marker:0, last:'2026-10-04 16:28'},
    {name:'code-review', total:11, zcode:7, claude:4, marker:0, last:'2026-10-04 15:52'}
  ]} : {ok:true, output:'技能内容更新：新增一条外部素材下载说明。'};
  }
}};`;
const results = [];
try {
  browser = spawn(browserPath, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
  ], { windowsHide: true, stdio: 'ignore' });
  browser.on('error', err => { results.push({ name: 'browser_start', ok: false, error: err.message }); });
  const portFile = resolve(profile, 'DevToolsActivePort');
  const deadline = Date.now() + 12000;
  while (!existsSync(portFile) && Date.now() < deadline) await pause(40);
  assert(existsSync(portFile), 'Browser did not expose its temporary DevTools endpoint');
  const port = readFileSync(portFile, 'utf8').split(/\r?\n/)[0];
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = pages.find(p => p.type === 'page');
  assert(target, 'Browser page target was not created');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
  let nextId = 0;
  const pending = new Map();
  ws.addEventListener('message', ev => {
    const data = JSON.parse(ev.data);
    const req = pending.get(data.id);
    if (!req) return;
    pending.delete(data.id);
    clearTimeout(req.timeout);
    data.error ? req.reject(new Error(JSON.stringify(data.error))) : req.resolve(data.result);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    const timeout = setTimeout(() => {
      pending.delete(id); reject(new Error(`CDP timeout: ${method}`));
    }, 8000);
    pending.set(id, { resolve, reject, timeout });
    ws.send(JSON.stringify({ id, method, params }));
  });
  closeBrowser = () => cdp('Browser.close');
  const evaluate = async expression => {
    const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const waitFor = async expression => {
    const deadline = Date.now() + 2500;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await pause(25);
    }
    throw new Error(`UI condition did not become true: ${expression}`);
  };
  const click = async selector => {
    const point = await evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      el.scrollIntoView({block:'center'});
      const rect = el.getBoundingClientRect();
      return {x:rect.x + rect.width/2, y:rect.y + rect.height/2, width:rect.width, height:rect.height};
    })()`);
    assert(point && point.width && point.height, `Missing visible target: ${selector}`);
    await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 });
    await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 });
  };
  const key = async (key, code, virtualKeyCode, modifiers = 0) => {
    await cdp('Input.dispatchKeyEvent', { type:'keyDown', key, code, windowsVirtualKeyCode:virtualKeyCode,
      nativeVirtualKeyCode:virtualKeyCode, modifiers });
    await cdp('Input.dispatchKeyEvent', { type:'keyUp', key, code, windowsVirtualKeyCode:virtualKeyCode,
      nativeVirtualKeyCode:virtualKeyCode, modifiers });
  };
  await cdp('Page.enable');
  await cdp('Runtime.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor:1, mobile:false });
  await cdp('Page.addScriptToEvaluateOnNewDocument', { source: bridgeScript });
  const reset = async () => {
    await cdp('Page.navigate', { url: pathToFileURL(htmlPath).href });
    await waitFor(`!!document.querySelector('#sec-rows tr[data-skill]')`);
  };
  const open = async () => {
    await click('nav.sidebar button[data-screen="security"]');
    await click('#sec-rows tr[data-skill]');
    await waitFor(`document.getElementById('drawer').classList.contains('open')`);
    await pause(230);
  };
  const screenshot = async name => {
    mkdirSync(captureDir, { recursive:true });
    const data = await cdp('Page.captureScreenshot', { format:'png', captureBeyondViewport:false });
    writeFileSync(resolve(captureDir, name), Buffer.from(data.data, 'base64'));
  };
  const auditLayout = async name => {
    const audit = await evaluate(`(() => {
      const main = document.querySelector('main');
      const errors = [];
      if(main.scrollWidth > main.clientWidth + 1) errors.push('main content overflows horizontally');
      if(document.documentElement.scrollWidth > innerWidth + 1) errors.push('document overflows horizontally');
      for(const el of document.querySelectorAll('button,select,input')) {
        const r = el.getBoundingClientRect(), css = getComputedStyle(el);
        if(!r.width || !r.height || css.visibility==='hidden' || el.closest('[hidden]') || r.bottom<=0 || r.top>=innerHeight)continue;
        if(r.left < -1 || r.right > innerWidth+1)errors.push((el.id || el.className)+' exceeds viewport width');
      }
      return {errors,viewport:{width:innerWidth,height:innerHeight},main:{clientWidth:main.clientWidth,scrollWidth:main.scrollWidth}};
    })()`);
    results.push({name:'layout_'+name,ok:audit.errors.length===0,...audit});
  };
  if (captureDir) {
    await reset();
    await pause(230);
    await screenshot(`${capturePrefix}.png`);
    if(layoutAudit)await auditLayout('overview');
    await open();
    await screenshot(`${capturePrefix}-drawer.png`);
    if(layoutAudit)await auditLayout('drawer');
    if (captureAll) {
      await reset();
      await click('nav.sidebar button[data-screen="security"]');
      await pause(230);
      await screenshot(`${capturePrefix}-security.png`);
      if(layoutAudit)await auditLayout('security');
      await click('nav.sidebar button[data-screen="usage"]');
      await waitFor(`document.querySelector('#usage-detail table') !== null`);
      await pause(230);
      await screenshot(`${capturePrefix}-usage.png`);
      if(layoutAudit)await auditLayout('usage');
      await click('nav.sidebar button[data-screen="settings"]');
      await pause(230);
      await screenshot(`${capturePrefix}-settings.png`);
      if(layoutAudit)await auditLayout('settings');
    }
  }
  const check = async (name, run) => {
    await reset();
    try { await run(); results.push({ name, ok:true }); }
    catch (err) { results.push({ name, ok:false, error:err.message }); }
  };
  if (!captureOnly) {
    await check('drawer_close_button', async () => {
      await open();
      await click('#btn-close-drawer');
      assert.equal(await evaluate(`document.getElementById('drawer').classList.contains('open')`), false,
        'Clicking the visible close button must dismiss the drawer');
      assert.equal(await evaluate(`document.getElementById('drawer').getAttribute('aria-hidden')`), 'true');
    });
    await check('drawer_backdrop', async () => {
      await open();
      assert.equal(await evaluate(`!!document.getElementById('drawer-backdrop')`), true,
        'Provide a visible backdrop that dismisses the drawer');
      await cdp('Input.dispatchMouseEvent', { type:'mousePressed', x:300, y:400, button:'left', clickCount:1 });
      await cdp('Input.dispatchMouseEvent', { type:'mouseReleased', x:300, y:400, button:'left', clickCount:1 });
      assert.equal(await evaluate(`document.getElementById('drawer').classList.contains('open')`), false,
        'Clicking outside the drawer must dismiss it');
    });
    await check('drawer_escape_restores_focus', async () => {
      await open();
      assert.equal(await evaluate(`document.getElementById('drawer').contains(document.activeElement)`), true,
        'Opening the drawer must move focus into its controls');
      await key('Escape', 'Escape', 27);
      assert.equal(await evaluate(`document.getElementById('drawer').classList.contains('open')`), false,
        'Escape must dismiss the drawer');
      assert.equal(await evaluate(`document.activeElement.matches('#sec-rows tr[data-skill]')`), true,
        'Closing the drawer must restore focus to its opening row');
    });
    await check('drawer_escape_dismisses', async () => {
      await open();
      await key('Escape', 'Escape', 27);
      assert.equal(await evaluate(`document.getElementById('drawer').classList.contains('open')`), false,
        'Escape must dismiss the drawer');
    });
    await check('drawer_focus_trap', async () => {
      await open();
      for (let i=0; i<12; i++) {
        await key('Tab', 'Tab', 9);
        assert.equal(await evaluate(`document.getElementById('drawer').contains(document.activeElement)`), true,
          'Tab must stay within the open drawer');
      }
      for (let i=0; i<6; i++) {
        await key('Tab', 'Tab', 9, 8);
        assert.equal(await evaluate(`document.getElementById('drawer').contains(document.activeElement)`), true,
          'Shift+Tab must stay within the open drawer');
      }
    });
    await check('drawer_focus_restored_after_refresh', async () => {
      await open();
      const skill = await evaluate(`document.querySelector('#sec-rows tr[data-skill]').dataset.skill`);
      // The real bridge refreshes the tables every two seconds. Its opener may
      // have been replaced while the user read the details.
      await evaluate(`tick()`);
      await key('Escape', 'Escape', 27);
      assert.equal(await evaluate(`document.activeElement.dataset.skill`), skill,
        'Closing after a state refresh must focus the replacement skill row');
    });
    await check('drawer_locks_background_scroll', async () => {
      const browserInfo = await cdp('Browser.getVersion');
      await evaluate(`window.__scroll_events = []; document.addEventListener('wheel', e => {
        window.__scroll_events.push({target:e.target.id,delta:e.deltaY,trusted:e.isTrusted});
      }, {passive:true});`);
      try {
      await click('nav.sidebar button[data-screen="security"]');
      // Add enough records to make the real background scroll container scrollable.
      await evaluate(`(() => {
        const section = document.getElementById('screen-security');
        const spacer = document.createElement('div');
        spacer.style.height = '1600px'; section.append(spacer);
      })()`);
      await open();
      const scroll = async () => {
        // mouseWheel dispatch waits on a visual-state/renderer acknowledgement
        // that can stall in macOS headless Chromium. The gesture API drives
        // real mouse input through Chromium's synthetic gesture queue instead.
        await cdp('Input.synthesizeScrollGesture', {
          x:300, y:400, yDistance:-220,
          gestureSourceType:'mouse', preventFling:true,
        });
      };
      const before = await evaluate(`document.getElementById('main-content').scrollTop`);
      await scroll();
      assert.equal(await evaluate(`document.getElementById('main-content').scrollTop`), before,
        'Wheeling over the modal backdrop must not scroll the background content');
      await key('Escape', 'Escape', 27);
      await pause(180);
      await scroll();
      await waitFor(`document.getElementById('main-content').scrollTop > ${before}`);
      assert(await evaluate(`document.getElementById('main-content').scrollTop`) > before,
        'Closing the drawer must restore wheel scrolling in the main content');
      } catch (error) {
        const diagnostic = await evaluate(`(() => {
          const main=document.getElementById('main-content'), r=main.getBoundingClientRect();
          return {viewport:[innerWidth,innerHeight],main:{top:main.scrollTop,
            height:main.clientHeight,scrollHeight:main.scrollHeight,rect:r.toJSON(),
            overflow:getComputedStyle(main).overflowY},target:document.elementFromPoint(300,400)?.outerHTML.slice(0,200),
            drawerHidden:document.getElementById('drawer').hidden,
            inert:document.getElementById('app-layout').inert,wheels:window.__scroll_events};
        })()`);
        throw new Error(error.message+'; scroll diagnostics: '+JSON.stringify({browser:browserInfo,...diagnostic}));
      }
    });
    await check('drawer_background_locked', async () => {
      await open();
      assert.equal(await evaluate(`document.querySelector('.layout').inert`), true,
        'The main layout must stop accepting interactions while the drawer is open');
      assert.equal(await evaluate(`document.getElementById('drawer').getAttribute('aria-modal')`), 'true');
      await key('Escape', 'Escape', 27);
      assert.equal(await evaluate(`document.querySelector('.layout').inert`), false,
        'Closing the drawer must unlock the main layout');
      await click('nav.sidebar button[data-screen="settings"]');
      assert.equal(await evaluate(`document.getElementById('screen-settings').classList.contains('active')`), true,
        'Navigation must be usable after closing the drawer');
    });
    await check('drawer_row_keyboard', async () => {
      await click('nav.sidebar button[data-screen="security"]');
      await evaluate(`document.querySelector('#sec-rows tr[data-skill]').focus()`);
      assert.equal(await evaluate(`document.activeElement.matches('#sec-rows tr[data-skill]')`), true,
        'Skill rows must be keyboard-focusable');
      await key('Enter', 'Enter', 13);
      await waitFor(`document.getElementById('drawer').classList.contains('open')`);
      await key('Escape', 'Escape', 27);
      await key(' ', 'Space', 32);
      await waitFor(`document.getElementById('drawer').classList.contains('open')`);
    });
    await check('drawer_initial_hidden', async () => {
      assert.equal(await evaluate(`document.getElementById('drawer').getAttribute('aria-hidden')`), 'true');
      for (let i=0; i<12; i++) {
        await key('Tab', 'Tab', 9);
        assert.equal(await evaluate(`document.getElementById('drawer').contains(document.activeElement)`), false,
          'A closed drawer must not keep off-screen controls in the Tab sequence');
      }
    });
    const changeLanguage = async value => {
      assert.equal(await evaluate(`!!document.getElementById('ui-language')`), true,
        'Provide a language selector for Chinese and English');
      await evaluate(`(() => {
        const select = document.getElementById('ui-language');
        select.value = ${JSON.stringify(value)};
        select.dispatchEvent(new Event('change', {bubbles:true}));
      })()`);
    };
    await check('ui_language_english_and_persisted', async () => {
      await changeLanguage('en');
      // A snapshot received while saving must not revert the user's choice.
      await evaluate(`tick()`);
      assert.equal(await evaluate(`document.getElementById('ui-language').value`), 'en',
        'An in-flight state refresh must not undo the new language selection');
      await waitFor(`window.__ui_test_state.settings.language === 'en'`);
      const nav = await evaluate(`Array.from(document.querySelectorAll('nav.sidebar button[data-screen]')).map(b=>b.innerText)`);
      for (const label of ['Overview', 'Security', 'Usage', 'Settings']) {
        assert(nav.some(text => text.includes(label)), `English navigation is missing ${label}`);
      }
      assert.equal(await evaluate(`document.getElementById('sync-time').innerText.includes('已同步')`), false,
        'The connection status must use the selected interface language');
      assert.equal(await evaluate(`window.__ui_test_actions.some(a => a.name==='set_language' && a.payload.language==='en')`), true,
        'The language choice must be persisted through the desktop bridge');
      await click('nav.sidebar button[data-screen="security"]');
      const updateRow = await evaluate(`Array.from(document.querySelectorAll('#sec-rows tr[data-skill]')).findIndex(row => row.innerText.includes('frontend-design'))`);
      assert(updateRow >= 0, 'The changed skill must be available for review');
      await click(`#sec-rows tr[data-skill]:nth-child(${updateRow + 1})`);
      await waitFor(`document.getElementById('drawer').classList.contains('open')`);
      const controls = await evaluate(`Array.from(document.querySelectorAll('#drawer button')).map(b=>b.innerText || b.getAttribute('aria-label') || b.title)`);
      for (const label of ['Close', 'View changes', 'Confirm update']) {
        assert(controls.some(text => text.includes(label)), `The detail controls are missing English ${label}`);
      }
      assert.equal(await evaluate(`document.getElementById('drawer-body').innerText.includes('项提示')`), false,
        'The finding count must use the selected interface language');
    });
    await check('ui_language_chinese_preserves_screen_and_data', async () => {
      await changeLanguage('en');
      await waitFor(`window.__ui_test_state.settings.language === 'en'`);
      await click('nav.sidebar button[data-screen="security"]');
      const count = await evaluate(`document.querySelectorAll('#sec-rows tr[data-skill]').length`);
      await open();
      await key('Escape', 'Escape', 27);
      await changeLanguage('zh-CN');
      await waitFor(`window.__ui_test_state.settings.language === 'zh-CN'`);
      assert.equal(await evaluate(`document.getElementById('screen-security').classList.contains('active')`), true,
        'Changing the language must keep the current page selected');
      assert.equal(await evaluate(`document.querySelectorAll('#sec-rows tr[data-skill]').length`), count,
        'Changing the language must preserve the loaded skill records');
      const nav = await evaluate(`Array.from(document.querySelectorAll('nav.sidebar button[data-screen]')).map(b=>b.innerText)`);
      for (const label of ['概览', '安全', '使用统计', '设置']) {
        assert(nav.some(text => text.includes(label)), `Chinese navigation is missing ${label}`);
      }
      await click('nav.sidebar button[data-screen="settings"]');
      const visibleText = await evaluate(`document.querySelector('.layout').innerText`);
      for (const jargon of ['漂移', '监听根', 'Registered roots', 'Root path']) {
        assert(!visibleText.includes(jargon), `Use everyday Chinese in place of ${jargon}`);
      }
    });
    await check('ui_language_initial_saved_english', async () => {
      await cdp('Page.addScriptToEvaluateOnNewDocument', {
        source:`window.__ui_test_state.settings.language = 'en';`
      });
      await reset();
      await waitFor(`document.getElementById('ui-language')?.value === 'en'`);
      assert.equal(await evaluate(`document.getElementById('sync-time').innerText.includes('已同步')`), false,
        'Loading the saved English language must translate the initial connection status');
      assert.equal(await evaluate(`document.documentElement.lang`), 'en');
    });
    await check('ui_language_stale_state_after_save', async () => {
      // The previous check installs the English saved preference for each page.
      await evaluate(`(() => {
        window.__ui_test_state_delay = 100;
        window.__ui_test_language_mutations = [];
        new MutationObserver(() => window.__ui_test_language_mutations.push(document.documentElement.lang))
          .observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
      })()`);
      await changeLanguage('zh-CN');
      // Capture an English snapshot while the save is pending; its delayed
      // response arrives after the 80ms save has completed.
      await evaluate(`void tick()`);
      await pause(125);
      assert.equal(await evaluate(`document.getElementById('ui-language').value`), 'zh-CN',
        'A stale language snapshot received after saving must not reverse the selection');
      const seen = await evaluate(`window.__ui_test_language_mutations`);
      assert(!seen.includes('en'), 'The UI must never flash back to the old language while saving');
    });
  }
} catch (err) {
  results.push({ name:'harness', ok:false, error:err.stack || err.message });
} finally {
  if (closeBrowser) {
    try { await closeBrowser(); } catch {}
  }
  if (ws) ws.close();
  if (browser && browser.exitCode === null) {
    browser.kill();
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), pause(3000)]);
  }
  const tempRoot = resolve(tmpdir()) + sep;
  assert(profile.startsWith(tempRoot) && basename(profile).startsWith('skill-radar-ui-'),
    'Only this run\'s temporary browser profile may be cleaned up');
  try { rmSync(profile, { recursive:true, force:true, maxRetries:6, retryDelay:150 }); } catch {}
}
console.log(JSON.stringify({ html:htmlPath, browser:browserPath, capture:captureDir, results }));
process.exitCode = results.some(r => !r.ok) ? 1 : 0;
