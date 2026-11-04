import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ['handleExternalResearch', 'handleBreakdownTopic', 'handleBreakdownConsumed', 'handleRewriteBreakdown'];
const declarations = new Map();
function visit(node) {
  if (ts.isVariableDeclaration(node) && names.includes(node.name.getText(ast))) {
    declarations.set(node.name.getText(ast), node.getText(ast));
  }
  ts.forEachChild(node, visit);
}
visit(ast);
assert.equal(declarations.size, names.length);
const executable = names.map(name => `const ${declarations.get(name)};`).join('\n')
  + '\nglobalThis.handlers = {' + names.join(',') + '};';
const compiled = ts.transpileModule(executable, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;

function setup(persona = '保险科普·教学示范') {
  const calls = { sessions: [], streams: [], pages: [], saved: [] };
  let request = null;
  let sessions = [];
  const context = {
    selectedPersona: persona,
    breakdownSequence: { current: 0 },
    consumedRewrites: { current: new Set() },
    useCallback: callback => callback,
    setBreakdownRequest: value => { request = typeof value === 'function' ? value(request) : value; },
    setCurrentPage: value => calls.pages.push(value),
    createSession: value => { calls.sessions.push(value); return { id: 'new-chat', persona: value }; },
    setSessions: update => { sessions = update(sessions); },
    saveSessions: value => calls.saved.push(value),
    setActiveSessionId: value => { calls.active = value; },
    sendUserAndStream: (...values) => calls.streams.push(values),
  };
  runInNewContext(compiled, context);
  return { handlers: context.handlers, calls, request: () => request };
}

test('trend breakdown transfers original metadata and current IP without starting a chat', () => {
  const app = setup();
  const item = { title: '保险新规', url: 'https://source.test/news', source: '官方来源', published_at: '2026-11-04' };
  app.handlers.handleBreakdownTopic(item);
  const first = app.request();
  assert.equal(first.persona, '保险科普·教学示范');
  for (const value of Object.values(item)) assert.ok(first.input.includes(value));
  assert.match(first.input, /不代表已读取原文/);
  assert.equal(app.calls.pages[0], 'breakdown');
  assert.equal(app.calls.streams.length, 0);
  app.handlers.handleBreakdownTopic(item);
  assert.notEqual(app.request().id, first.id);
  app.handlers.handleBreakdownConsumed(first.id);
  assert.ok(app.request(), 'an old consume cannot erase a newer request');
  app.handlers.handleBreakdownConsumed(app.request().id);
  assert.equal(app.request(), null);
});

test('rewrite uses the existing persisted chat flow with source, analysis and current IP', () => {
  const app = setup();
  const payload = { requestId: 'analysis-1', input: '原始材料', result: '结构拆解', persona: '保险科普·教学示范' };
  app.handlers.handleRewriteBreakdown(payload);
  app.handlers.handleRewriteBreakdown(payload);
  assert.equal(app.calls.sessions.length, 1);
  assert.equal(app.calls.streams.length, 1);
  assert.equal(app.calls.sessions[0], '保险科普·教学示范');
  assert.equal(app.calls.saved.length, 1);
  assert.equal(app.calls.active, 'new-chat');
  assert.equal(app.calls.pages[0], 'chat');
  const [id, prompt] = app.calls.streams[0];
  assert.equal(id, 'new-chat');
  for (const value of ['原始材料', '结构拆解', '保险科普·教学示范', '原创措辞', '不自动发布']) assert.ok(prompt.includes(value));
});

test('stale IP, empty analysis and empty source cannot start rewrite; general mode stays general', () => {
  const app = setup('新IP');
  for (const payload of [
    { input: '原文', result: '分析', persona: '旧IP' },
    { input: '', result: '分析', persona: '新IP' },
    { input: '原文', result: ' ', persona: '新IP' },
  ]) app.handlers.handleRewriteBreakdown({ requestId: 'old-analysis', ...payload });
  assert.equal(app.calls.streams.length, 0);
  const general = setup('');
  general.handlers.handleRewriteBreakdown({ requestId: 'general-analysis', input: '原文', result: '分析', persona: '' });
  assert.equal(general.calls.sessions[0], undefined);
  assert.match(general.calls.streams[0][1], /通用科普风格/);
});
