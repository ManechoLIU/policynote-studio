import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runXhsDraft} from '../../browser-extension/xhs-draft.mjs';
import {runInNewContext} from 'node:vm';
const payload={title:'标题',body:'正文',tags:['保险'],images:[{name:'a.png',mime:'image/png',base64:'AQID'}]};
function fake(options={}) {
 const calls=[], stages=[]; let attached=0, detached=0;
 const chromeApi={tabs:{get:async()=>({url:options.url || 'https://creator.xiaohongshu.com/publish/publish'})}, debugger:{
  attach:async()=>{attached++},detach:async()=>{detached++},
  sendCommand:async(target,method,args)=>{calls.push({method,args});if(method==='Input.insertText')return {};if(method==='Runtime.evaluate')return {result:{objectId:'doc'}};
    if(method==='DOM.getDocument')return {root:options.tree || {nodeType:9,nodeName:'#document',documentURL:'https://creator.xiaohongshu.com/publish/publish',children:[]}};
    if(method==='DOM.resolveNode')return {object:{objectId:'node-'+args.backendNodeId}};
    if(method==='Runtime.callFunctionOn' && args.functionDeclaration.includes('expectedLabel, click, expectedHost')) return {result:{value:{valid:true,visible:true,enabled:true,label:args.arguments[0].value,clicked:args.arguments[1].value}}};
    if(args.functionDeclaration.startsWith('function(){return ('))return {result:{value:false}};
    const stage=args.arguments[0].value;stages.push(stage);if(options.wait===stage)return new Promise(()=>{});
    return {result:{value:{state:(typeof options.states?.[stage]==='function'?options.states[stage](args.arguments[1].value):options.states?.[stage]) || (stage==='receipt'?'saved':stage==='save_probe'?(options.tree?'none':'one'):'ok')}}};
  }}};
 return {chromeApi,calls,stages,counts:()=>({attached,detached})};
}
test('fixed CDP commands and data arguments yield fresh notice only',async()=>{
 const f=fake();const output=await runXhsDraft({tabId:4,job:{...payload,body:'x\'); evil() //'},chromeApi:f.chromeApi});
 assert.equal(output.state,'saved');assert.equal(output.receiptKind,'fresh_notice');assert.equal(output.published,false);
 assert.deepEqual(f.stages,['ready','visible','tab','upload','upload_state','fill','fill','fill','visible','baseline','save_probe','save','receipt']);
 assert.deepEqual(f.counts(),{attached:1,detached:1});
 const fill=f.calls.find(c=>c.args.arguments?.[0]?.value==='fill');
 assert.equal(fill.args.arguments[1].value.body,"x'); evil() // #保险");
 assert.equal(fill.args.functionDeclaration.includes("x'); evil()"),false);
 assert.deepEqual(f.calls.filter(c=>c.method==='Input.insertText').map(c=>c.args.text),['标题',"x'); evil() // #保险"]);
 assert.ok(f.calls.every(c=>['Emulation.setFocusEmulationEnabled','Runtime.evaluate','Runtime.callFunctionOn','DOM.getDocument','Input.insertText'].includes(c.method)));
 assert.equal(f.calls[0].method,'Emulation.setFocusEmulationEnabled');assert.deepEqual(f.calls[0].args,{enabled:true});
});
test('bad origin and invalid or oversized data stop before attach',async()=>{
 const f=fake({url:'https://evil.example/publish/publish'});assert.equal((await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi})).state,'error');assert.equal(f.counts().attached,0);
 const g=fake();assert.equal((await runXhsDraft({tabId:1,job:{...payload,body:'a'.repeat(1000)},chromeApi:g.chromeApi})).state,'error');assert.equal(g.counts().attached,0);
});
test('upload failure and old receipt veto never send save',async()=>{
 for(const stage of ['upload_state','baseline']){const f=fake({states:{[stage]:'unknown'}});const r=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});assert.equal(r.state,'error');assert.equal(r.action_sent,false);assert.equal(r.phase,stage);assert.equal(f.stages.includes('save'),false);assert.equal(f.counts().detached,1);}
});
test('unknown after save and abort do not retry; detach occurs',async()=>{
 const f=fake({states:{receipt:'unknown'}});assert.equal((await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi})).state,'unconfirmed');assert.equal(f.stages.filter(x=>x==='save').length,1);assert.equal(f.counts().detached,1);
 const g=fake({wait:'save'}), controller=new AbortController();const pending=runXhsDraft({tabId:1,job:payload,chromeApi:g.chromeApi,signal:controller.signal});
 while(!g.stages.includes('save'))await new Promise(r=>setImmediate(r));controller.abort();assert.equal((await pending).state,'unconfirmed');assert.equal(g.counts().detached,1);assert.equal(g.stages.filter(x=>x==='save').length,1);
});
test('actual page function rejects duplicate and publication buttons',async()=>{
 const f=fake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args.arguments?.[0]?.value==='save').args.functionDeclaration;
 let clicks=0;
 const make=text=>({tagName:'BUTTON',innerText:text,isConnected:true,hidden:false,disabled:false,className:'',parentElement:null,getClientRects:()=>[{}],getAttribute:()=>null,click:()=>clicks++});
 const document={body:{textContent:''},querySelectorAll(sel){if(sel==='button')return this.buttons;if(sel==='div.creator-publish-popover,[role=dialog]'||sel==='.login-container,.login-modal,.login-box,.sso-login-wrapper,.qrcode-img')return [];return []},buttons:[]};
 const window={__mingyaDraftReceipt:{document,notices:new WeakSet()}};
 const context={document,window,location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),WeakSet};
 const run=runInNewContext(`(${code})`,context);
 document.buttons=[make('发布')];assert.equal(run('save',{}).state,'unknown');
 document.buttons=[make('存草稿'),make('保存草稿')];assert.equal(run('save',{}).state,'unknown');assert.equal(clicks,0);
 document.buttons=[make('存草稿'),make('发布')];assert.equal(run('save',{}).state,'ok');assert.equal(clicks,1);
});


function tree(labels){return {nodeType:9,nodeName:'#document',documentURL:'https://creator.xiaohongshu.com/publish/publish',children:[{nodeType:1,nodeName:'XHS-PUBLISH-BTN',localName:'xhs-publish-btn',backendNodeId:20,shadowRoots:[{shadowRootType:'closed',children:labels.map((text,i)=>({nodeType:1,nodeName:'BUTTON',backendNodeId:30+i,children:[{nodeType:3,nodeValue:text}]}))}]}]};}
test('closed shadow scans twice, resolves unique draft and never selects publish',async()=>{
 const f=fake({tree:tree(['存草稿','发布'])});const r=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 assert.equal(r.state,'saved');assert.equal(f.calls.filter(c=>c.method==='DOM.getDocument').length,2);
 assert.deepEqual(f.calls.filter(c=>c.method==='DOM.resolveNode').map(c=>c.args.backendNodeId),[30,20]);
 assert.equal(f.calls.filter(c=>c.args.functionDeclaration?.includes('expectedLabel, click, expectedHost') && c.args.arguments[1].value===true).length,1);
});
test('duplicate shadow candidates and publish-only fail before click',async()=>{
 for(const labels of [['存草稿','保存草稿'],['发布']]){const f=fake({tree:tree(labels)});const r=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});assert.equal(r.state,'error');assert.equal(f.calls.filter(c=>c.method==='DOM.resolveNode').length,0);}
});
test('actual baseline rejects old notice and confirmation accepts only exact draft intent',async()=>{
 const f=fake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args.arguments?.[0]?.value==='baseline').args.functionDeclaration;
 let clicked=0;
 const button=text=>({tagName:'BUTTON',innerText:text,textContent:text,isConnected:true,hidden:false,disabled:false,className:'',parentElement:null,getClientRects:()=>[{}],getAttribute:()=>null,click:()=>clicked++});
 const d={tagName:'DIV',innerText:'是否保存草稿？保存并离开取消',isConnected:true,hidden:false,className:'',parentElement:null,getClientRects:()=>[{}],getAttribute:()=>null,querySelector:()=>null,querySelectorAll:()=>[button('保存并离开'),button('取消')]};
 const document={body:{textContent:'草稿保存成功'},querySelectorAll(sel){if(sel==='div.creator-publish-popover,[role=dialog]')return this.dialogs||[];return []}};
 const window={};const run=runInNewContext(`(${code})`,{document,window,location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),WeakSet});
 assert.equal(run('baseline',{}).state,'unknown');document.body.textContent='';assert.equal(run('baseline',{}).state,'ok');
 document.dialogs=[d];assert.equal(run('confirm',{}).state,'ok');assert.equal(clicked,1);
 d.innerText='确认发布？保存并离开取消';assert.equal(run('confirm',{}).state,'unknown');assert.equal(clicked,1);
});
test('official new toast receipt requires a fresh exact safe success notice',async()=>{
 const f=fake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args.arguments?.[0]?.value==='baseline').args.functionDeclaration;
 const node=(tag,className,parentElement=null)=>({tagName:tag,className,parentElement,isConnected:true,hidden:false,getClientRects:()=>[{}],getAttribute:k=>k==='contenteditable'?null:null});
 const makeNotice=({editor=false,text='保存成功',description='保存成功',icons=1,descriptions=1}={})=>{
  const top=node('DIV',editor?'editor':'',null),toast=node('DIV','d-new-toast',top),slot=node('DIV','',toast),wrapper=node('DIV','d-toast-wrapper',slot),notice=node('DIV','d-toast-notice',wrapper);
  const iconNodes=Array.from({length:icons},()=>node('SPAN','d-toast-icon d-toast-icon-success',notice));
  const descriptionNodes=Array.from({length:descriptions},()=>({...node('SPAN','d-toast-description',notice),innerText:description,textContent:description}));
  Object.assign(notice,{innerText:text,textContent:text,querySelector:s=>s==='input,textarea,[contenteditable],[role=textbox]'?null:null,querySelectorAll:s=>s==='span.d-toast-icon.d-toast-icon-success'?iconNodes:s==='div.d-toast-main div.d-toast-content > span.d-toast-description'?descriptionNodes:[]});
  return notice;
 };
 const document={body:{textContent:''},legacy:[],official:[],dialogs:[],querySelectorAll(sel){if(sel==='div.creator-publish-popover,[role=dialog]')return this.dialogs;if(sel==='.d-message,.d-toast')return this.legacy;if(sel==='body > div > .d-new-toast > div > .d-toast-wrapper > .d-toast-notice')return this.official;return []}};
 const window={};const run=runInNewContext(`(${code})`,{document,window,location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),WeakSet});

 assert.equal(run('baseline',{}).state,'ok');
 const fresh=makeNotice();document.official=[fresh];assert.equal(run('receipt',{}).state,'saved');

 document.official=[];const old=makeNotice();document.official=[old];assert.equal(run('baseline',{}).state,'ok');assert.equal(run('receipt',{}).state,'pending');

 document.official=[];assert.equal(run('baseline',{}).state,'ok');document.official=[makeNotice({editor:true})];assert.equal(run('receipt',{}).state,'pending');

 document.official=[];document.body.textContent='正文提到了保存成功，但不是提示';assert.equal(run('baseline',{}).state,'ok');
 document.official=[makeNotice({icons:2})];assert.equal(run('receipt',{}).state,'pending');
 document.official=[makeNotice({description:'保存成功。',text:'保存成功。'})];assert.equal(run('receipt',{}).state,'pending');
});
test('actual upload refuses existing preview and binds new files before preview can be ready',async()=>{
 const f=fake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args.arguments?.[0]?.value==='upload').args.functionDeclaration;
 const input={isConnected:true,disabled:false,files:[],matches:s=>s==='input.upload-input[multiple]',getAttribute:k=>k==='accept'?'.jpg,.jpeg,.png,.webp':null,dispatchEvent(){}};
 const image={isConnected:true,hidden:false,parentElement:null,complete:true,naturalWidth:3,naturalHeight:2,getAttribute:()=>null,getClientRects:()=>[{}]};
 const button={isConnected:true,hidden:false,parentElement:null,disabled:false,getClientRects:()=>[{}],getAttribute:()=>null};
 const slot={isConnected:true,hidden:false,parentElement:null,innerText:'',getAttribute:()=>null,getClientRects:()=>[{}],querySelector:()=>null,querySelectorAll:s=>s==='img.img.preview'?[image]:[button]};
 const document={slots:[slot],fields:[],querySelectorAll(s){if(s==='input[type=file]')return [input];if(s==='.img-preview-area .pr')return this.slots;if(s.startsWith('div.d-input input'))return this.fields;return []}};
 const window={};class File {constructor(parts,name,opts){this.name=name;this.type=opts.type;this.size=parts[0].length;this.lastModified=123;}}
 class DataTransfer {constructor(){this.files=[];this.items={add:f=>this.files.push(f)}}};
 class Event {constructor(type,init={}){this.type=type;Object.assign(this,init);}}
 class InputEvent {constructor(type,init={}){this.type=type;Object.assign(this,init);}}
 const run=runInNewContext(`(${code})`,{document,window,location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},getComputedStyle:e=>({display:'block',visibility:'visible',opacity:e&&e.opacity!=null?String(e.opacity):'1'}),DataTransfer,File,Uint8Array,Event,InputEvent,atob:s=>Buffer.from(s,'base64').toString('binary')});
 assert.equal(run('upload_state',{count:1}).state,'unknown');
 assert.equal(run('upload',{images:payload.images}).state,'unknown');
 document.slots=[];document.fields=[{value:'已填标题'}];assert.equal(run('upload',{images:payload.images}).state,'unknown');
 document.fields=[];assert.equal(run('upload',{images:payload.images}).state,'pending');
 document.slots=[slot];assert.equal(run('upload',{images:payload.images}).state,'ok');
 // Extra non-image file input must not block the single image accepter.
 document.slots=[];input.files=[];delete window.__mingyaDraftUpload;
 const video={isConnected:true,disabled:false,files:[],getAttribute:k=>k==='accept'?'video/*':null,dispatchEvent(){}};
 document.querySelectorAll=function(s){if(s==='input[type=file]')return [video,input];if(s==='.img-preview-area .pr')return this.slots;if(s.startsWith('div.d-input input'))return this.fields;return []};
 assert.equal(run('upload',{images:payload.images}).state,'pending');
 document.slots=[slot];assert.equal(run('upload',{images:payload.images}).state,'ok');
 // No image accepter yet (图文 tab not ready) → pending, not hard unknown.
 document.slots=[];
 document.querySelectorAll=function(s){if(s==='input[type=file]')return [video];if(s==='.img-preview-area .pr')return this.slots;if(s.startsWith('div.d-input input'))return this.fields;return []};
 assert.equal(run('upload',{images:payload.images}).state,'pending');
 document.querySelectorAll=function(s){if(s==='input[type=file]')return [input];if(s==='.img-preview-area .pr')return this.slots;if(s.startsWith('div.d-input input'))return this.fields;return []};
 document.slots=[slot];assert.equal(run('upload_state',{count:1}).state,'ok');
 // Platform cleared/replaced FileList after consuming upload: still ok when preview slot is ready.
 input.files=[{name:'old.png',type:'image/png',size:3,lastModified:123}];assert.equal(run('upload_state',{count:1}).state,'ok');
 // Signature lost and no previews yet => pending (platform clears files before slots mount).
 document.slots=[];assert.equal(run('upload_state',{count:1}).state,'pending');
 // Progressive slots for a single step still pending until count matches.
 document.slots=[];input.files=[];delete window.__mingyaDraftUpload;
 assert.equal(run('upload',{images:[payload.images[0]],priorCount:0}).state,'pending');
 document.slots=[slot];assert.equal(run('upload',{images:[payload.images[0]],priorCount:0}).state,'ok');
 assert.equal(run('upload_state',{count:1}).state,'ok');
 // Sequential 2nd image: occupied first input + empty .upload-input more control.
 input.files=[{name:'a.png',type:'image/png',size:3,lastModified:123}];
 const more={isConnected:true,disabled:false,files:[],className:'upload-input',matches:s=>s==='.img-list > .top > input[multiple]',classList:{contains:n=>n==='upload-input'},getAttribute:k=>k==='accept'?'.jpg,.jpeg,.png,.webp':null,dispatchEvent(){}};
 document.querySelectorAll=function(s){if(s==='input[type=file]')return [input,more];if(s==='.img-preview-area .pr')return this.slots;if(s.startsWith('div.d-input input'))return this.fields;return []};
 assert.equal(run('upload',{images:[payload.images[0]],priorCount:1}).state,'pending');
 document.slots=[slot];assert.equal(run('upload_state',{count:2}).state,'pending');
 document.slots=[slot,slot];assert.equal(run('upload',{images:[payload.images[0]],priorCount:1}).state,'ok');
 assert.equal(run('upload_state',{count:2}).state,'ok');
 // 3rd image
 more.files=[{name:'b.png',type:'image/png',size:3,lastModified:124}];
 const more2={isConnected:true,disabled:false,files:[],className:'upload-input',matches:s=>s==='.img-list > .top > input[multiple]',classList:{contains:n=>n==='upload-input'},getAttribute:k=>k==='accept'?'.jpg,.jpeg,.png,.webp':null,dispatchEvent(){}};
 document.querySelectorAll=function(s){if(s==='input[type=file]')return [input,more2];if(s==='.img-preview-area .pr')return this.slots;if(s.startsWith('div.d-input input'))return this.fields;return []};
 assert.equal(run('upload',{images:[payload.images[0]],priorCount:2}).state,'pending');
 document.slots=[slot,slot,slot];assert.equal(run('upload',{images:[payload.images[0]],priorCount:2}).state,'ok');
 assert.equal(run('upload_state',{count:3}).state,'ok');
 // Hover-mask: edit-btn is in the DOM and enabled, but its parent opacity is 0 until hover.
 const hoverMask={isConnected:true,hidden:false,parentElement:null,opacity:'0',getAttribute:()=>null,getClientRects:()=>[{}]};
 button.parentElement=hoverMask;
 assert.equal(run('upload_state',{count:3}).state,'ok');
 button.disabled=true;assert.equal(run('upload_state',{count:3}).state,'pending');
 button.disabled=false;button.getAttribute=k=>k==='aria-disabled'?'true':null;assert.equal(run('upload_state',{count:3}).state,'pending');
});
function viewFake({badHit=false, oldTitle=false, noGrowth=false, offscreen=false}={}) {
 let view='open', pointerCount=0, saved=false;const calls=[], snapshots=[];
 const chromeApi={tabs:{get:async()=>({url:'https://creator.xiaohongshu.com/publish/publish'})},debugger:{attach:async()=>{},detach:async()=>{},sendCommand:async(_,method,args)=>{
  calls.push({method,args});
  if(method==='Input.insertText')return {};if(method==='Runtime.evaluate')return {result:{objectId:'doc'}};
  if(method==='DOM.getDocument')return {root:{nodeType:9,nodeName:'#document',documentURL:'https://creator.xiaohongshu.com/publish/publish',children:[]}};
  if(method==='DOM.describeNode')return {node:{backendNodeId:42}};
  if(method==='DOM.getContentQuads')return {quads:offscreen?[[210,210,230,210,230,230,210,230]]:[[10,10,30,10,30,30,10,30]]};
  if(method==='DOM.getNodeForLocation')return {backendNodeId:badHit?99:42};
  if(method==='DOM.resolveNode')return {object:{objectId:args.backendNodeId===42?'hit':'bad'}};
  if(method==='Input.dispatchMouseEvent') {if(args.type==='mouseReleased'){pointerCount++; view=pointerCount===1?'tab':pointerCount===2?'close':'open';}return {};}
  const fn=args.functionDeclaration;
  if(fn.startsWith('function(action){if(')){pointerCount++;view=pointerCount===1?'tab':pointerCount===2?'close':'open';return {result:{value:true}};}
  if(fn.startsWith('function(){return ('))return {result:{value:true}};
  if(fn.startsWith('function(){return {width'))return {result:{value:{width:100,height:100}}};
  if(fn.startsWith('function(action){return (') && args.objectId==='doc')return {result:args.arguments[0].value===view?{objectId:'view-'+view}:{type:'object',subtype:'null'}};
  if(fn.startsWith('function(action){return ('))return {result:{value:true}};
  if(fn.startsWith('function(hit)'))return {result:{value:args.arguments[0].objectId==='hit'}};
  if(fn.startsWith('function(target){return (')) {
   const value=view==='open'?{pending:true}:{ok:true,count:saved?(noGrowth?7:8):7,match:(saved||oldTitle)?{title:'标题',saved_at:'2026-11-04 23:01:41'}:null};
   snapshots.push({saved,value});return {result:{value}};
  }
  const stage=args.arguments[0].value;if(stage==='save')saved=true;
  return {result:{value:{state:stage==='save_probe'?'one':stage==='receipt'?'auth':'ok'}}};
 }}};
 return {chromeApi,calls,snapshots,getCount:()=>pointerCount};
}
test('closed drawer after baseline cannot be treated as a new-record save receipt',async()=>{
 const f=viewFake();const r=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 assert.deepEqual(f.snapshots.filter(s=>!s.saved && s.value.ok),[{saved:false,value:{ok:true,count:7,match:null}}]);
 assert.deepEqual(f.snapshots.filter(s=>s.saved),[{saved:true,value:{pending:true}}]);
 assert.equal(r.state,'unconfirmed');assert.equal(r.ok,false);assert.equal(r.receiptKind,undefined);
 assert.equal(f.getCount(),3);assert.equal(f.calls.filter(c=>c.method==='Input.dispatchMouseEvent').length,0);
 assert.equal(f.calls.filter(c=>c.args?.arguments?.[0]?.value==='save').length,1);
});
test('read-only drawer navigation does not depend on pointer hit testing',async()=>{
 const f=viewFake({badHit:true});const r=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 assert.equal(f.getCount(),3);
 assert.equal(f.calls.some(c=>['DOM.getContentQuads','DOM.getNodeForLocation'].includes(c.method)),false);
 assert.equal(f.calls.filter(c=>c.method==='Input.dispatchMouseEvent').length,0);
 assert.equal(f.calls.some(c=>c.args?.arguments?.[0]?.value==='upload'),true);
 assert.equal(f.calls.filter(c=>c.args?.arguments?.[0]?.value==='save').length,1);
 assert.equal(r.action_sent,undefined);
 assert.notEqual(r.phase,'view_tab');
});
test('read-only drawer navigation preserves baseline outside the viewport',async()=>{
 const f=viewFake({offscreen:true});const r=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 assert.equal(f.getCount(),3);
 assert.equal(f.calls.some(c=>['DOM.getContentQuads','DOM.getNodeForLocation'].includes(c.method)),false);
 assert.equal(f.calls.some(c=>c.args?.arguments?.[0]?.value==='upload'),true);
 assert.equal(f.calls.filter(c=>c.args?.arguments?.[0]?.value==='save').length,1);
 assert.notEqual(r.phase,'view_open');assert.notEqual(r.diagnostic,'viewport');
 // The fake still supplies no fresh receipt or valid new-record evidence.
 assert.notEqual(r.state,'saved');assert.notEqual(r.state,'record_found');
});
test('open click without drawer effect soft-skips baseline and still reaches save',async()=>{
 let clicks=0, saved=false, openedAttempt=false;const calls=[];
 const chromeApi={tabs:{get:async()=>({url:'https://creator.xiaohongshu.com/publish/publish'})},debugger:{attach:async()=>{},detach:async()=>{},sendCommand:async(_,method,args)=>{
  calls.push({method,args});
  if(method==='Input.insertText')return {};if(method==='Runtime.evaluate')return {result:{objectId:'doc'}};
  if(method==='DOM.describeNode')return {node:{backendNodeId:42}};
  if(method==='DOM.getContentQuads')return {quads:[[10,10,30,10,30,30,10,30]]};
  if(method==='DOM.getNodeForLocation')return {backendNodeId:42};
  if(method==='DOM.resolveNode')return {object:{objectId:args.backendNodeId===42?'hit':'bad'}};
  if(method==='DOM.getDocument')return {root:{nodeType:9,nodeName:'#document',documentURL:'https://creator.xiaohongshu.com/publish/publish',children:[]}};
  const fn=args.functionDeclaration||'';
  if(fn.startsWith('function(action){if(')){clicks++;openedAttempt=true;return {result:{value:true}};}
  if(fn.startsWith('function(){return ('))return {result:{value:true}};
  if(fn.startsWith('function(){return {width'))return {result:{value:{width:100,height:100}}};
  if(fn.startsWith('function(action){return (') && args.objectId==='doc'){
    // Before click, open control exists; after click the drawer still never appears.
    if(args.arguments?.[0]?.value==='open' && !openedAttempt) return {result:{objectId:'view-open'}};
    return {result:{type:'object',subtype:'null'}};
  }
  if(fn.startsWith('function(action){return ('))return {result:{value:true}};
  if(fn.startsWith('function(hit)'))return {result:{value:args.arguments[0].objectId==='hit'}};
  if(fn.startsWith('function(target){return ('))return {result:{value:{pending:true}}};
  const stage=args.arguments?.[0]?.value;if(stage==='save')saved=true;
  return {result:{value:{state:stage==='save_probe'?'one':stage==='receipt'?(saved?'saved':'pending'):'ok'}}};
 }}};
 const r=await runXhsDraft({tabId:1,job:payload,chromeApi});
 assert.equal(clicks,1);
 assert.equal(calls.some(c=>c.args?.arguments?.[0]?.value==='upload'),true);
 assert.equal(calls.filter(c=>c.args?.arguments?.[0]?.value==='save').length,1);
 assert.equal(r.state,'saved');
 assert.notEqual(r.phase,'view_tab');
 assert.notEqual(r.diagnostic,'view');
});
test('embedded drawer reader enforces count, exact same-row time, and duplicate refusal',async()=>{
 const f=viewFake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args?.functionDeclaration?.startsWith('function(target){return (')).args.functionDeclaration;
 class E {constructor(tag,cls='',text=''){this.tagName=tag;this.className=cls;this.text=text;this.children=[];this.parentElement=null;}get classList(){const a=this.className.split(' ').filter(Boolean);a.contains=x=>a.includes(x);return a}get textContent(){return this.text+this.children.map(c=>c.textContent).join('')} getClientRects(){return [{}]}getAttribute(){return null}add(e){e.parentElement=this;this.children.push(e);return e}querySelectorAll(sel){let all=[];const walk=n=>n.children.forEach(c=>{all.push(c);walk(c)});walk(this);if(sel==='*')return all;let direct=sel.startsWith(':scope > ');if(direct)sel=sel.slice(9);let [tag,...classes]=sel.split('.');return (direct?this.children:all).filter(e=>(!tag||e.tagName===tag.toUpperCase())&&classes.every(c=>e.classList.contains(c)));}}
 let doc=new E('DOCUMENT'), body=doc.add(new E('BODY')), drawer=body.add(new E('DIV','d-drawer d-drawer-right draft-drawer')),content=drawer.add(new E('DIV','d-drawer-content')),tab=content.add(new E('DIV','tab-item active')),label=tab.add(new E('SPAN','tab-item-text','图文笔记(2)')),list=content.add(new E('DIV','draft-list'));
 function row(title,time){const r=list.add(new E('DIV','draft-item')),info=r.add(new E('DIV','draft-info')),desc=info.add(new E('DIV','draft-info-desc'));desc.add(new E('DIV','draft-title-text has-title',title));if(time)desc.add(new E('DIV','',time));info.add(new E('DIV','','编辑'));info.add(new E('DIV','','删除'));return desc;}
 let a=row('标题','保存于 2026-11-04 23:01:41');row('其他','保存于 2026-11-04 23:02:41');
 const run=runInNewContext('('+code+')',{document:doc,location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})});
 assert.equal(run('标题').match.saved_at,'2026-11-04 23:01:41');
 const oldTitle=list.children[1].children[0].children[0].children[0];
 oldTitle.className='draft-title-text';oldTitle.text='暂无笔记标题';
 assert.equal(run('标题').ok,true);assert.equal(run('标题').match.title,'标题');
 oldTitle.text='未知占位';assert.equal(run('标题').error,'unknown_structure');
 oldTitle.className='draft-title-text has-title';oldTitle.text='其他';

 const savedRows=list.children;list.children=[];label.text='图文笔记(0)';
 const empty=list.add(new E('DIV','empty-state')),notice=empty.add(new E('DIV','d-result')),subtitle=notice.add(new E('DIV','d-result-subtitle'));subtitle.add(new E('SPAN','','暂无草稿'));
 assert.equal(run('标题').ok,true);assert.equal(run('标题').count,0);assert.equal(run('标题').match,null);
 subtitle.children[0].text='加载失败';assert.equal(run('标题').error,'unknown_structure');subtitle.children[0].text='暂无草稿';
 list.add(new E('DIV','','未知内容'));assert.equal(run('标题').error,'unknown_structure');
 list.children=savedRows;label.text='图文笔记(2)';
 label.text='图文笔记(3)';assert.equal(run('标题').error,'unknown_structure');label.text='图文笔记(2)';
 a.children.pop();assert.equal(run('标题').error,'unknown_structure');
 a.add(new E('DIV','','保存于 2026-11-04 23:01:41'));list.children[1].children[0].children[0].children[0].text='标题';assert.equal(run('标题').error,'unknown_structure');
});


test('auth-required before save explicitly confirms no save action was sent',async()=>{
 for(const stage of ['tab','upload','upload_state','fill']){
  const f=fake({states:{[stage]:'auth'}});
  const r=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
  assert.equal(r.state,'auth_required');assert.equal(r.action_sent,false);
  assert.equal(f.stages.includes('save'),false);assert.equal(f.counts().detached,1);
 }
});

test('authentication change after save is unconfirmed',async()=>{
 const f=fake({states:{receipt:'auth'}});
 const r=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 assert.equal(r.state,'unconfirmed');assert.equal('action_sent' in r,false);
 assert.equal(f.stages.filter(x=>x==='save').length,1);
});

test('image tab ignores aria-hidden, transparent and off-viewport copies', async()=>{
 const f=fake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args.arguments?.[0]?.value==='tab').args.functionDeclaration;
 let clicks=0;
 const make=(hidden=false,opacity='1',left=128)=>({tagName:'DIV',innerText:'上传图文',className:'creator-tab',isConnected:true,parentElement:null,hidden:false,opacity,getClientRects:()=>[{}],getBoundingClientRect:()=>({left,top:12,right:left+96,bottom:52,width:96,height:40}),getAttribute:k=>k==='aria-hidden'&&hidden?'true':null,click:()=>{clicks++}});
 const nodes=[make(false,'1',-9999),make(true,'0.00001'),make()];
 const document={querySelectorAll:sel=>sel==='div.creator-tab'?nodes:[]};
 const run=runInNewContext('('+code+')',{document,location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},innerWidth:1400,innerHeight:900,getComputedStyle:e=>({display:'flex',visibility:'visible',opacity:e.opacity})});
 assert.equal(run('tab',{}).state,'ok');assert.equal(clicks,1);
 nodes.push(make());assert.equal(run('tab',{}).state,'unknown');assert.equal(clicks,1);
});

test('waits for SPA readiness before attempting upload or save',async()=>{
 let reads=0;const f=fake({states:{ready:()=>++reads<3?'pending':'ok'}});
 assert.equal((await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi})).state,'saved');
 assert.deepEqual(f.stages.slice(0,5),['ready','ready','ready','visible','tab']);assert.equal(f.stages.filter(x=>x==='save').length,1);
});


test('sequential upload uses the multiple append input and never redispatches while previews mount', async()=>{
 const f=fake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args.arguments?.[0]?.value==='upload').args.functionDeclaration;
 const events=[];
 const input=(name,selector)=>({isConnected:true,disabled:false,files:[],className:name==='initial'?'upload-input':'',
  matches:s=>s===selector,classList:{contains:s=>name==='initial'&&s==='upload-input'},
  getAttribute:k=>k==='accept'?'.jpg,.jpeg,.png,.webp':null,
  dispatchEvent(e){events.push([name,e.type]);this.files=[];}});
 const initial=input('initial','input.upload-input[multiple]'),append=input('append','.img-list > .top > input[multiple]'),replace=input('replace','');
 const document={slots:[],inputs:[initial],querySelectorAll(s){if(s==='input[type=file]')return this.inputs;if(s==='.img-preview-area .pr')return this.slots;return []}};
 const window={};class File{constructor(parts,name,opts){this.name=name;this.type=opts.type;this.size=parts[0].length;this.lastModified=1;}}
 class DataTransfer{constructor(){this.files=[];this.items={add:f=>this.files.push(f)}}}
 class Event{constructor(type,init){this.type=type;Object.assign(this,init)}}
 const run=runInNewContext('('+code+')',{document,window,location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},DataTransfer,File,Event,InputEvent:Event,Uint8Array,atob:s=>Buffer.from(s,'base64').toString('binary')});
 run('upload',{images:payload.images,priorCount:0});
 run('upload',{images:payload.images,priorCount:0});
 assert.deepEqual(events,[['initial','change']]);
 document.slots=[{}];document.inputs=[initial,append,replace];
 run('upload',{images:payload.images,priorCount:1});
 run('upload',{images:payload.images,priorCount:1});
 assert.deepEqual(events,[['initial','change'],['append','change']]);
 document.slots=[{},{}];assert.equal(run('upload',{images:payload.images,priorCount:1}).state,'ok');
 document.inputs=[replace];assert.equal(run('upload',{images:payload.images,priorCount:2}).state,'pending');
});


test('fill prepares real browser input without changing framework values directly', async()=>{
 const f=fake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args.arguments?.[0]?.value==='fill').args.functionDeclaration;
 let titleFocused=0,titleSelected=0,bodyFocused=0,bodySelected=0;
 class Input{constructor(){this._value='';this.isConnected=true;this.parentElement=null;this.disabled=false;this.readOnly=false;this.className='';}get value(){return this._value}set value(v){this._value=v}getAttribute(){return null}getClientRects(){return [{}]}focus(){titleFocused++}select(){titleSelected++}blur(){}dispatchEvent(){}}
 const title=new Input();const editor={isConnected:true,parentElement:null,className:'tiptap ProseMirror',textContent:'',innerText:'',children:[],getClientRects:()=>[{}],getAttribute:()=>null,closest:()=>null,focus(){bodyFocused++},blur(){},dispatchEvent(){}};
 const preview={textContent:'标题',isConnected:true,parentElement:null,hidden:false,className:'title',getClientRects:()=>[{}],getAttribute:()=>null};
 const document={querySelectorAll(s){if(s==='div.d-input input,input[placeholder*="标题"]')return [title];if(s==='[contenteditable="true"]')return [editor];if(s==='.image-area .scroll-container > .title')return [preview];return []},createRange(){return {selectNodeContents(e){assert.equal(e,editor);bodySelected++}}}};
 const selection={removeAllRanges(){},addRange(){}};
 const run=runInNewContext('('+code+')',{document,window:{getSelection:()=>selection},location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},HTMLInputElement:Input,Event:class{},InputEvent:class{},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})});
 const data={title:'标题',body:'首段\n\n末段'};
 assert.equal(run('fill',{...data,part:'title'}).state,'ok');
 assert.equal(title.value,'');assert.equal(titleFocused,1);assert.equal(titleSelected,1);
 assert.equal(run('fill',{...data,part:'body'}).state,'ok');
 assert.equal(editor.textContent,'');assert.equal(bodyFocused,1);assert.equal(bodySelected,1);
 // Only the browser input command changes values; persisted paragraph serialization is checked later.
 title.value='标题';editor.children=[{tagName:'P',textContent:'首段'},{tagName:'P',textContent:''},{tagName:'P',textContent:'末段'}];
 assert.equal(run('fill',{...data,part:'verify'}).state,'ok');
 title.value='';assert.equal(run('fill',{...data,part:'verify'}).state,'pending');
});

test('fill verify waits for one exact visible title preview and rejects ambiguity', async()=>{
 const f=fake();await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 const code=f.calls.find(c=>c.args.arguments?.[0]?.value==='fill').args.functionDeclaration;
 const visibleNode=text=>({textContent:text,isConnected:true,parentElement:null,hidden:false,className:'title',getClientRects:()=>[{}],getAttribute:()=>null});
 const title={value:'标题',isConnected:true,parentElement:null,disabled:false,readOnly:false,className:'',getClientRects:()=>[{}],getAttribute:()=>null,blur(){}};
 const editor={isConnected:true,parentElement:null,className:'ProseMirror',innerText:'正文 #保险',children:[],getClientRects:()=>[{}],getAttribute:()=>null,closest:()=>null,blur(){}};
 let previews=[visibleNode('旧标题')];
 const document={querySelectorAll(selector){if(selector==='div.d-input input,input[placeholder*="标题"]')return [title];if(selector==='[contenteditable="true"]')return [editor];if(selector==='.image-area .scroll-container > .title')return previews;return []}};
 const run=runInNewContext('('+code+')',{document,window:{},location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})});
 const data={part:'verify',title:'标题',body:'正文 #保险'};
 assert.equal(run('fill',data).state,'pending');
 previews[0].textContent='标题';assert.equal(run('fill',data).state,'ok');
 previews=[];assert.equal(run('fill',data).state,'pending');
 previews=[visibleNode('标题'),visibleNode('标题')];assert.equal(run('fill',data).state,'unknown');
});

test('delayed title preview verification never repeats input and saves once after convergence', async()=>{
 let verifies=0;
 const f=fake({states:{fill:data=>data.part==='verify'&&++verifies<3?'pending':'ok'}});
 const result=await runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 assert.equal(result.state,'saved');
 assert.equal(verifies,3);
 assert.deepEqual(f.calls.filter(call=>call.method==='Input.insertText').map(call=>call.args.text),['标题','正文 #保险']);
 assert.equal(f.stages.filter(stage=>stage==='save').length,1);
});

test('title preview that never converges reaches no save action', async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 let verifies=0;
 const f=fake({states:{fill:data=>{if(data.part==='verify')verifies++;return data.part==='verify'?'pending':'ok'}}});
 const pending=runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi});
 while(verifies===0)await new Promise(resolve=>setImmediate(resolve));
 for(let i=0;i<45;i++){t.mock.timers.tick(250);await new Promise(resolve=>setImmediate(resolve));}
 const result=await pending;
 assert.equal(result.state,'error');
 assert.equal(result.action_sent,false);
 assert.equal(verifies,40);
 assert.equal(f.stages.includes('save'),false);
 assert.deepEqual(f.calls.filter(call=>call.method==='Input.insertText').map(call=>call.args.text),['标题','正文 #保险']);
});

function recoveryFake(options={}) {
 let saved=false, view='closed', delayed=0, interrupted=false, postSaveReads=0, redundantHits=0, baselineHitFailed=false, closeReceiptLost=false;
 const calls=[], stages=[], views=[];
 const chromeApi={tabs:{get:async()=>({url:saved&&options.crossOrigin?'https://other.example/publish/publish':'https://creator.xiaohongshu.com/publish/publish?from=tab_switch'})},debugger:{attach:async()=>{},detach:async()=>{},sendCommand:async(_,method,args)=>{
  calls.push({method,args,saved});
  if(method==='Input.insertText')return {};
  if(method==='Runtime.evaluate')return {result:{objectId:saved?'doc-after':'doc-before'}};
  if(method==='DOM.getDocument')return {root:{nodeType:9,nodeName:'#document',documentURL:'https://creator.xiaohongshu.com/publish/publish',children:[]}};
  if(method==='DOM.describeNode')return {node:{backendNodeId:42}};
  if(method==='DOM.getContentQuads')return {quads:[[10,10,30,10,30,30,10,30]]};
  if(method==='DOM.getNodeForLocation'){
   if(!saved&&!baselineHitFailed&&((options.baselineHit==='open'&&view==='closed')||(options.baselineHit==='close'&&view==='rows'))){baselineHitFailed=true;throw new Error('hit');}
   if(saved&&view==='rows'&&options.tabHitFails){redundantHits++;throw new Error('hit');}
   return {backendNodeId:42};
  }
  if(method==='DOM.resolveNode')return {object:{objectId:'hit'}};
  const fn=args.functionDeclaration;
  if(fn.startsWith('function(action){if(')){
   const action=args.arguments[0].value;views.push({action,saved});
   view=action==='open'?(options.activeOnOpen?'rows':'tab'):action==='tab'?'rows':'closed';
   if(!saved&&action==='close'&&options.closeReceiptLost&&!closeReceiptLost){closeReceiptLost=true;return {result:{value:false}};}
   return {result:{value:true}};
  }
  if(fn.startsWith('function(){return ('))return {result:{value:saved||!options.noBaseline}};
  if(fn.startsWith('function(){return {width'))return {result:{value:{width:100,height:100}}};
  if(fn.startsWith('function(action){return (') && args.objectId.startsWith('doc-')){
   const action=args.arguments[0].value;
   const available=action==='open'?view==='closed':action==='tab'?view!=='closed':view!=='closed';
   const waiting=saved&&action==='open'&&(options.noEntry||delayed++<(options.delay||0));
   return {result:available&&!waiting?{objectId:'view-'+action}:{subtype:'null'}};
  }
  if(fn.startsWith('function(action){return (')||fn.startsWith('function(hit)'))return {result:{value:true}};
  if(fn.startsWith('function(target){return (')){
   if(saved&&view==='rows')postSaveReads++;
   return {result:{value:view!=='rows'?{pending:true}:saved&&options.duplicate?{error:'unknown_structure'}:{ok:true,count:saved&&!options.noGrowth?11:10,match:saved&&postSaveReads>(options.targetDelay||0)?{title:'标题',saved_at:options.stale?'2026-11-04 22:52:40':'2026-11-05 22:52:40',images:[{loaded:!options.unloaded&&postSaveReads>(options.thumbnailDelay||0),natural_width:1080,natural_height:1440}]}:null}}};
  }
  const stage=args.arguments[0].value;stages.push(stage);
  if(stage==='save'){saved=true;view='closed';options.onSave?.();}
  if(options.contextAt===stage&&!interrupted){interrupted=true;throw new Error('Execution context was destroyed.');}
  return {result:{value:{state:stage==='save_probe'?'one':stage==='receipt'?'unknown':'ok'}}};
 }}};
 return {chromeApi,calls,stages,views,getRedundantHits:()=>redundantHits};
}
async function runRecoveryCase(t, options={}) {
 t.mock.timers.enable({apis:['Date','setTimeout'],now:Date.parse('2026-11-05T14:52:40Z')});
 const f=recoveryFake(options);let done=false,output,error;
 const pending=runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi,signal:options.signal}).then(value=>{output=value;done=true;},value=>{error=value;done=true;});
 for(let i=0;i<180&&!done;i++){await new Promise(resolve=>setImmediate(resolve));t.mock.timers.tick(250);}
 assert.equal(done,true,'receipt recovery must finish within its bound');await pending;if(error)throw error;
 assert.equal(f.stages.filter(stage=>stage==='save').length,1);
 assert.equal(f.stages.filter(stage=>stage==='upload').length,1);
 assert.equal(f.calls.filter(call=>call.method==='Input.insertText').length,2);
 assert.equal(f.views.some(item=>item.saved&&item.action==='close'),false);
 return {f,output};
}
for(const options of [{},{delay:2},{contextAt:'receipt'},{contextAt:'save'}])test(`saved draft recovery verifies a new record after ${JSON.stringify(options)}`,async t=>{
 const {f,output}=await runRecoveryCase(t,options);
 assert.equal(output.state,'record_found');assert.equal(output.receiptKind,'new_record');assert.equal(output.published,false);
 assert.deepEqual(f.views.filter(item=>item.saved).map(item=>item.action),['open','tab']);
});
for(const options of [{noGrowth:true},{duplicate:true},{noBaseline:true},{crossOrigin:true},{noEntry:true},{stale:true},{unloaded:true}])test(`saved draft recovery keeps uncertain evidence unconfirmed ${JSON.stringify(options)}`,async t=>{
 const {f,output}=await runRecoveryCase(t,options);
 assert.equal(output.state,'unconfirmed');assert.equal(output.receiptKind,undefined);
 if(options.noBaseline||options.crossOrigin||options.noEntry)assert.equal(f.views.some(item=>item.saved),false);
});
test('saved draft recovery stops on cancellation without opening the drawer',async t=>{
 const controller=new AbortController();const {f,output}=await runRecoveryCase(t,{signal:controller.signal,onSave:()=>controller.abort()});
 assert.equal(output.state,'unconfirmed');assert.equal(f.views.some(item=>item.saved),false);
});
for(const evidence of [{thumbnailDelay:1},{targetDelay:1}])test(`active image list waits for delayed evidence without another tab hit ${JSON.stringify(evidence)}`,async t=>{
 const {f,output}=await runRecoveryCase(t,{activeOnOpen:true,tabHitFails:true,...evidence});
 assert.equal(output.state,'record_found');assert.equal(output.receiptKind,'new_record');
 assert.equal(f.getRedundantHits(),0);
 assert.deepEqual(f.views.filter(item=>item.saved).map(item=>item.action),['open']);
});
for(const invalid of [{stale:true},{noGrowth:true}])test(`active image list never accepts delayed invalid evidence ${JSON.stringify(invalid)}`,async t=>{
 const {f,output}=await runRecoveryCase(t,{activeOnOpen:true,tabHitFails:true,targetDelay:1,...invalid});
 assert.equal(output.state,'unconfirmed');assert.equal(f.getRedundantHits(),0);
 assert.deepEqual(f.views.filter(item=>item.saved).map(item=>item.action),['open']);
});
for(const [options,expected] of [
 [{noBaseline:true},{baselineCount:null,observedCount:null,reason:'no_baseline'}],
 [{noEntry:true},{baselineCount:10,observedCount:null,reason:'view'}],
 [{noGrowth:true},{baselineCount:10,observedCount:10,reason:'count'}],
 [{targetDelay:100},{baselineCount:10,observedCount:11,reason:'target'}],
 [{stale:true},{baselineCount:10,observedCount:11,reason:'time'}],
 [{unloaded:true},{baselineCount:10,observedCount:11,reason:'image'}],
])test(`receipt diagnostics report only counts and the failed ${expected.reason} gate`,async t=>{
 const {output}=await runRecoveryCase(t,options);
 assert.equal(output.state,'unconfirmed');assert.deepEqual(output.receiptCheck,expected);
 assert.deepEqual(Object.keys(output.receiptCheck).sort(),['baselineCount','observedCount','reason']);
 assert.doesNotMatch(JSON.stringify(output.receiptCheck),/标题|正文|AQID|https?:|saved_at|1080|1440/);
});
for(const options of [{baselineHit:'open'},{baselineHit:'close'},{closeReceiptLost:true}])test(`read-only drawer navigation preserves a verified baseline ${JSON.stringify(options)}`,async t=>{
 const {f,output}=await runRecoveryCase(t,options);
 assert.equal(output.state,'record_found');assert.equal(output.receiptKind,'new_record');
 assert.deepEqual(f.views.filter(item=>!item.saved).map(item=>item.action),['open','tab','close']);
 assert.equal(f.calls.some(call=>['DOM.getContentQuads','DOM.getNodeForLocation'].includes(call.method)),false);
});
test('semantic drawer click revalidates identity, ancestors and blocking overlays atomically',async t=>{
 const {f}=await runRecoveryCase(t);
 const code=f.calls.find(call=>call.args?.functionDeclaration?.startsWith('function(action){if(')).args.functionDeclaration;
 function harness(action='open'){
  let clicks=0;
  const make=(tagName,className='',parentElement=null,textContent='')=>({tagName,className,parentElement,textContent,isConnected:true,hidden:false,disabled:false,attrs:{},queries:{},
   getClientRects(){return [{}]},getAttribute(key){return this.attrs[key]??null},hasAttribute(key){return Object.hasOwn(this.attrs,key)},
   querySelectorAll(selector){return this.queries[selector]||[]},contains(other){for(let n=other;n;n=n.parentElement)if(n===this)return true;return false;},
   closest(){for(let n=this;n;n=n.parentElement)if(n.className.split(' ').includes('draft-drawer'))return n;return null;},
   get classList(){return {contains:value=>this.className.split(' ').includes(value)}}});
  const header=make('DIV','header-draft'),label=make('SPAN','draft-title',header,'草稿箱(10)');
  header.queries[':scope > div.draft-title-box > span.draft-title']=[label];
  const drawer=make('DIV','d-drawer d-drawer-right draft-drawer'),content=make('DIV','d-drawer-content',drawer),tab=make('DIV','tab-item active',content),tabLabel=make('SPAN','tab-item-text',tab,'图文笔记(10)'),close=make('SPAN','d-drawer-close d-clickable',drawer);
  drawer.queries[':scope > div.d-drawer-content']=[content];drawer.queries['span.d-drawer-close.d-clickable']=[close];content.queries['span.tab-item-text']=[tabLabel];
  const state={headers:[header],drawers:action==='open'?[]:[drawer],blockers:[]};
  const document={querySelectorAll(selector){if(selector==='div.header > div.header-draft')return state.headers;if(selector==='div.d-drawer.d-drawer-right.draft-drawer')return state.drawers;if(selector.includes('.login-container'))return state.blockers;return [];}};
  const location={origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'};
  const run=runInNewContext('('+code+')',{document,location,getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),HTMLElement:{prototype:{click(){clicks++}}}});
  const target=action==='open'?label:action==='tab'?tabLabel:close;
  return {header,label,drawer,content,tab,state,location,make,run:()=>run.call(target,action),clicks:()=>clicks};
 }
 for(const action of ['open','tab','close']){const app=harness(action);assert.equal(app.run(),true);assert.equal(app.clicks(),1);}
 for(const mutate of [
  app=>{app.label.isConnected=false;},app=>{app.label.disabled=true;},app=>{app.header.attrs.disabled='';},
  app=>{app.header.attrs.inert='';},app=>{app.header.attrs['aria-hidden']='true';},app=>{app.header.attrs['aria-disabled']='true';},
  app=>{app.header.className='editor';},app=>{app.state.headers.push(app.header);},
  app=>{app.state.blockers=[app.make('DIV','login-modal')];},app=>{app.state.blockers=[app.make('DIV','creator-publish-popover')];},
  app=>{app.location.origin='https://other.example';},app=>{app.location.pathname='/other';},
 ]){const app=harness();mutate(app);assert.equal(app.run(),false);assert.equal(app.clicks(),0);}
 const ownDialog=harness('tab');ownDialog.drawer.attrs.role='dialog';ownDialog.state.blockers=[ownDialog.drawer];assert.equal(ownDialog.run(),true);
 const blockedTab=harness('tab');blockedTab.state.blockers=[blockedTab.make('DIV','creator-publish-popover')];assert.equal(blockedTab.run(),false);assert.equal(blockedTab.clicks(),0);
 const nestedDialog=harness('tab'),nested=nestedDialog.make('DIV','creator-publish-popover',nestedDialog.content);nested.attrs.role='dialog';nestedDialog.tab.parentElement=nested;nestedDialog.state.blockers=[nested];assert.equal(nestedDialog.run(),false);assert.equal(nestedDialog.clicks(),0);
 const wrongAction=harness('publish');assert.equal(wrongAction.run(),false);assert.equal(wrongAction.clicks(),0);
});

// Real-browser evidence (claude-probe 2026-11-06): a background tab (active:false) is hidden, rAF never fires,
// the draft drawer opens and is readable but its close transition never finishes, so the opener never returns.
// Emulation.setFocusEmulationEnabled makes the same background tab render (visible, rAF, close restores, covers load).
function renderFake(options={}) {
 let rendering=false, saved=false, view='closed', fills=0;
 const calls=[], stages=[], views=[];
 const chromeApi={tabs:{get:async()=>({url:'https://creator.xiaohongshu.com/publish/publish'})},debugger:{attach:async()=>{},detach:async()=>{},sendCommand:async(_,method,args)=>{
  calls.push({method,args,saved});
  if(method==='Emulation.setFocusEmulationEnabled'){if(options.emulation==='reject')throw new Error('not allowed');rendering=args.enabled===true;return {};}
  if(method==='Input.insertText')return {};
  if(method==='Runtime.evaluate')return {result:{objectId:'doc'}};
  if(method==='DOM.getDocument')return {root:{nodeType:9,nodeName:'#document',documentURL:'https://creator.xiaohongshu.com/publish/publish',children:[]}};
  const fn=args.functionDeclaration;
  if(fn.startsWith('function(action){if(')){
   const action=args.arguments[0].value;views.push({action,saved,rendering});
   view=action==='open'?'tab':action==='tab'?'rows':rendering?'closed':'closing';
   return {result:{value:true}};
  }
  if(fn.startsWith('function(){return ('))return {result:{value:view==='closed'||view==='closing'}};
  if(fn.startsWith('function(action){return (')){
   const action=args.arguments[0].value;
   const available=action==='open'?view==='closed':view==='tab'||view==='rows'||(action==='close'&&view==='closing');
   return {result:available?{objectId:'view-'+action}:{subtype:'null'}};
  }
  if(fn.startsWith('function(target){return (')){
   if(view!=='rows')return {result:{value:{pending:true}}};
   return {result:{value:{ok:true,count:saved?11:10,match:saved?{title:'标题',saved_at:'2026-11-05 22:52:40',images:[{loaded:rendering,natural_width:1080,natural_height:1440}]}:null}}};
  }
  const stage=args.arguments[0].value;stages.push(stage);
  if(stage==='visible')return {result:{value:{state:rendering?'ok':'hidden'}}};
  if(stage==='fill'&&++fills===3&&options.lostBeforeSave)rendering=false;
  if(stage==='save'){saved=true;view='closed';}
  return {result:{value:{state:stage==='save_probe'?'one':stage==='receipt'?'unknown':'ok'}}};
 }}};
 return {chromeApi,calls,stages,views};
}
async function runRenderCase(t, options={}) {
 t.mock.timers.enable({apis:['Date','setTimeout'],now:Date.parse('2026-11-05T14:52:40Z')});
 const f=renderFake(options);let done=false,output,error;
 const pending=runXhsDraft({tabId:1,job:payload,chromeApi:f.chromeApi}).then(value=>{output=value;done=true;},value=>{error=value;done=true;});
 for(let i=0;i<400&&!done;i++){await new Promise(resolve=>setImmediate(resolve));t.mock.timers.tick(250);}
 assert.equal(done,true,'adapter must finish within its bound');await pending;if(error)throw error;
 return {f,output};
}
test('background tab renders via focus emulation so the verified baseline survives drawer close',async t=>{
 const {f,output}=await runRenderCase(t);
 const emulation=f.calls.findIndex(c=>c.method==='Emulation.setFocusEmulationEnabled');
 const firstPage=f.calls.findIndex(c=>c.method==='Runtime.callFunctionOn');
 assert.ok(emulation>=0 && emulation<firstPage,'emulation must precede every page step');
 assert.deepEqual(f.calls[emulation].args,{enabled:true});
 assert.deepEqual(f.views.filter(v=>!v.saved).map(v=>[v.action,v.rendering]),[['open',true],['tab',true],['close',true]]);
 assert.equal(output.state,'record_found');assert.equal(output.receiptKind,'new_record');assert.equal(output.published,false);
 assert.equal(f.stages.filter(s=>s==='save').length,1);
});
test('page that cannot render stops before upload, fill or save and reports not sent',async t=>{
 const {f,output}=await runRenderCase(t,{emulation:'reject'});
 assert.equal(output.state,'error');assert.equal(output.action_sent,false);
 assert.equal(output.phase,'visible');assert.equal(output.diagnostic,'hidden');
 for(const stage of ['tab','upload','fill','baseline','save_probe','save'])assert.equal(f.stages.includes(stage),false,stage);
 assert.equal(f.calls.some(c=>c.method==='Input.insertText'),false);
 assert.equal(f.views.length,0);
});
test('rendering lost before the save click never sends save',async t=>{
 const {f,output}=await runRenderCase(t,{lostBeforeSave:true});
 assert.equal(output.state,'error');assert.equal(output.action_sent,false);assert.equal(output.diagnostic,'hidden');
 assert.equal(f.stages.includes('save'),false);assert.equal(f.stages.includes('save_probe'),false);
});
test('actual page visible step reports rendering state and keeps login precedence',async t=>{
 const {f}=await runRenderCase(t);
 const code=f.calls.find(c=>c.args?.arguments?.[0]?.value==='visible').args.functionDeclaration;
 const document={visibilityState:'hidden',login:[],querySelectorAll(sel){return sel.includes('.login-container')?this.login:[]}};
 const run=runInNewContext(`(${code})`,{document,window:{},location:{origin:'https://creator.xiaohongshu.com',pathname:'/publish/publish'},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})});
 assert.equal(run('visible',{}).state,'hidden');
 document.visibilityState='prerender';assert.equal(run('visible',{}).state,'hidden');
 document.visibilityState='visible';assert.equal(run('visible',{}).state,'ok');
 document.login=[{isConnected:true,hidden:false,parentElement:null,getClientRects:()=>[{}],getAttribute:()=>null}];assert.equal(run('visible',{}).state,'auth');
});
