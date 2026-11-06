import { useState, useRef, useEffect } from 'react';
import {
  createSchedule, executeSkill, runAgent, streamChat,
  fetchAccounts, publishNow, publishStatus, submitPublishSms, fetchOutputs, mediaUrl,
} from '../lib/api';
import { confirmClientDraftReview, getClientBrowserStatus, openClientXhsDrafts, prepareClientDraftReview, saveClientXhsDraft } from '../lib/clientBrowser';
import type { ClientBrowserResult, ClientDraftImage } from '../lib/clientBrowser';
import type { AccountItem, OutputFile } from '../lib/api';
import { loadPublishDraft, savePublishDraft } from '../lib/store';
import { renderMarkdown } from '../lib/sanitize';
import { ReferenceArtwork } from './MingyaPrimitives';
import '../styles/mingya-publish.css';
import { IconPublish, IconCopy, IconCheck, IconCalendar, IconSkills, IconEdit, IconStop, IconTrash, IconImage, IconVideo } from './icons';

interface PublishPageProps {
  persona: string;
}

// 平台列表须与后端 LOGIN_RUNNERS 对齐（有登录/发布链路的才列）
const PLATFORM_ART: Record<string, readonly [number, number, number, number]> = {
  xiaohongshu: [298,351,78,76], 'wechat-oa': [928,351,78,76], douyin: [298,522,78,76], 'weixin-channels': [928,522,80,76], bilibili: [298,691,78,76], zhihu: [928,691,80,76], kuaishou: [298,861,78,76],
};
const DISPLAY_ORDER = ['xiaohongshu','douyin','wechat-oa','weixin-channels','bilibili','zhihu','kuaishou'];

const PLATFORMS: { key: string; label: string; titleLimit?: number; bodyLimit: number; hint: string }[] = [
  { key: 'xiaohongshu', label: '小红书', titleLimit: 20, bodyLimit: 1000, hint: '标题≤20，正文≤1000，重情绪+话题标签；图文草稿保存在当前日常 Chrome 个人配置的小红书草稿箱，未公开发布。视频仍走原有服务端通道，那不是日常使用的 Chrome。' },
  { key: 'douyin', label: '抖音', titleLimit: 55, bodyLimit: 55, hint: '文案≤55，前几字是钩子' },
  { key: 'kuaishou', label: '快手', titleLimit: 30, bodyLimit: 1000, hint: '视频或图片(图文)，标题≤30，需附媒体' },
  { key: 'weixin-channels', label: '视频号', bodyLimit: 1000, hint: '需附视频，短描述+话题标签，微信扫码登录' },
  { key: 'zhihu', label: '知乎', bodyLimit: 5000, hint: '长文/回答，讲清逻辑' },
  { key: 'bilibili', label: 'B站', titleLimit: 80, bodyLimit: 2000, hint: '需附视频，标题≤80、简介≤2000，默认投「知识」分区' },
  { key: 'wechat-oa', label: '公众号', titleLimit: 64, bodyLimit: 20000, hint: '图文文章，正文用 Markdown，首图作封面，发到草稿箱；需先在账号页「登录公众号后台」扫码' },
];
const LABEL2KEY = Object.fromEntries(PLATFORMS.map((p) => [p.label, p.key]));

// 能一键发布的平台（有后端 publisher）
const PUBLISHABLE = new Set(['xiaohongshu', 'douyin', 'kuaishou', 'weixin-channels', 'zhihu', 'bilibili', 'wechat-oa']);
const DRAFT_PLATFORMS = new Set(['xiaohongshu', 'wechat-oa']);
// 必须附带媒体的平台（无媒体发不了）——公众号需要一张封面图，也计入
const MEDIA_REQUIRED = new Set(['xiaohongshu', 'douyin', 'kuaishou', 'weixin-channels', 'bilibili', 'wechat-oa']);
// 只能发视频的平台（抖音/视频号/B站：图文不走此链路，必须视频）
const VIDEO_ONLY = new Set(['douyin', 'weixin-channels', 'bilibili']);
const VIDEO_RE = /\.(mp4|mov|webm|mkv|avi|m4v|flv|ts)$/i;
const CLIENT_IMAGE_MIME = new Set(['image/png', 'image/jpeg', 'image/webp']);
const CLIENT_IMAGE_MAX_COUNT = 18;
const CLIENT_IMAGE_MAX_BYTES = 30 * 1024 * 1024;
const CLIENT_SINGLE_IMAGE_MAX_BYTES = 10 * 1024 * 1024;
const CLIENT_MISSING = '首次使用请下载浏览器连接器，在你平时使用的 Chrome 中安装，并在扩展设置中连接此工作台地址，然后刷新页面。';

function createClientAbort() {
  if (typeof AbortController !== 'undefined') return new AbortController();
  const listeners = new Set<() => void>();
  const signal = {
    aborted: false,
    addEventListener(_type: string, listener: () => void) { listeners.add(listener); },
    removeEventListener(_type: string, listener: () => void) { listeners.delete(listener); },
  };
  return {
    signal: signal as AbortSignal,
    abort() {
      if (signal.aborted) return;
      signal.aborted = true;
      listeners.forEach((listener) => listener());
    },
  };
}

function parseTags(value: string): string[] {
  return value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean);
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

async function loadClientDraftImages(paths: string[], signal?: AbortSignal): Promise<ClientDraftImage[]> {
  if (paths.length === 0 || paths.length > CLIENT_IMAGE_MAX_COUNT) throw new Error('图片数量需在 1 到 18 张');
  let total = 0;
  const images: ClientDraftImage[] = [];
  for (const path of paths) {
    const resolved = new URL(mediaUrl(path), location.origin);
    if (resolved.origin !== location.origin) throw new Error('只下载本站图片，未保存小红书草稿');
    const response = await fetch(resolved.href, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal });
    if (!response.ok || !response.url || new URL(response.url).origin !== location.origin) throw new Error('图片下载来源无效，未保存小红书草稿');
    const mime = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
    if (!CLIENT_IMAGE_MIME.has(mime)) throw new Error('图片格式仅支持 PNG、JPEG、WebP，未保存小红书草稿');
    const rawLength = response.headers.get('content-length');
    if (rawLength !== null) {
      if (!/^\d+$/.test(rawLength)) throw new Error('图片大小无效，未保存小红书草稿');
      const length = Number(rawLength);
      if (!Number.isSafeInteger(length) || length > CLIENT_SINGLE_IMAGE_MAX_BYTES || total + length > CLIENT_IMAGE_MAX_BYTES) throw new Error('单张图片不能超过 10MiB，总大小不能超过 30MiB，未保存小红书草稿');
    }
    if (!response.body) throw new Error('图片流不可用，未保存小红书草稿');
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        size += part.value.byteLength;
        if (size > CLIENT_SINGLE_IMAGE_MAX_BYTES || total + size > CLIENT_IMAGE_MAX_BYTES) throw new Error('单张图片不能超过 10MiB，总大小不能超过 30MiB，未保存小红书草稿');
        chunks.push(part.value);
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
    if (!size) throw new Error('图片为空，未保存小红书草稿');
    total += size;
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const name = decodeURIComponent(resolved.pathname.split('/').pop() || 'image');
    images.push({ name, mime: mime as ClientDraftImage['mime'], base64: bytesToBase64(bytes) });
  }
  return images;
}

// Duplicate-save protection is only surfaced when a draft result is unknown or a submit was blocked by it.
function isProtectionBusy(result: ClientBrowserResult): boolean {
  return result.state === 'busy' && result.message.includes('未核对');
}
function needsDraftReview(result: ClientBrowserResult): boolean {
  return result.state === 'unconfirmed' || isProtectionBusy(result);
}

function clientDraftMessage(result: ClientBrowserResult): { status: 'ok' | 'fail'; msg: string } {
  if (result.published !== false) return { status: 'fail', msg: '未确认保存至小红书草稿。请先查看小红书草稿，勿重复提交。' };
  if (result.ok && result.state === 'saved') return { status: 'ok', msg: '已保存至当前浏览器的小红书草稿，未公开发布。这是本次新草稿。' };
  if (result.ok && result.state === 'record_found') return { status: 'ok', msg: '草稿箱已核对到本次新增记录；请打开检查正文和图片。未公开发布。' };
  if (result.state === 'unconfirmed') return { status: 'fail', msg: result.message.startsWith('保存结果未确认：') && result.message.includes('勿重复提交') ? result.message : '保存结果未确认。请先查看小红书草稿，勿重复提交。' };
  if (isProtectionBusy(result)) return { status: 'fail', msg: '上一次保存的结果还没确认，为防止重复保存，这次没有提交。' };
  if (result.state === 'auth_required') return { status: 'fail', msg: result.message.trim() || '请在当前浏览器打开的小红书创作者页面登录后再保存草稿。' };
  if (result.state === 'unavailable') return { status: 'fail', msg: result.message.trim() || CLIENT_MISSING };
  return { status: 'fail', msg: result.message.trim() || '保存小红书草稿失败。请先查看小红书草稿，勿重复提交。' };
}

function parseSections(text: string): Record<string, string> {
  const parts = text.split(/^\s*={2,}\s*(.+?)\s*={2,}\s*$/m);
  const map: Record<string, string> = {};
  for (let i = 1; i < parts.length; i += 2) map[parts[i].trim()] = (parts[i + 1] || '').trim();
  return map;
}

type PubState = { status: 'publishing' | 'ok' | 'fail'; msg: string };

export default function PublishPage({ persona }: PublishPageProps) {
  const draft0 = loadPublishDraft();
  const [title, setTitle] = useState(draft0.title);
  const [body, setBody] = useState(draft0.body);
  const [platforms, setPlatforms] = useState<string[]>(draft0.platforms);
  const [overrides, setOverrides] = useState<Record<string, string>>(draft0.overrides);
  const [tags, setTags] = useState(draft0.tags || '');
  const [editing, setEditing] = useState<string | null>(null);
  const [copied, setCopied] = useState('');
  const [toast, setToast] = useState('');
  const [adapting, setAdapting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [bgChecking, setBgChecking] = useState(false);
  const [checkResult, setCheckResult] = useState('');
  const adaptCtl = useRef<AbortController | null>(null);

  // 发布相关
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [mediaFiles, setMediaFiles] = useState<OutputFile[]>([]);
  const [selectedMedia, setSelectedMedia] = useState<string[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  const [previewPlatform, setPreviewPlatform] = useState('');
  const [mediaKind, setMediaKind] = useState<'image' | 'video' | 'all'>('all');

  const [pub, setPub] = useState<Record<string, PubState>>({});
  const [publishing, setPublishing] = useState(false);
  // 发布时的短信验证窗口（抖音风控条件触发；没触发就不弹）
  const [pubSms, setPubSms] = useState<{ platform: string; name: string; state: string; message: string } | null>(null);
  const [pubSmsCode, setPubSmsCode] = useState('');
  const [pubSmsBusy, setPubSmsBusy] = useState(false);
  const [clientBusy, setClientBusy] = useState<'connect' | 'open' | 'draft' | 'review' | ''>('');
  const [clientMsg, setClientMsg] = useState(CLIENT_MISSING);
  const [needsReview, setNeedsReview] = useState(false);
  const clientAlive = useRef(true);
  const clientAbort = useRef<{ abort: () => void; signal: AbortSignal } | null>(null);
  const clientLock = useRef(false);
  const publishLock = useRef(false);

  // 草稿持久化：任何改动即写 localStorage，切页/刷新回来都在
  useEffect(() => {
    savePublishDraft({ title, body, platforms, overrides, tags });
  }, [title, body, platforms, overrides, tags]);

  useEffect(() => () => { adaptCtl.current?.abort(); }, []);   // 离开页面中止流
  useEffect(() => {
    clientAlive.current = true;
    const controller = createClientAbort();
    clientAbort.current = controller;
    return () => {
      clientAlive.current = false;
      controller.abort();
    };
  }, []);

  // 登录态 + 可选媒体列表
  useEffect(() => {
    fetchAccounts().then(setAccounts).catch(() => { /* 忽略 */ });
    fetchOutputs().then((roots) => {
      const files: OutputFile[] = [];
      const walk = (n: OutputFile) => {
        if (n.type === 'file') { if (n.kind === 'image' || n.kind === 'video') files.push(n); return; }
        for (const c of n.children || []) walk(c);
      };
      roots.forEach(walk);
      files.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
      setMediaFiles(files);
    }).catch(() => { /* 忽略 */ });
  }, []);

  const activePreview = platforms.includes(previewPlatform) && PLATFORMS.some((platform) => platform.key === previewPlatform) ? previewPlatform : PLATFORMS.find((platform) => platforms.includes(platform.key))?.key || '';
  const previewMedia = mediaFiles.find((file) => file.path === selectedMedia[0]);
  const loginOf = (key: string) => accounts.find((a) => a.platform === key)?.loggedIn ?? false;

  const toggle = (k: string) =>
    setPlatforms((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);
  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(''), 2800); };
  const effective = (k: string) => overrides[k] ?? body;
  const empty = !title.trim() && !body.trim();
  const isVideoPath = (p: string) => /\.(mp4|mov|flv|mkv|avi|webm|m4v|wmv|ts|mpe?g)$/i.test(p);
  const toggleMedia = (path: string) =>
    setSelectedMedia((prev) => {
      if (prev.includes(path)) return prev.filter((x) => x !== path);
      // 通用规则：图片和视频不能同时；视频一次只发一个
      if (isVideoPath(path)) {
        if (prev.length && !prev.every(isVideoPath)) { showToast('图片和视频不能同时发布，请先取消已选图片'); return prev; }
        return [path]; // 视频单选
      }
      if (prev.some(isVideoPath)) { showToast('图片和视频不能同时发布，请先取消已选视频'); return prev; }
      return [...prev, path]; // 图片可多选（图文）
    });

  // A. 智能一稿多改（流式：逐字改写，直接流进每个平台卡片）
  const adapt = () => {
    if (empty || platforms.length === 0 || adapting) return;
    const sel = PLATFORMS.filter((p) => platforms.includes(p.key));
    const prompt =
      `请执行 /skill-content-repurposing：把下面这条内容改编到这些平台：${sel.map((p) => p.label).join('、')}。` +
      `务必参考该 SKILL 的 platform-specs 与改写配方，贴合各平台原生格式、语气与字数。\n` +
      `【硬性要求】输出各平台“可直接复制发布的纯文本正文”，禁止任何 Markdown 语法：不要 **加粗**、# 标题、---、表格、代码块、编号列表符号；` +
      `小红书可用 emoji 和 #话题标签，按平台习惯自然分行即可。\n` +
      `严格只按下面格式输出、每个平台之间用分隔线，不要任何额外说明：\n` +
      sel.map((p) => `===${p.label}===\n<该平台纯文本正文>`).join('\n') +
      `\n\n原始内容：\n标题：${title}\n正文：${body}`;

    setAdapting(true);
    let acc = '';
    const base = { ...overrides };
    adaptCtl.current = streamChat(
      prompt, persona, `adapt-${Date.now()}`,
      (chunk) => {                       // 逐字：实时解析并流进对应平台卡片
        acc += chunk;
        const map = parseSections(acc);
        const next = { ...base };
        for (const [label, text] of Object.entries(map)) {
          const key = LABEL2KEY[label];
          if (key && platforms.includes(key)) next[key] = text;
        }
        setOverrides(next);
      },
      () => {                            // 完成
        const hit = Object.keys(parseSections(acc)).length;
        setAdapting(false);
        showToast(hit ? `已生成 ${hit} 个平台版本` : '未能解析，可重试');
      },
      () => { setAdapting(false); showToast('改写失败，请重试'); },
    );
  };
  const stopAdapt = () => { adaptCtl.current?.abort(); setAdapting(false); };

  const performPrecheck = async () => {
    const prompt =
      `你是社媒发布审核助手。针对下面这条待发内容做两项检查，用简洁中文分点输出：\n` +
      `1. **合规风险**：是否含极限词/医疗功效/敏感或违规表述，列出问题词+替换建议；无则写"未见明显风险"。\n` +
      `2. **标题/钩子**：给标题打 1-10 分，并给 1-2 个更好的备选。\n` +
      `最后一行给「✅可发 / ⚠️建议修改」结论。\n\n待检内容：\n标题：${title}\n正文：${body}`;
    const content = `待发布内容：\n标题：${title}\n正文：${body}`;
    const [general, personaResult] = await Promise.all([
      runAgent(prompt),
      persona ? executeSkill('persona-check', content, persona) : Promise.resolve(null),
    ]);
    return `${general.response}\n\n---\n\n## 人设一致性\n\n${personaResult?.response || '未选择画像，已跳过人设一致性检查。'}`;
  };

  // C. 发布前一键预检
  const check = async () => {
    if (empty) return;
    setChecking(true); setCheckResult('');
    try {
      setCheckResult(await performPrecheck());
    } catch (e) {
      setCheckResult(e instanceof Error ? e.message : '预检失败');
    } finally { setChecking(false); }
  };

  // D. 平台提交（小红书/公众号仅存平台草稿，点击即提交；含公开发布平台时预检后二次确认）
  const publishAll = async () => {
    if (empty || publishing || checking || publishLock.current) return;
    const usesClient = platforms.includes('xiaohongshu') && !selectedMedia.some(isVideoPath);
    if (usesClient && clientLock.current) return;
    if (usesClient) { clientLock.current = true; setClientBusy('draft'); }
    publishLock.current = true;
    const targets = PLATFORMS.filter((p) => platforms.includes(p.key) && PUBLISHABLE.has(p.key));
    if (targets.length === 0) {
      publishLock.current = false;
      if (usesClient) { clientLock.current = false; setClientBusy(''); }
      showToast('所选平台暂不支持一键发布（B站请用「复制」或终端 biliup）');
      return;
    }
    const draftOnly = targets.every((target) => DRAFT_PLATFORMS.has(target.key));
    if (draftOnly) {
      // Drafts are never public: clicking save is the submission. The advisory precheck runs alongside
      // the save instead of delaying it behind a confirmation dialog.
      if (!bgChecking) {
        setBgChecking(true);
        void performPrecheck()
          .then((report) => { if (clientAlive.current) setCheckResult(report); })
          .catch((e) => { if (clientAlive.current) setCheckResult(`预检失败：${e instanceof Error ? e.message : '未知错误'}\n\n预检仅用于提醒，不会阻止你继续提交。`); })
          .finally(() => { if (clientAlive.current) setBgChecking(false); });
      }
    } else {
      setChecking(true);
      try {
        setCheckResult(await performPrecheck());
      } catch (e) {
        setCheckResult(`预检失败：${e instanceof Error ? e.message : '未知错误'}\n\n预检仅用于提醒，不会阻止你继续提交。`);
      } finally {
        setChecking(false);
      }
      const publicTargets = targets.filter((target) => !DRAFT_PLATFORMS.has(target.key));
      const draftTargets = targets.filter((target) => DRAFT_PLATFORMS.has(target.key));
      const okToSend = window.confirm(
        `发布前预检已执行，结果已显示在页面中。人设评分只做提醒，不会阻止提交。\n\n` +
        (publicTargets.length ? `公开发布：${publicTargets.map((target) => target.label).join('、')}。\n` : '') +
        (draftTargets.length ? `仅存平台草稿：${draftTargets.map((target) => target.label).join('、')}；这些草稿不会公开发布。小红书图文草稿保存在当前日常 Chrome 的小红书草稿箱。视频仍走原有服务端通道，那不是日常使用的 Chrome。\n` : '') +
        `确定继续？`);
      if (!okToSend) { publishLock.current = false; if (usesClient) { clientLock.current = false; setClientBusy(''); } return; }
    }

    setPublishing(true);
    for (const t of targets) {
      const clientImageDraft = t.key === 'xiaohongshu' && !selectedMedia.some((path) => isVideoPath(path));
      if (!clientImageDraft && !loginOf(t.key)) {
        setPub((r) => ({ ...r, [t.key]: { status: 'fail', msg: '未登录 · 去账号页登录' } }));
        continue;
      }
      if (MEDIA_REQUIRED.has(t.key) && selectedMedia.length === 0) {
        setPub((r) => ({ ...r, [t.key]: { status: 'fail', msg: '需附带图片/视频' } }));
        continue;
      }
      if (VIDEO_ONLY.has(t.key) && !selectedMedia.some((p) => VIDEO_RE.test(p))) {
        setPub((r) => ({ ...r, [t.key]: { status: 'fail', msg: `${t.label}只能发视频，请从内容库选一个视频` } }));
        continue;
      }
      setPub((r) => ({ ...r, [t.key]: { status: 'publishing', msg: t.key === 'xiaohongshu' ? (clientImageDraft ? '保存当前浏览器图文草稿中…可能需 1-2 分钟' : '通过服务端保存视频草稿中…可能需 1-2 分钟') : DRAFT_PLATFORMS.has(t.key) ? '保存平台草稿中…可能需 1-2 分钟' : '发布中…可能需 1-2 分钟' } }));
      try {
        if (clientImageDraft) {
          let images: ClientDraftImage[];
          try {
            images = await loadClientDraftImages(selectedMedia, clientAbort.current?.signal);
          } catch (error) {
            if (clientAlive.current) setPub((r) => ({ ...r, [t.key]: { status: 'fail', msg: error instanceof Error ? error.message : '图片下载失败，未保存小红书草稿' } }));
            continue;
          }
          setNeedsReview(false);
          const draft = await saveClientXhsDraft({ title, body: effective(t.key), tags: parseTags(tags), images }, clientAbort.current?.signal);
          if (!clientAlive.current) continue;
          setPub((r) => ({ ...r, [t.key]: clientDraftMessage(draft) }));
          setNeedsReview(needsDraftReview(draft));
          continue;
        }
        const res = await publishNow(t.key, { title, body: effective(t.key), media: selectedMedia, tags });
        if (t.key === 'xiaohongshu') {
          const saved = res.ok === true && res.outcome === 'draft_saved' && res.published === false && !res.async;
          const failureMessage = res.ok === false
            ? `${typeof res.message === 'string' && res.message.trim() ? res.message : '保存小红书草稿失败'}。请先检查创作者后台草稿，勿重复提交（请用明亚工作台的 Chrome 查看同一环境本地草稿）`
            : '未确认保存至小红书草稿，请用明亚工作台的 Chrome 查看同一环境的小红书草稿箱';
          setPub((r) => ({ ...r, [t.key]: saved
            ? { status: 'ok', msg: '已保存至小红书草稿，未公开发布（服务端视频通道回执）。' }
            : { status: 'fail', msg: failureMessage } }));
        } else if (t.key === 'wechat-oa') {
          setPub((r) => ({ ...r, [t.key]: res.ok && !res.async
            ? { status: 'ok', msg: '已保存至公众号草稿箱，未公开发布' }
            : { status: 'fail', msg: res.detail || res.message || '保存公众号草稿失败' } }));
        } else if (res.async) {
          // 抖音：异步发布，轮询状态；风控触发短信墙时弹输入框（条件触发，没触发就直接跑完）
          await pollAsyncPublish(t.key, t.label);
        } else {
          setPub((r) => ({
            ...r,
            [t.key]: res.ok
              ? { status: 'ok', msg: '已发布 ✅' }
              : { status: 'fail', msg: res.detail || res.message || '发布失败' },
          }));
        }
      } catch (e) {
        setPub((r) => ({ ...r, [t.key]: { status: 'fail', msg: e instanceof Error ? e.message : '发布失败' } }));
      }
    }
    setPublishing(false);
    publishLock.current = false;
    if (usesClient) { clientLock.current = false; if (clientAlive.current) setClientBusy(''); }
    showToast('提交流程结束，见各平台卡片状态');
  };

  // 异步发布轮询（抖音）：直到 success/error；遇 sms_required/verifying 弹短信窗口
  const pollAsyncPublish = (key: string, label: string) => new Promise<void>((resolve) => {
    const started = Date.now();
    const iv = setInterval(async () => {
      if (Date.now() - started > 15 * 60 * 1000) {   // 15min 兜底
        clearInterval(iv); setPubSms(null);
        setPub((r) => ({ ...r, [key]: { status: 'fail', msg: '发布超时' } }));
        resolve(); return;
      }
      let s;
      try { s = await publishStatus(key); } catch { return; }  // 单次失败忽略
      if (s.state === 'sms_required' || s.state === 'verifying') {
        setPubSms({ platform: key, name: label, state: s.state, message: s.message });
        setPub((r) => ({ ...r, [key]: { status: 'publishing', msg: s.message || '需短信验证' } }));
      } else if (s.state === 'success') {
        clearInterval(iv); setPubSms(null);
        setPub((r) => ({ ...r, [key]: { status: 'ok', msg: '已发布 ✅' } }));
        resolve();
      } else if (s.state === 'error') {
        clearInterval(iv); setPubSms(null);
        setPub((r) => ({ ...r, [key]: { status: 'fail', msg: s.message || '发布失败' } }));
        resolve();
      } else {
        setPub((r) => ({ ...r, [key]: { status: 'publishing', msg: s.message || '发布中…' } }));
      }
    }, 2500);
  });

  const submitPubSms = async () => {
    if (!pubSms) return;
    const code = pubSmsCode.replace(/\D/g, '');
    if (code.length < 4) { showToast('验证码应为 4-6 位数字'); return; }
    setPubSmsBusy(true);
    try {
      await submitPublishSms(pubSms.platform, code);
      setPubSmsCode('');
      setPubSms((p) => p && ({ ...p, state: 'verifying', message: '正在验证验证码…' }));
    } catch (e) {
      showToast(e instanceof Error ? e.message : '提交失败');
    } finally {
      setPubSmsBusy(false);
    }
  };

  const copyFor = (key: string) => {
    const text = (title ? title + '\n\n' : '') + effective(key);
    navigator.clipboard?.writeText(text);
    setCopied(key); setTimeout(() => setCopied(''), 1400);
  };
  const addToCalendar = async (key: string) => {
    if (empty) return;
    const d = new Date();
    await createSchedule({
      title: title.trim() || effective(key).slice(0, 20), date: d.toISOString().slice(0, 10),
      platform: PLATFORMS.find((p) => p.key === key)?.label || '', time: '', status: 'draft', note: effective(key),
    });
    showToast('已存为本地排期草稿并加入今天的日历，未保存至平台草稿箱');
  };

  const connectBrowser = async () => {
    if (clientLock.current) return;
    clientLock.current = true;
    setClientBusy('connect');
    try {
      const res = await getClientBrowserStatus(clientAbort.current?.signal);
      if (!clientAlive.current) return;
      if (res.ok === true && res.state === 'connected' && res.published === false) setClientMsg(res.message.trim() || '已连接当前浏览器。');
      else setClientMsg(res.state === 'unavailable' ? (res.message.trim() || CLIENT_MISSING) : (res.message.trim() || CLIENT_MISSING));
    } catch {
      if (clientAlive.current) setClientMsg(CLIENT_MISSING);
    } finally {
      clientLock.current = false;
      if (clientAlive.current) setClientBusy('');
    }
  };

  const openClientDrafts = async () => {
    if (clientLock.current) return;
    clientLock.current = true;
    setClientBusy('open');
    try {
      const res = await openClientXhsDrafts(clientAbort.current?.signal);
      if (!clientAlive.current) return;
      if (res.ok === true && res.state === 'opened' && res.published === false) setClientMsg('已在当前浏览器打开小红书创作页。请查看草稿箱。本操作不会保存或发布。');
      else setClientMsg(res.message.trim() || '打开小红书草稿失败');
    } catch {
      if (clientAlive.current) setClientMsg('打开小红书草稿失败');
    } finally {
      clientLock.current = false;
      if (clientAlive.current) setClientBusy('');
    }
  };

  const markReviewed = (message: string) => {
    setNeedsReview(false);
    setClientMsg(message);
    setPub((r) => ({ ...r, xiaohongshu: { status: 'ok', msg: '已确认上一篇并解除重复保存保护，可以重新点「存小红书草稿」。未重试、上传、删除或发布。' } }));
  };
  const reviewClientDraft = async () => {
    if (clientLock.current) return;
    clientLock.current = true;
    setClientBusy('review');
    try {
      const prepared = await prepareClientDraftReview(clientAbort.current?.signal);
      if (!clientAlive.current) return;
      if (prepared.ok === true && prepared.state === 'reviewed') {
        markReviewed(prepared.message.trim() || '当前没有待核对任务。');
        return;
      }
      if (prepared.ok !== true || prepared.state !== 'review_ready' || !prepared.reviewToken) {
        setClientMsg(prepared.message.trim() || '解除保护结果未确认，请刷新状态核对；不会自动重试');
        return;
      }
      const accepted = window.confirm('请确认你已在小红书草稿箱看过上一篇（已存在或确实没有保存）。此操作只解除重复保存保护，不会重试、上传、删除或发布。');
      if (!accepted) return;
      const reviewed = await confirmClientDraftReview(prepared.reviewToken, clientAbort.current?.signal);
      if (!clientAlive.current) return;
      if (reviewed.ok === true && reviewed.state === 'reviewed') markReviewed(reviewed.message.trim() || '已解除提交保护。');
      else setClientMsg(reviewed.message.trim() || '解除保护结果未确认，请刷新状态核对；不会自动重试');
    } catch {
      if (clientAlive.current) setClientMsg('解除保护结果未确认，请刷新状态核对；不会自动重试');
    } finally {
      clientLock.current = false;
      if (clientAlive.current) setClientBusy('');
    }
  };

  const canPublish = platforms.some((k) => PUBLISHABLE.has(k));
  const targets = PLATFORMS.filter((platform) => platforms.includes(platform.key) && PUBLISHABLE.has(platform.key));
  const hasDraftTarget = targets.some((platform) => DRAFT_PLATFORMS.has(platform.key));
  const hasPublicTarget = targets.some((platform) => !DRAFT_PLATFORMS.has(platform.key));
  const submitLabel = targets.length === 1 && targets[0].key === 'xiaohongshu'
    ? '存小红书草稿'
    : targets.length === 1 && targets[0].key === 'wechat-oa'
    ? '存公众号草稿'
    : hasDraftTarget ? hasPublicTarget ? '提交所选平台' : '存平台草稿' : '一键发布';

  return (
    <div className="publish-page">
      <div className="publish-editor">
        <h1 className="page-title"><IconPublish size={21} /> 发布中心</h1>
        <p className="page-subtitle">一次编辑 → AI 一键改写成各平台版本 → 预检 → 附媒体 → 提交。小红书图文仅存当前日常 Chrome 的小红书草稿箱，未公开发布；公众号存平台草稿，其他平台公开发布。视频不走当前浏览器扩展。</p>
        <button type="button" className="btn btn-sm" disabled={!!clientBusy} onClick={connectBrowser}>
          {clientBusy === 'connect' ? '正在连接当前浏览器…' : '连接当前浏览器'}
        </button>
        <button type="button" className="btn btn-sm" disabled={!!clientBusy} onClick={openClientDrafts}>
          {clientBusy === 'open' ? '正在打开草稿…' : '查看小红书草稿'}
        </button>
        <a className="btn btn-sm" href="/api/browser-extension/download" download>下载浏览器连接器</a>
        <p className="pv-hint" aria-live="polite">{clientMsg}</p>
        <details className="pv-hint">
          <summary>首次连接说明</summary>
          <p>在你平时使用的 Chrome 中打开工作台。下载并解压连接器，在 Chrome 的扩展管理页面选择「加载已解压的扩展程序」，选中解压目录，按浏览器提示确认权限。打开连接器，填入此工作台地址并连接，再刷新工作台。每位使用者只需在自己的浏览器中安装一次。</p>
          <p>连接器已连接不代表小红书已登录。草稿保存在这个 Chrome 个人配置中，其他浏览器和手机 App 不会自动同步。</p>
        </details>
        {platforms.includes('xiaohongshu') && selectedMedia.some(isVideoPath) && <p className="pv-hint">小红书视频仍走原有服务端通道，不是日常使用的 Chrome 个人配置，也不会由当前浏览器扩展保存。</p>}

        <label className="field-label">标题</label>
        <input className="field" value={title} placeholder="标题（部分平台需要）"
          onChange={(e) => setTitle(e.target.value)} />
        <label className="field-label">正文（母版）</label>
        <textarea className="field" style={{ minHeight: 180 }} value={body}
          placeholder="写下你的内容，右侧按各平台规则实时预览；点「一键适配」让 AI 分平台改写…"
          onChange={(e) => setBody(e.target.value)} />

        <label className="field-label">话题标签 <span style={{ color: 'var(--text-secondary)', fontWeight: 400, fontSize: 12 }}>（逗号分隔，如「AI,职场,干货」；当前仅把 #标签拼进正文文本，尚未实现平台话题联想绑定）</span></label>
        <input className="field" value={tags} placeholder="AI,职场,干货"
          onChange={(e) => setTags(e.target.value)} />

        <label className="field-label" style={{ marginTop: 14 }}>
          媒体附件 {selectedMedia.length > 0 && <span className="pv-badge">{selectedMedia.length} 个</span>}
          <span style={{ color: 'var(--text-secondary)', fontWeight: 400, fontSize: 12 }}>（小红书/抖音/快手/微信视频号/B站必需，从内容库选；抖音、视频号、B站须为视频）</span>
        </label>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <div className="mingya-media-choices"><button type="button" onClick={() => { setMediaKind('image'); setShowPicker((value) => mediaKind === 'image' ? !value : true); }}><IconImage size={25} /><strong>选择图片</strong><span>从内容库选择图片</span></button><button type="button" onClick={() => { setMediaKind('video'); setShowPicker((value) => mediaKind === 'video' ? !value : true); }}><IconVideo size={25} /><strong>选择视频</strong><span>从内容库选择视频</span></button></div>
          {selectedMedia.map((path) => (
            <div key={path} className="media-chip" onClick={() => toggleMedia(path)} title="点击移除">
              {mediaFiles.find((f) => f.path === path)?.kind === 'image'
                ? <img src={mediaUrl(path)} alt="" /> : <span className="media-vid">🎬</span>}
              <span className="media-x">×</span>
            </div>
          ))}
        </div>
        {showPicker && (
          <div className="media-grid">
            {mediaFiles.length === 0 && <div className="dash-empty">内容库暂无图片/视频</div>}
            {mediaFiles.filter((file) => mediaKind === 'all' || file.kind === mediaKind).slice(0, 40).map((f) => (
              <div key={f.path}
                className={`media-cell ${selectedMedia.includes(f.path) ? 'sel' : ''}`}
                onClick={() => toggleMedia(f.path)} title={f.path}>
                {f.kind === 'image'
                  ? <img src={mediaUrl(f.path)} alt={f.name} loading="lazy" />
                  : <span className="media-vid">🎬<br />{f.name.slice(0, 12)}</span>}
                {selectedMedia.includes(f.path) && <span className="media-check">✓</span>}
              </div>
            ))}
          </div>
        )}

        <p className="publish-saved-note">本地编辑草稿已自动保存，切换页面/刷新回来内容都在，不等于平台草稿。小红书图文在当前浏览器检查登录状态；其他通道需对应账号已登录且媒体齐全。</p>
        {checkResult && (
          <div className="panel" style={{ marginTop: 14 }}>
            <div className="panel-title"><IconCheck size={14} /> 发布前预检</div>
            <div className="skill-body-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(checkResult) }} />
          </div>
        )}
      </div>

      <div className="publish-previews">
<section className="mingya-publish-platforms"><h2><span className="mingya-publish-section-rule" aria-hidden="true" />发布平台 <small>（可多选）</small></h2><div className="mingya-platform-options">{[...PLATFORMS].sort((a,b) => DISPLAY_ORDER.indexOf(a.key) - DISPLAY_ORDER.indexOf(b.key)).map((p) => <button key={p.key} type="button" className={`mingya-platform-option ${platforms.includes(p.key) ? 'selected' : ''}`} aria-pressed={platforms.includes(p.key)} onClick={() => toggle(p.key)}><ReferenceArtwork page="accounts" crop={PLATFORM_ART[p.key]} className="mingya-publish-platform-icon" /><span className="mingya-platform-name"><span className="mingya-checkbox" aria-hidden="true">{platforms.includes(p.key) ? '✓' : ''}</span>{p.key === 'wechat-oa' ? '微信公众号' : p.label}</span><span className="mingya-platform-status" title={pub[p.key]?.msg}>{pub[p.key]?.msg || (p.key === 'xiaohongshu' && !selectedMedia.some(isVideoPath) ? '登录状态由小红书页面确认' : loginOf(p.key) ? '已登录' : '未登录')}</span></button>)}</div></section><div className="mingya-publish-right-body"><section className="mingya-publish-preview-section"><div className="mingya-preview-heading"><h2><span className="mingya-publish-section-rule" aria-hidden="true" />预览</h2><label>切换平台：<select aria-label="切换预览平台" value={activePreview} disabled={!platforms.length} onChange={(event) => setPreviewPlatform(event.target.value)}>{!platforms.length && <option value="">未选择</option>}{PLATFORMS.filter((p) => platforms.includes(p.key)).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}</select></label></div>
        {platforms.length === 0 && <div className="dash-empty">选择至少一个平台查看预览</div>}
        {PLATFORMS.filter((p) => platforms.includes(p.key)).map((p) => {
          const text = effective(p.key);
          const over = text.length > p.bodyLimit;
          const titleOver = p.titleLimit != null && title.length > p.titleLimit;
          const isEdit = editing === p.key;
          const ps = pub[p.key];
          const publishable = PUBLISHABLE.has(p.key);
          const logged = loginOf(p.key);
          return (
            <div key={p.key} hidden={p.key !== activePreview} className={`card pv-card pv-${p.key}`}>
              <div className="pv-head">
                <span className="pv-plat">
                  {p.label}
                  {overrides[p.key] != null && <span className="pv-badge">AI 版</span>}
                  {publishable && (p.key === 'xiaohongshu' && !selectedMedia.some(isVideoPath) ? <span className="pv-badge">登录状态由小红书页面确认</span> : logged
                    ? <span className="pv-badge pv-badge-ok">已登录</span>
                    : <span className="pv-badge">未登录</span>)}
                </span>
                <span className={`pv-count ${over ? 'over' : ''}`}>{text.length}/{p.bodyLimit}</span>
              </div>
              <div className="pv-body"><div className="mingya-publish-cover">{previewMedia ? (previewMedia.kind === 'image' ? <img src={mediaUrl(previewMedia.path)} alt={previewMedia.name} /> : <video src={mediaUrl(previewMedia.path)} controls preload="metadata" />) : <><IconImage size={44} /><span>暂无封面，发布后将展示所选配图</span></>}</div>
                {p.titleLimit != null && (
                  <div className={`pv-title ${titleOver ? 'over' : ''}`}>{title || <span className="pv-ph">标题…</span>}</div>
                )}
                {isEdit
                  ? <textarea className="field" style={{ minHeight: 120 }} value={text} autoFocus
                      onChange={(e) => setOverrides((o) => ({ ...o, [p.key]: e.target.value }))} />
                  : <div className="pv-text">{text || <span className="pv-ph">正文预览…</span>}{adapting && overrides[p.key] != null && <span className="streaming-cursor" />}</div>}
              </div>
              {ps && (
                <div className={`pv-pubstate ${ps.status}`}>
                  {ps.status === 'publishing' && <span className="live-pulse" />}
                  {ps.status === 'ok' ? '✅ ' : ps.status === 'fail' ? '⚠️ ' : ''}{ps.msg}
                </div>
              )}
              {p.key === 'xiaohongshu' && needsReview && (
                <div className="pv-review">
                  <p className="pv-hint">请先打开小红书草稿箱，看看上一篇是否已经保存。确认后点「我已核对，继续」，再重新提交。</p>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button type="button" className="btn btn-sm" disabled={!!clientBusy} onClick={openClientDrafts}>
                      {clientBusy === 'open' ? '正在打开草稿箱…' : '打开草稿箱'}
                    </button>
                    <button type="button" className="btn btn-sm" disabled={!!clientBusy} onClick={reviewClientDraft}>
                      {clientBusy === 'review' ? '正在解除保护…' : '我已核对，继续'}
                    </button>
                  </div>
                </div>
              )}
              <div className="pv-foot">
                <span className="pv-hint">{p.hint}{over ? ' · 已超字数' : ''}</span>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="pv-copy" onClick={() => setEditing(isEdit ? null : p.key)}>
                    <IconEdit size={13} />{isEdit ? '完成' : '编辑'}
                  </button>
                  <button className="pv-copy" onClick={() => copyFor(p.key)}>
                    {copied === p.key ? <IconCheck size={13} /> : <IconCopy size={13} />}{copied === p.key ? '已复制' : '复制'}
                  </button>
                </div>
              </div>
            </div>
          );
        })}
        </section><aside className="mingya-publish-settings"><section className="mingya-publish-review"><h2><span className="mingya-publish-section-rule" aria-hidden="true" />审核设置</h2><p>发布前会检查内容与人设一致性，并在真实提交前再次确认。</p><p>人工逐版本审核与导出请切换“人工审核发布”。</p></section><section className="mingya-publish-personalization"><h2><span className="mingya-publish-section-rule" aria-hidden="true" />平台个性化设置</h2>{[...PLATFORMS].sort((a,b) => DISPLAY_ORDER.indexOf(a.key) - DISPLAY_ORDER.indexOf(b.key)).map((p) => <div key={p.key} className="mingya-personalization-row"><ReferenceArtwork page="accounts" crop={PLATFORM_ART[p.key]} className="mingya-personalization-icon" /><span>{p.key === 'wechat-oa' ? '微信公众号' : p.label}</span><button type="button" disabled={!platforms.includes(p.key)} onClick={() => { setPreviewPlatform(p.key); setEditing(p.key); }}>{platforms.includes(p.key) ? '单独设置 ›' : '先选平台'}</button></div>)}</section></aside></div>
        <div className="publish-actions">
          {adapting ? (
            <button className="btn btn-sm" onClick={stopAdapt}><IconStop size={13} /> 停止生成</button>
          ) : (
            <button className="btn btn-sm btn-primary" disabled={empty || platforms.length === 0} onClick={adapt}>
              <IconSkills size={14} /> 一键适配各平台
            </button>
          )}
          <button className="btn btn-sm" disabled={empty || checking || bgChecking || adapting} onClick={check}>
            <IconCheck size={14} /> {checking || bgChecking ? '预检中…' : '发布前预检'}
          </button>
          <button className="btn btn-sm" disabled={empty} onClick={() => addToCalendar(platforms[0] || 'xiaohongshu')}>
            <IconCalendar size={14} /> 存本地草稿并排期
          </button>
          <button className="btn btn-sm btn-primary" disabled={empty || publishing || checking || !canPublish}
            title={canPublish ? hasDraftTarget ? '小红书图文存当前浏览器草稿，视频走服务端；公众号存平台草稿，其他所选平台公开发布' : '真实发布到已登录平台' : '所选平台无一键发布（B站走终端 biliup）'}
            onClick={publishAll}>
            <IconPublish size={14} /> {publishing ? hasDraftTarget ? '提交中…' : '发布中…' : submitLabel}
          </button>
          <button className="btn btn-sm btn-ghost" disabled={empty || adapting}
            onClick={() => { setTitle(''); setBody(''); setTags(''); setOverrides({}); setCheckResult(''); setPub({}); showToast('已清空'); }}>
            <IconTrash size={13} /> 清空
          </button>
        </div>
        {adapting && <div className="adapt-hint"><span className="live-pulse" />AI 正在逐字改写各平台版本…可随时停止。</div>}
      </div>

      {toast && <div className="toast ok"><span className="toast-icon">✓</span>{toast}</div>}

      {pubSms && (
        <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) setPubSms(null); }}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 380 }}>
            <h3 style={{ margin: '0 0 4px' }}>发布验证 · {pubSms.name}</h3>
            <p style={{ fontSize: 13, color: /错误|过期|失败|重新|未完成|不正确|失效/.test(pubSms.message || '') ? 'var(--red)' : 'var(--text-secondary)' }}>
              {pubSms.message || '平台风控要求短信验证，验证码已发到你手机，请输入：'}
            </p>
            {pubSms.state === 'verifying' ? (
              <div className="dash-empty" style={{ padding: 16 }}>正在验证验证码…</div>
            ) : (
              <>
                <input inputMode="numeric" autoFocus
                  placeholder="请输入手机收到的验证码" value={pubSmsCode}
                  onChange={(e) => setPubSmsCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                  onKeyDown={(e) => { if (e.key === 'Enter') submitPubSms(); }}
                  style={{ width: '100%', boxSizing: 'border-box', textAlign: 'center',
                    letterSpacing: 6, fontSize: 20, padding: '10px 12px', margin: '4px 0 10px',
                    border: '1px solid var(--border)', borderRadius: 8 }} />
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                  <button className="btn btn-sm btn-ghost" onClick={() => setPubSms(null)}>关闭</button>
                  <button className="btn btn-sm btn-primary" disabled={pubSmsBusy} onClick={submitPubSms}>
                    {pubSmsBusy ? '提交中…' : '提交验证码'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
