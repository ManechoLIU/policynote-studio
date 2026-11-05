import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../../browser-extension/background.js',import.meta.url),'utf8').replace(/^import .*;\s*/m,'');
const id='123e4567-e89b-42d3-a456-426614174000',other='123e4567-e89b-42d3-a456-426614174001';
const origin='https://work.example', popup='chrome-extension://extension/popup.html';
const row=(rid=id,state='unknown')=>({id:rid,origin,state,action:'draft',ownerTabId:7,action_sent:true,createdAt:1,updatedAt:2});
function harness(records){let listener,sets=0;const store={origins:[origin],jobReceipts:records};const event={addListener(){}};const chrome={runtime:{id:'extension',getURL:p=>'chrome-extension://extension/'+p,onMessage:{addListener:f=>listener=f},onInstalled:event,onStartup:event},storage:{local:{get:async()=>structuredClone(store),set:async value=>{if('jobReceipts' in value)sets++;Object.assign(store,structuredClone(value));}}},scripting:{getRegisteredContentScripts:async()=>[],registerContentScripts:async()=>{},unregisterContentScripts:async()=>{}}};vm.runInNewContext(source,{chrome,URL,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:()=>other},runXhsDraft:()=>{throw Error('No draft allowed')}});return {store,get sets(){return sets},send:(message={},sender={id:'extension',url:popup})=>new Promise(resolve=>listener({channel:'mingya-popup',action:'review_unknown',origin,receipts:[{id,updatedAt:2}],...message},sender,resolve))};}
test('internal recovery accepts only the exact popup sender and confirmed snapshot',async()=>{
 const h=harness([row(),{...row(other),origin:'https://other.example'}]);
 assert.equal((await h.send({}, {id:'extension',url:origin,tab:{id:7}})).ok,false);assert.equal(h.sets,0);
 assert.equal((await h.send()).ok,true);assert.equal(h.store.jobReceipts[0].state,'reviewed');assert.equal(h.store.jobReceipts[0].updatedAt,2);assert.equal(h.store.jobReceipts[1].state,'unknown');
});
test('new unknown, changed timestamp, or in-flight task blocks recovery',async()=>{
 for(const records of [[row(),row(other)],[{...row(),updatedAt:3}],[row(),row(other,'in_flight')]]){const h=harness(records);assert.equal((await h.send()).ok,false);assert.equal(h.sets,0);}
});
test('concurrent recovery requests share the background claim',async()=>{
 const h=harness([row()]);const results=await Promise.all([h.send(),h.send()]);assert.equal(results.filter(r=>r.ok).length,1);assert.equal(h.sets,1);
});
