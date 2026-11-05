function allowedOrigin(raw) {
  try {
    const u = new URL(raw);
    if (u.username || u.password || u.hostname.includes('*') || u.pathname !== '/' || u.search || u.hash || u.origin === 'null') return null;
    const loopback = ['localhost','127.0.0.1','[::1]'].includes(u.hostname);
    if (u.protocol !== 'https:' && !(u.protocol === 'http:' && loopback)) return null;
    return u.origin;
  } catch {return null;}
}

const RECEIPT_STATES = {
  in_flight: '执行中',
  unknown: '结果待核对',
  reviewed: '已人工核对',
  not_sent: '未执行保存',
  completed: '已完成',
};
const RECEIPT_PHASES = {
  attach: '连接页面', view_open: '打开草稿箱', view_tab: '切换草稿标签', view_close: '关闭草稿箱',
  ready: '准备任务', tab: '切换发布类型', upload: '上传图片', upload_state: '检查上传状态',
  fill: '填写内容', baseline: '记录草稿基线', save_probe: '检查保存入口', save: '触发保存', confirm: '确认保存',
  receipt: '核对保存结果', task: '浏览器任务',
};
const RECEIPT_DIAGNOSTICS = {
  location: '页面地址不匹配', document: '页面文档已变化', page: '页面状态不符合预期', view: '草稿箱状态未确认',
  identity: '账号身份未确认', box: '草稿箱结构未确认', hit: '草稿记录匹配未确认', viewport: '目标不在可操作区域',
  changed: '页面状态已变化', unknown: '结果未确认', upload: '上传状态未确认', node: '页面控件未确认',
  ambiguous: '页面存在多个候选', stopped: '任务已停止', browser_error: '浏览器操作未确认',
  adapter_unconfirmed: '适配器结果未确认', tab_url: '发布页地址未确认', tab_create: '创建发布页失败',
  tab_wait: '发布页加载未完成', tab_closed: '发布页已关闭', task_throw: '浏览器任务异常',
};

function isReceipt(row) {
  if (!row || typeof row !== 'object' || Array.isArray(row)) return false;
  if (allowedOrigin(row.origin) !== row.origin || typeof row.id !== 'string' || !row.id || !Number.isInteger(row.ownerTabId)) return false;
  if (!['open','draft'].includes(row.action) || !Object.hasOwn(RECEIPT_STATES, row.state) || typeof row.action_sent !== 'boolean') return false;
  if (![row.createdAt, row.updatedAt].every(value => Number.isFinite(value) && value >= 0)) return false;
  if (row.phase !== undefined && !Object.hasOwn(RECEIPT_PHASES, row.phase)) return false;
  if (row.diagnostic !== undefined && !Object.hasOwn(RECEIPT_DIAGNOSTICS, row.diagnostic)) return false;
  return true;
}

function readRecoverySnapshot(raw, input) {
  const origin = allowedOrigin(input);
  if (!origin || !raw || !Array.isArray(raw.origins) || raw.origins.some(value => allowedOrigin(value) !== value) || !raw.origins.includes(origin)) return null;
  const records = raw.jobReceipts === undefined ? [] : raw.jobReceipts;
  if (!Array.isArray(records) || records.some(row => !isReceipt(row))) return null;
  let latest = null;
  let latestUnknown = null;
  for (const row of records) {
    if (row.origin !== origin) continue;
    if (!latest || row.updatedAt >= latest.updatedAt) latest = row;
    if (row.state === 'unknown' && (!latestUnknown || row.updatedAt >= latestUnknown.updatedAt)) latestUnknown = row;
  }
  return {origin, records, latest, latestUnknown};
}

const recoveryStatus = document.getElementById('receipt-status');
const refreshRecoveryButton = document.getElementById('refresh-receipt');
const reviewUnknownButton = document.getElementById('review-unknown');
const originInput = document.getElementById('origin');

function recoveryMessage(snapshot) {
  if (!snapshot.latest) return '当前工作台没有任务记录。';
  const row = snapshot.latest;
  const phase = row.phase === undefined ? '未记录' : RECEIPT_PHASES[row.phase];
  const diagnostic = row.diagnostic === undefined ? '未记录' : RECEIPT_DIAGNOSTICS[row.diagnostic];
  return `状态：${RECEIPT_STATES[row.state]}；阶段：${phase}；诊断：${diagnostic}；更新时间：${row.updatedAt}`;
}

async function refreshRecovery() {
  if (!recoveryStatus || !refreshRecoveryButton || !reviewUnknownButton || !originInput) return null;
  reviewUnknownButton.disabled = true;
  try {
    const raw = await chrome.storage.local.get(['origins','jobReceipts']);
    const snapshot = readRecoverySnapshot(raw, originInput.value.trim());
    if (!snapshot) {recoveryStatus.textContent = '无法读取已授权工作台的任务记录。'; return null;}
    recoveryStatus.textContent = recoveryMessage(snapshot);
    reviewUnknownButton.disabled = !snapshot.latestUnknown;
    return snapshot;
  } catch {
    recoveryStatus.textContent = '无法读取已授权工作台的任务记录。';
    return null;
  }
}

if (refreshRecoveryButton && reviewUnknownButton && recoveryStatus && originInput) {
  refreshRecoveryButton.addEventListener('click', refreshRecovery);
  originInput.addEventListener('input', () => {
    reviewUnknownButton.disabled = true;
    recoveryStatus.textContent = '请刷新查看当前工作台的最近任务。';
  });
  reviewUnknownButton.addEventListener('click', async () => {
    reviewUnknownButton.disabled = true;
    const before = await refreshRecovery();
    if (!before?.latestUnknown) return;
    const accepted = confirm('此操作不会重试，也不会自动保存。请先确认草稿箱中是否出现新草稿；只有完成核对后才解除提交保护。');
    if (!accepted) return;
    try {
      const receipts = before.records
        .filter(row => row.origin === before.origin && row.state === 'unknown')
        .map(row => ({id:row.id, updatedAt:row.updatedAt}));
      const reply = await chrome.runtime.sendMessage({channel:'mingya-popup', action:'review_unknown', origin:before.origin, receipts});
      if (reply?.ok === true) await refreshRecovery();
      else recoveryStatus.textContent = '任务状态已变化，无法解除；请刷新后重新核对。';
    } catch {
      recoveryStatus.textContent = '解除提交保护失败；保护仍保持。';
    }
  });
  refreshRecovery();
}

document.getElementById('connect').addEventListener('click', async () => {
  const status = document.getElementById('status');
  const origin = allowedOrigin(document.getElementById('origin').value.trim());
  if (!origin) {status.textContent = '请输入 HTTPS 工作台来源；仅本机回环允许 HTTP。'; return;}
  try {
    const parsed = new URL(origin);
    const matchPattern = origin + '/*'; // include port (127.0.0.1:7862) so dynamic scripts actually match
    const granted = await chrome.permissions.request({origins:[matchPattern]});
    if (!granted) {status.textContent = '未授予所需权限。'; return;}
    const {origins = [], registrations = {}} = await chrome.storage.local.get(['origins','registrations']);
    const scripts = await chrome.scripting.getRegisteredContentScripts();
    const live = (Array.isArray(scripts) ? scripts : []).find(script => script && Array.isArray(script.matches) && script.matches.includes(matchPattern));
    const ownedByOtherOrigin = id => Object.entries(registrations).some(([key, value]) => key !== origin && value === id);
    const liveId = live && !ownedByOtherOrigin(live.id) ? live.id : null;
    const storedId = registrations[origin] && !ownedByOtherOrigin(registrations[origin]) ? registrations[origin] : null;
    const id = liveId || storedId || ('workbench-' + crypto.randomUUID());
    let registeredNow = false;
    // After unpacked Reload, persisted dynamic scripts can be stale/non-injecting; force rebind.
    // Also drop legacy hostname-only matches (http://127.0.0.1/*) that may not inject on :7862.
    const staleIds = (Array.isArray(scripts) ? scripts : [])
      .filter(script => script && script.id === id && Array.isArray(script.matches) && script.matches.some(m => typeof m === 'string' && (m === matchPattern || m === (parsed.protocol + '//' + parsed.hostname + '/*'))))
      .map(script => script.id)
      .filter(Boolean);
    if (staleIds.length) {
      try { await chrome.scripting.unregisterContentScripts({ids:staleIds}); } catch {}
    }
    await chrome.scripting.registerContentScripts([{id, matches:[matchPattern], js:['relay.js'], allFrames:false, runAt:'document_start', persistAcrossSessions:true}]);
    registeredNow = true;
    const known = origins.includes(origin);
    if (!known || registrations[origin] !== id) {
      try {await chrome.storage.local.set({origins: known ? origins : [...origins, origin], registrations:{...registrations, [origin]: id}});}
      catch (e) {if (registeredNow) await chrome.scripting.unregisterContentScripts({ids:[id]}); throw e;}
    }
    status.textContent = '工作台已绑定。请刷新工作台页面；小红书登录状态需另行确认。';
    try {
      if (chrome.tabs?.query && chrome.scripting?.executeScript) {
        const tabs = await chrome.tabs.query({url: [matchPattern, parsed.protocol + '//' + parsed.hostname + '/*']});
        for (const tab of tabs) {
          if (!Number.isInteger(tab?.id)) continue;
          try { await chrome.scripting.executeScript({target:{tabId:tab.id}, files:['relay.js']}); } catch {}
        }
      }
    } catch {}

    await refreshRecovery();
  } catch {status.textContent = '连接未完成，请检查扩展权限后重试。';}
});
