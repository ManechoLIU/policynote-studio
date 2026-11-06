import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const transpile = (relative) => ts.transpileModule(readFileSync(new URL(relative, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const settle = () => new Promise(resolve => setImmediate(resolve));

function loadClient() {
  const listeners = new Set();
  const posts = [];
  const timers = [];
  let uuid = 0;
  const location = { origin: 'https://workbench.example' };
  const window = {
    location,
    postMessage(data, origin) { posts.push({ data, origin }); },
    addEventListener(type, fn) { listeners.add(fn); },
    removeEventListener(type, fn) { listeners.delete(fn); },
  };
  const exports = {};
  runInNewContext(transpile('../../web/frontend/src/lib/clientBrowser.ts'), {
    exports, window, location, crypto: { randomUUID: () => `id-${++uuid}` },
    setTimeout(fn) { const timer = { fn, cleared: false }; timers.push(timer); return timer; },
    clearTimeout(timer) { if (timer) timer.cleared = true; },
  });
  return { posts, listeners, timers, window, api: exports, emit: (event) => [...listeners].forEach(fn => fn(event)) };
}

test('saved and record_found require their exact receipt kinds and success-state pairing', () => {
  const { api } = loadClient();
  const base = { ok: true, message: 'result', published: false };
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'saved' }), null);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'saved', receiptKind: 'new_record' }), null);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'record_found' }), null);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'record_found', receiptKind: 'fresh_notice' }), null);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'error' }), null);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'saved', receiptKind: 'fresh_notice' }).state, 'saved');
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'record_found', receiptKind: 'new_record' }).state, 'record_found');
  const token = '123e4567-e89b-42d3-a456-426614174000';
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'review_ready' }), null);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'review_ready', reviewToken: 'bad' }), null);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'review_ready', reviewToken: token }).reviewToken, token);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'reviewed', reviewToken: token }), null);
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'reviewed' }).state, 'reviewed');
  assert.equal(api.parseClientBrowserResult({ ...base, state: 'busy', reviewToken: token }), null);
});

test('request id, origin, and frame must match, and a late reply is ignored', async () => {
  const harness = loadClient();
  const pending = harness.api.openClientXhsDrafts();
  const reply = (source, origin, id, result) => harness.emit({ source, origin, data: { channel: 'mingya-browser', version: 1, id, result } });
  const good = { ok: true, state: 'opened', message: '已打开', published: false };
  reply({}, 'https://workbench.example', 'id-1', good);
  reply(harness.window, 'https://other.example', 'id-1', good);
  reply(harness.window, 'https://workbench.example', 'id-2', good);
  reply(harness.window, 'https://workbench.example', 'id-1', { ok: true, state: 'opened', message: '已发布', published: true });
  reply(harness.window, 'https://workbench.example', 'id-1', { ok: true, state: 'mystery', message: '未知', published: false });
  assert.equal(harness.listeners.size, 1);
  assert.equal(harness.posts[0].data.action, 'open');
  assert.equal('payload' in harness.posts[0].data, false);
  reply(harness.window, 'https://workbench.example', 'id-1', good);
  assert.equal(JSON.stringify(await pending), JSON.stringify(good));
  assert.equal(harness.listeners.size, 0);
  assert.equal(harness.timers[0].cleared, true);
  reply(harness.window, 'https://workbench.example', 'id-1', { ok: true, state: 'saved', message: '晚回成功', published: false });
  assert.equal((await pending).state, 'opened');
  assert.equal(harness.posts.length, 1);
});

test('draft timeout is unconfirmed, clears the listener, and does not retry', async () => {
  const harness = loadClient();
  const pending = harness.api.saveClientXhsDraft({ title: '题', body: '文', tags: ['保险'], images: [] });
  assert.equal(harness.posts[0].data.action, 'draft');
  assert.equal(harness.posts[0].data.payload.title, '题');
  harness.timers[0].fn();
  const result = await pending;
  assert.equal(result.state, 'unconfirmed');
  assert.equal(result.ok, false);
  assert.equal(result.published, false);
  assert.match(result.message, /勿重复提交/);
  assert.equal(harness.listeners.size, 0);
  assert.equal(harness.posts.length, 1);
});

test('abort before and during a request does not leave a listener or send a second request', async () => {
  const harness = loadClient();
  const early = new AbortController();
  early.abort();
  const skipped = await harness.api.getClientBrowserStatus(early.signal);
  assert.equal(skipped.state, 'error');
  assert.equal(harness.posts.length, 0);
  const controller = new AbortController();
  const pending = harness.api.getClientBrowserStatus(controller.signal);
  controller.abort();
  assert.equal((await pending).message, '已取消');
  assert.equal(harness.listeners.size, 0);
  assert.equal(harness.posts.length, 1);
});

test('review helpers use fixed payloads and timeout or abort never claims success', async () => {
  const token='123e4567-e89b-42d3-a456-426614174000';
  const prepared=loadClient(), preparePending=prepared.api.prepareClientDraftReview();
  assert.equal(prepared.posts[0].data.action,'review_prepare');
  assert.equal('payload' in prepared.posts[0].data,false);
  prepared.timers[0].fn();
  const timeout=await preparePending;
  assert.equal(timeout.state,'unconfirmed');assert.equal(timeout.ok,false);
  assert.match(timeout.message,/解除保护结果未确认.*不会自动重试/);
  assert.equal(prepared.posts.length,1);

  const committed=loadClient(), controller=new AbortController();
  const commitPending=committed.api.confirmClientDraftReview(token,controller.signal);
  assert.equal(committed.posts[0].data.payload.reviewToken,token);
  assert.deepEqual(Object.keys(committed.posts[0].data.payload),['reviewToken']);
  controller.abort();
  const aborted=await commitPending;
  assert.equal(aborted.state,'unconfirmed');assert.equal(aborted.ok,false);
  assert.match(aborted.message,/解除保护结果未确认/);
  assert.equal(committed.posts.length,1);

  const invalid=loadClient();
  assert.equal((await invalid.api.confirmClientDraftReview('bad')).state,'error');
  assert.equal(invalid.posts.length,0);
});

test('relay forwards only fixed review actions and the one-key UUID payload', async () => {
  let listener;const sent=[];const replies=[];
  const win={location:{origin:'https://workbench.example'},top:null,addEventListener(_,fn){listener=fn},postMessage(value,origin){replies.push({value,origin})}};win.top=win;
  const chrome={runtime:{sendMessage:async message=>{sent.push(message);return {ok:true,state:message.action==='review_prepare'?'review_ready':'reviewed',message:'ok',published:false,...(message.action==='review_prepare'?{reviewToken:'123e4567-e89b-42d3-a456-426614174000'}:{})}}}};
  const document={documentElement:{setAttribute(){}}};
  runInNewContext(readFileSync(new URL('../../browser-extension/relay.js',import.meta.url),'utf8'),{window:win,document,chrome,globalThis:{}});
  const id='123e4567-e89b-42d3-a456-426614174001',token='123e4567-e89b-42d3-a456-426614174000';
  const emit=(action,payload,extra={})=>listener({source:win,origin:win.location.origin,data:{channel:'mingya-workbench',version:1,id,action,...(payload===undefined?{}:{payload}),...extra}});
  emit('review_prepare');emit('review',{reviewToken:token});
  emit('review',{reviewToken:'bad'});emit('review',{reviewToken:token,extra:true});emit('review_prepare',{reviewToken:token});emit('review',undefined,{extra:true});
  await settle();
  assert.deepEqual(sent.map(message=>message.action),['review_prepare','review']);
  assert.equal('payload' in sent[0],false);
  assert.deepEqual(sent[1].payload,{reviewToken:token});
  assert.equal(replies.length,2);
});

function page(options = {}) {
  const state = [], refs = [], effects = [];
  let cursor = 0;
  let pending = [];
  const publishes = [];
  const clientCalls = [];
  const toasts = [];
  let statusImpl = options.status || (async () => ({ ok: true, state: 'connected', message: '已连接当前浏览器', published: false }));
  let openImpl = options.open || (async () => ({ ok: true, state: 'opened', message: '已打开', published: false }));
  let saveImpl = options.save || (async () => ({ ok: true, state: 'saved', message: '已保存', published: false }));
  let prepareReviewImpl = options.prepareReview || (async () => ({ok:true,state:'review_ready',message:'请核对',published:false,reviewToken:'123e4567-e89b-42d3-a456-426614174000'}));
  let confirmReviewImpl = options.confirmReview || (async () => ({ok:true,state:'reviewed',message:'已解除提交保护',published:false}));
  const fetchCalls = [];
  const jsx = (type, props) => ({ type, props });
  const modules = {
    react: {
      useState(initial) {
        const index = cursor++;
        if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
        return [state[index], value => { state[index] = typeof value === 'function' ? value(state[index]) : value; }];
      },
      useRef(initial) { const index = cursor++; return refs[index] ??= { current: initial }; },
      useEffect(callback, dependencies = []) {
        const index = cursor++;
        const previous = effects[index];
        if (!previous || dependencies.some((value, position) => value !== previous.dependencies[position])) {
          pending.push(() => { previous?.cleanup?.(); effects[index] = { dependencies, cleanup: callback() }; });
        }
      },
    },
    'react/jsx-runtime': { jsx, jsxs: jsx },
    '../lib/store': { loadPublishDraft: () => ({ title: options.title ?? '待发标题', body: options.body ?? '待发正文', platforms: options.platforms ?? ['xiaohongshu'], overrides: {}, tags: '保险,科普' }), savePublishDraft() {} },
    '../lib/api': {
      fetchAccounts: async () => (options.platforms ?? ['xiaohongshu']).map(platform => ({ platform, loggedIn: options.loggedIn !== false })),
      fetchOutputs: async () => options.files ?? [{ type: 'file', kind: 'image', path: 'cover.png', name: 'cover.png' }],
      runAgent: options.runAgent || (async () => ({ response: '未见明显风险' })),
      executeSkill: async () => ({ response: '人设一致' }),
      publishNow: async (platform, payload) => { publishes.push({ platform, payload }); return options.publish?.[platform] ?? { ok: true, message: '完成' }; },
      publishStatus: async () => { throw new Error('no poll'); },
      createSchedule: async () => ({}),
      mediaUrl: path => options.mediaUrl ? options.mediaUrl(path) : `/media/${path}`,
      openXhsChromeDrafts: async () => { throw new Error('server chrome must not be called'); },
      submitPublishSms: async () => ({}),
    },
    '../lib/clientBrowser': {
      getClientBrowserStatus: async (signal) => { clientCalls.push(['status', signal]); return statusImpl(); },
      openClientXhsDrafts: async (signal) => { clientCalls.push(['open', signal]); return openImpl(); },
      saveClientXhsDraft: async (payload, signal) => { clientCalls.push(['draft', payload, signal]); return saveImpl(payload); },
      prepareClientDraftReview: async (signal) => { clientCalls.push(['review_prepare',signal]); return prepareReviewImpl(); },
      confirmClientDraftReview: async (token,signal) => { clientCalls.push(['review',token,signal]); return confirmReviewImpl(token); },
    },
    '../lib/sanitize': { renderMarkdown: value => value },
    './MingyaPrimitives': { ReferenceArtwork: 'artwork' },
    '../styles/mingya-publish.css': {},
    './icons': new Proxy({}, { get: () => 'icon' }),
  };
  const exports = {};
  const confirmations = [];
  runInNewContext(transpile('../../web/frontend/src/components/PublishPage.tsx'), {
    exports, Error, require: name => modules[name],
    window: { confirm(message) { confirmations.push(message); return typeof options.confirm === 'function' ? options.confirm(message) : options.confirm !== false; } },
    location: { origin: 'https://workbench.example' },
    fetch: async (url, init) => {
      fetchCalls.push({ url, credentials: init?.credentials, redirect: init?.redirect });
      if (options.fetchError) throw new Error('下载中断');
      let index = 0;
      const chunks = options.chunks ?? [new Uint8Array([1, 2, 3])];
      return { ok: options.fetchOk !== false, url: options.responseUrl ?? url, headers: { get: key => key === 'content-type' ? (options.mime ?? 'image/png') : key === 'content-length' ? (options.length ?? null) : null }, body: { getReader: () => ({ read: async () => index < chunks.length ? { done: false, value: chunks[index++] } : { done: true }, cancel: async () => {}, releaseLock() {} }) } };
    },
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    setTimeout() {}, setInterval() { throw new Error('no poll'); },
    URL, decodeURIComponent, AbortController,
  });
  return {
    publishes, clientCalls, confirmations, fetchCalls, toasts,
    render() { cursor = 0; pending = []; const tree = exports.default({ persona: '保险科普' }); pending.forEach(fn => fn()); return tree; },
  };
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object' || !tree.props) return [];
  return [tree, ...nodes(tree.props.children)];
}
function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join('');
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree);
  return tree?.props ? text(tree.props.children) : '';
}
function button(tree, label) {
  const node = nodes(tree).find(node => node.type === 'button' && text(node).includes(label));
  assert.ok(node, `missing ${label}`);
  return node;
}
const submit = tree => nodes(tree).find(node => node.type === 'button' && /^(存小红书草稿|提交所选平台|一键发布)$/.test(text(node).trim()));
const card = (tree, platform) => nodes(nodes(tree).find(node => node.props.className === `card pv-card pv-${platform}`)).find(node => node.props.className?.startsWith('pv-pubstate'));

async function chooseImage(app) {
  app.render();
  await settle();
  button(app.render(), '选择图片').props.onClick();
  nodes(app.render()).find(node => node.props.className === 'media-cell ').props.onClick();
  return app.render();
}

test('connect and open use the current browser once per gesture and never the server chrome', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const app = page({ status: () => gate.then(() => ({ ok: true, state: 'connected', message: '已连接当前浏览器', published: false })) });
  const tree = app.render();
  assert.equal(button(tree, '连接当前浏览器').props.className, 'btn btn-sm');
  assert.equal(button(tree, '查看小红书草稿').props.className, 'btn btn-sm');
  assert.match(text(tree), /浏览器连接器/);
  assert.equal(text(tree).includes('用 Chrome 查看小红书草稿'), false);
  const handler = button(tree, '连接当前浏览器').props.onClick;
  const first = handler();
  await handler();
  assert.equal(app.clientCalls.filter(call => call[0] === 'status').length, 1);
  release();
  await first;
  const connected = app.render();
  assert.match(text(nodes(connected).find(node => node.props['aria-live'] === 'polite')), /已连接当前浏览器/);
  await button(connected, '查看小红书草稿').props.onClick();
  await settle();
  const opened = app.render();
  assert.match(text(nodes(opened).find(node => node.props['aria-live'] === 'polite')), /不会保存或发布/);
  assert.equal(app.publishes.length, 0);
  assert.equal(app.clientCalls.some(call => call[0] === 'open'), true);
});

const unknownSave = async () => ({ ok: false, state: 'unconfirmed', message: '保存结果未确认：未取得保存前的草稿数量（保存前未知篇，当前未知篇）。请先查看草稿箱，勿重复提交。', published: false });
async function reviewNeeded(options = {}) {
  const app = page({ save: unknownSave, ...options });
  await submit(await chooseImage(app)).props.onClick();
  return app;
}
const reviewButton = tree => nodes(tree).find(node => node.type === 'button' && /我已核对|正在解除保护/.test(text(node)));

test('the review control is hidden until a draft result is unknown or blocked by protection', async () => {
  const app = page();
  const initial = app.render();
  assert.equal(nodes(initial).some(node => node.type === 'button' && /核对|解除保护/.test(text(node))), false);
  await submit(await chooseImage(app)).props.onClick();
  assert.equal(reviewButton(app.render()), undefined);
  const unknown = await reviewNeeded();
  assert.ok(reviewButton(unknown.render()));
  assert.ok(button(unknown.render(), '打开草稿箱'));
  const blocked = page({ save: async () => ({ ok: false, state: 'busy', message: '有未核对的草稿任务保护；检查草稿箱后，可在工作台点「核对后解除保护」，或在扩展弹窗解除提交保护。', published: false }) });
  await submit(await chooseImage(blocked)).props.onClick();
  const blockedTree = blocked.render();
  assert.ok(reviewButton(blockedTree));
  assert.match(text(card(blockedTree, 'xiaohongshu')), /为防止重复保存，这次没有提交/);
  assert.doesNotMatch(text(blockedTree), /扩展弹窗/);
  const busy = page({ save: async () => ({ ok: false, state: 'busy', message: '已有浏览器任务正在进行。', published: false }) });
  await submit(await chooseImage(busy)).props.onClick();
  assert.equal(reviewButton(busy.render()), undefined);
});

test('manual review prepares, confirms exact warning, commits once and clears the stale warning', async () => {
  const app = await reviewNeeded();
  await reviewButton(app.render()).props.onClick();
  assert.deepEqual(app.clientCalls.filter(call=>call[0].startsWith('review')).map(call=>call[0]),['review_prepare','review']);
  assert.equal(app.clientCalls.find(call=>call[0]==='review')[1],'123e4567-e89b-42d3-a456-426614174000');
  assert.equal(app.confirmations.at(-1),'请确认你已在小红书草稿箱看过上一篇（已存在或确实没有保存）。此操作只解除重复保存保护，不会重试、上传、删除或发布。');
  const after = app.render();
  assert.match(text(nodes(after).find(node=>node.props['aria-live']==='polite')),/已解除提交保护/);
  assert.equal(reviewButton(after), undefined);
  const state = card(after, 'xiaohongshu');
  assert.match(state.props.className, /pv-pubstate ok/);
  assert.match(text(state), /可以重新点「存小红书草稿」/);
  assert.doesNotMatch(text(state), /勿重复提交/);
  assert.equal(app.clientCalls.filter(call=>call[0]==='draft').length, 1);
});

test('manual review cancel makes zero commit and no pending work is reported directly', async () => {
  const cancelled = await reviewNeeded({ confirm: message => !message.startsWith('请确认你已在小红书草稿箱') });
  await reviewButton(cancelled.render()).props.onClick();
  assert.equal(cancelled.clientCalls.filter(call=>call[0]==='review_prepare').length,1);
  assert.equal(cancelled.clientCalls.filter(call=>call[0]==='review').length,0);
  assert.ok(reviewButton(cancelled.render()), 'cancel keeps the review control');
  const empty = await reviewNeeded({ prepareReview: async()=>({ok:true,state:'reviewed',message:'当前没有待核对任务',published:false}) });
  const confirmationsBefore = empty.confirmations.length;
  await reviewButton(empty.render()).props.onClick();
  assert.equal(empty.confirmations.length, confirmationsBefore);
  assert.equal(empty.clientCalls.filter(call=>call[0]==='review').length,0);
  assert.match(text(nodes(empty.render()).find(node=>node.props['aria-live']==='polite')),/没有待核对任务/);
  assert.equal(reviewButton(empty.render()), undefined);
});

test('pending manual review owns the client lock and unknown results are not retried', async () => {
  let release;const gate=new Promise(resolve=>{release=resolve});
  const app = await reviewNeeded({ prepareReview: () => gate });
  const initial = app.render();
  const pending = reviewButton(initial).props.onClick();
  await button(initial,'连接当前浏览器').props.onClick();
  const mid=app.render();
  assert.equal(button(mid,'正在解除保护').props.disabled,true);
  assert.equal(button(mid,'连接当前浏览器').props.disabled,true);
  assert.equal(button(mid,'查看小红书草稿').props.disabled,true);
  assert.equal(button(mid,'打开草稿箱').props.disabled,true);
  assert.equal(app.clientCalls.filter(call=>call[0]==='status').length,0);
  release({ok:false,state:'unconfirmed',message:'解除保护结果未确认，请刷新状态核对；不会自动重试',published:false});
  await pending;
  assert.equal(app.clientCalls.filter(call=>call[0]==='review_prepare').length,1);
  assert.equal(app.clientCalls.filter(call=>call[0]==='review').length,0);
  assert.match(text(nodes(app.render()).find(node=>node.props['aria-live']==='polite')),/不会自动重试/);
  assert.ok(reviewButton(app.render()), 'unconfirmed review keeps the control');
});

test('image draft uses the client helper without a server login or publishNow', async () => {
  const app = page({ loggedIn: false });
  const tree = await chooseImage(app);
  await submit(tree).props.onClick();
  await settle();
  assert.equal(app.confirmations.length, 0);
  assert.equal(app.publishes.length, 0);
  assert.equal(app.clientCalls.filter(call => call[0] === 'draft').length, 1);
  const payload = app.clientCalls.find(call => call[0] === 'draft')[1];
  assert.equal(JSON.stringify(payload.tags), JSON.stringify(['保险', '科普']));
  assert.equal(payload.images[0].mime, 'image/png');
  assert.equal(payload.images[0].name, 'cover.png');
  assert.equal(payload.images[0].base64, Buffer.from([1, 2, 3]).toString('base64'));
  assert.equal(app.fetchCalls[0].credentials, 'same-origin');
  assert.equal(app.fetchCalls[0].url, 'https://workbench.example/media/cover.png');
  assert.match(text(card(app.render(), 'xiaohongshu')), /已保存至当前浏览器的小红书草稿/);
});

test('douyin keeps publishNow and does not call the browser draft helper', async () => {
  const app = page({ platforms: ['douyin'], files: [{ type: 'file', kind: 'video', path: 'clip.mp4', name: 'clip.mp4' }], publish: { douyin: { ok: true, message: 'published' } } });
  app.render();
  await settle();
  button(app.render(), '选择视频').props.onClick();
  nodes(app.render()).find(node => node.props.className === 'media-cell ').props.onClick();
  await submit(app.render()).props.onClick();
  assert.deepEqual(app.publishes.map(item => item.platform), ['douyin']);
  assert.equal(app.clientCalls.some(call => call[0] === 'draft'), false);
  assert.match(text(card(app.render(), 'douyin')), /已发布/);
});

test('record_found confirms the new record while requiring content review; unconfirmed never claims success', async () => {
  const found = page({ save: async () => ({ ok: true, state: 'record_found', message: '有记录', published: false, receiptKind: 'new_record' }) });
  await submit(await chooseImage(found)).props.onClick();
  const foundState = card(found.render(), 'xiaohongshu');
  assert.match(foundState.props.className, /pv-pubstate ok/);
  assert.match(text(foundState), /已核对到本次新增记录/);
  assert.match(text(foundState), /请打开检查正文和图片/);
  assert.doesNotMatch(text(foundState), /完整保存|正文和图片已核对/);
  const missing = page({ save: async () => ({ ok: false, state: 'unavailable', message: '没有扩展', published: false }) });
  await submit(await chooseImage(missing)).props.onClick();
  const failed = missing.render();
  assert.match(text(card(failed, 'xiaohongshu')), /没有扩展/);
  assert.equal(/成功/.test(text(failed)), false);
  const unknown = page({ save: async () => ({ ok: false, state: 'unconfirmed', message: '未知', published: false }) });
  await submit(await chooseImage(unknown)).props.onClick();
  assert.match(text(card(unknown.render(), 'xiaohongshu')), /勿重复提交/);
  assert.equal(unknown.clientCalls.filter(call => call[0] === 'draft').length, 1);
});

test('a failed image download does not save, and video stays on the server channel', async () => {
  const broken = page({ fetchError: true });
  await submit(await chooseImage(broken)).props.onClick();
  assert.equal(broken.clientCalls.some(call => call[0] === 'draft'), false);
  assert.equal(broken.publishes.length, 0);
  assert.match(text(card(broken.render(), 'xiaohongshu')), /下载中断|未保存/);
  const video = page({ loggedIn: true, files: [{ type: 'file', kind: 'video', path: 'clip.mp4', name: 'clip.mp4' }], publish: { xiaohongshu: { ok: true, outcome: 'draft_saved', published: false, message: '服务端草稿' } } });
  video.render();
  await settle();
  button(video.render(), '选择视频').props.onClick();
  nodes(video.render()).find(node => node.props.className === 'media-cell ').props.onClick();
  const tree = video.render();
  assert.match(text(tree), /不是日常使用的 Chrome/);
  await submit(tree).props.onClick();
  assert.equal(video.publishes[0].platform, 'xiaohongshu');
  assert.equal(video.clientCalls.some(call => call[0] === 'draft'), false);
});

test('the same render cannot open two client requests', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const app = page({ open: () => gate.then(() => ({ ok: true, state: 'opened', message: '已打开', published: false })) });
  const handler = button(app.render(), '查看小红书草稿').props.onClick;
  const first = handler();
  const second = handler();
  assert.equal(app.clientCalls.filter(call => call[0] === 'open').length, 1);
  release();
  await Promise.all([first, second]);
  assert.equal(app.clientCalls.filter(call => call[0] === 'open').length, 1);
});


test('posted draft abort is unconfirmed, but an early abort sends nothing', async () => {
  const h = loadClient(), early = new AbortController();
  early.abort();
  assert.equal((await h.api.saveClientXhsDraft({title:'t',body:'b',tags:[],images:[]}, early.signal)).state, 'error');
  assert.equal(h.posts.length, 0);
  const ctl = new AbortController();
  const pending = h.api.saveClientXhsDraft({title:'t',body:'b',tags:[],images:[]}, ctl.signal);
  ctl.abort();
  const result = await pending;
  assert.equal(result.state, 'unconfirmed');
  assert.match(result.message, /先查看.*勿重复提交/);
  assert.equal(h.posts.length, 1);
  assert.equal(h.listeners.size, 0);
});

test('pending open keeps both controls busy when connect is invoked from same render', async () => {
  let release; const gate = new Promise(r => { release = r; });
  const app = page({open: () => gate});
  const initial = app.render();
  const pending = button(initial, '查看小红书草稿').props.onClick();
  await button(initial, '连接当前浏览器').props.onClick();
  const mid = app.render();
  assert.equal(button(mid, '正在打开草稿').props.disabled, true);
  assert.equal(button(mid, '连接当前浏览器').props.disabled, true);
  assert.equal(app.clientCalls.filter(c => c[0] === 'status').length, 0);
  release({ok:true,state:'opened',message:'ok',published:false}); await pending;
});

test('cross-origin final response and oversized streaming images never send draft', async () => {
  for (const options of [
    {responseUrl:'https://other.example/final.png'},
    {length:String(10*1024*1024+1)},
    {chunks:[new Uint8Array(10*1024*1024),new Uint8Array(1)]},
  ]) {
    const app=page(options); await submit(await chooseImage(app)).props.onClick();
    assert.equal(app.clientCalls.filter(c=>c[0]==='draft').length,0);
    assert.equal(app.fetchCalls[0].redirect,'error');
  }
});

test('image total limit is checked as each stream arrives', async () => {
  const files=Array.from({length:4},(_,i)=>({type:'file',kind:'image',path:`${i}.png`,name:`${i}.png`}));
  const app=page({files,chunks:[new Uint8Array(10*1024*1024)]});
  app.render(); await settle(); button(app.render(),'选择图片').props.onClick();
  for (let i=0;i<4;i++) nodes(app.render()).filter(n=>n.props.className==='media-cell ')[0].props.onClick();
  await submit(app.render()).props.onClick();
  assert.equal(app.clientCalls.filter(c=>c[0]==='draft').length,0);
  assert.equal(app.fetchCalls.length,4);
});


test('unconfirmed receipt diagnosis remains a failure and is visible without retry', async () => {
 const message='保存结果未确认：记录封面尚未读取完成（保存前12篇，当前13篇）。请先查看草稿箱，勿重复提交。';
 const app=page({save:async()=>({ok:false,state:'unconfirmed',message,published:false})});
 await submit(await chooseImage(app)).props.onClick();
 const rendered=card(app.render(),'xiaohongshu');
 assert.match(rendered.props.className,/pv-pubstate fail/);assert.match(text(rendered),/保存前12篇，当前13篇/);
 assert.equal(app.clientCalls.filter(call=>call[0]==='draft').length,1);
});

test('draft-only save starts immediately and is not held behind a slow advisory precheck', async () => {
  const app = page({ runAgent: () => new Promise(() => {}) });
  await submit(await chooseImage(app)).props.onClick();
  await settle();
  assert.equal(app.confirmations.length, 0);
  assert.equal(app.clientCalls.filter(call => call[0] === 'draft').length, 1);
  const tree = app.render();
  assert.match(text(card(tree, 'xiaohongshu')), /已保存至当前浏览器的小红书草稿/);
  assert.equal(submit(tree).props.disabled, false);
  assert.equal(button(tree, '预检中').props.disabled, true);
});
