// Regression checks for quiet usage polling in an isolated Chromium desktop bridge.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync, readFileSync, mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename, dirname, resolve, sep} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const args = process.argv.slice(2);
const option = name => args.includes(name) ? args[args.indexOf(name) + 1] : null;
const browserPath = option('--browser') || process.env.SKILL_RADAR_BROWSER;
assert(browserPath && existsSync(browserPath), 'Provide Chromium with --browser');
const htmlPath = resolve(option('--html') || resolve(repo, 'tray/web/index.html'));
const profile = mkdtempSync(resolve(tmpdir(), 'skill-radar-usage-refresh-'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const row = (name, usage_state = 'never') => ({
  name, path: 'F:/Skills/' + name, total: usage_state === 'active' ? 5 : 0,
  last: usage_state === 'active' ? '2026-10-04T10:20:00Z' : null,
  installed_at: '2026-08-01T02:03:04Z', updated_at: '2026-09-01T04:05:06Z', usage_state,
});
const never = Array.from({length: 60}, (_, index) => row('unused-' + index));
const active = row('recently-used', 'active');
const usage = {
  ok: true, rows: [{name: active.name, total: 5, codex: 5, zcode: 0, claude: 0, marker: 0, last: active.last}],
  installed_rows: [...never, active], idle_groups: {never, inactive: [], unknown: []},
  inventory_summary: {total_installed: 61, never: 60, inactive: 0, unknown: 0, active: 1,
    total_invocations: 5, coverage_complete: true, inventory_complete: true},
  status: {phase: 'ready', scanning: false, files_scanned: 12, new_records: 0,
    last_scan: '2026-10-04T10:35:00Z', roots: [], errors: []},
};
const state = {guard: 'running', watched_roots: 1, skills: {}, events: [],
  settings: {language: 'zh-CN', roots: [{path: 'F:/Skills'}]}, usage_status: usage.status};
const bridge = `window.__refresh_boot=crypto.randomUUID();
window.__refresh_state=${JSON.stringify(state)};window.__refresh_usage=${JSON.stringify(usage)};
window.__refresh_calls=[];window.__refresh_delay=160;window.__refresh_error='';
window.__refresh_inflight=0;window.__refresh_max_inflight=0;window.__refresh_completed=0;
try{localStorage.clear();}catch{}
window.pywebview={api:{
  get_state:async()=>structuredClone(window.__refresh_state),
  act:async(name,payload)=>{
    window.__refresh_calls.push({name,payload});
    if(name==='get_usage'){
      window.__refresh_inflight++;
      window.__refresh_max_inflight=Math.max(window.__refresh_max_inflight,window.__refresh_inflight);
      try{
        await new Promise(resolve=>setTimeout(resolve,window.__refresh_delay));
        return window.__refresh_error?{error:window.__refresh_error}:structuredClone(window.__refresh_usage);
      }finally{window.__refresh_inflight--;window.__refresh_completed++;}
    }
    if(name==='list_reports')return{ok:true,reports:[]};
    return{ok:true};
  }
}};`;
const results = [];
let browser, ws, cdp;
try {
  browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  {windowsHide: true, stdio: 'ignore'});
  browser.on('error', error => results.push({name: 'browser_start', ok: false, error: error.message}));
  const portFile = resolve(profile, 'DevToolsActivePort');
  const deadline = Date.now() + 12000;
  while (!existsSync(portFile) && Date.now() < deadline) await pause(40);
  assert(existsSync(portFile), 'Chromium did not expose its temporary endpoint');
  const port = readFileSync(portFile, 'utf8').split(/\r?\n/)[0];
  const target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(page => page.type === 'page');
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
  const click = async selector => {
    const point = await evaluate(`(()=>{
      const el=document.querySelector(${JSON.stringify(selector)});el.scrollIntoView({block:'center'});
      const bounds=el.getBoundingClientRect();return{x:bounds.x+bounds.width/2,y:bounds.y+bounds.height/2};
    })()`);
    for (const type of ['mousePressed', 'mouseReleased']) {
      await cdp('Input.dispatchMouseEvent', {type, ...point, button: 'left', clickCount: 1});
    }
  };
  await cdp('Page.enable'); await cdp('Runtime.enable');
  await cdp('Emulation.setDeviceMetricsOverride', {width: 1080, height: 720, deviceScaleFactor: 1, mobile: false});
  await cdp('Page.addScriptToEvaluateOnNewDocument', {source: bridge});
  const reset = async () => {
    const boot = await evaluate('window.__refresh_boot');
    await cdp('Page.navigate', {url: pathToFileURL(htmlPath).href});
    await waitFor(`window.__refresh_boot!==${JSON.stringify(boot)} && typeof switchScreen==='function' && CONNECTED`);
    await evaluate(`switchScreen('usage')`);
    await waitFor(`USAGE_PHASE==='ready'`);
  };
  const observe = () => evaluate(`(()=>{
    window.__usage_first_row=document.querySelector('#usage-detail tbody tr');
    window.__refresh_mutations=[];
    new MutationObserver(records=>window.__refresh_mutations.push({
      phase:USAGE_PHASE,loading:document.getElementById('usage-detail').innerText.includes('正在读取'),
      removed:records.reduce((sum,record)=>sum+record.removedNodes.length,0),
      scroll:document.getElementById('main-content').scrollTop,
    })).observe(document.getElementById('usage-detail'),{childList:true,subtree:true});
    window.__refresh_call_start=window.__refresh_calls.filter(call=>call.name==='get_usage').length;
  })()`);
  const check = async (name, test) => {
    try {await reset(); const evidence = await test(); results.push({name, ok: true, evidence});}
    catch (error) {results.push({name, ok: false, error: error.stack || error.message});}
  };
  await check('automatic_poll_keeps_usage_content', async () => {
    await observe();
    await pause(4300);
    const evidence = await evaluate(`({
      usageRequests:window.__refresh_calls.filter(call=>call.name==='get_usage').length-window.__refresh_call_start,
      loadingFrames:window.__refresh_mutations.filter(record=>record.loading).length,
      removedNodes:window.__refresh_mutations.reduce((sum,record)=>sum+record.removed,0),
      sameRow:document.querySelector('#usage-detail tbody tr')===window.__usage_first_row,
    })`);
    assert(evidence.usageRequests >= 2, 'Automatic polling was not exercised: ' + JSON.stringify(evidence));
    assert.equal(evidence.loadingFrames, 0, 'Unchanged data flashed loading: ' + JSON.stringify(evidence));
    assert.equal(evidence.sameRow, true, 'Unchanged polling replaced usage rows: ' + JSON.stringify(evidence));
    return evidence;
  });
  await check('unchanged_poll_preserves_interaction', async () => {
    await observe();
    await evaluate(`(()=>{
      const details=document.querySelector('.usage-active');details.open=true;
      window.__refresh_details=details;window.__refresh_focus=details.querySelector('summary');
      window.__refresh_focus.focus({preventScroll:true});document.getElementById('main-content').scrollTop=450;
      window.__refresh_scroll=document.getElementById('main-content').scrollTop;
    })()`);
    await evaluate('tick()');
    await pause(260);
    const evidence = await evaluate(`({
      detailsSame:document.querySelector('.usage-active')===window.__refresh_details,
      expanded:document.querySelector('.usage-active')?.open,
      focusSame:document.activeElement===window.__refresh_focus,
      scrollBefore:window.__refresh_scroll,scrollAfter:document.getElementById('main-content').scrollTop,
      mutationScrolls:window.__refresh_mutations.map(record=>record.scroll),
    })`);
    assert.equal(evidence.detailsSame, true, 'Polling rebuilt interactive content: ' + JSON.stringify(evidence));
    assert.equal(evidence.expanded, true, 'Polling collapsed recent skills: ' + JSON.stringify(evidence));
    assert.equal(evidence.focusSame, true, 'Polling removed keyboard focus: ' + JSON.stringify(evidence));
    assert.equal(evidence.scrollAfter, evidence.scrollBefore, 'Polling moved the scroll position: ' + JSON.stringify(evidence));
    return evidence;
  });
  await check('background_update_changes_usage_statistics', async () => {
    await evaluate(`window.__refresh_usage.inventory_summary.total_invocations=12345`);
    await evaluate('tick()');
    await waitFor(`document.getElementById('usage-bars').innerText.includes('12,345')`);
    return {updated: true};
  });
  await check('one_usage_request_in_flight', async () => {
    await observe();
    await evaluate('window.__refresh_delay=550; void tick(); void tick(); void tick()');
    await pause(100);
    const evidence = await evaluate(`({usageRequests:window.__refresh_calls.filter(call=>call.name==='get_usage').length-window.__refresh_call_start})`);
    assert.equal(evidence.usageRequests, 1, 'Concurrent polling duplicated usage requests: ' + JSON.stringify(evidence));
    return evidence;
  });
  await check('automatic_poll_keeps_refresh_button', async () => {
    await evaluate(`(()=>{
      const button=document.getElementById('btn-refresh-usage');
      window.__refresh_button_changes=[];window.__refresh_button_disabled=button.disabled;
      new MutationObserver(()=>{
        if(button.disabled===window.__refresh_button_disabled)return;
        window.__refresh_button_disabled=button.disabled;
        const style=getComputedStyle(button);
        window.__refresh_button_changes.push({disabled:button.disabled,opacity:style.opacity,
          background:style.backgroundColor,color:style.color});
      }).observe(button,{attributes:true,attributeFilter:['disabled']});
    })()`);
    await pause(4300);
    const evidence = await evaluate(`({changes:window.__refresh_button_changes})`);
    assert.equal(evidence.changes.filter(change => change.disabled).length, 0,
      'Background polling visibly disabled the refresh button: ' + JSON.stringify(evidence));
    return evidence;
  });
  await check('failed_background_poll_preserves_content', async () => {
    await observe();
    await evaluate(`(()=>{
      document.getElementById('main-content').scrollTop=450;
      window.__refresh_scroll=document.getElementById('main-content').scrollTop;
      window.__refresh_completed_start=window.__refresh_completed;window.__refresh_error='permission denied';
    })()`);
    await evaluate('tick()');
    await waitFor(`window.__refresh_completed>window.__refresh_completed_start`);
    const evidence = await evaluate(`({phase:USAGE_PHASE,
      sameRow:document.querySelector('#usage-detail tbody tr')===window.__usage_first_row,
      rowCount:document.querySelectorAll('#usage-detail tbody tr').length,
      scrollBefore:window.__refresh_scroll,scrollAfter:document.getElementById('main-content').scrollTop,
      removedNodes:window.__refresh_mutations.reduce((sum,record)=>sum+record.removed,0),
    })`);
    assert.equal(evidence.sameRow, true, 'A transient background failure removed saved data: ' + JSON.stringify(evidence));
    assert.equal(evidence.scrollAfter, evidence.scrollBefore, 'A background failure moved the page: ' + JSON.stringify(evidence));
    return evidence;
  });
  await check('manual_refresh_during_poll_forces_once', async () => {
    await observe();
    await evaluate(`window.__refresh_delay=400;window.__refresh_max_inflight=0;void tick()`);
    await waitFor(`window.__refresh_inflight===1`);
    await click('#btn-refresh-usage');
    await pause(1050);
    const evidence = await evaluate(`({
      requests:window.__refresh_calls.filter(call=>call.name==='get_usage').slice(window.__refresh_call_start),
      maxInFlight:window.__refresh_max_inflight,
    })`);
    assert.equal(evidence.requests.filter(request => request.payload.force === true).length, 1,
      'A manual refresh during polling did not force a history scan: ' + JSON.stringify(evidence));
    assert.equal(evidence.maxInFlight, 1, 'Manual refresh overlapped automatic requests: ' + JSON.stringify(evidence));
    return evidence;
  });
} catch (error) {
  results.push({name: 'harness', ok: false, error: error.stack || error.message});
} finally {
  try {if (cdp) await cdp('Browser.close');} catch {}
  if (ws) ws.close();
  if (browser && browser.exitCode === null) {
    browser.kill();
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), pause(2000)]);
  }
  assert(profile.startsWith(resolve(tmpdir()) + sep) && basename(profile).startsWith('skill-radar-usage-refresh-'));
  try {rmSync(profile, {recursive: true, force: true, maxRetries: 6, retryDelay: 150});} catch {}
}
console.log(JSON.stringify({html: htmlPath, results}));
process.exitCode = results.some(result => !result.ok) ? 1 : 0;
