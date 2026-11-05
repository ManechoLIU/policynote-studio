try{document.documentElement.setAttribute('data-mingya-relay','1');}catch(e){}
(() => {
  if (window.top !== window || globalThis.__mingyaRelayInstalledV1 === true) return;
  globalThis.__mingyaRelayInstalledV1 = true;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  window.addEventListener('message', event => {
    const m = event.data;
    if (event.source !== window || event.origin !== window.location.origin || !m || typeof m !== 'object' || m.channel !== 'mingya-workbench' || m.version !== 1 || !uuid.test(m.id || '') || !['status','open','draft','review_prepare','review'].includes(m.action)) return;
    if (Object.keys(m).some(k => !['channel','version','id','action','payload'].includes(k))) return;
    if (!['draft','review'].includes(m.action) && 'payload' in m) return;
    if (m.action === 'review' && (!m.payload || typeof m.payload !== 'object' || Array.isArray(m.payload) || Object.keys(m.payload).length!==1 || !uuid.test(m.payload.reviewToken || ''))) return;
    chrome.runtime.sendMessage({channel:m.channel,version:1,id:m.id,action:m.action,...(['draft','review'].includes(m.action) ? {payload:m.payload} : {})}).then(result => {
      window.postMessage({channel:'mingya-browser',version:1,id:m.id,result}, window.location.origin);
    }).catch(() => {
      window.postMessage({channel:'mingya-browser',version:1,id:m.id,result:{ok:false,state:'unavailable',message:'浏览器扩展未连接。',published:false}}, window.location.origin);
    });
  });
})();
