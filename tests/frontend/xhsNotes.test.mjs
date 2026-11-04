import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const pageSource = readFileSync(new URL('../../web/frontend/src/components/TrendsPage.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(pageSource + '\nexport const xhsUrlTestTools = { xhsOriginalUrl, xhsImageUrl };', {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  },
});
const settle = () => new Promise(resolve => setImmediate(resolve));

function setup(props = {}, pane = true) {
  const state = [];
  const effects = [];
  const callbacks = [];
  const refs = [];
  const calls = [];
  let cursor = 0;
  let enteredXhs = false;
  let failSave = false;
  let deferredSave = false;
  const saveReplies = [];
  const stateWrites = [];
  const references = [];
  const saves = [];
  const timers = new Map();
  let nextTimer = 0;
  const request = (kind, platforms, limit, signal) => {
    let resolve;
    let reject;
    const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
    calls.push({ kind, platforms, limit, signal, resolve, reject });
    return promise;
  };
  const createElement = (type, props) => ({ type, props });
  const modules = {
    react: {
      useState(initial) {
        const index = cursor++;
        if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
        return [state[index], value => {
          stateWrites.push(index);
          state[index] = typeof value === 'function' ? value(state[index]) : value;
        }];
      },
      useRef(initial) {
        const index = cursor++;
        if (!(index in refs)) refs[index] = { current: initial };
        return refs[index];
      },
      useCallback(fn, deps) {
        const index = cursor++;
        const previous = callbacks[index];
        if (!previous || deps.some((value, position) => value !== previous.deps[position])) {
          callbacks[index] = { deps, fn };
        }
        return callbacks[index].fn;
      },
      useEffect(callback, dependencies) {
        const index = cursor++;
        const previous = effects[index];
        const changed = !previous
          || dependencies.length !== previous.dependencies.length
          || dependencies.some((value, position) => value !== previous.dependencies[position]);
        if (changed) {
          pending.push(() => {
            previous?.cleanup?.();
            effects[index] = { dependencies, cleanup: callback() || undefined };
          });
        }
      },
    },
    'react/jsx-runtime': { jsx: createElement, jsxs: createElement },
    '../lib/api': {
      fetchXhsFeed(signal) { return request('feed', undefined, undefined, signal); },
      startLogin(platform, scope) { return request('connect', platform, scope); },
      loginStatus(platform) { return request('login-status', platform); },
      searchXhsNotes(query, signal) { return request(query === '保险' ? 'default-insurance' : 'search', query, undefined, signal); },
      readXhsNote(url, signal) { return request('detail', url, undefined, signal); },
      fetchTrends(platforms, limit, signal) {
        return request('general', platforms, limit, signal);
      },
      fetchInsuranceTrends(limit, signal) {
        return request('industry', undefined, limit, signal);
      },
      async createIdea(item) {
        saves.push(item);
        if (failSave) throw new Error('disk full');
        if (deferredSave) return new Promise((resolve, reject) => saveReplies.push({ resolve, reject }));
        return { id: 'idea-1', title: 'saved' };
      },
    },
    './icons': { IconFire: 'fire', IconRefresh: 'refresh', IconBookmark: 'bookmark', IconCheck: 'check' },
  };
  const pending = [];
  const exports = {};
  runInNewContext(outputText, { exports, AbortController, URL, require: name => modules[name],
    setInterval(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearInterval(id) { timers.delete(id); },
  });
  return {
    get connectionCalls() { return calls.filter(call => ['connect', 'login-status'].includes(call.kind)); },
    get feedCalls() { return calls.filter(call => call.kind === 'feed'); },
    get defaultCalls() { return calls.filter(call => ['feed', 'default-insurance'].includes(call.kind)); },
    tickConnection() { return Promise.all([...timers.values()].map(callback => callback())); },
    get calls() { return pane ? calls.filter(call => ['search', 'detail'].includes(call.kind)) : calls; },
    saves,
    references,
    saveReplies,
    stateWrites,
    deferSave(value = true) { deferredSave = value; },
    exported: exports.xhsUrlTestTools,
    failSave(value) { failSave = value; },
    render(nextProps = props) {
      props = nextProps;
      cursor = 0;
      pending.length = 0;
      const tree = exports.default({ persona: '保险科普', insurancePersona: true, onIndustry() {}, onAll() {}, onReference: value => references.push(value), onUseTopic() {}, ...props });
      pending.splice(0).forEach(callback => callback());
      // Pane-focused cases explicitly enter the source; no special production entry point.
      if (pane && !enteredXhs) {
        enteredXhs = true;
        button(tree, '小红书').props.onClick();
        const selectedTree = this.render(props);
        nodes(selectedTree).find(node => node.type === 'input' && node.props['aria-label'] === '小红书搜索关键词').props.onChange({ target: { value: '家庭保险' } });
        return this.render(props);
      }
      return tree;
    },
    unmount() {
      effects.forEach(effect => effect?.cleanup?.());
    },
  };
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object' || !('props' in tree)) return [];
  return [tree, ...nodes(tree.props?.children)];
}

function text(tree) {
  return nodes(tree).flatMap(node => {
    const children = node.props?.children;
    return Array.isArray(children) ? children : [children];
  }).filter(value => typeof value === 'string' || typeof value === 'number').join('');
}

function columns(tree) {
  return nodes(tree).filter(node => node.props?.className === 'card trend-col');
}

function compact(tree) {
  return text(tree).replace(/\s+/g, '');
}

function button(tree, label) {
  const result = nodes(tree).find(node => node.type === 'button' && compact(node).includes(label));
  assert.ok(result, `missing button ${label}`);
  return result;
}

function shown(app) {
  app.render();
  return app.render();
}

const note = (extra = {}) => ({ id: '0123456789abcdef01234567', title: '家庭保障问题', body: null,
  content_status: 'summary', images: [], url: 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567',
  author: null, published_at: null, metrics: { likes: null, saves: null, comments: null }, ...extra });
const payload = (items = [note()], error = null) => ({ items, error, query: '家庭保险', cached: false, fetched_at: null, source: 'xiaohongshu_cli' });

test('read reconnect is explicit, rejects creator-only success, and never automatically searches', async () => {
  const app = setup();
  const form = nodes(shown(app)).find(node => node.type === 'form');
  form.props.onSubmit({ preventDefault() {} });
  app.calls[0].resolve(payload([], { code: 'auth_required', message: '读取登录已失效', retryable: false }));
  await settle();
  for (const scope of ['default', 'community']) {
    const connecting = button(shown(app), '重新连接读取账号').props.onClick();
    const call = app.connectionCalls.at(-1);
    assert.equal(call.kind, 'connect'); assert.equal(call.platforms, 'xiaohongshu'); assert.equal(call.limit, 'community');
    call.resolve({ mode: 'qr', state: 'starting', scope: 'community' }); await connecting;
    shown(app);
    const tick = app.tickConnection();
    app.connectionCalls.at(-1).resolve({ mode: 'qr', state: 'success', scope });
    await tick;
    const tree = shown(app);
    if (scope === 'community') assert.equal(app.defaultCalls.length, 2);
    else assert.match(text(tree), /未确认成功/);
    assert.equal(app.calls.length, 1);
  }
  app.unmount();
});
async function results(app, items) {
  const form = nodes(shown(app)).find(n => n.type === 'form');
  form.props.onSubmit({ preventDefault() {} });
  app.calls.at(-1).resolve(payload(items)); await settle(); return shown(app);
}

test('manual keyword submit and duplicate submit are bounded independently of default loading', async () => {
  const app = setup(); let tree = shown(app);
  assert.equal(app.calls.length, 0);
  const form = nodes(tree).find(n => n.type === 'form');
  form.props.onSubmit({ preventDefault() {} }); form.props.onSubmit({ preventDefault() {} });
  assert.equal(app.calls.length, 1); assert.equal(app.calls[0].platforms, '家庭保险');
  app.calls[0].resolve(payload()); await settle(); tree = shown(app);
  assert.match(text(tree), /家庭保障问题/); assert.equal(text(tree).includes('实时热搜'), false);
});

test('legacy modes, six platform filters and actions remain; switching to XHS performs no search', () => {
  const app = setup({ persona: '' }, false); let tree = shown(app);
  assert.equal(app.calls[0].kind, 'general');
  for (const label of ['微博', '抖音', '知乎', 'B站', '百度', '头条']) assert.ok(button(tree, label));
  button(tree, '小红书').props.onClick(); tree = shown(app);
  assert.equal(app.calls.some(c => c.kind === 'search'), false);
  assert.equal(app.calls[0].signal.aborted, true);
  assert.match(pageSource, /onUseTopic\(it.title\)/);
  assert.match(pageSource, /onBreakdown\(it\)/);
});

test('mixed images/text-only cards omit unavailable metrics; failed image is explicit', async () => {
  const app = setup(); let tree = await results(app, [note({ images: ['https://sns-img.xhscdn.com/cover.jpg'], metrics: { likes: 0, saves: null, comments: 7 } }), note({ id: 'abcdef0123456789abcdef01' })]);
  const articles = nodes(tree).filter(n => n.type === 'article'); assert.equal(articles.length, 2);
  assert.equal(nodes(articles[1]).some(n => n.type === 'img'), false);
  assert.equal(nodes(articles[1]).some(n => n.props.className === 'xhs-note-media'), false);
  assert.match(text(tree), /点赞 0/); assert.match(text(tree), /评论 7/); assert.equal(text(tree).includes('收藏 0'), false);
  for (const label of ['原文', '一键拆解', '按当前IP仿写', '收藏']) assert.match(text(articles[0]), new RegExp(label));
  nodes(tree).find(n => n.type === 'img').props.onError(); tree = shown(app); assert.match(text(tree), /原图未加载/);
});

test('detail failure hands off title/source with explicit unread boundary once; save persists real reference', async () => {
  const app = setup(); let tree = await results(app);
  await button(tree, '收藏').props.onClick(); assert.equal(app.saves.length, 1); assert.match(app.saves[0].source, /xiaohongshu.com/);
  tree = shown(app); const action = button(tree, '一键拆解');
  const first = action.props.onClick(); action.props.onClick(); assert.equal(app.calls.filter(c => c.kind === 'detail').length, 1);
  app.calls.at(-1).resolve(payload([], { code: 'timeout', message: '详情超时' })); await first;
  assert.equal(app.references.length, 1); assert.equal(app.references[0].intent, 'breakdown');
  assert.match(app.references[0].input, /未读全文/); assert.match(app.references[0].input, /家庭保障问题/);
  assert.equal(app.references[0].persona, '保险科普');
  await action.props.onClick(); assert.equal(app.references.length, 1);
});

test('rewrite waits for real body, forwards no synthetic analysis, and cancel suppresses late results', async () => {
  const app = setup(); let tree = await results(app);
  const pending = button(tree, '按当前IP仿写').props.onClick();
  app.calls.at(-1).resolve(payload([note({ body: '真实详情材料', content_status: 'full' })])); await pending;
  assert.equal(app.references[0].intent, 'rewrite'); assert.match(app.references[0].input, /真实详情材料/);
  assert.equal('result' in app.references[0], false);
  const other = setup(); tree = await results(other); const cancelled = button(tree, '一键拆解').props.onClick();
  const detail = other.calls.at(-1); button(shown(other), '取消').props.onClick();
  detail.resolve(payload([note({ body: '过期内容', content_status: 'full' })])); await cancelled;
  assert.equal(detail.signal.aborted, true); assert.equal(other.references.length, 0);
});

test('persona changes and unmount ignore stale search/detail replies', async () => {
  const app = setup(); const form = nodes(shown(app)).find(n => n.type === 'form'); form.props.onSubmit({ preventDefault() {} });
  const old = app.calls[0]; app.render({ persona: '新IP', insurancePersona: false });
  old.resolve(payload([note({ title: '旧IP结果' })])); await settle(); assert.equal(text(shown(app)).includes('旧IP结果'), false);
  const other = setup(); let tree = await results(other); const pending = button(tree, '一键拆解').props.onClick();
  const detail = other.calls.at(-1); other.unmount(); detail.resolve(payload([note({ body: '迟到全文', content_status: 'full' })])); await pending;
  assert.equal(other.references.length, 0); assert.equal(detail.signal.aborted, true);
});

test('official links and image URLs are restricted without a new generic proxy', () => {
  const api = setup().exported;
  assert.equal(api.xhsOriginalUrl('javascript:alert(1)'), undefined);
  assert.equal(api.xhsOriginalUrl('https://evil.test/explore/0123456789abcdef01234567'), undefined);
  assert.equal(api.xhsImageUrl('http://sns-img.xhscdn.com/a'), undefined);
  assert.equal(api.xhsImageUrl('https://xhscdn.com.evil.test/a'), undefined);
  assert.ok(api.xhsImageUrl('https://sns-img.xhscdn.com/a'));
});

test('search/detail API encode inputs and forward abort signals', async () => {
  const calls = []; const sandbox = { exports: {}, window: { location: { pathname: '/' } }, AbortController,
    setTimeout, clearTimeout, fetch: async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => payload() }; } };
  runInNewContext(ts.transpileModule(apiSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, sandbox);
  await sandbox.exports.searchXhsNotes('保险 & 医疗'); await sandbox.exports.readXhsNote(note().url);
  assert.equal(calls[0].url, '/api/xhs/notes/search?q=%E4%BF%9D%E9%99%A9%20%26%20%E5%8C%BB%E7%96%97');
  assert.ok(calls[1].url.startsWith('/api/xhs/notes/detail?url=https%3A%2F%2F'));
  const controller = new AbortController(); controller.abort();
  await assert.rejects(sandbox.exports.searchXhsNotes('保险', controller.signal), error => error.name === 'AbortError');
  assert.equal(calls.length, 2);
});

test('App routes a reference to the existing breakdown or new-chat flow, guards stale context and uses general style without an IP', () => {
  const appSource = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
  const ast = ts.createSourceFile('App.tsx', appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let declaration;
  function visit(node) { if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'handleXhsReference') declaration = node.getText(ast); ts.forEachChild(node, visit); }
  visit(ast); assert.ok(declaration);
  const compiled = ts.transpileModule(`const ${declaration}; globalThis.handoff = handleXhsReference;`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  function mount(persona = '保险科普', page = 'trends') {
    const calls = { requests: [], prompts: [], pages: [] };
    const context = { selectedPersona: persona, currentPage: page, useCallback: fn => fn, breakdownSequence: { current: 0 },
      setBreakdownRequest: value => calls.requests.push(value), setCurrentPage: value => calls.pages.push(value),
      handleExternalResearch: prompt => calls.prompts.push(prompt) };
    runInNewContext(compiled, context); return { calls, handoff: context.handoff };
  }
  const app = mount(); const input = '未读全文，标题与出处';
  app.handoff({ intent: 'breakdown', persona: '保险科普', input });
  assert.equal(app.calls.requests[0].input, input); assert.equal(app.calls.requests[0].persona, '保险科普');
  assert.equal(app.calls.pages[0], 'breakdown'); assert.equal(app.calls.prompts.length, 0);
  app.handoff({ intent: 'rewrite', persona: '旧IP', input }); assert.equal(app.calls.prompts.length, 0);
  app.handoff({ intent: 'rewrite', persona: '保险科普', input });
  for (const word of ['保险科普', '原创措辞', '只借鉴', '未读全文', '不自动发布']) assert.ok(app.calls.prompts[0].includes(word));
  const general = mount(''); general.handoff({ intent: 'rewrite', persona: '', input }); assert.match(general.calls.prompts[0], /通用科普风格/);
  const navigated = mount('保险科普', 'chat'); navigated.handoff({ intent: 'rewrite', persona: '保险科普', input }); assert.equal(navigated.calls.prompts.length, 0);
});


test('pending favorite survives a same-IP refresh, dedupes double clicks and stale callbacks after success', async () => {
  const app = setup(); let tree = await results(app); app.deferSave();
  const favorite = button(tree, '收藏'); const first = favorite.props.onClick();
  favorite.props.onClick(); assert.equal(app.saves.length, 1, 'same-tick double click deduped');
  button(tree, '刷新').props.onClick(); const refresh = app.calls.at(-1);
  app.saveReplies[0].resolve({ id: 'saved-1' }); await first;
  // Use the old callback before a re-render: durable success must guard it too.
  favorite.props.onClick(); assert.equal(app.saves.length, 1, 'successful favorite must guard stale callback');
  refresh.resolve(payload()); await settle(); tree = shown(app);
  assert.ok(button(tree, '已收藏').props.disabled, 'refresh must retain the successful favorite');
  await button(tree, '已收藏').props.onClick(); assert.equal(app.saves.length, 1);
});

test('favorite success after detail cancel survives, but persona change and unmount suppress stale state writes', async () => {
  const app = setup(); let tree = await results(app); app.deferSave();
  const favorite = button(tree, '收藏').props.onClick();
  const analysis = button(tree, '一键拆解').props.onClick(); const detail = app.calls.at(-1);
  button(shown(app), '取消').props.onClick();
  app.saveReplies[0].resolve({ id: 'saved-1' }); await favorite;
  assert.ok(button(shown(app), '已收藏').props.disabled);
  detail.resolve(payload()); await analysis; assert.equal(app.references.length, 0);

  for (const boundary of ['persona', 'unmount']) {
    const other = setup(); tree = await results(other); other.deferSave();
    const saving = button(tree, '收藏').props.onClick();
    if (boundary === 'persona') other.render({ persona: '新IP', insurancePersona: false });
    else other.unmount();
    const writes = other.stateWrites.length;
    other.saveReplies[0].resolve({ id: 'saved-old' }); await saving;
    assert.equal(other.stateWrites.length, writes, `${boundary} must ignore stale save completion`);
  }
});

test('v2 uses one invariant header/filter/search/list shell and source switches preserve XHS results without refetch', async () => {
  const app = setup({ persona: '保险科普' }, false); let tree = shown(app);
  const shell = nodes(tree).find(n => n.props.className === 'trend-filter-panel'); assert.ok(shell);
  assert.deepEqual(nodes(shell).filter(n => n.props['data-filter-row']).map(n => n.props['data-filter-row']), ['scope', 'source', 'search']);
  assert.equal(nodes(tree).filter(n => n.type === 'form').length, 1);
  button(tree, '小红书').props.onClick(); tree = shown(app);
  assert.equal(nodes(tree).filter(n => n.props.className === 'page-head').length, 1);
  assert.equal(app.calls.some(c => c.kind === 'search'), false);
  nodes(tree).find(n => n.type === 'input' && n.props['aria-label'] === '小红书搜索关键词').props.onChange({ target: { value: '家庭保险' } });
  tree = shown(app);
  const form = nodes(tree).find(n => n.type === 'form'); form.props.onSubmit({ preventDefault() {} });
  app.calls.at(-1).resolve(payload()); await settle(); tree = shown(app);
  assert.match(text(tree), /家庭保障问题/);
  button(tree, '微博').props.onClick(); shown(app);
  button(shown(app), '小红书').props.onClick(); tree = shown(app);
  assert.match(text(tree), /家庭保障问题/); assert.equal(app.calls.filter(c => c.kind === 'search').length, 1);
});

test('selecting XHS loads the real recommendation feed once without a keyword or search click', async () => {
  const app = setup({ persona: '' }, false);
  button(shown(app), '小红书').props.onClick();
  let tree = shown(app);
  assert.equal(nodes(tree).find(n => n.type === 'input' && n.props['aria-label'] === '小红书搜索关键词').props.value, '');
  assert.equal(app.feedCalls.length, 1);
  assert.equal(app.calls.some(call => call.kind === 'search'), false);
  app.feedCalls[0].resolve({ ...payload([note({ title: '家庭保险如何配置' })]), query: '', source: 'xiaohongshu_homefeed' });
  await settle(); tree = shown(app);
  assert.match(text(tree), /家庭保险如何配置/);
  button(tree, '微博').props.onClick(); shown(app);
  button(shown(app), '小红书').props.onClick(); tree = shown(app);
  assert.equal(app.feedCalls.length, 1);
  assert.match(text(tree), /家庭保险如何配置/);
  app.unmount();
});

test('v2 insurance scope filters platform rows without returning unfiltered rankings or new requests', async () => {
  const app = setup({ persona: '保险科普' }, false); let tree = shown(app);
  button(tree, '微博').props.onClick(); shown(app); const request = app.calls.at(-1);
  request.resolve({ trends: [{ platform: 'weibo', items: [{ title: '无关娱乐新闻', hot: '10', url: '' }, { title: '重疾保险责任', hot: '8', url: '' }] }], updated: 1 });
  await settle(); tree = shown(app); assert.match(text(tree), /重疾保险责任/); assert.equal(text(tree).includes('无关娱乐新闻'), false);
  const count = app.calls.length; button(tree, '全部热点').props.onClick(); tree = shown(app);
  assert.match(text(tree), /无关娱乐新闻/); assert.equal(app.calls.length, count);
  button(tree, '保险相关').props.onClick(); tree = shown(app);
  nodes(tree).find(n => n.type === 'input').props.onChange({ target: { value: '不存在' } });
  tree = shown(app); assert.match(text(tree), /本次总榜 2 条中未匹配保险内容/); assert.equal(text(tree).includes('无关娱乐新闻'), false);
});

test('v2 industry selection filters loaded groups locally; platforms remain independently selectable down to empty', async () => {
  const app = setup({ persona: '保险科普' }, false); shown(app);
  app.calls[0].resolve({ trends: [
    { platform: 'insurance', items: [{ title: '协会资讯', url: '', hot: '' }] },
    { platform: 'regulation', items: [{ title: '监管资讯', url: '', hot: '' }] },
    { platform: 'medical', items: [{ title: '医保资讯', url: '', hot: '' }] },
  ], updated: 1 }); await settle(); let tree = shown(app);
  const count = app.calls.length;
  nodes(tree).find(n => n.type === 'select').props.onChange({ target: { value: 'medical' } }); tree = shown(app);
  assert.equal(app.calls.length, count); assert.match(text(tree), /医保资讯/); assert.equal(text(tree).includes('协会资讯'), false);
  button(tree, '微博').props.onClick(); shown(app);
  for (const label of ['微博', '抖音', '知乎']) button(shown(app), label).props.onClick();
  tree = shown(app); assert.match(text(tree), /请选择至少一个平台/);
  assert.equal(nodes(tree).some(n => n.props.className === 'card trend-col'), false);
  const general = setup({ persona: '' }, false); assert.equal(button(shown(general), '保险相关').props.disabled, true);
});

test('v2 switching sources cancels late detail handoff while preserving keyword and successful favorite', async () => {
  const app = setup({ persona: '保险科普' }, false); let tree = shown(app);
  button(tree, '小红书').props.onClick(); tree = shown(app);
  nodes(tree).find(n => n.type === 'input').props.onChange({ target: { value: '理赔流程' } }); tree = shown(app);
  nodes(tree).find(n => n.type === 'form').props.onSubmit({ preventDefault() {} });
  app.calls.at(-1).resolve(payload()); await settle(); tree = shown(app);
  await button(tree, '收藏').props.onClick();
  const pending = button(tree, '一键拆解').props.onClick(); const detail = app.calls.at(-1);
  button(tree, '微博').props.onClick(); shown(app);
  detail.resolve(payload([note({ body: '迟到正文', content_status: 'full' })])); await pending;
  assert.equal(detail.signal.aborted, true); assert.equal(app.references.length, 0);
  button(shown(app), '小红书').props.onClick(); tree = shown(app);
  assert.equal(nodes(tree).find(n => n.type === 'input').props.value, '理赔流程');
  assert.ok(button(tree, '已收藏').props.disabled); assert.equal(app.calls.filter(c => c.kind === 'search').length, 1);
});


test('insurance IP loads insurance references automatically with an empty search field', async () => {
  const app = setup({ persona: '保险科普' }, false);
  button(shown(app), '小红书').props.onClick();
  let tree = shown(app);
  assert.equal(nodes(tree).find(n => n.type === 'input' && n.props['aria-label'] === '小红书搜索关键词').props.value, '');
  assert.equal(app.defaultCalls.length, 1);
  assert.equal(app.defaultCalls[0].platforms, '保险');
  app.defaultCalls[0].resolve({ ...payload([note({ title: '家庭保险如何配置' })]), query: '保险' });
  await settle(); tree = shown(app);
  assert.match(text(tree), /家庭保险如何配置/);
  assert.match(text(tree), /按当前保险 IP 自动加载/);
  app.unmount();
});
