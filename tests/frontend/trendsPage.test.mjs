import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const pageSource = readFileSync(new URL('../../web/frontend/src/components/TrendsPage.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(pageSource, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  },
});
const settle = () => new Promise(resolve => setImmediate(resolve));

function setup(props = {}) {
  const state = [];
  const effects = [];
  const callbacks = [];
  const refs = [];
  const calls = [];
  let cursor = 0;
  let failSave = false;
  const saves = [];
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
      fetchTrends(platforms, limit, signal) {
        return request('general', platforms, limit, signal);
      },
      fetchInsuranceTrends(limit, signal) {
        return request('industry', undefined, limit, signal);
      },
      async createIdea(item) {
        saves.push(item);
        if (failSave) throw new Error('disk full');
        return { id: 'idea-1', title: 'saved' };
      },
    },
    './icons': { IconFire: 'fire', IconRefresh: 'refresh', IconBookmark: 'bookmark', IconCheck: 'check' },
  };
  const pending = [];
  const exports = {};
  runInNewContext(outputText, { exports, AbortController, require: name => modules[name] });
  return {
    calls,
    saves,
    failSave(value) { failSave = value; },
    render(nextProps = props) {
      props = nextProps;
      cursor = 0;
      pending.length = 0;
      const tree = exports.default({ onUseTopic() {}, ...props });
      pending.splice(0).forEach(callback => callback());
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
  }).filter(value => typeof value === 'string').join('');
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

function statusLine(tree) {
  const node = nodes(tree).find(node => node.props?.className?.startsWith('trend-status-line'));
  assert.ok(node, '统一内容槽应有可见状态位');
  return node;
}

function chooseIndustry(tree, value = 'all') {
  const select = nodes(tree).find(node => node.type === 'select' && node.props?.['aria-label'] === '行业资讯来源');
  assert.ok(select, '行业资讯应保留来源下拉');
  select.props.onChange({ target: { value } });
}

function selectedPlatformLabels(tree) {
  return ['微博', '抖音', '知乎', 'B站', '百度', '头条'].filter(label => button(tree, label).props['aria-pressed'] === true);
}

function shown(app) {
  app.render();
  return app.render();
}

test('initial loading shows a card for every selected platform', () => {
  const app = setup();
  const tree = app.render();
  assert.equal(app.calls[0].kind, 'general');
  assert.deepEqual(columns(tree).map(compact), ['微博正在读取热榜…', '抖音正在读取热榜…', '知乎正在读取热榜…']);
  assert.match(compact(button(tree, '刷新')), /刷新中/);
  assert.equal(columns(tree).some(column => text(column).includes('做内容')), false);
});

test('a slow response cannot overwrite the platform selected later', async () => {
  const app = setup();
  shown(app);
  assert.equal(app.calls[0].platforms, 'weibo,douyin,zhihu');
  button(shown(app), '微博').props.onClick();
  button(shown(app), '抖音').props.onClick();
  button(shown(app), '知乎').props.onClick();
  button(shown(app), '百度').props.onClick();
  const tree = shown(app);
  assert.equal(app.calls.at(-1).platforms, 'baidu');
  assert.equal(app.calls[0].signal.aborted, true);
  assert.match(compact(tree), /百度正在读取热榜…/);
  app.calls[0].resolve({
    trends: [{ platform: 'weibo', label: '微博', items: [{ title: 'OLD-HOT-TITLE', hot: '1', url: '' }], ok: true }],
    updated: 10,
  });
  await settle();
  const stale = shown(app);
  assert.equal(text(stale).includes('OLD-HOT-TITLE'), false);
  assert.match(compact(button(stale, '刷新')), /刷新中/);
  app.calls.at(-1).resolve({
    trends: [{ platform: 'baidu', label: '百度', items: [{ title: 'NEW-HOT-TITLE', hot: '9', url: 'https://example.test' }], ok: true }],
    updated: 20,
  });
  await settle();
  const current = shown(app);
  assert.match(text(current), /NEW-HOT-TITLE/);
  assert.equal(text(current).includes('OLD-HOT-TITLE'), false);
  assert.equal(compact(button(current, '刷新')), '刷新');
  const item = nodes(current).find(node => node.props?.className === 'trend-item');
  assert.equal(nodes(item).some(node => node.props?.className === 'trend-save'), true);
  assert.equal(nodes(item).some(node => node.type === 'button' && text(node).includes('做内容')), true);
});

test('clearing every platform stops loading and ignores the old response', async () => {
  const app = setup();
  shown(app);
  for (const label of ['微博', '抖音', '知乎']) button(shown(app), label).props.onClick();
  const cleared = shown(app);
  assert.equal(columns(cleared).length, 0);
  assert.match(text(cleared), /请选择至少一个平台/);
  assert.equal(compact(button(cleared, '刷新')), '刷新');
  assert.equal(button(cleared, '刷新').props.disabled, false);
  app.calls[0].resolve({
    trends: [{ platform: 'weibo', label: '微博', items: [{ title: 'REVIVED-TITLE', hot: '', url: '' }] }],
    updated: 99,
  });
  await settle();
  const after = shown(app);
  assert.equal(columns(after).length, 0);
  assert.equal(text(after).includes('REVIVED-TITLE'), false);
  assert.match(text(after), /请选择至少一个平台/);
  assert.equal(compact(button(after, '刷新')), '刷新');
});

test('timeout and transport failures restore refresh without blaming the proxy', async () => {
  const timedOut = setup();
  shown(timedOut);
  timedOut.calls[0].reject(new Error('热点读取超时（25秒）'));
  await settle();
  const timeoutTree = shown(timedOut);
  assert.match(text(timeoutTree), /热点读取超时（25秒）/);
  assert.equal(text(timeoutTree).includes('EASEL_PROXY'), false);
  assert.equal(columns(timeoutTree).length, 3);
  assert.equal(button(timeoutTree, '刷新').props.disabled, false);
  assert.equal(compact(button(timeoutTree, '刷新')), '刷新');

  const failed = setup();
  shown(failed);
  failed.calls[0].reject(new Error('请确认已配置外网代理（EASEL_PROXY）'));
  await settle();
  const failedTree = shown(failed);
  assert.match(text(failedTree), /网络或服务异常/);
  assert.equal(text(failedTree).includes('EASEL_PROXY'), false);
  assert.equal(columns(failedTree).length, 3);
  assert.equal(compact(button(failedTree, '刷新')), '刷新');

  const unavailable = setup();
  shown(unavailable);
  unavailable.calls[0].resolve({
    trends: ['weibo', 'douyin', 'zhihu'].map(platform => ({ platform, label: platform, items: [], ok: false })),
    updated: 0,
  });
  await settle();
  const sourceTree = shown(unavailable);
  assert.match(text(sourceTree), /数据源不可用|数据源故障/);
  assert.equal(text(sourceTree).includes('EASEL_PROXY'), false);
  assert.equal(columns(sourceTree).length, 3);
  assert.equal(compact(button(sourceTree, '刷新')), '刷新');
});

test('a failed favorite stays unsaved and reports the existing error', async () => {
  const app = setup();
  shown(app);
  app.failSave(true);
  app.calls[0].resolve({
    trends: [{ platform: 'weibo', label: '微博', items: [{ title: '可收藏选题', hot: '3', url: '' }], ok: true }],
    updated: 30,
  });
  await settle();
  let tree = shown(app);
  const save = nodes(tree).find(node => node.props?.className === 'trend-save');
  save.props.onClick();
  await settle();
  tree = shown(app);
  const again = nodes(tree).find(node => node.props?.className === 'trend-save');
  assert.equal(again.props.children.type, 'bookmark');
  assert.match(text(tree), /收藏失败/);
});

test('a stale payload is labeled as cache instead of live data', async () => {
  const app = setup();
  shown(app);
  app.calls[0].resolve({
    trends: [{
      platform: 'weibo', label: '微博', ok: true, stale: true, updated_at: 1700000000,
      items: [{ title: '缓存选题', hot: '', url: '' }],
    }, ...['douyin', 'zhihu'].map(platform => ({ platform, label: platform, ok: true, items: [] }))],
    updated: 1700000000,
    stale: true,
  });
  await settle();
  const tree = shown(app);
  assert.match(text(statusLine(tree)), /缓存/);
  assert.equal(text(tree).includes('实时'), false);
  assert.match(text(columns(tree)[0]), /缓存/);
  assert.match(text(tree), /缓存选题/);
});

test('keyword search filters loaded trend titles without reloading the platform data', async () => {
  const app = setup();
  shown(app);
  app.calls[0].resolve({
    trends: [{
      platform: 'weibo', label: '微博', ok: true,
      items: [
        { title: '医疗保险怎么选', hot: '8', url: '' },
        { title: '新款手机发布', hot: '6', url: '' },
      ],
    }],
    updated: 42,
  });
  await settle();
  let tree = shown(app);
  const search = nodes(tree).find(node => node.type === 'input' && node.props?.['aria-label'] === '搜索热点关键词');
  assert.ok(search, '热点雷达应提供关键词搜索框');
  search.props.onChange({ target: { value: '医疗' } });
  tree = shown(app);
  assert.match(text(tree), /医疗保险怎么选/);
  assert.equal(text(tree).includes('新款手机发布'), false);
  assert.equal(app.calls.length, 1);
});

test('insurance IP loads industry sources and dates without invented heat ranks, and preserves full hotlists', async () => {
  const app = setup({ persona: '小保讲明白', personaDescription: '保险科普教学' });
  assert.deepEqual(columns(app.render()).map(compact), ['保险行业正在读取行业动态…', '监管动态正在读取行业动态…', '医保动态正在读取行业动态…']);
  shown(app);
  assert.equal(app.calls[0].kind, 'industry');
  assert.equal(app.calls[0].limit, 30);
  app.calls[0].resolve({ trends: [{ platform: 'insurance', label: '保险行业', ok: true, items: [
    { title: '理赔服务报告', hot: '', url: 'https://example.test/report', source: '中国保险行业协会', published_at: '2026-11-03' },
    { title: '行业服务通知', hot: '', url: 'https://example.test/notice', source: '监管来源', published_at: '' },
  ] }, ...['regulation', 'medical'].map(platform => ({ platform, label: platform, ok: true, items: [] }))], updated: 42 });
  await settle();
  let tree = shown(app);
  assert.match(text(tree), /理赔服务报告/);
  assert.match(text(tree), /中国保险行业协会/);
  assert.match(text(tree), /2026-11-03/);
  assert.match(text(tree), /日期未提供/);
  assert.match(text(statusLine(tree)), /行业动态|政策资讯/);
  assert.equal(text(tree).includes('实时热搜'), false);
  for (const label of ['微博', '抖音', '知乎', 'B站', '百度', '头条']) {
    assert.equal(button(tree, label).props['aria-pressed'], false, '来源控件固定可见，行业态不冒充平台已选');
  }
  const ranks = nodes(tree).filter(node => node.props?.className?.startsWith('trend-rank')).map(node => node.props.children);
  assert.deepEqual(ranks, []);
  const save = nodes(tree).find(node => node.props?.className === 'trend-save');
  await save.props.onClick();
  assert.equal(app.saves[0].source, '中国保险行业协会');
  const beforeScope = app.calls.length;
  button(tree, '全部热点').props.onClick();
  tree = shown(app);
  assert.equal(app.calls.length, beforeScope, '内容范围独立于数据来源，不触发切源');
  assert.equal(app.calls.at(-1).kind, 'industry');
  button(tree, '微博').props.onClick();
  tree = shown(app);
  assert.equal(app.calls.at(-1).kind, 'general');
  assert.equal(app.calls.at(-1).platforms, 'weibo,douyin,zhihu', '保留原默认三平台多选');
  const labelsToKeys = { 微博: 'weibo', 抖音: 'douyin', 知乎: 'zhihu', B站: 'bilibili', 百度: 'baidu', 头条: 'toutiao' };
  assert.equal(app.calls.at(-1).platforms, selectedPlatformLabels(tree).map(label => labelsToKeys[label]).join(','));
  assert.ok(button(tree, 'B站'));
  assert.ok(button(tree, '头条'));
  app.calls.at(-1).resolve({ trends: [{ platform: 'weibo', label: '微博', ok: true, items: [
    { title: '热门游戏赛事', hot: '9', url: '' },
    { title: '医保新政策', hot: '8', url: '' },
  ] }], updated: 43 });
  await settle();
  tree = shown(app);
  assert.match(text(tree), /热门游戏赛事/);
  assert.deepEqual(nodes(tree).filter(node => node.props?.className?.startsWith('trend-rank')).map(node => node.props.children), [1, 2]);
  button(tree, '微博').props.onClick();
  tree = shown(app);
  const retained = selectedPlatformLabels(tree);
  assert.equal(retained.includes('微博'), false);
  chooseIndustry(tree);
  tree = shown(app);
  assert.equal(app.calls.at(-1).kind, 'industry');
  const returnLabel = retained[0] || '微博';
  button(tree, returnLabel).props.onClick();
  tree = shown(app);
  assert.equal(app.calls.at(-1).kind, 'general');
  assert.ok(selectedPlatformLabels(tree).includes(returnLabel));
  for (const label of retained) assert.ok(selectedPlatformLabels(tree).includes(label), '保留多选来源状态');
});

test('industry search matches loaded titles and sources locally', async () => {
  const app = setup({ persona: '保险科普' });
  shown(app);
  app.calls[0].resolve({ trends: [{ platform: 'insurance', label: '保险行业', ok: true, items: [
    { title: '理赔服务报告', hot: '', url: '', source: '中国保险行业协会', published_at: '2026-11-03' },
    { title: '行业服务通知', hot: '', url: '', source: '国家金融监督管理总局', published_at: '' },
  ] }], updated: 42 });
  await settle();
  let tree = shown(app);
  const search = nodes(tree).find(node => node.type === 'input');
  search.props.onChange({ target: { value: '理赔' } });
  tree = shown(app);
  assert.match(text(tree), /理赔服务报告/);
  assert.equal(text(tree).includes('行业服务通知'), false);
  search.props.onChange({ target: { value: '监督管理' } });
  tree = shown(app);
  assert.match(text(tree), /行业服务通知/);
  assert.equal(text(tree).includes('理赔服务报告'), false);
  assert.equal(app.calls.length, 1);
});

test('empty industry groups stay empty and distinguish unavailable sources', async () => {
  const app = setup({ persona: '保险科普·教学示范' });
  shown(app);
  app.calls[0].resolve({ trends: [
    { platform: 'insurance', label: '保险行业', ok: true, items: [] },
    { platform: 'regulation', label: '监管动态', ok: false, items: [] },
    { platform: 'medical', label: '医保动态', ok: true, items: [] },
  ], updated: 42 });
  await settle();
  const tree = shown(app);
  assert.equal(text(tree).includes('热门游戏赛事'), false);
  assert.match(text(columns(tree)[0]), /暂无行业动态/);
  assert.equal(text(columns(tree)[0]).includes('数据源不可用'), false);
  assert.match(text(columns(tree)[1]), /数据源不可用/);
  assert.equal(app.calls.length, 1);
});

test('general mode keeps the full hotlist and does not assume an insurance identity', async () => {
  const app = setup();
  shown(app);
  app.calls[0].resolve({ trends: [{ platform: 'weibo', label: '微博', ok: true,
    items: [{ title: '热门游戏赛事', hot: '9', url: '' }] }], updated: 42 });
  await settle();
  const tree = shown(app);
  assert.match(text(tree), /热门游戏赛事/);
  assert.equal(button(tree, '保险相关').props.disabled, true);
  assert.equal(button(tree, '保险相关').props['aria-pressed'], false);
  assert.equal(button(tree, '全部热点').props['aria-pressed'], true);
  assert.equal(nodes(tree).some(node => node.props?.className === 'trend-current-ip'), false);
  assert.equal(app.calls[0].kind, 'general');
});

for (const mode of ['industry', 'general']) {
  test(`${mode} breakdown entry carries the original article metadata and preserves existing actions`, async () => {
    const breakdowns = [];
    const topics = [];
    const app = setup({
      ...(mode === 'industry' ? { persona: '保险科普' } : {}),
      onBreakdown: item => breakdowns.push(item),
      onUseTopic: title => topics.push(title),
    });
    shown(app);
    const item = {
      title: ' 理赔服务新进展 ', url: 'https://example.test/article?id=7', hot: '',
      source: '中国保险行业协会', published_at: '2026-11-04',
    };
    app.calls[0].resolve({ trends: [{
      platform: mode === 'industry' ? 'insurance' : 'weibo', label: mode === 'industry' ? '保险行业' : '微博',
      ok: true, items: [item],
    }], updated: 42 });
    await settle();
    nodes(shown(app)).find(node => node.type === 'input').props.onChange({ target: { value: '理赔' } });
    let tree = shown(app);
    button(tree, '拆解').props.onClick();
    assert.equal(breakdowns.length, 1);
    for (const field of ['title', 'url', 'source', 'published_at']) assert.equal(breakdowns[0][field], item[field]);
    assert.deepEqual(topics, []);
    button(tree, '做内容').props.onClick();
    assert.deepEqual(topics, [' 理赔服务新进展 ']);
    assert.equal(breakdowns.length, 1);
    const save = nodes(tree).find(node => node.props.className === 'trend-save');
    await save.props.onClick();
    tree = shown(app);
    assert.equal(app.saves[0].title, ' 理赔服务新进展 ');
    assert.ok(button(tree, '拆解'));
    assert.equal(app.calls.length, 1, 'article actions must not reload trends or call a model');
  });
}

test('omitting the optional breakdown handler leaves existing trend actions intact', async () => {
  const app = setup();
  shown(app);
  app.calls[0].resolve({ trends: [{ platform: 'weibo', label: '微博', ok: true,
    items: [{ title: '原有热点', url: '', hot: '10' }] }], updated: 42 });
  await settle();
  const tree = shown(app);
  assert.equal(nodes(tree).some(node => node.type === 'button' && text(node) === '拆解'), false);
  assert.ok(button(tree, '做内容'));
  assert.ok(nodes(tree).find(node => node.props.className === 'trend-save'));
});

test('switching modes or profiles aborts and ignores replies from the previous source', async () => {
  const app = setup({ persona: '保险科普' });
  shown(app);
  const industry = app.calls[0];
  button(shown(app), '微博').props.onClick();
  shown(app);
  const general = app.calls.at(-1);
  assert.equal(industry.signal.aborted, true);
  industry.resolve({ trends: [{ platform: 'insurance', label: '保险行业', ok: true, items: [{ title: '旧行业资讯', hot: '', url: '' }] }], updated: 1 });
  await settle();
  assert.equal(text(shown(app)).includes('旧行业资讯'), false);
  chooseIndustry(shown(app));
  shown(app);
  assert.equal(general.signal.aborted, true);
  general.resolve({ trends: [{ platform: 'weibo', label: '微博', ok: true, items: [{ title: '旧泛热点', hot: '3', url: '' }] }], updated: 2 });
  await settle();
  assert.equal(text(shown(app)).includes('旧泛热点'), false);
  const previousProfile = app.calls.at(-1);
  app.render({ persona: '保险新IP', personaDescription: '医疗保障' });
  shown(app);
  assert.equal(previousProfile.signal.aborted, true);
  assert.equal(app.calls.at(-1).kind, 'industry');
  previousProfile.resolve({ trends: [{ platform: 'insurance', label: '保险行业', ok: true, items: [{ title: '旧画像资讯', hot: '', url: '' }] }], updated: 3 });
  await settle();
  assert.equal(text(shown(app)).includes('旧画像资讯'), false);
});

test('industry loading is independent of unselected general platforms', () => {
  const app = setup({ persona: '保险科普' });
  shown(app);
  button(shown(app), '微博').props.onClick();
  shown(app);
  for (const label of selectedPlatformLabels(shown(app))) button(shown(app), label).props.onClick();
  assert.match(text(shown(app)), /请选择至少一个平台/);
  assert.equal(selectedPlatformLabels(shown(app)).length, 0);
  assert.equal(columns(shown(app)).length, 0);
  chooseIndustry(shown(app));
  const tree = shown(app);
  assert.equal(app.calls.at(-1).kind, 'industry');
  assert.equal(columns(tree).length, 3);
  assert.match(text(tree), /正在读取行业动态/);
  assert.equal(text(tree).includes('请选择至少一个平台'), false);
});

test('industry cache and transport errors remain explicit without claiming live heat', async () => {
  const app = setup({ persona: '保险科普' });
  shown(app);
  app.calls[0].resolve({ trends: [{ platform: 'insurance', label: '保险行业', ok: false, stale: true,
    items: [{ title: '缓存行业资讯', hot: '', url: '', source: '中国保险行业协会', published_at: '2026-11-01' }] }], updated: 42 });
  await settle();
  let tree = shown(app);
  assert.match(text(tree), /缓存行业资讯/);
  assert.match(text(nodes(columns(tree)[0]).find(node => node.props?.className === 'trend-col-head')), /缓存/);
  assert.match(text(statusLine(tree)), /数据源故障/);
  assert.equal(text(tree).includes('实时热搜'), false);
  button(tree, '刷新').props.onClick();
  app.calls.at(-1).reject(new Error('network offline'));
  await settle();
  tree = shown(app);
  assert.match(text(tree), /读取失败/);
  assert.equal(button(tree, '刷新').props.disabled, false);
  assert.equal(nodes(tree).some(node => node.props?.className === 'trend-item'), false);
});

test('fetchInsuranceTrends uses the industry endpoint with bounded timeout and abort', async () => {
  const timers = [];
  const urls = [];
  let hanging = true;
  const sandbox = {
    exports: {}, window: { location: { pathname: '/' } }, AbortController,
    fetch(url) {
      urls.push(url);
      return hanging ? new Promise(() => {}) : Promise.resolve({ ok: true, json: async () => ({ trends: [], updated: 7 }) });
    },
    setTimeout(fn, ms) { const timer = { fn, ms, cleared: false }; timers.push(timer); return timer; },
    clearTimeout(timer) { timer.cleared = true; },
  };
  const compiled = ts.transpileModule(apiSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  runInNewContext(compiled, sandbox);
  assert.equal(typeof sandbox.exports.fetchInsuranceTrends, 'function');
  const pending = sandbox.exports.fetchInsuranceTrends();
  assert.equal(urls[0], '/api/insurance/trends?limit=30');
  assert.equal(timers[0].ms, 25000);
  timers[0].fn();
  await assert.rejects(pending, /超时/);
  assert.equal(timers[0].cleared, true);
  const controller = new AbortController();
  const aborted = sandbox.exports.fetchInsuranceTrends(15, controller.signal);
  assert.equal(urls[1], '/api/insurance/trends?limit=15');
  controller.abort();
  await assert.rejects(aborted, error => error.name === 'AbortError');
  assert.equal(timers[1].cleared, true);
  await assert.rejects(sandbox.exports.fetchInsuranceTrends(15, controller.signal), error => error.name === 'AbortError');
  assert.equal(urls.length, 2);
  hanging = false;
  const result = await sandbox.exports.fetchInsuranceTrends();
  assert.equal(result.updated, 7);
  assert.equal(timers.at(-1).cleared, true);
});

test('fetchTrends times out bounded work and honors an external abort', async () => {
  const timers = [];
  let fetchCalls = 0;
  let hanging = true;
  const sandbox = {
    exports: {},
    window: { location: { pathname: '/' } },
    AbortController,
    fetch(_url, options) {
      fetchCalls += 1;
      return new Promise((resolve) => {
        if (!hanging) resolve({ ok: true, json: async () => ({ trends: [], updated: 7 }) });
        else void options;
      });
    },
    setTimeout(fn, ms) {
      const timer = { fn, ms, cleared: false };
      timers.push(timer);
      return timer;
    },
    clearTimeout(timer) {
      if (timer) timer.cleared = true;
    },
  };
  const compiled = ts.transpileModule(apiSource, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  runInNewContext(compiled, sandbox);
  const pending = sandbox.exports.fetchTrends('weibo', 12);
  const deadline = timers.find(timer => timer.ms === 25000);
  assert.ok(deadline, 'fetchTrends must arm a 25s deadline');
  deadline.fn();
  await assert.rejects(pending, /热点读取超时（25秒）/);
  assert.equal(deadline.cleared, true);
  assert.equal(fetchCalls, 1);

  const controller = new AbortController();
  const aborted = sandbox.exports.fetchTrends('douyin', 15, controller.signal);
  const externalTimer = timers.filter(timer => timer.ms === 25000 && !timer.cleared).at(-1);
  controller.abort();
  await assert.rejects(aborted, error => error.name === 'AbortError');
  assert.equal(externalTimer.cleared, true);

  hanging = false;
  const result = await sandbox.exports.fetchTrends('zhihu');
  assert.deepEqual(result.trends, []);
  assert.equal(result.updated, 7);
  assert.equal(timers.at(-1).cleared, true);
});
