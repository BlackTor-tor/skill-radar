// Verify that the usage ranking table presents the most recently recorded skill first.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync,readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename,dirname,resolve,sep} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

const repo=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const args=process.argv.slice(2), option=name=>args.includes(name)?args[args.indexOf(name)+1]:null;
const browserPath=option('--browser') || process.env.SKILL_RADAR_BROWSER;
assert(browserPath && existsSync(browserPath),'Provide Chromium with --browser');
const htmlPath=resolve(option('--html') || resolve(repo,'tray/web/index.html'));
const profile=mkdtempSync(resolve(tmpdir(),'skill-radar-usage-recent-sort-'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const usage={ok:true,rows:[
  {name:'older-many',total:99,codex:99,zcode:0,claude:0,marker:0,last:'2026-10-01T00:00:00Z'},
  {name:'newer-few',total:1,codex:1,zcode:0,claude:0,marker:0,last:'2026-10-04T12:00:00Z'},
  {name:'same-time-high',total:9,codex:9,zcode:0,claude:0,marker:0,last:'2026-10-04T10:00:00Z'},
  {name:'same-time-low',total:2,codex:2,zcode:0,claude:0,marker:0,last:'2026-10-04T10:00:00Z'},
  {name:'no-date',total:100,codex:100,zcode:0,claude:0,marker:0,last:null},
],installed_rows:[],idle_groups:{never:[],inactive:[],unknown:[]},
  inventory_summary:{total_installed:0,never:0,inactive:0,unknown:0,active:0,total_invocations:211,coverage_complete:true},
  status:{phase:'ready',scanning:false,files_scanned:1,last_scan:'2026-10-05T00:00:00Z',roots:[],errors:[]}};
const state={guard:'running',watched_roots:0,settings:{language:'zh-CN',roots:[]},skills:{},events:[],usage_status:usage.status};
const bridge=`window.__recent_sort_state=${JSON.stringify(state)};window.__recent_sort_usage=${JSON.stringify(usage)};try{localStorage.clear()}catch{}
window.pywebview={api:{get_state:async()=>structuredClone(window.__recent_sort_state),act:async(name)=>name==='get_usage'?structuredClone(window.__recent_sort_usage):{ok:true}}};`;
let browser,ws,cdp; const results=[];
try {
  browser=spawn(browserPath,['--headless=new','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
  const portFile=resolve(profile,'DevToolsActivePort'); const deadline=Date.now()+12000;
  while(!existsSync(portFile)&&Date.now()<deadline)await pause(40);
  assert(existsSync(portFile),'Chromium did not expose its temporary endpoint');
  const port=readFileSync(portFile,'utf8').split(/\r?\n/)[0];
  const target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(page=>page.type==='page');
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
  let id=0; const pending=new Map();
  ws.addEventListener('message',event=>{const data=JSON.parse(event.data),request=pending.get(data.id);if(!request)return;pending.delete(data.id);clearTimeout(request.timeout);data.error?request.reject(new Error(JSON.stringify(data.error))):request.resolve(data.result);});
  cdp=(method,params={})=>new Promise((resolve,reject)=>{const current=++id,timeout=setTimeout(()=>{pending.delete(current);reject(new Error('CDP timeout '+method));},8000);pending.set(current,{resolve,reject,timeout});ws.send(JSON.stringify({id:current,method,params}));});
  const evaluate=async expression=>{const value=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert(!value.exceptionDetails,JSON.stringify(value.exceptionDetails));return value.result.value;};
  const waitFor=async expression=>{const until=Date.now()+3000;while(Date.now()<until){if(await evaluate(expression))return;await pause(25);}throw new Error('UI condition: '+expression);};
  await cdp('Page.enable'); await cdp('Runtime.enable');
  await cdp('Page.addScriptToEvaluateOnNewDocument',{source:bridge});
  await cdp('Page.navigate',{url:pathToFileURL(htmlPath).href});
  await waitFor(`window.__recent_sort_state && typeof switchScreen==='function'`);
  await evaluate(`switchScreen('usage')`); await waitFor(`USAGE_PHASE==='ready'`);
  await evaluate(`document.querySelector('[data-usage-view="ranking"]').click()`);
  const names=await evaluate(`Array.from(document.querySelectorAll('#usage-detail tbody tr td:first-child')).map(cell=>cell.textContent.trim())`);
  results.push({name:'ranking_sorted_by_recent_record',ok:true,evidence:{names}});
  assert.deepEqual(names,['newer-few','same-time-high','same-time-low','older-many','no-date'],
    'Expected recent records first, missing records last, and deterministic ties: '+JSON.stringify(names));
} catch(error) {
  results.push({name:'ranking_sorted_by_recent_record',ok:false,error:error.stack||error.message});
} finally {
  try{if(cdp)await cdp('Browser.close')}catch{}
  if(ws)ws.close();
  if(browser&&browser.exitCode===null){browser.kill();await Promise.race([new Promise(resolve=>browser.once('exit',resolve)),pause(2000)]);}
  assert(profile.startsWith(resolve(tmpdir())+sep)&&basename(profile).startsWith('skill-radar-usage-recent-sort-'));
  try{rmSync(profile,{recursive:true,force:true,maxRetries:6,retryDelay:150})}catch{}
}
console.log(JSON.stringify({html:htmlPath,results}));
process.exitCode=results.some(result=>!result.ok)?1:0;
