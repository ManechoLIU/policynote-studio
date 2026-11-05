import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const popupSource = readFileSync(new URL('../../browser-extension/popup.js', import.meta.url), 'utf8');
const freshId = '123e4567-e89b-42d3-a456-426614174000';
const freshRegistrationId = 'workbench-' + freshId;
const bound = '工作台已绑定。请刷新工作台页面；小红书登录状态需另行确认。';

function harness({ originValue, store, granted = true, scripts = [], setImpl }) {
  let handler;
  const elements = { origin: { value: originValue }, status: { textContent: '' }, connect: { addEventListener(_, fn) { handler = fn; } } };
  const state = structuredClone(store);
  const registered = scripts.map(script => structuredClone(script));
  const calls = { register: [], unregister: [], set: [], request: [] };
  const chrome = {
    permissions: { request: async (permission) => { calls.request.push(structuredClone(permission)); return granted; } },
    storage: { local: {
      get: async () => structuredClone(state),
      set: async (value) => {
        calls.set.push(structuredClone(value));
        if (setImpl) await setImpl(value);
        Object.assign(state, structuredClone(value));
      },
    } },
    scripting: {
      getRegisteredContentScripts: async () => structuredClone(registered),
      registerContentScripts: async (scriptsToAdd) => { calls.register.push(structuredClone(scriptsToAdd)); registered.push(...scriptsToAdd); },
      unregisterContentScripts: async (filter) => { calls.unregister.push(structuredClone(filter)); },
    },
  };
  vm.runInNewContext(popupSource, { URL, chrome, document: { getElementById: (id) => elements[id] }, crypto: { randomUUID: () => freshId } });
  return { click: () => handler(), elements, calls, state };
}

test('stored origin with missing content-script registration is repaired before success', async () => {
  const app = harness({
    originValue: 'http://127.0.0.1:7862',
    store: { origins: ['http://127.0.0.1:7862'], registrations: { 'http://127.0.0.1:7862': 'workbench-kept' } },
    scripts: [],
  });
  await app.click();
  assert.equal(app.calls.register.length, 1);
  assert.equal(app.calls.register[0][0].id, 'workbench-kept');
  assert.deepEqual(app.calls.register[0][0].matches, ['http://127.0.0.1:7862/*']);
  assert.deepEqual(app.calls.register[0][0].js, ['relay.js']);
  assert.equal(app.calls.register[0][0].allFrames, false);
  assert.equal(app.calls.register[0][0].runAt, 'document_start');
  assert.equal(app.calls.register[0][0].persistAcrossSessions, true);
  assert.equal(app.elements.status.textContent, bound);
  assert.deepEqual(app.state.origins, ['http://127.0.0.1:7862']);
  assert.deepEqual(app.calls.request[0].origins, ['http://127.0.0.1:7862/*']);
  assert.equal(app.calls.request[0].permissions, undefined);
});

test('same host different ports keep exact-origin allowlist and port-exact relay match', async () => {
  const app = harness({
    originValue: 'http://127.0.0.1:7863',
    store: { origins: ['http://127.0.0.1:7862'], registrations: { 'http://127.0.0.1:7862': 'shared-id' } },
    scripts: [{ id: 'shared-id', matches: ['http://127.0.0.1/*'], js: ['relay.js'] }],
  });
  await app.click();
  assert.equal(app.calls.register.length, 1);
  assert.equal(app.calls.register[0][0].id, freshRegistrationId);
  assert.deepEqual(app.calls.register[0][0].matches, ['http://127.0.0.1:7863/*']);
  assert.equal(app.calls.register[0][0].runAt, 'document_start');
  assert.equal(app.calls.unregister.length, 0);
  assert.deepEqual(app.state.origins, ['http://127.0.0.1:7862', 'http://127.0.0.1:7863']);
  assert.equal(app.state.registrations['http://127.0.0.1:7862'], 'shared-id');
  assert.equal(app.state.registrations['http://127.0.0.1:7863'], freshRegistrationId);
  assert.deepEqual(app.calls.request[0].origins, ['http://127.0.0.1:7863/*']);
});

test('same host with a lost relay repairs once and still records the exact new origin', async () => {
  const app = harness({
    originValue: 'http://localhost:7861',
    store: { origins: ['http://localhost:7860'], registrations: { 'http://localhost:7860': 'existing-id' } },
    scripts: [],
  });
  await app.click();
  assert.equal(app.calls.register.length, 1);
  assert.equal(app.calls.register[0][0].id, freshRegistrationId);
  assert.deepEqual(app.calls.register[0][0].matches, ['http://localhost:7861/*']);
  assert.equal(app.calls.register[0][0].runAt, 'document_start');
  assert.deepEqual(app.state.origins, ['http://localhost:7860', 'http://localhost:7861']);
  assert.equal(app.state.registrations['http://localhost:7860'], 'existing-id');
  assert.equal(app.state.registrations['http://localhost:7861'], freshRegistrationId);
});

test('an origin without permission is not stored or reported bound', async () => {
  const app = harness({
    originValue: 'https://work.example',
    store: { origins: ['https://work.example'], registrations: { 'https://work.example': 'workbench-kept' } },
    granted: false,
    scripts: [],
  });
  await app.click();
  assert.equal(app.calls.register.length, 0);
  assert.equal(app.calls.set.length, 0);
  assert.equal(app.elements.status.textContent, '未授予所需权限。');
  assert.deepEqual(app.state.origins, ['https://work.example']);
});

test('a live registration for the same origin is force-rebound after Reload', async () => {
  const app = harness({
    originValue: 'http://127.0.0.1:7862',
    store: { origins: ['http://127.0.0.1:7862'], registrations: { 'http://127.0.0.1:7862': 'workbench-kept' } },
    scripts: [{ id: 'workbench-kept', matches: ['http://127.0.0.1:7862/*'], js: ['relay.js'], runAt: 'document_idle' }],
  });
  await app.click();
  assert.equal(app.calls.unregister.length, 1);
  assert.equal(app.calls.register.length, 1);
  assert.deepEqual(app.calls.register[0][0].matches, ['http://127.0.0.1:7862/*']);
  assert.equal(app.calls.register[0][0].runAt, 'document_start');
  assert.equal(app.elements.status.textContent, bound);
  assert.deepEqual(app.state.origins, ['http://127.0.0.1:7862']);
});
