import { runXhsDraft } from './xhs-draft.mjs';
const CREATOR = 'https://creator.xiaohongshu.com/publish/publish';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
let active = null;
const pendingReviews = new Map();
const result = (ok, state, message, receiptKind) => ({ok, state, message, published:false, ...(receiptKind ? {receiptKind} : {})});
const error = () => result(false, 'error', '浏览器连接请求无效。');
const unknown = () => result(false, 'unconfirmed', '草稿结果未确认，请先检查草稿箱；不要重复提交。');
function validJob(p) {
  if (!p || typeof p !== 'object' || Array.isArray(p) || Object.keys(p).some(k => !['title','body','tags','images'].includes(k))) return false;
  if (typeof p.title !== 'string' || !p.title.trim() || p.title.length > 20 || typeof p.body !== 'string' || p.body.length > 1000) return false;
  if (!Array.isArray(p.tags) || p.tags.length > 20 || p.tags.some(t => typeof t !== 'string' || t.length > 100)) return false;
  if (!Array.isArray(p.images) || p.images.length < 1 || p.images.length > 18) return false;
  let total = 0;
  for (const image of p.images) {
    if (!image || Object.keys(image).some(k => !['name','mime','base64'].includes(k)) || typeof image.name !== 'string' || image.name.length > 255 || !['image/png','image/jpeg','image/webp'].includes(image.mime) || typeof image.base64 !== 'string') return false;
    const b = image.base64;
    if (!b || b.length % 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(b)) return false;
    const size = b.length / 4 * 3 - (b.endsWith('==') ? 2 : b.endsWith('=') ? 1 : 0);
    if (size > 10 * 1024 * 1024) return false;
    total += size;
  }
  return total <= 30 * 1024 * 1024;
}
function receiptCheckMessage(raw) {
  const check = raw?.receiptCheck;
  const labels = {no_baseline:'未取得保存前的草稿数量',view:'草稿列表尚未读取完成',count:'保存前后的草稿数量未能对应',target:'未找到唯一对应记录',time:'记录保存时间未能对应',image:'记录封面尚未读取完成',navigation:'核对页面状态未确认',timeout:'核对等待已结束'};
  const validCount = value => value === null || (Number.isSafeInteger(value) && value >= 0 && value <= 10000);
  if (raw?.state !== 'unconfirmed' || !check || typeof check !== 'object' || Array.isArray(check)
      || Object.keys(check).length !== 3 || !Object.hasOwn(check,'reason') || !Object.hasOwn(labels,check.reason)
      || !Object.hasOwn(check,'baselineCount') || !Object.hasOwn(check,'observedCount')
      || !validCount(check.baselineCount) || !validCount(check.observedCount)) return null;
  const before = check.baselineCount === null ? '未知' : check.baselineCount;
  const after = check.observedCount === null ? '未知' : check.observedCount;
  return `保存结果未确认：${labels[check.reason]}（保存前${before}篇，当前${after}篇）。请先查看草稿箱，勿重复提交。`;
}
async function waitComplete(tabId, signal) {
  const tab = await chrome.tabs.get(tabId);
  if (tab.status === 'complete') return true;
  return new Promise(resolve => {
    const finish = value => {chrome.tabs.onUpdated.removeListener(updated); chrome.tabs.onRemoved.removeListener(removed); signal.removeEventListener('abort', aborted); resolve(value);};
    const updated = (id, change) => {if (id === tabId && change.status === 'complete') finish(true);};
    const removed = id => {if (id === tabId) finish(false);};
    const aborted = () => finish(false);
    chrome.tabs.onUpdated.addListener(updated); chrome.tabs.onRemoved.addListener(removed); signal.addEventListener('abort', aborted, {once:true});
    if (signal.aborted) aborted();
    // Close the get→listener race: tab may complete between the first get and addListener.
    chrome.tabs.get(tabId).then(t => { if (t?.status === 'complete') finish(true); }).catch(() => finish(false));
  });
}
async function dispatch(message, sender) {
  try {
    if (!message || message.channel !== 'mingya-workbench' || message.version !== 1 || !UUID.test(message.id || '') || !['status','open','draft','review_prepare','review'].includes(message.action)) return error();
    if (!sender || sender.frameId !== 0 || !Number.isInteger(sender.tab?.id) || typeof sender.url !== 'string') return error();
    const url = new URL(sender.url), origin = url.origin;
    if (url.username || url.password || origin === 'null' || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1','[::1]'].includes(url.hostname)))) return error();
    const {origins} = await chrome.storage.local.get('origins');
    if (!Array.isArray(origins) || !origins.includes(origin)) return error();
    if (message.action === 'status') return result(true,'connected','浏览器扩展已连接。');
    if (message.action === 'draft' && !validJob(message.payload)) return error();
    if (message.action === 'review_prepare' && message.payload !== undefined) return error();
    if (message.action === 'review' && (!message.payload || Object.keys(message.payload).length !== 1 || !UUID.test(message.payload.reviewToken || ''))) return error();
    if (active) return active.origin === origin && active.id === message.id ? unknown() : result(false,'busy','已有浏览器任务正在进行。');
    const owner = {origin, id:message.id};
    active = owner; // Claim synchronously before any permission or storage await.
    let running = Promise.resolve();
    try {
      if (message.action === 'draft' && !await chrome.permissions.contains({permissions:['debugger']})) return result(false,'error','请先在扩展中授权浏览器操作权限。');
      const loaded = await chrome.storage.local.get('jobReceipts');
      const records = loaded.jobReceipts === undefined ? [] : loaded.jobReceipts;
      if (!Array.isArray(records)) return unknown();
      if (message.action === 'review_prepare' || message.action === 'review') {
        if (records.some(row => !row || typeof row !== 'object' || !['unknown','completed','reviewed','not_sent','in_flight'].includes(row.state))) return error();
        if (records.some(row => row.state === 'in_flight')) return result(false,'busy','仍有任务执行中，请稍后核对。');
        const pending = records.filter(row => row.origin === origin && row.state === 'unknown');
        if (pending.some(row => !UUID.test(row.id || '') || !Number.isFinite(row.updatedAt))) return error();
        const signature = JSON.stringify(pending.map(row => [row.id,row.updatedAt]).sort((a,b) => a[0].localeCompare(b[0])));
        if (message.action === 'review_prepare') {
          if (!pending.length) return result(true,'reviewed','当前工作台没有待核对的任务；未重试或保存。');
          for (const [key,value] of pendingReviews) if (value.expiresAt < Date.now()) pendingReviews.delete(key);
          if (!pendingReviews.has(origin) && pendingReviews.size >= 256) return result(false,'busy','核对请求已达上限，请稍后再试。');
          const token = crypto.randomUUID();
          pendingReviews.set(origin,{token,signature,expiresAt:Date.now()+120000});
          return {...result(true,'review_ready','请确认已在当前浏览器草稿箱核对上次任务。'),reviewToken:token};
        }
        const prepared = pendingReviews.get(origin);
        if (!prepared || prepared.token !== message.payload.reviewToken || prepared.expiresAt < Date.now()) return result(false,'error','核对请求已失效，请重新核对草稿箱。');
        pendingReviews.delete(origin);
        if (!pending.length || prepared.signature !== signature) return result(false,'error','任务状态已变化，请重新核对草稿箱。');
        try { await chrome.storage.local.set({jobReceipts:records.map(row => pending.includes(row) ? {...row,state:'reviewed'} : row)}); }
        catch { return result(false,'unconfirmed','解除保护结果未确认，请刷新状态核对；不会自动重试。'); }
        return result(true,'reviewed','已记录人工核对并解除提交保护；未重试、上传、删除或发布。');
      }
      if (records.some(row => row && row.origin === origin && row.id === message.id)) return unknown();
      if (records.length >= 1024) return result(false,'busy','浏览器任务记录已达上限。');
      if (message.action === 'draft' && records.some(row => !row || row.state === 'in_flight' || row.state === 'unknown')) {
        return result(false, 'busy', '有未核对的草稿任务保护；检查草稿箱后，可在工作台点「核对后解除保护」，或在扩展弹窗解除提交保护。');
      }
      const record = {origin, id:message.id, ownerTabId:sender.tab.id, action:message.action,
                      state:'in_flight', action_sent:true, createdAt:Date.now(), updatedAt:Date.now()};
      const updated = [...records, record];
      // Persist the intent before any tab or draft action; store no content.
      await chrome.storage.local.set({jobReceipts:updated});
      const finish = async (output, notSent = false) => {
        record.state = notSent ? 'not_sent' : (output.state === 'opened' || output.state === 'saved' || output.state === 'record_found') ? 'completed' : 'unknown';
        if (notSent) record.action_sent = false;
        record.updatedAt = Date.now();
        try {await chrome.storage.local.set({jobReceipts:updated}); return output;}
        catch {return unknown();}
      };
      if (message.action === 'open') {
        try {await chrome.tabs.create({url:CREATOR,active:true}); return await finish(result(true,'opened','已打开小红书发布页。'));}
        catch {return await finish(unknown());}
      }
      const controller = new AbortController();
      let timer, removed, definitelyNotSent = false;
      const timeout = new Promise(resolve => {
        timer = setTimeout(() => {controller.abort(); resolve(unknown());}, 185000);
      });
      const task = (async () => {
        const tab = await chrome.tabs.create({url:CREATOR,active:false});
        if (controller.signal.aborted || !Number.isInteger(tab?.id)) {record.diagnostic=record.diagnostic||'tab_create'; return unknown();}
        const closed = new Promise(resolve => {
          removed = id => {if (id === tab.id) {controller.abort(); record.diagnostic='tab_closed'; resolve(unknown());}};
          chrome.tabs.onRemoved.addListener(removed);
        });
        const execute = (async () => {
          if (!await waitComplete(tab.id, controller.signal)) {
            definitelyNotSent = true; record.diagnostic='tab_wait';
            return result(false,'error','发布页未能完成加载。');
          }
          // Wait until creator publish URL is actually present (about:blank/complete race).
          let current=null;
          for (let i=0;i<40;i++){
            if (controller.signal.aborted) break;
            current = await chrome.tabs.get(tab.id);
            try {
              if (current?.url && new URL(current.url).origin === 'https://creator.xiaohongshu.com' && new URL(current.url).pathname === '/publish/publish') break;
            } catch {}
            await new Promise(r=>setTimeout(r,250));
          }
          let onCreator = false;
          try { onCreator = !!(current?.url && new URL(current.url).origin === 'https://creator.xiaohongshu.com' && new URL(current.url).pathname === '/publish/publish'); } catch { onCreator = false; }
          if (controller.signal.aborted || !onCreator) {
            definitelyNotSent = true; record.diagnostic='tab_url';
            return result(false,'error','发布页未能打开。');
          }
          const raw = await runXhsDraft({tabId:tab.id, job:message.payload, chromeApi:chrome, onProgress:phase=>{record.phase=phase}, signal:controller.signal});
          if (controller.signal.aborted || !raw || raw.published !== false) return unknown();
          if (raw.state === 'saved' && raw.ok === true && raw.receiptKind === 'fresh_notice') return result(true,'saved','草稿保存提示已确认。','fresh_notice');
          if (raw.state === 'record_found' && raw.ok === true && raw.receiptKind === 'new_record') return result(true,'record_found','发现新草稿记录，请核对内容。','new_record');
          if (typeof raw.phase === 'string' && /^[a-z_]{1,32}$/.test(raw.phase)) record.phase=raw.phase;
          if (typeof raw.diagnostic === 'string' && /^[a-z_]{1,32}$/.test(raw.diagnostic)) record.diagnostic=raw.diagnostic;
          if (raw.state === 'error' && raw.action_sent !== true) {
            definitelyNotSent = true;
            if (raw.diagnostic === 'hidden') return result(false,'error','小红书页面未能在后台完成渲染，尚未执行保存，可直接重试。');
            return result(false,'error','尚未执行保存，请检查浏览器连接器中的任务状态后重试。');
          }
          if (raw.state === 'auth_required' && raw.action_sent === false) {
            definitelyNotSent = true;
            return result(false,'auth_required','请先在当前浏览器登录小红书。');
          }
          if (!record.diagnostic) record.diagnostic='adapter_unconfirmed';
          const detail = receiptCheckMessage(raw);
          return detail ? result(false,'unconfirmed',detail) : unknown();
        })();
        const output = await Promise.race([execute, closed]);
        if (controller.signal.aborted) await execute.catch(() => {});
        return output;
      })().catch((err) => {
        const msg = (err && typeof err.message === 'string') ? err.message : String(err || '');
        const allowed = ['location','document','page','view','identity','box','hit','viewport','changed','unknown','upload','node','ambiguous','stopped','tab_url','tab_create','tab_wait','tab_closed'];
        record.diagnostic = record.diagnostic || (allowed.includes(msg) ? msg : 'task_throw');
        if (!record.phase) record.phase = 'task';
        const cleaned = String(msg).toLowerCase().replace(/[^a-z0-9_ .:\/-]+/g,' ').replace(/\s+/g,' ').trim().slice(0,64);
        if (cleaned) record.err = cleaned;
        return unknown();
      });
      running = task;
      try {
        const output = await Promise.race([task, timeout]);
        return await finish(output, definitelyNotSent && !controller.signal.aborted && ['auth_required','error'].includes(output.state));
      }
      finally {clearTimeout(timer); if (removed) chrome.tabs.onRemoved.removeListener(removed);}
    } finally {
      // After timeout keep the fence until the adapter actually stops.
      running.finally(() => {if (active === owner) active = null;});
    }

  } catch {return error();}
}
async function reviewUnknown(message, sender) {
  if (sender?.id !== chrome.runtime.id || sender?.url !== chrome.runtime.getURL('popup.html') || sender.tab || message.action !== 'review_unknown' || Object.keys(message).some(key=>!['channel','action','origin','receipts'].includes(key))) return error();
  if (typeof message.origin !== 'string' || !Array.isArray(message.receipts) || !message.receipts.length || message.receipts.length>1024 || message.receipts.some(row=>!row || Object.keys(row).some(key=>!['id','updatedAt'].includes(key)) || !UUID.test(row.id) || !Number.isFinite(row.updatedAt))) return error();
  if(active)return result(false,'busy','有任务执行中，请稍后核对。');
  const owner={recovery:true};active=owner;
  try {
    const {origins,jobReceipts:records}=await chrome.storage.local.get(['origins','jobReceipts']);
    if(!Array.isArray(origins)||!origins.includes(message.origin)||!Array.isArray(records)||records.some(row=>!row||typeof row!=='object'||!['unknown','completed','reviewed','not_sent','in_flight'].includes(row.state))||records.some(row=>row.state==='in_flight'))return error();
    const pending=records.filter(row=>row.origin===message.origin && row.state==='unknown');
    const signature=rows=>JSON.stringify(rows.map(row=>[row.id,row.updatedAt]).sort((a,b)=>a[0].localeCompare(b[0])));
    if(!pending.length || signature(pending)!==signature(message.receipts))return result(false,'error','任务状态已变化，请重新核对草稿箱。');
    await chrome.storage.local.set({jobReceipts:records.map(row=>pending.includes(row)?{...row,state:'reviewed'}:row)});
    return {ok:true,message:'已记录人工核对；未重试或保存。'};
  } catch {return error();}
  finally {if(active===owner)active=null;}
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Status: synchronous reply (proven). Validate sender URL only; origins enforced on draft/open.
  if (message?.channel === 'mingya-workbench' && message?.version === 1 && message?.action === 'status') {
    try {
      if (!sender || sender.frameId !== 0 || !Number.isInteger(sender.tab?.id) || typeof sender.url !== 'string') {
        sendResponse(error()); return false;
      }
      const url = new URL(sender.url), origin = url.origin;
      if (url.username || url.password || origin === 'null' || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1','[::1]'].includes(url.hostname)))) {
        sendResponse(error()); return false;
      }
      // Origins gate kept best-effort without awaiting storage (MV3 SW async reply was hanging).
      sendResponse(result(true,'connected','浏览器扩展已连接。'));
      return false;
    } catch {
      try { sendResponse(error()); } catch {}
      return false;
    }
  }
  const action=message?.channel==='mingya-popup'?reviewUnknown(message,sender):dispatch(message,sender);
  action.then(sendResponse, () => sendResponse(error()));return true;
});


const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);
function isAllowedWorkbenchUrl(raw) {
  try {
    const url = new URL(raw);
    if (url.username || url.password || url.origin === 'null') return null;
    if (url.protocol === 'https:') return url;
    if (url.protocol === 'http:' && LOOPBACK_HOSTS.has(url.hostname)) return url;
    return null;
  } catch { return null; }
}
async function ensureBoundContentScripts() {
  try {
    const { origins = [], registrations = {} } = await chrome.storage.local.get(['origins', 'registrations']);
    if (!Array.isArray(origins) || !origins.length) return;
    const live = await chrome.scripting.getRegisteredContentScripts().catch(() => []);
    const byId = new Map((Array.isArray(live) ? live : []).filter(Boolean).map(s => [s.id, s]));
    const nextRegs = { ...registrations };
    const usedIds = new Set();
    const reservedIds = new Set([...byId.keys(), ...Object.values(registrations).filter(id => typeof id === 'string')]);
    const newId = () => {
      const base = 'workbench-' + crypto.randomUUID();
      let candidate = base, suffix = 1;
      while (reservedIds.has(candidate)) candidate = base + '-' + suffix++;
      reservedIds.add(candidate);
      return candidate;
    };
    for (const origin of origins) {
      const url = isAllowedWorkbenchUrl(origin.endsWith('/') ? origin : origin + '/');
      if (!url) continue;
      // Prefer exact origin+port match so :7862 pages are covered after Reload.
      const matchPattern = url.origin + '/*';
      let id = typeof nextRegs[origin] === 'string' && nextRegs[origin] ? nextRegs[origin] : null;
      if (!id || usedIds.has(id)) id = newId();
      usedIds.add(id);
      const existing = byId.get(id);
      const needs = !existing || !Array.isArray(existing.matches) || !existing.matches.includes(matchPattern) || existing.runAt !== 'document_start';
      if (existing && needs) {
        try { await chrome.scripting.unregisterContentScripts({ ids: [id] }); } catch {}
      }
      if (needs) {
        try {
          await chrome.scripting.registerContentScripts([{
            id, matches: [matchPattern], js: ['relay.js'], allFrames: false, runAt: 'document_start', persistAcrossSessions: true,
          }]);
          byId.set(id, { id, matches: [matchPattern], runAt: 'document_start' });
        } catch {}
      }
      nextRegs[origin] = id;
    }
    if (JSON.stringify(nextRegs) !== JSON.stringify(registrations)) {
      try { await chrome.storage.local.set({ registrations: nextRegs }); } catch {}
    }
  } catch {}
}
chrome.runtime.onInstalled.addListener(() => { ensureBoundContentScripts(); });
chrome.runtime.onStartup.addListener(() => { ensureBoundContentScripts(); });
ensureBoundContentScripts();
