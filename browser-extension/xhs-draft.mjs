// Fixed, narrowly scoped page functions; caller content is passed only as CDP arguments.
const LIMIT = 179000; // Reserve up to one second for debugger detach within the 180s bound.
const result = (state, message, receiptKind) => ({ok: state === 'saved' || state === 'record_found', state, message, published:false, ...(receiptKind ? {receiptKind} : {})});
const unknown = () => result('unconfirmed', '草稿结果未确认，请先查看草稿箱，勿重复提交。');
const fail = () => result('error', '未能安全完成草稿操作，请检查当前浏览器页面。');
const okURL = url => { try { const u=new URL(url); return u.protocol==='https:' && u.host==='creator.xiaohongshu.com' && u.pathname==='/publish/publish' && !u.username && !u.password; } catch { return false; } };

function pageStep(action, data) {
  if (location.origin !== 'https://creator.xiaohongshu.com' || location.pathname !== '/publish/publish') return {state:'location'};
  const visible=e=>!!e && e.isConnected && e.getClientRects().length>0 && (()=>{for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);if(n.hidden||s.display==='none'||s.visibility!=='visible'||Number(s.opacity)<0.01||n.getAttribute('aria-hidden')==='true')return false;}return true;})();
  const safe=e=>{for(let n=e;n;n=n.parentElement){const ce=n.getAttribute('contenteditable');if((ce!==null&&ce!=='false')||['INPUT','TEXTAREA'].includes(n.tagName)||n.getAttribute('role')==='textbox'||/editor|editable|preview/i.test(n.className||''))return false;}return true;};
  const all=(sel)=>[...document.querySelectorAll(sel)].filter(visible);
  if (all('.login-container,.login-modal,.login-box,.sso-login-wrapper,.qrcode-img').length) return {state:'auth'};
  if (action==='tab' || action==='ready') {
    const tabs=all('div.creator-tab').filter(e=>(e.innerText||'').trim()==='上传图文' && safe(e) && (()=>{const r=e.getBoundingClientRect();return r.width>0 && r.height>0 && r.left>=0 && r.top>=0 && r.right<=innerWidth && r.bottom<=innerHeight;})());
    if(tabs.length!==1)return {state:action==='ready' && tabs.length===0?'pending':'unknown'}; if(action==='ready')return {state:'ok'}; tabs[0].click(); return {state:'ok'};
  }
  if(action==='upload') {
    // Sequential one-image upload. priorCount is the number of already-ready slots.
    const prior=Number.isInteger(data.priorCount)?data.priorCount:0;
    const images=Array.isArray(data.images)?data.images:[];
    if(images.length<1 || (prior>0 && images.length!==1)) return {state:'unknown'};
    const expectCount=prior+images.length;
    const slotsNow=[...document.querySelectorAll('.img-preview-area .pr')];
    if(slotsNow.length>expectCount) return {state:'unknown'};
    const prev=window.__mingyaDraftUpload, ours=prev&&prev.document===document;
    // Retry after a prior bind: platform already grew slots → ok. Existing previews without our bind stay unknown.
    if(slotsNow.length===expectCount && ours && prev.expectCount===expectCount) return {state:'ok'};
    if(prior===0){ if(slotsNow.length) return {state:'unknown'}; }
    else if(slotsNow.length!==prior) return slotsNow.length<prior?{state:'pending'}:{state:'unknown'};
    const after=()=>{const n=document.querySelectorAll('.img-preview-area .pr').length;return n===prior?{state:'pending'}:n===expectCount?{state:'ok'}:{state:'unknown'};};
    // A consumed FileList can be cleared or replaced before its preview mounts. Never resubmit it.
    if(ours && prev.priorCount===prior && prev.expectCount===expectCount) return after();
    const acceptOk=e=>{const accept=(e.getAttribute('accept')||'').toLowerCase().split(',').map(x=>x.trim()).filter(Boolean);return accept.length>0 && accept.every(x=>['image/*','image/png','image/jpeg','image/webp','.png','.jpg','.jpeg','.webp'].includes(x));};
    const inputs=[...document.querySelectorAll('input[type=file]')].filter(e=>e.isConnected && !e.disabled && acceptOk(e));
    // The editor has two adjacent inputs: multiple appends, single replaces the selected image.
    const selector=prior===0?'input.upload-input[multiple]':'.img-list > .top > input[multiple]';
    const valid=inputs.filter(e=>e.matches(selector));
    if(valid.length===0)return {state:'pending'};
    if(valid.length!==1)return {state:'unknown'};
    if(prior===0){
      const fields=[...document.querySelectorAll('div.d-input input,input[placeholder*="标题"],div.ql-editor,div.editor-container [contenteditable="true"]')];
      if(fields.some(e => String(e.value || e.textContent || '').trim())) return {state:'unknown'};
    }
    const files=new DataTransfer();
    for(const image of images){const raw=atob(image.base64);const bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);files.items.add(new File([bytes],image.name,{type:image.mime}));}
    valid[0].files=files.files;
    const signature=[...valid[0].files].map(f=>[f.name,f.type,f.size,f.lastModified]);
    if(signature.length!==images.length) return {state:'unknown'};
    window.__mingyaDraftUpload={document,input:valid[0],signature,priorCount:prior,expectCount};
    valid[0].dispatchEvent(new Event('change',{bubbles:true}));
    return after();
  }
  if(action==='upload_state') {
    const upload=window.__mingyaDraftUpload;
    if(!upload || upload.document!==document) return {state:'unknown'};
    if(upload.expectCount!==data.count && upload.signature.length!==data.count) return {state:'unknown'};
    const slots=[...document.querySelectorAll('.img-preview-area .pr')];
    if(slots.length>data.count)return {state:'unknown'};
    // Platform often replaces the input node or clears input.files before preview slots mount.
    // Keep pending until slots catch up or the outer adapter timeout fires.
    if(slots.length!==data.count)return {state:'pending'};
    // Edit button sits under a hover-mask (ancestor opacity 0 until hover). Require the node, not visible().
    for(const slot of slots){const text=slot.innerText||'';if(text.includes('上传失败')||text.includes('重试'))return {state:'unknown'};const imgs=[...slot.querySelectorAll('img.img.preview')],controls=[...slot.querySelectorAll('.image-editor-control .image-editor-actions.has-editor-info button.edit-btn')];if(slot.querySelector('.processing-container,.progress-container,.spinner-red,.prerender,.mask.uploading')||imgs.length!==1||controls.length!==1||!visible(slot)||!visible(imgs[0])||!imgs[0].complete||imgs[0].naturalWidth<=0||imgs[0].naturalHeight<=0||controls[0].disabled||controls[0].getAttribute('aria-disabled')==='true')return {state:'pending'};}
    return {state:'ok'};
  }
  if(action==='fill') {
    const titles=all('div.d-input input,input[placeholder*="标题"]');
    if(titles.length===0) return {state:'pending'};
    // Prefer Quill / editor-container; never treat image-editor contenteditable as the body.
    let editors=all('div.ql-editor');
    if(editors.length!==1) editors=all('div.editor-container [contenteditable="true"]');
    if(editors.length!==1) editors=all('[contenteditable="true"]').filter(e=>!e.closest('.img-preview-area,.image-editor,.image-editor-control'));
    if(titles.length!==1||editors.length!==1||titles[0].disabled||titles[0].readOnly)return {state:'unknown'};
    const title=titles[0], editor=editors[0];
    if(data.part==='title'){title.focus();title.select();return {state:'ok'};}
    if(data.part==='body'){
      editor.focus();const selection=window.getSelection();if(!selection)return {state:'unknown'};
      const range=document.createRange();range.selectNodeContents(editor);selection.removeAllRanges();selection.addRange(range);
      return {state:'ok'};
    }
    if(data.part!=='verify')return {state:'unknown'};
    editor.blur();title.blur();
    // ProseMirror creates one paragraph per inserted line, including empty paragraphs.
    const children=[...editor.children];
    const body=children.length && children.every(e=>e.tagName==='P')?children.map(e=>e.textContent||'').join('\n'):editor.innerText;
    if(title.value!==data.title || body!==data.body)return {state:'pending'};
    const previews=all('.image-area .scroll-container > .title');
    if(previews.length>1)return {state:'unknown'};
    if(previews.length===0 || previews[0].textContent!==data.title)return {state:'pending'};
    if(all('div.title-container div.max_suffix,div.edit-container div.length-error').length)return {state:'unknown'};
    return {state:'ok'};
  }
  const success=/^(?:草稿保存成功|保存草稿成功|草稿已保存|已保存草稿|已存为草稿|已保存至草稿箱|草稿保存完成)[！!。]?$/;
  const officialNoticeSelector='body > div > .d-new-toast > div > .d-toast-wrapper > .d-toast-notice';
  if(action==='baseline') {
    const text=(document.body?.textContent||'').replace(/\s+/g,'');
    if(['草稿保存成功','保存草稿成功','草稿已保存','已保存草稿','已存为草稿','已保存至草稿箱','草稿保存完成'].some(x=>text.includes(x)) || all('div.creator-publish-popover,[role=dialog]').length)return {state:'unknown'};
    const notices=[...document.querySelectorAll('.d-message,.d-toast'),...document.querySelectorAll(officialNoticeSelector)];
    window.__mingyaDraftReceipt={document, notices:new WeakSet(notices)};return {state:'ok'};
  }
  if(action==='save_probe') {
    const buttons=all('button').filter(e=>/^(暂存离开|存草稿|保存草稿)$/.test((e.innerText||'').trim()) && safe(e) && !e.disabled && e.getAttribute('aria-disabled')!=='true');
    return {state:buttons.length===0?'none':buttons.length===1?'one':'unknown'};
  }
  if(action==='save') {
    const receipt=window.__mingyaDraftReceipt;
    if(!receipt||receipt.document!==document)return {state:'unknown'};
    const buttons=all('button').filter(e=>/^(暂存离开|存草稿|保存草稿)$/.test((e.innerText||'').trim()) && safe(e) && !e.disabled && e.getAttribute('aria-disabled')!=='true');
    if(buttons.length!==1)return {state:'unknown'};
    buttons[0].click();return {state:'ok'};
  }
  if(action==='confirm') {
    const dialogs=all('div.creator-publish-popover,[role=dialog]');
    if(dialogs.length!==1 || !safe(dialogs[0]) || dialogs[0].querySelector('input,textarea,[contenteditable],[role=textbox]'))return {state:'unknown'};
    const buttons=[...dialogs[0].querySelectorAll('button,[role="button"]')].filter(visible);
    const label=e=>{const t=(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim(), a=(e.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();return t&&a&&t!==a?'':t||a;};
    const labels=buttons.map(label), yes=['离开','确认离开','保存并离开','暂存离开','存草稿','保存草稿'], no=['取消','继续编辑','返回'];
    if(!labels.length||labels.some(x=>!yes.includes(x)&&!no.includes(x)))return {state:'unknown'};
    const prompts=['是否保存草稿','是否保存草稿并离开','是否将当前内容保存为草稿并离开','当前内容将保存至草稿箱，是否离开','当前内容将保存为草稿，是否离开','是否暂存并离开','是否暂存当前内容并离开'];
    const compact=(dialogs[0].innerText||'').replace(/\s+/g,''), suffix=labels.join('');
    if(!prompts.some(p=>['','？','?','。'].some(end=>compact===p+end+suffix)))return {state:'unknown'};
    const candidates=buttons.filter((e,i)=>yes.includes(labels[i]));
    if(candidates.length!==1||!safe(candidates[0])||candidates[0].disabled||candidates[0].getAttribute('aria-disabled')==='true')return {state:'unknown'};
    candidates[0].click();return {state:'ok'};
  }
  if(action==='receipt') {
    const receipt=window.__mingyaDraftReceipt;
    if(!receipt||receipt.document!==document)return {state:'unknown'};
    if(all('div.creator-publish-popover,[role=dialog]').length)return {state:'dialog'};
    const official=all(officialNoticeSelector);
    const exactOfficial=e=>{
      const icons=[...e.querySelectorAll('span.d-toast-icon.d-toast-icon-success')];
      const descriptions=[...e.querySelectorAll('div.d-toast-main div.d-toast-content > span.d-toast-description')];
      return icons.length===1 && descriptions.length===1 && visible(icons[0]) && visible(descriptions[0])
        && (descriptions[0].innerText||descriptions[0].textContent||'').trim()==='保存成功'
        && (e.innerText||e.textContent||'').trim()==='保存成功';
    };
    if(official.some(e=>!receipt.notices.has(e) && safe(e) && !e.querySelector('input,textarea,[contenteditable],[role=textbox]') && exactOfficial(e)))return {state:'saved'};
    const notices=all('body > .d-message,body > .d-toast');
    if(notices.some(e=>!receipt.notices.has(e) && safe(e) && !e.querySelector('input,textarea,[contenteditable],[role=textbox]') && success.test((e.innerText||'').replace(/\s+/g,' ').trim())))return {state:'saved'};
    return {state:'pending'};
  }
  return {state:'unknown'};
}

const SHADOW_ACTION = 'function(expectedLabel, click, expectedHost) {\n    const allowed = new Set([\'暂存离开\', \'存草稿\', \'保存草稿\']);\n    const clean = value => (value || \'\').replace(/\\s+/g, \' \').trim();\n    const labelOf = node => {\n        const text = clean(node.innerText || node.textContent);\n        const aria = clean(node.getAttribute(\'aria-label\'));\n        return text && aria && text !== aria ? \'\' : (text || aria);\n    };\n    const root = this.getRootNode();\n    const host = root && root.host;\n    const label = labelOf(this);\n    const semantic = this.tagName === \'BUTTON\' || this.getAttribute(\'role\') === \'button\';\n    const valid = this.isConnected && location.origin === \'https://creator.xiaohongshu.com\'\n        && location.pathname === \'/publish/publish\' && semantic && allowed.has(label)\n        && label === expectedLabel && root.mode === \'closed\'\n        && host && host === expectedHost && host.tagName === \'XHS-PUBLISH-BTN\' && host.isConnected\n        && !this.matches(\'input,textarea,[contenteditable]\');\n    const visible = node => {\n        const rect = node.getBoundingClientRect();\n        if (!rect.width || !rect.height || !node.getClientRects().length\n                || rect.bottom <= 0 || rect.right <= 0\n                || rect.top >= innerHeight || rect.left >= innerWidth) return false;\n        for (let el = node; el; el = el.parentElement || el.getRootNode().host) {\n            const style = getComputedStyle(el);\n            if (el.hidden || style.display === \'none\' || style.visibility !== \'visible\'\n                    || Number(style.opacity) === 0) return false;\n        }\n        return true;\n    };\n    let enabled = valid;\n    for (let el = this; el; el = el.parentElement || el.getRootNode().host) {\n        if (el.disabled || el.hasAttribute(\'disabled\') || el.getAttribute(\'aria-disabled\') === \'true\'\n                || el.hasAttribute(\'inert\') || getComputedStyle(el).pointerEvents === \'none\'\n                || el.matches(\'input,textarea,[contenteditable],[role=\\"textbox\\"]\') || /editor|editable|preview/i.test(el.className || \'\')) enabled = false;\n    }\n    const state = {valid: !!valid, label, visible: !!valid && visible(this), enabled: !!enabled, clicked: false};\n    if (!click || !state.valid || !state.visible || !state.enabled) return state;\n    // Check again in the same synchronous call that invokes the semantic action.\n    const matches = [...root.querySelectorAll(\'button,[role="button"]\')]\n        .filter(node => allowed.has(labelOf(node)) && visible(node));\n    if (matches.length !== 1 || matches[0] !== this) return {...state, valid: false};\n    HTMLElement.prototype.click.call(this);\n    return {...state, clicked: true};\n}';
function flatten(root) {
  const stack=[root], out=[];
  while(stack.length){const node=stack.pop();if(!node || out.length>=50000)throw new Error('tree');out.push(node);stack.push(...(node.children||[]),...(node.shadowRoots||[]));}return out;
}
async function shadowCandidates(command) {
  const root=(await command('DOM.getDocument',{depth:-1,pierce:true}))?.root;
  if(root?.nodeType!==9||root.nodeName!=='#document'||!okURL(root.documentURL))throw new Error('document');
  const found=[];
  for(const host of flatten(root)) {
    if(host.nodeType!==1||host.nodeName!=='XHS-PUBLISH-BTN'||host.localName!=='xhs-publish-btn')continue;
    for(const shadow of host.shadowRoots||[]){if(shadow.shadowRootType!=='closed')continue;
      for(const node of flatten(shadow)){
        const attrs={};for(let i=0;i<(node.attributes||[]).length;i+=2)attrs[node.attributes[i]]=node.attributes[i+1];
        if(node.nodeName!=='BUTTON')continue;
        const text=flatten(node).filter(x=>x.nodeType===3).map(x=>x.nodeValue||'').join('').replace(/\s+/g,' ').trim();
        const aria=(attrs['aria-label']||'').replace(/\s+/g,' ').trim();const label=text&&aria&&text!==aria?'':text||aria;
        if(!['暂存离开','存草稿','保存草稿'].includes(label))continue;
        if(!Number.isInteger(node.backendNodeId)||!Number.isInteger(host.backendNodeId))throw new Error('identity');
        found.push({node:node.backendNodeId,host:host.backendNodeId,label});
      }
    }
  }
  return found;
}

const FIND_VIEW = "action => {\n  if (location.origin !== 'https://creator.xiaohongshu.com' || location.pathname !== '/publish/publish') return null;\n  const visible = e => !!e && !!e.getClientRects().length && (() => {\n    for (let n=e; n; n=n.parentElement) { const s=getComputedStyle(n); if (n.hidden || s.display==='none' || s.visibility!=='visible' || Number(s.opacity)===0) return false; } return true;\n  })();\n  const safe = e => { for (let n=e; n; n=n.parentElement) {\n    const ce=n.getAttribute('contenteditable'), role=n.getAttribute('role');\n    if ((ce !== null && ce !== 'false') || n.isContentEditable || ['INPUT','TEXTAREA'].includes(n.tagName) || role==='textbox' || /editor|editable|preview/i.test(n.className || '')) return false;\n  } return true; };\n  const all=(root, sel)=>[...root.querySelectorAll(sel)].filter(visible);\n  const text=e=>String(e.textContent || '').trim();\n  const drawers=all(document, 'div.d-drawer.d-drawer-right.draft-drawer');\n  if (action==='open') {\n    if (drawers.length !== 0) return null;\n    const headers=all(document, 'div.header > div.header-draft');\n    if (headers.length !== 1) return null;\n    const h=headers[0], labels=all(h, ':scope > div.draft-title-box > span.draft-title');\n    if (labels.length!==1 || !safe(h) || !safe(labels[0]) || !/^草稿箱\\(\\d+\\)$/.test(text(labels[0]))) return null;\n    return labels[0];\n  }\n  if (drawers.length !== 1 || !safe(drawers[0])) return null;\n  if (action === 'close') {\n    const close=all(drawers[0], 'span.d-drawer-close.d-clickable');\n    return close.length === 1 && safe(close[0]) ? close[0] : null;\n  }\n  if (action !== 'tab') return null;\n  const contents=all(drawers[0], ':scope > div.d-drawer-content');\n  if (contents.length!==1) return null;\n  const tabs=all(contents[0], 'span.tab-item-text').filter(e=>/^图文笔记\\(\\d+\\)$/.test(text(e)));\n  if (tabs.length!==1 || !safe(tabs[0])) return null;\n  const tab=tabs[0].parentElement;\n  return tab && tab.tagName==='DIV' && tab.classList.contains('tab-item') && safe(tab) ? tabs[0] : null;\n}";
const READ_VIEW = "({action, target}) => {\n  if (location.origin !== 'https://creator.xiaohongshu.com' || location.pathname !== '/publish/publish') return {error:'unexpected_location'};\n  const visible = e => !!e && !!e.getClientRects().length && (() => {\n    for (let n=e; n; n=n.parentElement) {\n      const s=getComputedStyle(n);\n      if (n.hidden || s.display==='none' || s.visibility!=='visible' || Number(s.opacity)===0) return false;\n    } return true;\n  })();\n  const safe = e => {\n    for (let n=e; n; n=n.parentElement) {\n      const ce=n.getAttribute('contenteditable'), role=n.getAttribute('role');\n      if ((ce !== null && ce !== 'false') || n.isContentEditable || ['INPUT','TEXTAREA'].includes(n.tagName) || role==='textbox' || /editor|editable|preview/i.test(n.className || '')) return false;\n    } return true;\n  };\n  const txt = e => String(e.textContent || '').trim();\n  const all = (root, sel) => [...root.querySelectorAll(sel)].filter(visible);\n  const drawers = all(document, 'div.d-drawer.d-drawer-right.draft-drawer');\n  if (drawers.length > 1) return {error:'unknown_structure'};\n  if (!drawers.length) return {pending:true};\n  if (!safe(drawers[0])) return {error:'unknown_structure'};\n  const contents=all(drawers[0], ':scope > div.d-drawer-content');\n  if (contents.length !== 1) return {error:'unknown_structure'};\n  const content=contents[0];\n  const tabs=all(content, 'span.tab-item-text').filter(e => /^图文笔记\\(\\d+\\)$/.test(txt(e)));\n  if (tabs.length !== 1 || tabs[0].parentElement.tagName !== 'DIV' || !tabs[0].parentElement.classList.contains('tab-item') || !safe(tabs[0])) return {error:'unknown_structure'};\n  const tab=tabs[0].parentElement;\n  if (!tab.classList.contains('active')) return {error:'unknown_structure'};\n  const lists=all(content, '.draft-list');\n  if (lists.length !== 1 || !safe(lists[0])) return {error:'unknown_structure'};\n  const rows=all(lists[0], ':scope > .draft-item');\n  const count=Number(txt(tabs[0]).match(/\\((\\d+)\\)$/)[1]);\n  if (!Number.isSafeInteger(count) || count !== rows.length) return {error:'unknown_structure'};\n  const extra=[...lists[0].children].filter(e => visible(e) && !e.classList.contains('draft-item'));\n  if (extra.length) {\n    const empty=extra[0], subtitles=all(empty, '.d-result-subtitle');\n    if (count !== 0 || extra.length !== 1 || !empty.classList.contains('empty-state') || !safe(empty) || subtitles.length !== 1 || txt(subtitles[0]) !== '暂无草稿' || txt(empty) !== '暂无草稿' || [...empty.querySelectorAll('*')].some(e => !safe(e))) return {error:'unknown_structure'};\n  }\n  let found=[];\n  for (const row of rows) {\n    const infos=all(row, ':scope > .draft-info');\n    if (infos.length !== 1) return {error:'unknown_structure'};\n    const descs=all(infos[0], ':scope > .draft-info-desc');\n    if (descs.length !== 1 || !safe(descs[0]) || [...descs[0].querySelectorAll('*')].some(e => !safe(e))) return {error:'unknown_structure'};\n    const titles=all(descs[0], ':scope > .draft-title-text');\n    if (titles.length !== 1 || (!titles[0].classList.contains('has-title') && txt(titles[0]) !== '暂无笔记标题')) return {error:'unknown_structure'};\n    const times=[...txt(descs[0]).matchAll(/保存于\\s*(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/g)];\n    const controls=all(infos[0], '*').filter(e => ['编辑','删除'].includes(txt(e)) && ![...e.children].some(c => txt(c) === txt(e)));\n    if (times.length !== 1 || controls.filter(e=>txt(e)==='编辑').length !== 1 || controls.filter(e=>txt(e)==='删除').length !== 1) return {error:'unknown_structure'};\n    if (!titles[0].classList.contains('has-title') || txt(titles[0]) !== target) continue;\n    const classes=[...row.classList].filter(c => /^[A-Za-z_][A-Za-z0-9_-]{0,40}$/.test(c) && !/token|secret|session|cookie/i.test(c)).slice(0,8);\n    const images=all(row, 'img').slice(0,6).map(e=>({loaded:e.complete === true, natural_width:e.naturalWidth, natural_height:e.naturalHeight}));\n    found.push({title:target, saved_at:times[0][1].replace(/\\s+/g,' '), record:{tag:row.tagName, classes}, images});\n  }\n  if (found.length > 1) return {error:'unknown_structure'};\n  return {ok:true, count, match:found[0] || null};\n}";
const FRESH_VIEW = "() => {\n  const visible = e => !!e.getClientRects().length && (() => {\n    for (let n=e; n; n=n.parentElement) { const s=getComputedStyle(n); if(n.hidden || s.display==='none' || s.visibility!=='visible' || Number(s.opacity)===0) return false; } return true;\n  })();\n  return ![...document.querySelectorAll('textarea,input:not([type=\"file\"]),[contenteditable]:not([contenteditable=\"false\"]),[role=\"textbox\"]')].some(visible);\n}";

function validJob(job) {
  if(!job || typeof job.title!=='string' || !job.title.trim() || job.title.length>20 || typeof job.body!=='string' || !Array.isArray(job.tags) || job.tags.length>20 || !Array.isArray(job.images) || job.images.length<1 || job.images.length>18)return false;
  if(job.tags.some(t=>typeof t!=='string'||t.length>100||/[\r\n]/.test(t)))return false;
  let total=0;
  for(const image of job.images){if(!image||typeof image.name!=='string'||image.name.length>255||!['image/png','image/jpeg','image/webp'].includes(image.mime)||typeof image.base64!=='string'||!image.base64||image.base64.length%4||!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(image.base64))return false;const size=image.base64.length/4*3-(image.base64.endsWith('==')?2:image.base64.endsWith('=')?1:0);if(size<=0||size>10*1024*1024)return false;total+=size;}
  return total<=30*1024*1024 && (job.body + job.tags.map(t=>t.trim().replace(/^#+/,'')).filter(Boolean).map(t=>' #'+t).join('')).length<=1000;
}

export async function runXhsDraft({tabId,job,chromeApi,onProgress=()=>{},signal}) {
  if(!Number.isInteger(tabId)||!validJob(job)||!chromeApi?.debugger||!chromeApi?.tabs)return fail();
  let attached=false, sent=false, stopped=false, timer, abortListener, phaseName='attach';
  const target={tabId};
  const stop=()=>{stopped=true;};
  const interrupted=new Promise(resolve=>{timer=setTimeout(()=>{stop();resolve('timeout');},LIMIT);abortListener=()=>{stop();resolve('abort');};signal?.addEventListener('abort',abortListener,{once:true});if(signal?.aborted)abortListener();});
  const check=()=>{if(stopped)throw new Error('stopped');};
  const work=(async()=>{
    check(); const tab=await chromeApi.tabs.get(tabId);check();if(!okURL(tab?.url))return fail();
    await chromeApi.debugger.attach(target,'1.3');attached=true;check();
    const call=async(action,data={})=>{
      phaseName=action;onProgress(action);check();const tabNow=await chromeApi.tabs.get(tabId);check();if(!okURL(tabNow?.url))throw new Error('location');
      const root=await chromeApi.debugger.sendCommand(target,'Runtime.evaluate',{expression:'document',returnByValue:false});check();
      if(!root?.result?.objectId)throw new Error('document');
      const response=await chromeApi.debugger.sendCommand(target,'Runtime.callFunctionOn',{objectId:root.result.objectId,functionDeclaration:pageStep.toString(),arguments:[{value:action},{value:data}],returnByValue:true,awaitPromise:false});check();
      if(response?.exceptionDetails || !response?.result?.value)throw new Error('page');return response.result.value.state;
    };
    const command=async(method,params)=>{check();const t=await chromeApi.tabs.get(tabId);check();if(!okURL(t?.url))throw new Error('location');const r=await chromeApi.debugger.sendCommand(target,method,params);check();return r;};
    const evaluate=async(fn,args=[],byValue=true)=>{
      const doc=await command('Runtime.evaluate',{expression:'document',returnByValue:false});
      const id=doc?.result?.objectId;if(!id)throw new Error('document');
      const out=await command('Runtime.callFunctionOn',{objectId:id,functionDeclaration:fn,arguments:args,returnByValue:byValue});
      if(out?.exceptionDetails)throw new Error('page');return out?.result;
    };
    const findView=async(action)=>evaluate('function(action){return ('+FIND_VIEW+')(action)}',[{value:action}],false);
    const pointerView=async(action,optional=false)=>{
      phaseName='view_'+action;onProgress(phaseName);
      const soft=error=>{if(optional)return false;throw error instanceof Error?error:new Error('view');};
      let obj;
      try{obj=await findView(action);}catch(error){return soft(error);}
      if(!obj?.objectId)return soft(new Error('view'));
      const id=obj.objectId;
      try{
        // Read-only drawer controls use semantic identity, not background-tab hit coordinates.
        // Revalidate and click in one synchronous call; callers still verify the resulting view.
        const clicked=await command('Runtime.callFunctionOn',{objectId:id,functionDeclaration:`function(action){if(!['open','tab','close'].includes(action)||!this.isConnected||(${FIND_VIEW})(action)!==this)return false;
          for(let n=this;n;n=n.parentElement){if(n.disabled||n.hasAttribute('disabled')||n.hasAttribute('inert')||n.getAttribute('aria-disabled')==='true'||n.getAttribute('aria-hidden')==='true')return false;}
          const visible=e=>!!e.isConnected&&!!e.getClientRects().length&&(()=>{for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);if(n.hidden||n.getAttribute('aria-hidden')==='true'||s.display==='none'||s.visibility!=='visible'||Number(s.opacity)===0)return false;}return true;})();
          const drawer=this.closest('div.d-drawer.d-drawer-right.draft-drawer');
          const blockers=[...document.querySelectorAll('.login-container,.login-modal,.login-box,.sso-login-wrapper,.qrcode-img,div.creator-publish-popover,[role="dialog"]')];
          if(blockers.some(e=>visible(e)&&!(e===drawer&&e.getAttribute('role')==='dialog')))return false;
          HTMLElement.prototype.click.call(this);return true;
        }`,arguments:[{value:action}],returnByValue:true});
        if(clicked?.exceptionDetails || clicked?.result?.value!==true)throw new Error('view');
        return true;
      }catch(error){return soft(error);}
    };
    const waitView=async(action)=>{phaseName='view_'+action;onProgress(phaseName);for(let i=0;i<15;i++){const found=await findView(action);if(found?.objectId)return true;await new Promise(r=>setTimeout(r,200));check();}return false;};
    const readView=async()=>{const out=await evaluate('function(target){return ('+READ_VIEW+')({action:"read",target})}',[{value:job.title}]);return out?.value;};
    const fresh=async()=> (await evaluate('function(){return ('+FRESH_VIEW+')()}'))?.value===true;
    let pageReady=false;
    for(let i=0;i<40;i++){
      const state=await call('ready');
      if(state==='auth')return {...result('auth_required','请先在当前浏览器登录小红书。'),action_sent:false};
      if(state==='ok'){pageReady=true;break;}
      if(state!=='pending')throw new Error('unknown');
      await new Promise(resolve=>setTimeout(resolve,250));check();
    }
    if(!pageReady)throw new Error('view');
    let localBaseline=null;
    // Draft-box baseline is an auxiliary receipt aid. A failed open must not block real save.
    if(await fresh()) {
      const opened=await pointerView('open',true);
      const tabShown=opened ? await waitView('tab') : false;
      if(opened && !tabShown) {
        // Open click landed but the draft-box tab never appeared: soft-close if needed, then save without baseline.
        if(!(await waitView('open'))) await pointerView('close',true);
        if(!(await fresh())) throw new Error('view');
      }
      if(opened && tabShown) {
        let captured=null;
        try {
          if(!await pointerView('tab'))throw new Error('view');
          let snapshot;for(let i=0;i<15;i++){snapshot=await readView();if(snapshot?.ok)break;await new Promise(r=>setTimeout(r,200));check();}
          if(snapshot?.ok && snapshot.match===null && Number.isSafeInteger(snapshot.count) && snapshot.count>=0)captured=snapshot.count;
          if(!await pointerView('close') || !await waitView('open') || !await fresh())throw new Error('view');
          localBaseline=captured;
        } catch (error) {
          // Baseline is receipt aid only. Prefer soft-skip over hard-fail when drawer can be dismissed
          // or the failure was a viewport/hit geometry miss on a background/small window.
          if(!(await waitView('open'))) {
            await pointerView('close',true);
          }
          const softGeom = ['viewport','box','hit','changed','view'].includes(error?.message);
          if(!(await waitView('open')) || !(await fresh())) {
            await pointerView('close',true);
            if(!(await fresh()) && !softGeom) throw (error instanceof Error ? error : new Error('view'));
            localBaseline=null;
          } else {
            localBaseline=softGeom?captured:null;
          }
        }
      }
    }
    const phase=async(name,data={})=>{onProgress(name);const state=await call(name,data);if(state==='auth')return 'auth';if(state!=='ok')throw new Error('unknown');return 'ok';};
    if(await phase('tab')==='auth')return sent ? unknown() : {...result('auth_required','请先在当前浏览器登录小红书。'), action_sent:false};
    const startedAt=Date.now();
    // Upload images one-by-one (matches verified xhs_publish._upload_images); wait each slot ready before next.
    for(let index=0;index<job.images.length;index++){
      onProgress('upload');let uploaded=false;
      for(let i=0;i<40;i++){
        const state=await call('upload',{images:[job.images[index]],priorCount:index});
        if(state==='auth')return sent ? unknown() : {...result('auth_required','请先在当前浏览器登录小红书。'), action_sent:false};
        if(state==='ok'){uploaded=true;break;}
        if(state==='pending'){await new Promise(resolve=>setTimeout(resolve,250));check();continue;}
        throw new Error('upload');
      }
      if(!uploaded)throw new Error('upload');
      onProgress('upload_state');let ready=false;
      for(let i=0;i<120;i++){const state=await call('upload_state',{count:index+1});if(state==='auth')return sent ? unknown() : {...result('auth_required','请先在当前浏览器登录小红书。'), action_sent:false};if(state==='ok'){ready=true;break;}if(state!=='pending')throw new Error('upload');await new Promise(resolve=>setTimeout(resolve,500));check();}
      if(!ready)throw new Error('upload');
    }
    const body=job.body+job.tags.map(t=>t.trim().replace(/^#+/,'')).filter(Boolean).map(t=>' #'+t).join('');
    // Trusted browser text input updates the framework state as well as the visible DOM.
    // Each input is sent once; verification observes delayed editor updates without rewriting.
    const fillPart=async part=>{
      for(let i=0;i<40;i++){
        const state=await call('fill',{part,title:job.title,body});
        if(state==='auth')return false;
        if(state==='ok')return true;
        if(state!=='pending')throw new Error('unknown');
        await new Promise(resolve=>setTimeout(resolve,250));check();
      }
      throw new Error('unknown');
    };
    for(const part of ['title','body']){
      if(!await fillPart(part))return {...result('auth_required','请先在当前浏览器登录小红书。'),action_sent:false};
      await command('Input.insertText',{text:part==='title'?job.title:body});
    }
    if(!await fillPart('verify'))return {...result('auth_required','请先在当前浏览器登录小红书。'),action_sent:false};
    const recordFailure=snapshot=>{
      if(!snapshot?.ok) return 'view';
      if(snapshot.match?.title!==job.title) return 'target';
      if(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(snapshot.match?.saved_at||'')) return 'time';
      const imgs=snapshot.match.images;
      if(!Array.isArray(imgs) || imgs.length<1 || !imgs.some(im=>im && im.loaded===true && im.natural_width>0 && im.natural_height>0)) return 'image';
      if(localBaseline===null) return 'no_baseline';
      if(snapshot.count!==localBaseline+1) return 'count';
      const m=snapshot.match.saved_at.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
      if(!m) return 'time';
      // saved_at is creator-local Asia/Shanghai (UTC+8).
      const savedMs=Date.UTC(+m[1],+m[2]-1,+m[3],+m[4],+m[5],+m[6])-8*3600*1000;
      if(savedMs<startedAt-120000 || savedMs>Date.now()+120000) return 'time';
      return null;
    };
    const matchRecord=snapshot=>recordFailure(snapshot)===null;
    const recordFound=()=>result('record_found','草稿箱出现新增记录，请核对正文和图片。','new_record');
    const contextChanged=error=>['document','page'].includes(error?.message) || /execution context|cannot find context|cannot find object|inspected target navigated/i.test(error?.message||'');
    const recoverRecord=async()=>{
      let lastSnapshot=null, lastFailure='view';
      const uncertain=(reason=stopped||signal?.aborted?'timeout':lastFailure)=>({...unknown(),receiptCheck:{
        baselineCount:Number.isSafeInteger(localBaseline)&&localBaseline>=0?localBaseline:null,
        observedCount:Number.isSafeInteger(lastSnapshot?.count)&&lastSnapshot.count>=0?lastSnapshot.count:null,
        reason,
      }});
      if(!sent || localBaseline===null)return uncertain(localBaseline===null?'no_baseline':'view');
      let opened=false, selected=false;
      // Saving can replace the document and its notice marker. Inspect only; never send save again.
      for(let i=0;i<40;i++){
        if(stopped || signal?.aborted)return uncertain('timeout');
        try {
          phaseName='receipt';onProgress('receipt');
          const current=await chromeApi.tabs.get(tabId);check();
          if(!okURL(current?.url))return uncertain('navigation');
          const snapshot=await readView();
          lastSnapshot=snapshot;lastFailure=recordFailure(snapshot);
          if(lastFailure===null)return recordFound();
          if(!snapshot?.ok && !opened && await fresh() && (await findView('open'))?.objectId){
            opened=true;if(!await pointerView('open',true))return uncertain();
          } else if(!snapshot?.ok && !selected && (await findView('tab'))?.objectId){
            selected=true;if(!await pointerView('tab',true))return uncertain();
          }
        } catch(error) {if(!contextChanged(error))return uncertain(error?.message==='location'?'navigation':undefined);}
        await new Promise(resolve=>setTimeout(resolve,250));
      }
      return uncertain();
    };
    try {
      await phase('baseline');onProgress('save');
      const light=await call('save_probe');
      const candidates=await shadowCandidates(command);
      if(light==='one' && candidates.length===0){sent=true;if(await call('save')!=='ok')return recoverRecord();}
      else if(light==='none' && candidates.length===1){
        const again=await shadowCandidates(command);if(JSON.stringify(again)!==JSON.stringify(candidates))throw new Error('changed');
        const candidate=candidates[0];const button=(await command('DOM.resolveNode',{backendNodeId:candidate.node}))?.object?.objectId;
        const host=(await command('DOM.resolveNode',{backendNodeId:candidate.host}))?.object?.objectId;if(!button||!host)throw new Error('node');
        const inspect=async(click)=>{const r=await command('Runtime.callFunctionOn',{objectId:button,functionDeclaration:SHADOW_ACTION,arguments:[{value:candidate.label},{value:click},{objectId:host}],returnByValue:true});if(r?.exceptionDetails)throw new Error('node');return r?.result?.value;};
        const before=await inspect(false);if(!before?.valid||!before.visible||!before.enabled||before.label!==candidate.label)throw new Error('node');
        sent=true;const after=await inspect(true);if(!after?.valid||!after.visible||!after.enabled||!after.clicked)return recoverRecord();
      } else throw new Error('ambiguous');
      onProgress('receipt');let confirmed=false;
      for(let i=0;i<60;i++){
        const state=await call('receipt');
        if(state==='saved')return result('saved','草稿保存提示已确认。','fresh_notice');
        const snapshot=await readView();if(matchRecord(snapshot))return recordFound();
        if(state==='dialog'){if(confirmed)return unknown();confirmed=true;if(await call('confirm')!=='ok')return recoverRecord();continue;}
        if(state==='unknown')return recoverRecord();
        if(state!=='pending')return unknown();
        await new Promise(resolve=>setTimeout(resolve,500));check();
      }
      return recoverRecord();
    } catch(error) {if(sent && contextChanged(error))return recoverRecord();throw error;}
  })().catch(error=>{const diagnostic=['location','document','page','view','identity','box','hit','viewport','changed','unknown','upload','node','ambiguous','stopped'].includes(error?.message)?error.message:'browser_error';return {...(sent?unknown():fail()),...(!sent?{action_sent:false}:{}),phase:phaseName,diagnostic};});
  try {
    const output=await Promise.race([work,interrupted]);
    if(typeof output==='string')return sent?unknown():fail();
    return output;
  } finally {
    stop();clearTimeout(timer);signal?.removeEventListener('abort',abortListener);
    if(attached) { let cleanupTimer; try { await Promise.race([chromeApi.debugger.detach(target),new Promise(resolve=>{cleanupTimer=setTimeout(resolve,1000);})]); } catch {} finally {clearTimeout(cleanupTimer);} }
    else work.finally(()=>{if(attached)chromeApi.debugger.detach(target).catch(()=>{});});
  }
}
