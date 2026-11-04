import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import type { CSSProperties } from 'react';
import { fetchOutputs, fetchOutputContent, mediaUrl, deleteOutput } from '../lib/api';
import type { OutputNode, OutputMeta } from '../lib/api';
import { renderMarkdown } from '../lib/sanitize';
import { PageBanner, ReferenceArtwork } from './MingyaPrimitives';
import '../styles/mingya-library.css';
import { IconOutputs, IconImage, IconVideo, IconMusic, IconFile, IconFolder, IconRefresh, IconChevron, IconTrash } from './icons';

const FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'image', label: '图片' },
  { key: 'video', label: '视频' },
  { key: 'audio', label: '音频' },
  { key: 'text', label: '文档' },
  { key: 'other', label: '其他' },
];

const KIND_LABEL: Record<string, string> = {
  article: '文章', 'xhs-note': '小红书', video: '视频', cards: '卡片',
  poster: '海报', audio: '音频', other: '其他',
};
const PLATFORM_LABEL: Record<string, string> = { xiaohongshu: '小红书', xhs: '小红书', 'wechat-oa': '微信公众号' };
const STATUS_LABEL: Record<string, string> = { draft: '草稿', ready: '待发', published: '已发' };
const STATUS_COLOR: Record<string, string> = { draft: 'var(--text-tertiary)', ready: 'var(--amber)', published: 'var(--green)' };

const badge: CSSProperties = {
  fontSize: 11, padding: '1px 7px', borderRadius: 999,
  background: 'var(--surface-hover)', color: 'var(--text-secondary)', whiteSpace: 'nowrap',
};
const statusBadge = (s: string): CSSProperties => ({
  ...badge,
  background: `color-mix(in srgb, ${STATUS_COLOR[s] || 'var(--text-tertiary)'} 13%, transparent)`,
  color: STATUS_COLOR[s] || 'var(--text-secondary)',
});

function kindIcon(kind: string | undefined, size = 30) {
  if (kind === 'video') return <IconVideo size={size} />;
  if (kind === 'audio') return <IconMusic size={size} />;
  if (kind === 'image') return <IconImage size={size} />;
  return <IconFile size={size} />;
}
function OpenIcon() {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 3h7v7M21 3l-9 9M10 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-5" /></svg>;
}
function fileSize(bytes?: number) {
  if (bytes === undefined) return '';
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
const isHtml = (name: string) => /\.html?$/i.test(name);
const kindLabel = (f: OutputNode) =>
  f.kind === 'text' ? (isHtml(f.name) ? '卡片' : '文档')
    : f.kind === 'image' ? '图片' : f.kind === 'video' ? '视频' : f.kind === 'audio' ? '音频' : '文件';

/** 递归找目录下第一张图/视频作封面缩略图。 */
function firstMedia(node: OutputNode): OutputNode | null {
  if (node.type === 'file') return (node.kind === 'image' || node.kind === 'video') ? node : null;
  for (const c of node.children || []) {
    const m = firstMedia(c);
    if (m) return m;
  }
  return null;
}

/** 展示头声明的封面 → 伪 file 节点（供 Thumb 渲染）。 */
function coverNode(m?: OutputMeta): OutputNode | null {
  if (!m?.cover) return null;
  const kind = /\.(mp4|mov|webm|mkv)$/i.test(m.cover) ? 'video' : 'image';
  return { name: 'cover', type: 'file', path: m.cover, kind } as OutputNode;
}

/** 按名称路径解析到当前目录的 children（stackNames 稳定，刷新后仍有效）。 */
function resolvePath(roots: OutputNode[], names: string[]): OutputNode[] {
  let nodes = roots;
  for (const nm of names) {
    const found = nodes.find((n) => n.type === 'dir' && n.name === nm);
    if (!found) return nodes;   // 路径失效（被删/改）→ 停在能解析到的层
    nodes = found.children || [];
  }
  return nodes;
}

function Thumb({ f, big }: { f: OutputNode | null; big?: boolean }) {
  if (f && f.kind === 'image') return <img src={mediaUrl(f.path)} alt="" loading="lazy" />;
  if (f && f.kind === 'video') return <video src={mediaUrl(f.path)} preload="metadata" muted />;
  return <div className="gcard-ph">{kindIcon(f?.kind, big ? 34 : 30)}</div>;
}

interface OutputsPageProps {
  /** 从对话跳转进来的目录（outputs 相对路径，不含 outputs/ 前缀）；消费后由父层清空 */
  jumpPath?: string;
  onJumpHandled?: () => void;
}

export default function OutputsPage({ jumpPath, onJumpHandled }: OutputsPageProps) {
  const [roots, setRoots] = useState<OutputNode[]>([]);
  const [treeError, setTreeError] = useState('');
  const [stack, setStack] = useState<string[]>([]);   // 当前所在的文件夹名称路径
  const [filter, setFilter] = useState('all');
  const [platform, setPlatform] = useState('all');
  const [selected, setSelected] = useState<OutputNode | null>(null);
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const reqSeq = useRef(0);

  const load = useCallback(() => {
    setTreeError('');
    fetchOutputs().then(setRoots).catch(() => setTreeError('加载产物列表失败'));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    window.addEventListener('mingya:refresh-outputs', load);
    return () => window.removeEventListener('mingya:refresh-outputs', load);
  }, [load]);

  const currentNodes = useMemo(() => resolvePath(roots, stack), [roots, stack]);
  const platformOptions = useMemo(() => [...new Set(roots.map((node) => node.meta?.platform).filter((value): value is string => !!value))], [roots]);
  const activeProjectPlatform = stack.length ? roots.find((node) => node.name === stack[0])?.meta?.platform : undefined;
  const platformNodes = useMemo(() => platform === 'all' ? currentNodes : currentNodes.filter((node) => (node.meta?.platform || activeProjectPlatform || 'unmarked') === platform), [currentNodes, platform, activeProjectPlatform]);
  const dirs = useMemo(
    () => platformNodes.filter((n) => n.type === 'dir').sort((a, b) => (b.mtime || 0) - (a.mtime || 0)),
    [platformNodes]);
  const files = useMemo(() => {
    const fs = platformNodes.filter((n) => n.type === 'file').sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    return filter === 'all' ? fs : fs.filter((f) => filter === 'other' ? !['image', 'video', 'audio', 'text'].includes(f.kind || '') : f.kind === filter);
  }, [platformNodes, filter]);

  const atTop = stack.length === 0;
  const atProjectRoot = stack.length === 1;
  // 当前项目的展示头（进入项目后才有），用于「成品/素材」分区
  const projectMeta = useMemo(
    () => (stack.length >= 1 ? roots.find((r) => r.name === stack[0])?.meta : undefined),
    [roots, stack]);
  const deliverableSet = useMemo(
    () => new Set(atProjectRoot ? (projectMeta?.deliverablePaths || []) : []),
    [projectMeta, atProjectRoot]);
  const hasSplit = atProjectRoot && deliverableSet.size > 0;
  const deliverableFiles = useMemo(
    () => (hasSplit ? files.filter((f) => deliverableSet.has(f.path)) : []),
    [files, deliverableSet, hasSplit]);
  const restFiles = useMemo(
    () => (hasSplit ? files.filter((f) => !deliverableSet.has(f.path)) : files),
    [files, deliverableSet, hasSplit]);

  const enterDir = useCallback((name: string) => { setStack((s) => [...s, name]); setFilter('all'); setPlatform('all'); }, []);
  const goTo = useCallback((depth: number) => { setStack((s) => s.slice(0, depth)); setFilter('all'); setPlatform('all'); }, []);

  const remove = useCallback(async (node: OutputNode, e: React.MouseEvent) => {
    e.stopPropagation();
    const isDir = node.type === 'dir';
    const label = isDir ? `项目/文件夹「${node.meta?.title || node.name}」及其全部内容` : `文件「${node.name}」`;
    if (!window.confirm(`确定删除${label}？\n此操作不可恢复。`)) return;
    try {
      await deleteOutput(node.path);
      setSelected((cur) => (cur?.path === node.path ? null : cur));
      load();
    } catch (err) {
      alert((err as Error).message || '删除失败');
    }
  }, [load]);

  const open = useCallback(async (f: OutputNode) => {
    const seq = ++reqSeq.current;
    setSelected(f); setContent('');
    if (f.kind === 'text' && !isHtml(f.name)) {
      setLoading(true);
      try {
        const res = await fetchOutputContent(f.path);
        if (seq === reqSeq.current) setContent(res.isBinary ? '' : res.content);
      } finally { if (seq === reqSeq.current) setLoading(false); }
    }
  }, []);

  // 等内容树加载后区分文件与目录；文件链接直接打开预览。
  useEffect(() => {
    if (!jumpPath || roots.length === 0) return;
    const parts = jumpPath.split('/').filter(Boolean);
    const parent: string[] = [];
    let nodes = roots;
    let node: OutputNode | undefined;
    for (const part of parts) {
      node = nodes.find(candidate => candidate.name === part);
      if (!node || node.type !== 'dir') break;
      parent.push(part);
      nodes = node.children || [];
    }
    if (node?.path !== parts.join('/')) node = undefined;
    setStack(parent);
    setFilter('all');
    setPlatform('all');
    if (node?.type === 'file') void open(node);
    else setSelected(null);
    onJumpHandled?.();
  }, [jumpPath, roots, open, onJumpHandled]);

  const preview = () => {
    if (!selected) return null;
    const url = mediaUrl(selected.path);
    if (selected.kind === 'image') return <img src={url} alt={selected.name} style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
    if (selected.kind === 'video') return <video src={url} controls style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
    if (selected.kind === 'audio') return <audio src={url} controls style={{ width: '100%' }} />;
    if (selected.kind === 'text' && isHtml(selected.name)) return (
      <>
        {/* allow-scripts：让预览页自带的「复制到公众号」按钮(execCommand('copy'))能运行；
            allow="clipboard-write"：授予剪贴板写权限。不给 allow-same-origin —— iframe 保持
            opaque origin，脚本跑得起来但访问不到本站，安全。(修复：内嵌预览里复制按钮点了没反应) */}
        <iframe src={`${url}?v=${selected.mtime ?? 0}`} title={selected.name} sandbox="allow-scripts" allow="clipboard-write"
          style={{ width: '100%', height: '68vh', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: '#fff' }} />
        <div style={{ marginTop: 8 }}><a href={url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-start)', fontSize: 13 }}>在新标签打开 ↗</a></div>
      </>
    );
    if (selected.kind === 'text') {
      if (loading) return <div className="loading"><div className="spinner" />加载中…</div>;
      return <div className="outputs-viewer-content" dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }} />;
    }
    return <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>无法预览。<a href={url} download style={{ color: 'var(--accent-start)' }}>下载 {selected.name}</a></div>;
  };

  /** 展示真实封面、文件与元数据；打开和删除操作保持原行为。 */
  const renderDir = (d: OutputNode) => {
    const m = d.meta;
    const cover = coverNode(m) || firstMedia(d);
    return (
      <div key={d.path} className="card card-hover gcard" onClick={() => enterDir(d.name)}>
        <button className="gcard-thumb" aria-label={`打开${m?.title || d.name}`}>
          {cover ? <Thumb f={cover} big /> : <div className="gcard-ph"><IconFolder size={54} /><span>文件夹</span></div>}
        </button>
        <div className="gcard-meta">
          <span className="gcard-file-icon"><IconOutputs size={25} /></span>
          <div className="gcard-description">
            <button className="gcard-name" title={m?.title || d.name}>{m?.title || d.name}</button>
            <div className="gcard-sub"><span>文件夹 · {d.fileCount ?? 0} 个文件</span>
              {m?.platform && <span style={badge}>{m.platform}</span>}
              {m?.kind && <span style={badge}>{KIND_LABEL[m.kind] || m.kind}</span>}
              {m?.status && <span style={statusBadge(m.status)}>{STATUS_LABEL[m.status] || m.status}</span>}
            </div>
          </div>
          <div className="gcard-actions">
            <button className="gcard-open" title="打开文件夹" aria-label={`打开${m?.title || d.name}`}><OpenIcon /></button>
            <button className="gcard-del" title="删除" aria-label={`删除${m?.title || d.name}`} onClick={(e) => remove(d, e)}><IconTrash size={23} /></button>
          </div>
        </div>
      </div>
    );
  };

  const renderFile = (f: OutputNode) => (
    <div key={f.path} className="card card-hover gcard" onClick={() => open(f)}>
      <button className="gcard-thumb" aria-label={`预览${f.name}`}><Thumb f={f} /></button>
      <div className="gcard-meta">
        <span className={`gcard-file-icon gcard-file-${f.kind}`}>{kindIcon(f.kind, 25)}</span>
        <div className="gcard-description">
          <button className="gcard-name" title={f.name}>{f.name}</button>
          <div className="gcard-sub"><span>{f.name.includes('.') ? f.name.split('.').pop()?.toUpperCase() : kindLabel(f)}{f.size !== undefined ? ` · ${fileSize(f.size)}` : ''}</span><span>共 1 个文件</span></div>
        </div>
        <div className="gcard-actions">
          <button className="gcard-open" title="打开预览" aria-label={`预览${f.name}`}><OpenIcon /></button>
          <button className="gcard-del" title="删除" aria-label={`删除${f.name}`} onClick={(e) => remove(f, e)}><IconTrash size={23} /></button>
        </div>
      </div>
    </div>
  );

  const empty = dirs.length === 0 && files.length === 0;

  return (
    <div className="gallery-page">
      <PageBanner page="outputs" title="内容库" subtitle="笔记、图片与视频，在这里整理"
        actions={<button className="btn library-refresh" onClick={load}><IconRefresh size={21} />刷新</button>}>
        <ReferenceArtwork page="outputs" crop={[860, 64, 662, 185]} className="library-banner-art" />
      </PageBanner>
      {treeError && <div className="notice-error">{treeError}</div>}
      <div className="library-filters">
        <div className="library-filter-row"><span className="library-filter-label">平台：</span>
          <button className={`chip ${platform === 'all' ? 'active' : ''}`} onClick={() => setPlatform('all')}>全部</button>
          {platformOptions.map((value) => <button key={value} className={`chip ${platform === value ? 'active' : ''}`} onClick={() => setPlatform(value)}>{PLATFORM_LABEL[value] || value}</button>)}
          {roots.some((node) => !node.meta?.platform) && <button className={`chip ${platform === 'unmarked' ? 'active' : ''}`} onClick={() => setPlatform('unmarked')}>未标记</button>}
        </div>
        <div className="library-filter-row"><span className="library-filter-label">文件类型：</span>
          {FILTERS.map((f) => <button key={f.key} className={`chip ${filter === f.key ? 'active' : ''}`} onClick={() => setFilter(f.key)}>
            {f.key !== 'all' && kindIcon(f.key, 23)}{f.label}
          </button>)}
        </div>
      </div>
      {stack.length > 0 && <div className="library-location">
        <button className="btn btn-sm" onClick={() => goTo(stack.length - 1)}><span style={{ transform: 'rotate(180deg)', display: 'inline-flex' }}><IconChevron size={15} /></span>返回上级</button>
        <button className="crumb" onClick={() => goTo(0)}>内容库</button>
        {stack.map((name, i) => <span key={i}><span className="crumb-sep">/</span><button className="crumb" onClick={() => goTo(i + 1)}>{i === 0 ? projectMeta?.title || name : name}</button></span>)}
        <span className="library-count">{dirs.length} 个文件夹 · {files.length} 个文件</span>
      </div>}
      {atProjectRoot && !!projectMeta?.tags?.length && <div className="library-tags">{projectMeta.tags.map((tag) => <span key={tag} style={badge}>#{tag}</span>)}</div>}
      {empty && !treeError ? (
        <div className="empty-state" style={{ height: 300 }}>
          <div className="library-empty-thumbnails" aria-hidden="true"><span><IconImage size={40} /></span><span><IconFolder size={40} /></span><span><IconFile size={40} /></span></div>
          <p>{atTop ? '还没有产物——去对话或技能库生成第一条内容吧' : '这个文件夹是空的'}</p>
        </div>
      ) : hasSplit ? (
        <>
          {/* 成品区 */}
          {deliverableFiles.length > 0 && (
            <>
              <div className="section-label" style={{ margin: '6px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>
                成品 · {deliverableFiles.length}
              </div>
              <div className="gallery-grid">{deliverableFiles.map(renderFile)}</div>
            </>
          )}
          {/* 素材 / 过程文件区 */}
          {(dirs.length > 0 || restFiles.length > 0) && (
            <>
              <div className="section-label" style={{ margin: '18px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-tertiary)' }}>
                素材 / 过程文件
              </div>
              <div className="gallery-grid">
                {dirs.map(renderDir)}
                {restFiles.map(renderFile)}
              </div>
            </>
          )}
        </>
      ) : (
        <div className="gallery-grid">
          {dirs.map(renderDir)}
          {files.map(renderFile)}
        </div>
      )}

      {selected && (
        <div className="drawer-overlay" onClick={() => setSelected(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ minWidth: 0 }}>
                <div className="skill-detail-title" style={{ fontSize: 16 }}>{selected.name}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 3, fontFamily: "'SF Mono','Consolas',monospace" }}>{selected.path}</div>
              </div>
              <button className="icon-btn" onClick={() => setSelected(null)}>×</button>
            </div>
            <div className="drawer-body">{preview()}</div>
            <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn btn-sm btn-danger" onClick={(e) => remove(selected, e)}><IconTrash size={13} /> 删除此文件</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
