import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/PublishPage.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
});
const settle = () => new Promise(resolve => setImmediate(resolve));

function setup(platforms = ['xiaohongshu'], responses = {}, { loggedIn = true, confirm = true } = {}) {
  const state = [], refs = [], effects = [], confirmations = [], calls = [], schedules = [];
  let cursor = 0;
  let pending = [];
  let prechecks = 0;
  let polls = 0;
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
    '../lib/store': {
      loadPublishDraft: () => ({ title: '待发标题', body: '待发正文', platforms, overrides: {}, tags: '保险' }),
      savePublishDraft() {},
    },
    '../lib/api': {
      fetchAccounts: async () => platforms.map(platform => ({ platform, loggedIn })),
      fetchOutputs: async () => [{ type: 'file', kind: 'video', path: 'clip.mp4', name: 'clip.mp4' }],
      runAgent: async () => { prechecks++; return { response: '未见明显风险' }; },
      executeSkill: async () => ({ response: '人设一致' }),
      publishNow: async (platform, payload) => {
        calls.push({ platform, payload });
        if (responses[platform] instanceof Error) throw responses[platform];
        return responses[platform] ?? { ok: true, message: '完成' };
      },
      publishStatus: async () => { throw new Error('draft submission must not poll public-publish status'); },
      createSchedule: async payload => { schedules.push(payload); return {}; },
      mediaUrl: path => path,
    },
    '../lib/sanitize': { renderMarkdown: value => value },
    './MingyaPrimitives': { ReferenceArtwork: 'artwork' },
    '../styles/mingya-publish.css': {},
    './icons': new Proxy({}, { get: () => 'icon' }),
  };
  const exports = {};
  runInNewContext(outputText, {
    exports, Error, require: name => modules[name],
    window: { confirm(message) { confirmations.push(message); return confirm; } },
    setTimeout() {},
    setInterval() { polls++; throw new Error('XHS draft receipt must not enter asynchronous public-publish polling'); },
  });
  return {
    calls, confirmations, schedules, prechecks: () => prechecks, polls: () => polls,
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
const submitButton = tree => nodes(tree).find(node => node.type === 'button' && /^(存小红书草稿|存公众号草稿|存平台草稿|提交所选平台|一键发布)$/.test(text(node).trim()));
const status = (tree, platform) => nodes(nodes(tree).find(node => node.props.className === `card pv-card pv-${platform}`)).find(node => node.props.className?.startsWith('pv-pubstate'));
async function ready(app, withMedia = true) {
  app.render();
  await settle();
  if (withMedia) {
    button(app.render(), '选择视频').props.onClick();
    nodes(app.render()).find(node => node.props.className === 'media-cell ').props.onClick();
  }
  return app.render();
}

test('XHS-only action and confirmation save a platform draft without promising public publication', async () => {
  const app = setup(['xiaohongshu'], { xiaohongshu: { ok: true, outcome: 'draft_saved', published: false, message: '已保存至小红书草稿，未公开发布' } });
  const tree = await ready(app);
  assert.match(text(tree), /仅存.*小红书.*草稿/);
  assert.equal(text(submitButton(tree)).trim(), '存小红书草稿');
  await submitButton(tree).props.onClick();
  assert.equal(app.prechecks(), 1);
  assert.match(app.confirmations[0], /小红书.*草稿/);
  assert.match(app.confirmations[0], /未公开发布|不会公开发布/);
  assert.equal(app.confirmations[0].includes('【真实发布】'), false);
  const result = status(app.render(), 'xiaohongshu');
  assert.equal(result.props.className, 'pv-pubstate ok');
  assert.match(text(result), /已保存至小红书草稿，未公开发布/);
  assert.equal(text(result).includes('已发布'), false);
  assert.equal(app.calls[0].platform, 'xiaohongshu');
  assert.equal(app.calls[0].payload.media[0], 'clip.mp4');
});

test('missing or inconsistent XHS receipts never show draft success or public success', async () => {
  for (const receipt of [
    { ok: true, message: '已发布' },
    { ok: true, outcome: 'draft_saved', message: '已发布' },
    { ok: true, outcome: 'draft_saved', published: true, message: '已发布' },
    { ok: true, outcome: 'draft_saved', published: true, errorCode: 'DRAFT_BUTTON_NOT_FOUND', message: '已发布' },
    { ok: true, outcome: 'published', published: false, message: '已发布' },
    { ok: false, outcome: 'failed', published: false, message: '无草稿证据' },
    { ok: true, async: true, message: '已开始' },
  ]) {
    const app = setup(['xiaohongshu'], { xiaohongshu: receipt });
    const tree = await ready(app);
    await submitButton(tree).props.onClick();
    const result = status(app.render(), 'xiaohongshu');
    assert.equal(result.props.className, 'pv-pubstate fail');
    assert.equal(text(result).includes('已发布'), false);
    assert.equal(text(result).includes('已保存'), false);
    if (receipt.ok === true) assert.match(text(result), /未确认保存至小红书草稿/);
    assert.equal(app.polls(), 0);
  }
});

test('explicit XHS draft failure shows the safe cause and warns against repeat submissions', async () => {
  const app = setup(['xiaohongshu'], { xiaohongshu: {
    ok: false, outcome: 'failed', published: false, errorCode: 'DRAFT_BUTTON_NOT_FOUND',
    message: '图片已上传，但未找到保存草稿按钮，尚未确认保存成功',
    detail: '/private/runtime/session.log: stack trace and CDP details',
  } });
  await submitButton(await ready(app)).props.onClick();
  const tree = app.render();
  const result = status(tree, 'xiaohongshu');
  assert.equal(result.props.className, 'pv-pubstate fail');
  assert.match(text(result), /图片已上传，但未找到保存草稿按钮/);
  assert.match(text(result), /请先检查创作者后台草稿，勿重复提交/);
  assert.equal(text(result).includes('/private/'), false);
  assert.equal(text(result).includes('CDP'), false);
  assert.equal(submitButton(tree).props.disabled, false);
  assert.equal(app.calls.length, 1);
  assert.equal(app.polls(), 0);
});

test('network rejection restores the XHS submit action without retrying or implying success', async () => {
  let reject;
  const pending = new Promise((_resolve, fail) => { reject = fail; });
  const app = setup(['xiaohongshu'], { xiaohongshu: pending });
  const task = submitButton(await ready(app)).props.onClick();
  await settle();
  assert.equal(button(app.render(), '提交中').props.disabled, true);
  reject(new Error('网络连接中断，草稿保存结果未知'));
  await task;
  const tree = app.render();
  assert.equal(submitButton(tree).props.disabled, false);
  const result = status(tree, 'xiaohongshu');
  assert.equal(result.props.className, 'pv-pubstate fail');
  assert.match(text(result), /网络连接中断/);
  assert.equal(text(result).includes('已保存'), false);
  assert.equal(app.calls.length, 1);
  assert.equal(app.prechecks(), 1);
  assert.equal(app.polls(), 0);
});

test('mixed confirmation separates public targets from XHS and WeChat draft targets', async () => {
  const app = setup(['xiaohongshu', 'douyin', 'wechat-oa'], {
    xiaohongshu: { ok: true, outcome: 'draft_saved', published: false, message: '' },
    douyin: { ok: true, message: 'published' },
    'wechat-oa': { ok: true, message: '已创建草稿' },
  });
  const tree = await ready(app);
  assert.match(text(submitButton(tree)), /提交/);
  await submitButton(tree).props.onClick();
  assert.match(app.confirmations[0], /公开发布[：:]抖音/);
  assert.match(app.confirmations[0], /平台草稿[：:]小红书、公众号/);
  assert.match(app.confirmations[0], /草稿.*不会公开发布/);
  const current = app.render();
  assert.match(text(status(current, 'xiaohongshu')), /已保存至小红书草稿/);
  assert.match(text(status(current, 'wechat-oa')), /公众号草稿/);
  assert.equal(text(status(current, 'wechat-oa')).includes('已发布'), false);
  assert.match(text(status(current, 'douyin')), /已发布/);
  assert.equal(app.calls.length, 3);
});

test('local scheduling is labeled separately and never submits a platform draft', async () => {
  const app = setup();
  const tree = await ready(app);
  assert.match(text(tree), /本地.*草稿.*不.*平台草稿/);
  await button(tree, '本地草稿并排期').props.onClick();
  assert.equal(app.schedules.length, 1);
  assert.equal(app.schedules[0].status, 'draft');
  assert.equal(app.calls.length, 0);
  assert.match(text(app.render()), /本地排期草稿/);
});

test('cancel, login and media gates remain effective for XHS drafts', async () => {
  const cancelled = setup(['xiaohongshu'], {}, { confirm: false });
  await submitButton(await ready(cancelled)).props.onClick();
  assert.equal(cancelled.calls.length, 0);
  const loggedOut = setup(['xiaohongshu'], {}, { loggedIn: false });
  await submitButton(await ready(loggedOut)).props.onClick();
  assert.equal(loggedOut.calls.length, 0);
  assert.match(text(status(loggedOut.render(), 'xiaohongshu')), /未登录/);
  const missingMedia = setup();
  await submitButton(await ready(missingMedia, false)).props.onClick();
  assert.equal(missingMedia.calls.length, 0);
  assert.match(text(status(missingMedia.render(), 'xiaohongshu')), /需附带/);
});
