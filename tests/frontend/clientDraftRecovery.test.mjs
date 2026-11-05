import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../browser-extension/popup.js', import.meta.url), 'utf8');

function element(value = '') {
  const listeners = {};
  return {
    value,
    textContent: '',
    disabled: false,
    addEventListener(type, listener) { listeners[type] = listener; },
    fire(type) { return listeners[type]?.(); },
  };
}

function harness(store, origin = 'https://work.example', { confirmResult = true, reply = { ok: true } } = {}) {
  const state = structuredClone(store);
  const calls = { get: 0, set: [], confirm: [], messages: [] };
  const elements = {
    origin: element(origin), status: element(), connect: element(),
    'receipt-status': element(), 'refresh-receipt': element(), 'review-unknown': element(),
  };
  elements['review-unknown'].disabled = true;
  const chrome = {
    storage: { local: {
      async get() { calls.get++; return structuredClone(state); },
      async set(value) { calls.set.push(structuredClone(value)); Object.assign(state, structuredClone(value)); },
    } },
    permissions: { request: async () => false },
    scripting: { getRegisteredContentScripts: async () => [] },
    runtime: { sendMessage: async message => {
      calls.messages.push(structuredClone(message));
      if (reply.ok === true) state.jobReceipts = state.jobReceipts.map(row => row.origin === message.origin && row.state === 'unknown' ? { ...row, state: 'reviewed' } : row);
      return structuredClone(reply);
    } },
  };
  const confirm = message => { calls.confirm.push(message); return confirmResult; };
  vm.runInNewContext(source, { URL, chrome, document: { getElementById: id => elements[id] }, crypto: { randomUUID: () => 'id' }, confirm });
  return { state, calls, elements, refresh: () => elements['refresh-receipt'].fire('click'), review: () => elements['review-unknown'].fire('click') };
}

const receipt = (overrides = {}) => ({
  origin: 'https://work.example', id: 'a', ownerTabId: 1, action: 'draft', state: 'unknown',
  action_sent: true, phase: 'upload', diagnostic: 'browser_error', createdAt: 1000, updatedAt: 2000,
  ...overrides,
});

test('refresh shows only allowlisted metadata from the latest receipt for the exact authorized origin', async () => {
  const app = harness({
    origins: ['https://work.example', 'https://other.example'],
    jobReceipts: [receipt(), receipt({ origin: 'https://other.example', phase: 'receipt', updatedAt: 9000 })],
  });
  assert.equal(app.elements['review-unknown'].disabled, true);
  await app.refresh();
  assert.match(app.elements['receipt-status'].textContent, /状态：结果待核对/);
  assert.match(app.elements['receipt-status'].textContent, /阶段：上传图片/);
  assert.match(app.elements['receipt-status'].textContent, /诊断：浏览器操作未确认/);
  assert.match(app.elements['receipt-status'].textContent, /更新时间：2000/);
  assert.doesNotMatch(app.elements['receipt-status'].textContent, /other|9000|a/);
  assert.equal(app.elements['review-unknown'].disabled, false);
});

test('producer task and adapter-unconfirmed metadata remain reviewable without exposing raw fields', async () => {
  const app = harness({origins:['https://work.example'],jobReceipts:[receipt({phase:'task',diagnostic:'adapter_unconfirmed',err:'PRIVATE_DETAIL'})]});
  await app.refresh();
  assert.match(app.elements['receipt-status'].textContent, /阶段：浏览器任务/);
  assert.match(app.elements['receipt-status'].textContent, /诊断：适配器结果未确认/);
  assert.doesNotMatch(app.elements['receipt-status'].textContent, /adapter_unconfirmed|PRIVATE_DETAIL/);
  assert.equal(app.elements['review-unknown'].disabled, false);
  await app.review();
  assert.equal(app.calls.messages.length, 1);
});

test('review confirms the manual-only boundary and sends an immutable unknown snapshot to background', async () => {
  const target = receipt({ extra: 7 });
  const olderUnknown = receipt({ id: 'older', updatedAt: 1500 });
  const inspection = receipt({ id: 'inspection', action: 'open', state: 'completed', phase: undefined, diagnostic: undefined, updatedAt: 2500 });
  const other = receipt({ id: 'c', origin: 'https://other.example', state: 'in_flight', updatedAt: 3000 });
  const app = harness({ origins: ['https://work.example', 'https://other.example'], jobReceipts: [target, olderUnknown, inspection, other] });
  await app.refresh();
  assert.match(app.elements['receipt-status'].textContent, /状态：已完成/);
  assert.equal(app.elements['review-unknown'].disabled, false);
  await app.review();
  assert.equal(app.calls.confirm.length, 1);
  assert.match(app.calls.confirm[0], /不会重试，也不会自动保存/);
  assert.match(app.calls.confirm[0], /先确认草稿箱中是否出现新草稿/);
  assert.equal(app.calls.get >= 3, true);
  assert.equal(app.calls.set.length, 0);
  assert.deepEqual(app.calls.messages[0], {
    channel: 'mingya-popup', action: 'review_unknown', origin: 'https://work.example',
    receipts: [{ id: 'a', updatedAt: 2000 }, { id: 'older', updatedAt: 1500 }],
  });
  assert.equal(app.state.jobReceipts.length, 4);
  assert.deepEqual(app.state.jobReceipts[0], { ...target, state: 'reviewed' });
  assert.deepEqual(app.state.jobReceipts[1], { ...olderUnknown, state: 'reviewed' });
  assert.deepEqual(app.state.jobReceipts[2], inspection);
  assert.deepEqual(app.state.jobReceipts[3], other);
  assert.equal(app.elements['review-unknown'].disabled, true);
});

test('a stale popup rereads and cannot review a record that became in-flight', async () => {
  const app = harness({ origins: ['https://work.example'], jobReceipts: [receipt()] });
  await app.refresh();
  app.state.jobReceipts[0].state = 'in_flight';
  await app.review();
  assert.equal(app.calls.set.length, 0);
  assert.equal(app.calls.messages.length, 0);
  assert.equal(app.elements['review-unknown'].disabled, true);
  assert.match(app.elements['receipt-status'].textContent, /状态：执行中/);
});

test('cancel and a newly entered origin never send a review request', async () => {
  const cancelled = harness({ origins: ['https://work.example'], jobReceipts: [receipt()] }, 'https://work.example', { confirmResult: false });
  await cancelled.review();
  assert.equal(cancelled.calls.messages.length, 0);
  const changed = harness({ origins: ['https://work.example'], jobReceipts: [receipt()] });
  await changed.refresh();
  changed.elements.origin.value = 'https://new.example';
  await changed.review();
  assert.equal(changed.calls.messages.length, 0);
});

test('unknown storage schema and unlisted metadata fail closed without exposing raw values', async () => {
  for (const jobReceipts of [
    { bad: true },
    [receipt({ diagnostic: 'PRIVATE_SECRET' })],
    [receipt({ updatedAt: 'yesterday' })],
  ]) {
    const app = harness({ origins: ['https://work.example'], jobReceipts });
    await app.refresh();
    assert.equal(app.elements['review-unknown'].disabled, true);
    assert.doesNotMatch(app.elements['receipt-status'].textContent, /PRIVATE_SECRET|yesterday/);
    await app.review();
    assert.equal(app.calls.set.length, 0);
    assert.equal(app.calls.messages.length, 0);
  }
});

test('popup markup provides explicit refresh and protected review controls without HTML injection sinks', () => {
  const html = readFileSync(new URL('../../browser-extension/popup.html', import.meta.url), 'utf8');
  assert.match(html, /id="receipt-status"/);
  assert.match(html, /id="refresh-receipt"/);
  assert.match(html, /id="review-unknown"[^>]*disabled/);
  assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML/);
});
