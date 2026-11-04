import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/ManualPublishPage.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
});

function textOf(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (typeof node === 'object' && node.props) return textOf(node.props.children);
  return '';
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object' || !tree.props) return [];
  return [tree, ...nodes(tree.props.children)];
}

function setup(draft = {}) {
  const saved = { title: '标题', body: '正文', platforms: ['xiaohongshu', 'douyin'], overrides: {}, tags: '话题', ...draft };
  const state = [];
  const refs = [];
  const effects = [];
  let cursor = 0;
  let pending = [];
  const createElement = (type, props) => ({ type, props });
  const icon = new Proxy({}, { get: () => 'icon' });
  const modules = {
    react: {
      useState(initial) {
        const index = cursor++;
        if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
        return [state[index], (value) => {
          state[index] = typeof value === 'function' ? value(state[index]) : value;
        }];
      },
      useRef(value) {
        const index = cursor++;
        if (!(index in refs)) refs[index] = { current: value };
        return refs[index];
      },
      useEffect(callback, dependencies = []) {
        const index = cursor++;
        const previous = effects[index];
        if (!previous || dependencies.some((value, position) => value !== previous.dependencies[position])) {
          pending.push(() => {
            previous?.cleanup?.();
            effects[index] = { dependencies, cleanup: callback() };
          });
        }
      },
    },
    'react/jsx-runtime': { jsx: createElement, jsxs: createElement },
    '../lib/api': {
      createSchedule: () => Promise.resolve({}),
      executeSkill: () => Promise.resolve({ response: '' }),
      exportManualPackage: () => Promise.reject(new Error('test must not export')),
      fetchOutputs: () => Promise.resolve([]),
      mediaUrl: (path) => path,
      runAgent: () => Promise.resolve({ response: '' }),
      streamChat: () => ({ abort() {} }),
    },
    '../lib/store': {
      loadPublishDraft: () => saved,
      savePublishDraft: () => {},
    },
    '../lib/sanitize': { renderMarkdown: (value) => value },
    './MingyaPrimitives': { ReferenceArtwork: 'artwork' },
    '../styles/mingya-publish.css': {},
    './icons': icon,
  };
  const exports = {};
  runInNewContext(outputText, { exports, require: (name) => modules[name] });
  return {
    render() {
      cursor = 0;
      pending = [];
      const tree = exports.default({ persona: 'persona' });
      pending.forEach((callback) => callback());
      return tree;
    },
  };
}

function reviewBox(tree) {
  const review = nodes(tree).find((node) => node.props.className === 'mingya-publish-review');
  const boxes = nodes(review).filter((node) => node.type === 'input' && node.props.type === 'checkbox');
  assert.equal(boxes.length, 1);
  return boxes[0];
}

function card(tree, key) {
  return nodes(tree).find((node) => node.props.className === `card pv-card pv-${key}`);
}

function control(tree, key, label) {
  return nodes(card(tree, key)).find((node) => (node.type === 'button' || node.type === 'a') && textOf(node).includes(label));
}

test('当前平台审核绑定右侧卡，切换与改稿后失效', () => {
  const view = setup();
  let tree = view.render();
  assert.equal(nodes(tree).filter((node) => node.type === 'input' && node.props.type === 'checkbox').length, 1);
  for (const key of ['xiaohongshu', 'douyin']) {
    assert.equal(nodes(card(tree, key)).some((node) => node.type === 'input' && node.props.type === 'checkbox'), false);
  }
  let box = reviewBox(tree);
  assert.equal(box.props['aria-label'], '小红书人工审核');
  assert.equal(box.props.disabled, false);
  assert.equal(box.props.checked, false);
  assert.equal(control(tree, 'xiaohongshu', '复制标题').props.disabled, true);
  assert.equal(control(tree, 'xiaohongshu', '导出稿件和素材 ZIP').props.disabled, true);
  assert.equal(control(tree, 'xiaohongshu', '审核后打开官方发布页').props.disabled, true);

  box.props.onChange({ target: { checked: true } });
  tree = view.render();
  box = reviewBox(tree);
  assert.equal(box.props.checked, true);
  assert.equal(control(tree, 'xiaohongshu', '复制标题').props.disabled, false);
  assert.equal(control(tree, 'xiaohongshu', '导出稿件和素材 ZIP').props.disabled, false);
  assert.equal(control(tree, 'xiaohongshu', '打开官方发布页').props.href, 'https://creator.xiaohongshu.com/publish/publish?source=official');
  assert.equal(control(tree, 'douyin', '复制标题').props.disabled, true);
  assert.equal(control(tree, 'douyin', '审核后打开官方发布页').props.disabled, true);

  nodes(tree).find((node) => node.type === 'select').props.onChange({ target: { value: 'douyin' } });
  tree = view.render();
  box = reviewBox(tree);
  assert.equal(box.props['aria-label'], '抖音人工审核');
  assert.equal(box.props.checked, false);
  assert.equal(control(tree, 'xiaohongshu', '复制标题').props.disabled, false);

  nodes(tree).find((node) => node.type === 'select').props.onChange({ target: { value: 'xiaohongshu' } });
  tree = view.render();
  nodes(card(tree, 'xiaohongshu')).find((node) => node.type === 'button' && textOf(node).includes('编辑此平台版本')).props.onClick();
  tree = view.render();
  box = reviewBox(tree);
  assert.equal(box.props.disabled, true);
  assert.equal(box.props.checked, true);
  nodes(tree).find((node) => node.type === 'textarea' && node.props['aria-label'] === '小红书正文').props.onChange({ target: { value: '改过的单平台正文' } });
  tree = view.render();
  box = reviewBox(tree);
  assert.equal(box.props.checked, false);
  nodes(card(tree, 'xiaohongshu')).find((node) => node.type === 'button' && textOf(node).includes('完成编辑')).props.onClick();
  tree = view.render();
  box = reviewBox(tree);
  assert.equal(box.props.disabled, false);
  assert.equal(box.props.checked, false);
  assert.equal(control(tree, 'xiaohongshu', '复制全文').props.disabled, true);
  assert.equal(control(tree, 'xiaohongshu', '导出稿件和素材 ZIP').props.disabled, true);

  box.props.onChange({ target: { checked: true } });
  tree = view.render();
  nodes(tree).find((node) => node.props.id === 'manual-title').props.onChange({ target: { value: '新标题' } });
  tree = view.render();
  assert.equal(reviewBox(tree).props.checked, false);
  assert.equal(control(tree, 'xiaohongshu', '复制正文').props.disabled, true);
});

test('未选平台与空文案不能审核', () => {
  const emptyPlatforms = setup({ platforms: [] });
  let tree = emptyPlatforms.render();
  let box = reviewBox(tree);
  assert.equal(box.props['aria-label'], '人工审核');
  assert.equal(box.props.disabled, true);
  assert.equal(box.props.checked, false);
  assert.equal(nodes(tree).find((node) => node.type === 'select').props.disabled, true);
  box.props.onChange({ target: { checked: true } });
  tree = emptyPlatforms.render();
  nodes(tree).find((node) => node.type === 'button' && textOf(node).includes('小红书')).props.onClick();
  tree = emptyPlatforms.render();
  box = reviewBox(tree);
  assert.equal(box.props['aria-label'], '小红书人工审核');
  assert.equal(box.props.checked, false);
  assert.equal(control(tree, 'xiaohongshu', '复制标题').props.disabled, true);

  const blank = setup({ title: '  ', body: '', platforms: ['xiaohongshu'], overrides: {}, tags: '' });
  tree = blank.render();
  box = reviewBox(tree);
  assert.equal(box.props.disabled, true);
  assert.equal(box.props.checked, false);
  assert.equal(control(tree, 'xiaohongshu', '导出稿件和素材 ZIP').props.disabled, true);
});
