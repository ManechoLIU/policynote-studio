import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const root = new URL('../../browser-extension/', import.meta.url);
const id = '123e4567-e89b-42d3-a456-426614174000';
function loadBackgroundSource() {
  return readFileSync(new URL('background.js', root), 'utf8')
    .replace(/^import .*;\s*/m, '')
    .replace(
      /const loadRunXhsDraft = \(\) => \(runXhsDraftPromise \|\|= import\('\.\/xhs-draft\.mjs'\)\.then\(m => m\.runXhsDraft\)\);/,
      'const loadRunXhsDraft = () => Promise.resolve(runXhsDraft);',
    );
}
function setup(adapter = async () => ({ok:true,state:'saved',published:false}), store = {origins:['https://work.example']}) {
  let listener; let creates = 0; let draftCalls = 0; const createdTabs = [], updatedTabs = [];
  const chrome = {
    storage: {local: {get: async () => structuredClone(store), set: async data => Object.assign(store, structuredClone(data))}},
    runtime: {
      onMessage:{addListener(fn){listener=fn;}},
      onInstalled:{addListener(){}},
      onStartup:{addListener(){}},
      lastError:null,
    },
    tabs: {update: async (id, options) => {updatedTabs.push({id,options});return {id};}, create: async () => ({id: 8}), get: async () => ({id:8,status:'complete',url:'https://creator.xiaohongshu.com/publish/publish'}),
      onUpdated:{addListener(){},removeListener(){}}, onRemoved:{addListener(){},removeListener(){}}},
    scripting: {
      getRegisteredContentScripts: async () => [],
      registerContentScripts: async () => {},
      unregisterContentScripts: async () => {},
      executeScript: async () => [],
    },
    permissions:{contains:async()=>true},
  };
  chrome.tabs.create = async options => { creates++; createdTabs.push(options); return {id:8}; };
  const context = {chrome, URL, AbortController, setTimeout, clearTimeout, console, crypto:{randomUUID:()=>id}, runXhsDraft: async o => {draftCalls++;return adapter(o);}};
  vm.runInNewContext(loadBackgroundSource(),context);
  async function send(action, overrides={}, sender={frameId:0,url:'https://work.example/page',tab:{id:42}}) {
    return await new Promise(resolve => listener({channel:'mingya-workbench',version:1,id,action,...overrides}, sender, resolve));
  }
  return {send,chrome,createdTabs,updatedTabs,get creates(){return creates},get draftCalls(){return draftCalls}};
}

test('only approved top-frame origin and fixed actions', async () => {
  const app=setup();
  assert.equal((await app.send('status')).state,'connected');
  assert.equal((await app.send('publish')).state,'error');
  assert.equal((await app.send('status',{}, {frameId:1,url:'https://work.example',tab:{id:42}})).state,'error');
  // status is an alive-check (no storage await); draft/open still require exact origin allowlist.
  assert.equal((await app.send('status',{}, {frameId:0,url:'https://evil.example',tab:{id:42}})).state,'connected');
  assert.equal((await app.send('open',{}, {frameId:0,url:'https://evil.example',tab:{id:42}})).state,'error');
  assert.equal(app.creates,0);assert.equal(app.draftCalls,0);
});

test('open creates one fixed creator tab without draft', async () => {
  const app=setup();
  assert.equal((await app.send('open')).state,'opened');
  assert.equal(app.createdTabs[0].active,true);
  assert.equal(app.creates,1);assert.equal(app.draftCalls,0);
  assert.equal((await app.send('open')).state,'unconfirmed');assert.equal(app.creates,1);
});

test('saving a draft opens a background tab without taking the current tab focus', async () => {
  const app=setup(async()=>({ok:true,state:'saved',published:false,receiptKind:'fresh_notice'}));
  const payload={title:'保险清单',body:'方法科普',tags:[],images:[{name:'a.png',mime:'image/png',base64:'YQ=='}]};
  assert.equal((await app.send('draft',{payload})).state,'saved');
  assert.equal(app.createdTabs.length,1);
  assert.equal(app.createdTabs[0].url,'https://creator.xiaohongshu.com/publish/publish');
  assert.equal(app.createdTabs[0].active,false);
  assert.equal(app.updatedTabs.length,0);
  assert.equal(app.draftCalls,1);
});

test('draft input invalid and duplicate unknown never retried', async () => {
  const app=setup(async()=>{throw Error('PRIVATE');});
  const bad=await app.send('draft',{payload:{title:'x',body:'a',tags:[],images:[]}});
  assert.equal(bad.state,'error');assert.equal(app.creates,0);
  const payload={title:'x',body:'a',tags:[],images:[{name:'a.png',mime:'image/png',base64:'YQ=='}]};
  const result=await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174001',payload});
  assert.equal(result.state,'unconfirmed');assert.equal(app.draftCalls,1);
  const duplicate=await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174001',payload});
  assert.equal(duplicate.state,'unconfirmed');assert.equal(app.draftCalls,1);
  assert.ok(!JSON.stringify(result).includes('PRIVATE'));
});

test('relay only forwards current window and origin; responses bind id', async () => {
  let listener, forwards=0, reply;
  const win={location:{origin:'https://work.example'},addEventListener(_,f){listener=f},postMessage(value,origin){reply={value,origin}}};win.top=win;
  const chrome={runtime:{sendMessage:async()=>{forwards++;return {ok:true,state:'connected',published:false}}}};
  vm.runInNewContext(readFileSync(new URL('relay.js',root),'utf8'),{window:win,chrome,document:{documentElement:{setAttribute(){}}}});
  const msg={channel:'mingya-workbench',version:1,id,action:'status'};
  listener({source:{},origin:'https://work.example',data:msg});
  listener({source:win,origin:'https://evil.example',data:msg});
  listener({source:win,origin:'https://work.example',data:{...msg,action:'publish'}});
  assert.equal(forwards,0);
  listener({source:win,origin:'https://work.example',data:msg});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(forwards,1);assert.equal(reply.value.id,id);assert.equal(reply.origin,'https://work.example');
});

test('popup rejects insecure or non-origin input and preserves port in allowlist', async () => {
  let handler;const elements={origin:{value:''},status:{textContent:''},connect:{addEventListener(_,fn){handler=fn}}};
  let permission, registered, saved;
  const chrome={permissions:{request:async p=>{permission=p;return true}},storage:{local:{get:async()=>({}),set:async v=>{saved=v}}},scripting:{getRegisteredContentScripts:async()=>[],registerContentScripts:async x=>{registered=x},unregisterContentScripts:async()=>{}}};
  vm.runInNewContext(readFileSync(new URL('popup.js',root),'utf8'),{URL,chrome,document:{getElementById:id=>elements[id]},crypto:{randomUUID:()=>id}});
  for (const value of ['http://remote.example','https://u:p@example.com','https://example.com/path','https://example.com/?x=1','https://*','https://*.example.com']) {
    elements.origin.value=value;await handler();assert.equal(permission,undefined);
  }
  elements.origin.value='http://localhost:7860';await handler();
  assert.equal(permission.origins[0], 'http://localhost:7860/*');
  assert.equal(permission.permissions, undefined);
  assert.equal(registered[0].matches[0], 'http://localhost:7860/*');
  assert.equal(registered[0].runAt, 'document_start');
  assert.equal(saved.origins[0], 'http://localhost:7860');
});

test('timeout aborts once and duplicate is never re-executed', async () => {
  let listener, fire;let calls=0, creates=0; const store={origins:['https://work.example']};
  const chrome={storage:{local:{get:async()=>structuredClone(store),set:async data=>Object.assign(store,structuredClone(data))}},runtime:{onMessage:{addListener(fn){listener=fn}},onInstalled:{addListener(){}},onStartup:{addListener(){}}},permissions:{contains:async()=>true},scripting:{getRegisteredContentScripts:async()=>[],registerContentScripts:async()=>{},unregisterContentScripts:async()=>{},executeScript:async()=>[]},tabs:{create:async()=>{creates++;return {id:8}},get:async()=>({id:8,status:'complete',url:'https://creator.xiaohongshu.com/publish/publish'}),onUpdated:{addListener(){},removeListener(){}},onRemoved:{addListener(){},removeListener(){}}}};
  const context={chrome,URL,AbortController,setTimeout:f=>{fire=f;return 1},clearTimeout:()=>{},crypto:{randomUUID:()=>id},console,runXhsDraft:()=>{calls++;return new Promise(()=>{})}};
  vm.runInNewContext(loadBackgroundSource(),context);
  const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const message={channel:'mingya-workbench',version:1,id,action:'draft',payload};
  const sender={frameId:0,url:'https://work.example',tab:{id:42}};
  const send=()=>new Promise(resolve=>listener(message,sender,resolve));
  const pending=send();await new Promise(resolve=>setImmediate(resolve));fire();
  assert.equal((await pending).state,'unconfirmed');
  assert.equal((await send()).state,'unconfirmed');
  assert.equal(calls,1);assert.equal(creates,1);
});


test('unknown receipt survives worker restart and rejects same nonce in another tab', async () => {
  const store={origins:['https://work.example']};
  const payload={title:'PRIVATE_TITLE',body:'PRIVATE_BODY',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const first=setup(async()=>{throw Error('PRIVATE');},store);
  assert.equal((await first.send('draft',{payload})).state,'unconfirmed');
  const second=setup(async()=>{throw Error('SHOULD_NOT_RUN');},store);
  assert.equal((await second.send('draft',{payload},{frameId:0,url:'https://work.example',tab:{id:99}})).state,'unconfirmed');
  assert.equal(second.creates,0);assert.equal(second.draftCalls,0);
  const serialized=JSON.stringify(store);
  for (const value of ['PRIVATE_TITLE','PRIVATE_BODY','YQ==']) assert.equal(serialized.includes(value),false);
});

test('storage failure prevents all side effects', async () => {
  const app=setup();
  app.chrome.storage.local.set=async()=>{throw Error('SECRET')};
  const result=await app.send('open');
  assert.equal(result.ok,false);assert.equal(app.creates,0);assert.equal(app.draftCalls,0);
});

test('concurrent identical claims allow at most one side effect', async () => {
  const app=setup();
  const [a,b]=await Promise.all([app.send('open'),app.send('open')]);
  assert.equal(app.creates,1);assert.equal([a.state,b.state].filter(x=>x==='opened').length,1);
});

test('recovered in-flight receipt blocks a new draft but permits inspection', async () => {
  const store={origins:['https://work.example'], jobReceipts:[{origin:'https://work.example',id,ownerTabId:42,action:'draft',state:'in_flight',action_sent:true,createdAt:1,updatedAt:1}]};
  const app=setup(undefined,store);
  const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const blocked=await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174001',payload});
  assert.equal(blocked.state,'busy');
  assert.match(blocked.message,/解除提交保护/);
  assert.equal(app.creates,0);assert.equal(app.draftCalls,0);
  assert.equal(store.jobReceipts.length,1);
  assert.equal((await app.send('open',{id:'123e4567-e89b-42d3-a456-426614174002'})).state,'opened');
  assert.equal(app.creates,1);assert.equal(app.draftCalls,0);
});

test('existing unknown receipt fence returns busy without running draft adapter', async () => {
  const store={origins:['https://work.example'], jobReceipts:[{origin:'https://work.example',id:'123e4567-e89b-42d3-a456-426614174099',ownerTabId:7,action:'draft',state:'unknown',action_sent:true,createdAt:1,updatedAt:2}]};
  const app=setup(async()=>{throw Error('SHOULD_NOT_RUN');},store);
  const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const blocked=await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174001',payload});
  assert.equal(blocked.ok,false);
  assert.equal(blocked.state,'busy');
  assert.match(blocked.message,/解除提交保护/);
  assert.equal(app.creates,0);
  assert.equal(app.draftCalls,0);
  assert.equal(store.jobReceipts.length,1);
  assert.equal(store.jobReceipts[0].state,'unknown');
});


test('duplicate relay injection installs only one listener', async () => {
  const listeners=[];let sends=0;
  const win={location:{origin:'https://work.example'},addEventListener(_,fn){listeners.push(fn)},postMessage(){}};win.top=win;
  const context=vm.createContext({window:win,document:{documentElement:{setAttribute(){}}},chrome:{runtime:{sendMessage:async()=>{sends++;return {ok:true,state:'connected',published:false}}}}});
  const source=readFileSync(new URL('relay.js',root),'utf8');
  vm.runInContext(source,context);vm.runInContext(source,context);
  for (const fn of listeners) fn({source:win,origin:'https://work.example',data:{channel:'mingya-workbench',version:1,id,action:'status'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(listeners.length,1);assert.equal(sends,1);
});

test('same host different ports share relay but keep separate exact-origin authorization', async () => {
  let handler, registrations=0, stored;
  const elements={origin:{value:'http://localhost:7861'},status:{},connect:{addEventListener(_,f){handler=f}}};
  const state={origins:['http://localhost:7860'],registrations:{'http://localhost:7860':'existing-id'}};
  const chrome={permissions:{request:async()=>true},storage:{local:{get:async()=>state,set:async x=>{stored=x}}},scripting:{getRegisteredContentScripts:async()=>[{id:'existing-id',matches:['http://localhost/*'],js:['relay.js']}],registerContentScripts:async()=>{registrations++},unregisterContentScripts:async()=>{}}};
  vm.runInNewContext(readFileSync(new URL('popup.js',root),'utf8'),{URL,chrome,document:{getElementById:id=>elements[id]},crypto:{randomUUID:()=>id}});
  await handler();
  assert.equal(registrations,1);
  assert.ok(stored.registrations['http://localhost:7861']);
  assert.deepEqual(Array.from(stored.origins),['http://localhost:7860','http://localhost:7861']);
  const app=setup(undefined,{origins:['http://localhost:7860']});
  assert.equal((await app.send('status',{}, {frameId:0,url:'http://localhost:7860/app',tab:{id:1}})).state,'connected');
  assert.equal((await app.send('status',{}, {frameId:0,url:'http://localhost:7861/app',tab:{id:1}})).state,'connected');
  assert.equal((await app.send('open',{}, {frameId:0,url:'http://localhost:7861/app',tab:{id:1}})).state,'error');
});

test('startup recovery migrates duplicate registration ids without dropping either exact origin', async () => {
  const origins=['http://localhost:7860','http://localhost:7861'];
  const store={origins,registrations:{[origins[0]]:'shared-id',[origins[1]]:'shared-id'}};
  const scripts=[{id:'shared-id',matches:[origins[1]+'/*'],runAt:'document_start'}];
  const registered=[],unregistered=[];
  const chrome={
    runtime:{onMessage:{addListener(){}},onInstalled:{addListener(){}},onStartup:{addListener(){}}},
    storage:{local:{get:async()=>structuredClone(store),set:async value=>Object.assign(store,structuredClone(value))}},
    scripting:{
      getRegisteredContentScripts:async()=>structuredClone(scripts),
      unregisterContentScripts:async({ids})=>{unregistered.push(...ids);for(const value of ids){const index=scripts.findIndex(script=>script.id===value);if(index>=0)scripts.splice(index,1);}},
      registerContentScripts:async values=>{registered.push(...structuredClone(values));scripts.push(...structuredClone(values));},
    },
  };
  vm.runInNewContext(loadBackgroundSource(),{chrome,URL,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:()=>id},console,runXhsDraft:async()=>{throw Error('not called')}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(unregistered.includes('shared-id'),true);
  assert.deepEqual(registered.map(item=>item.matches[0]).sort(),origins.map(origin=>origin+'/*').sort());
  assert.equal(store.registrations[origins[0]],'shared-id');
  assert.notEqual(store.registrations[origins[1]],'shared-id');
  assert.notEqual(store.registrations[origins[0]],store.registrations[origins[1]]);
});


test('debugger permission is required at installation, not optional', () => {
  const manifest=JSON.parse(readFileSync(new URL('manifest.json',root),'utf8'));
  assert.ok(manifest.permissions.includes('debugger'));
  assert.ok(!manifest.optional_permissions?.includes('debugger'));
  assert.ok(manifest.host_permissions.includes('http://127.0.0.1:7862/*'));
  assert.equal(manifest.content_scripts?.[0]?.js?.[0], 'relay.js');
  assert.equal(manifest.content_scripts?.[0]?.run_at, 'document_start');
});

test('wildcard origins never request permission or register scripts', async () => {
  let handler, requests=0, registrations=0;
  const elements={origin:{value:''},status:{},connect:{addEventListener(_,f){handler=f}}};
  const chrome={permissions:{request:async()=>{requests++;return true}},storage:{local:{get:async()=>({}),set:async()=>{}}},scripting:{getRegisteredContentScripts:async()=>[{id:'existing-id',matches:['http://localhost/*'],js:['relay.js']}],registerContentScripts:async()=>{registrations++}}};
  vm.runInNewContext(readFileSync(new URL('popup.js',root),'utf8'),{URL,chrome,document:{getElementById:id=>elements[id]},crypto:{randomUUID:()=>id}});
  for (const value of ['https://*','https://*.example.com']) {elements.origin.value=value;await handler();}
  assert.equal(requests,0);assert.equal(registrations,0);
});

test('proven auth before save permits only a fresh UUID and does not expose internal marker', async () => {
  const store={origins:['https://work.example']};
  const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const app=setup(async()=>({ok:false,state:'auth_required',published:false,action_sent:false}),store);
  const first=await app.send('draft',{payload});
  assert.equal(first.state,'auth_required');assert.equal('action_sent' in first,false);
  assert.equal(store.jobReceipts[0].state,'not_sent');assert.equal(store.jobReceipts[0].action_sent,false);
  assert.equal((await app.send('draft',{payload})).state,'unconfirmed');
  assert.equal(app.creates,1);
  assert.equal((await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174001',payload})).state,'auth_required');
  assert.equal(app.creates,2);
});

for (const marker of [undefined, true, null]) test(`unproven auth action ${marker} remains unknown`, async () => {
  const store={origins:['https://work.example']};
  const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const raw={ok:false,state:'auth_required',published:false};if (marker !== undefined) raw.action_sent=marker;
  const app=setup(async()=>raw,store);
  assert.equal((await app.send('draft',{payload})).state,'unconfirmed');
  assert.equal(store.jobReceipts[0].state,'unknown');
  const blocked=await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174001',payload});
  assert.equal(blocked.state,'busy');
  assert.match(blocked.message,/解除提交保护/);
  assert.equal(app.creates,1);
});

test('late not-sent auth after abort cannot clear unknown', async () => {
  let listener, fire, complete;const store={origins:['https://work.example']};
  const chrome={storage:{local:{get:async()=>structuredClone(store),set:async data=>Object.assign(store,structuredClone(data))}},runtime:{onMessage:{addListener(fn){listener=fn}},onInstalled:{addListener(){}},onStartup:{addListener(){}}},permissions:{contains:async()=>true},scripting:{getRegisteredContentScripts:async()=>[],registerContentScripts:async()=>{},unregisterContentScripts:async()=>{},executeScript:async()=>[]},tabs:{create:async()=>({id:8}),get:async()=>({id:8,status:'complete',url:'https://creator.xiaohongshu.com/publish/publish'}),onUpdated:{addListener(){},removeListener(){}},onRemoved:{addListener(){},removeListener(){}}}};
  vm.runInNewContext(loadBackgroundSource(),{chrome,URL,AbortController,setTimeout:f=>{fire=f;return 1},clearTimeout:()=>{},runXhsDraft:()=>new Promise(resolve=>{complete=resolve})});
  const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const msg={channel:'mingya-workbench',version:1,id,action:'draft',payload},sender={frameId:0,url:'https://work.example',tab:{id:42}};
  const pending=new Promise(resolve=>listener(msg,sender,resolve));
  await new Promise(resolve=>setImmediate(resolve));fire();
  assert.equal((await pending).state,'unconfirmed');
  complete({ok:false,state:'auth_required',published:false,action_sent:false});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(store.jobReceipts[0].state,'unknown');assert.equal(store.jobReceipts[0].action_sent,true);
});

test('proven pre-save failure preserves diagnosis and permits a fresh request only', async()=>{
 const store={origins:['https://work.example']};
 const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
 const app=setup(async({onProgress})=>{onProgress('tab');return {ok:false,state:'error',published:false,action_sent:false,diagnostic:'unknown'}},store);
 assert.equal((await app.send('draft',{payload})).state,'error');
 assert.equal(store.jobReceipts[0].state,'not_sent');assert.equal(store.jobReceipts[0].phase,'tab');assert.equal(store.jobReceipts[0].diagnostic,'unknown');
 assert.equal((await app.send('draft',{payload})).state,'unconfirmed');assert.equal(app.creates,1);
 assert.equal((await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174001',payload})).state,'error');assert.equal(app.creates,2);
});

test('adapter error without action_sent is treated as not_sent', async () => {
  const store={origins:['https://work.example']};
  const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const app=setup(async()=>({ok:false,state:'error',published:false,message:'未能安全完成草稿操作，请检查当前浏览器页面。'}),store);
  assert.equal((await app.send('draft',{payload})).state,'error');
  assert.equal(store.jobReceipts[0].state,'not_sent');
  assert.equal(store.jobReceipts[0].action_sent,false);
  assert.equal((await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174002',payload})).state,'error');
  assert.equal(app.creates,2);
});

test('adapter throw records sanitized task_throw diagnostic', async () => {
  const store={origins:['https://work.example']};
  const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
  const app=setup(async()=>{throw new Error('view');},store);
  assert.equal((await app.send('draft',{payload})).state,'unconfirmed');
  assert.equal(store.jobReceipts[0].state,'unknown');
  assert.equal(store.jobReceipts[0].diagnostic,'view');
  assert.equal(store.jobReceipts[0].err,'view');
  const blocked=await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174003',payload});
  assert.equal(blocked.state,'busy');
});

test('workbench review uses a confirmed snapshot without opening or replaying a task', async () => {
  const old={origin:'https://work.example',id:'123e4567-e89b-42d3-a456-426614174099',ownerTabId:7,action:'draft',state:'unknown',action_sent:true,createdAt:1,updatedAt:2};
  const other={...old,origin:'https://other.example'};
  const store={origins:['https://work.example'],jobReceipts:[old,other]};
  const app=setup(undefined,store);
  const ready=await app.send('review_prepare');
  assert.equal(ready.state,'review_ready');assert.match(ready.reviewToken,/^[0-9a-f-]{36}$/);
  assert.equal(store.jobReceipts[0].state,'unknown');
  const done=await app.send('review',{payload:{reviewToken:ready.reviewToken}});
  assert.equal(done.state,'reviewed');assert.equal(done.published,false);
  assert.equal(store.jobReceipts[0].state,'reviewed');assert.equal(store.jobReceipts[0].updatedAt,2);
  assert.equal(store.jobReceipts[1].state,'unknown');
  assert.equal(app.creates,0);assert.equal(app.draftCalls,0);
  const payload={title:'x',body:'a',tags:[],images:[{name:'a.png',mime:'image/png',base64:'YQ=='}]};
  assert.equal((await app.send('draft',{id:old.id,payload})).state,'unconfirmed');
  assert.equal(app.creates,0);
});

test('workbench review rejects changed snapshots, child frames and unbound origins', async () => {
  const row={origin:'https://work.example',id:'123e4567-e89b-42d3-a456-426614174099',ownerTabId:7,action:'draft',state:'unknown',action_sent:true,createdAt:1,updatedAt:2};
  for(const mutate of [rows=>rows[0].updatedAt++,rows=>rows.push({...row,id:'123e4567-e89b-42d3-a456-426614174098'}),rows=>rows.push({...row,state:'in_flight'})]){
    const store={origins:['https://work.example'],jobReceipts:[{...row}]};const app=setup(undefined,store);
    const ready=await app.send('review_prepare');assert.equal(ready.state,'review_ready');
    mutate(store.jobReceipts);
    assert.equal((await app.send('review',{payload:{reviewToken:ready.reviewToken}})).ok,false);
    assert.equal(store.jobReceipts[0].state,'unknown');assert.equal(app.creates,0);
  }
  const app=setup(undefined,{origins:['https://work.example'],jobReceipts:[row]});
  for(const sender of [{frameId:1,url:'https://work.example',tab:{id:7}},{frameId:0,url:'https://evil.example',tab:{id:7}}])
    assert.equal((await app.send('review_prepare',{},sender)).ok,false);
  assert.equal((await app.send('review',{payload:{reviewToken:id,extra:1}})).ok,false);
  assert.equal((await app.send('review',{payload:{reviewToken:id}})).ok,false);
  assert.equal(app.creates,0);assert.equal(app.draftCalls,0);
});


test('unconfirmed receipt diagnosis exposes only bounded counts and fixed reasons', async () => {
 const payload={title:'x',body:'a',tags:[],images:[{name:'a',mime:'image/png',base64:'YQ=='}]};
 const store={origins:['https://work.example']};
 const app=setup(async()=>({ok:false,state:'unconfirmed',published:false,message:'PRIVATE',receiptCheck:{baselineCount:12,observedCount:13,reason:'image'}}),store);
 const output=await app.send('draft',{payload});
 assert.equal(output.state,'unconfirmed');assert.equal(output.ok,false);assert.equal(output.published,false);
 assert.match(output.message,/记录封面尚未读取完成/);assert.match(output.message,/保存前12篇，当前13篇/);
 assert.doesNotMatch(JSON.stringify(output),/PRIVATE/);assert.equal(store.jobReceipts[0].state,'unknown');
 assert.equal((await app.send('draft',{id:'123e4567-e89b-42d3-a456-426614174004',payload})).state,'busy');
 assert.equal(app.draftCalls,1);
 for(const receiptCheck of [
  {baselineCount:-1,observedCount:13,reason:'image'},
  {baselineCount:12,observedCount:'PRIVATE',reason:'count'},
  {baselineCount:12,observedCount:13,reason:'PRIVATE'},
  {baselineCount:12,observedCount:13,reason:'image',title:'PRIVATE'},
  {baselineCount:12,observedCount:10001,reason:'count'},
 ]){
  const invalid=setup(async()=>({ok:false,state:'unconfirmed',published:false,message:'PRIVATE',receiptCheck}));
  const result=await invalid.send('draft',{payload});assert.equal(result.state,'unconfirmed');
  assert.doesNotMatch(result.message,/PRIVATE|保存前/);
 }
});
