import { useState, useEffect } from 'react';
import { fetchPersonaFiles, savePersonaFile, deletePersona, fetchAccountAnalytics } from '../lib/api';
import type { PersonaFile, AccountAnalytics } from '../lib/api';
import { renderMarkdown } from '../lib/sanitize';
import { marked } from 'marked';
import type { Token } from 'marked';
import { PageBanner, ReferenceArtwork } from './MingyaPrimitives';
import { IconEdit, IconTrash, IconPlus, IconCheck, IconChart, IconFile } from './icons';
import '../styles/mingya-profile.css';

// 粉丝量级：把粉丝数映射成人话档位（画像里“粉丝量级”一栏要的是量级而非精确值）
function fanTier(n: number): string {
  if (n <= 0) return '新号 / 0';
  if (n < 500) return '冷启动 <500';
  if (n < 1000) return '数百';
  if (n < 5000) return '千级';
  if (n < 10000) return '数千';
  if (n < 100000) return '万级';
  if (n < 1000000) return '十万级';
  return '百万+';
}

// 用注释围栏包住自动抓取段，重复抓取只替换该段、不动用户手写内容（幂等）
const WX_START = '<!-- wechat-oa:auto:start -->';
const WX_END = '<!-- wechat-oa:auto:end -->';

function buildWechatBlock(a: AccountAnalytics): string {
  const fans = a.followers ?? 0;
  const posts = a.posts ?? 0;
  const reads = a.likes ?? 0;
  const tops = [...new Set(a.notes.map((n) => n.title).filter(Boolean))].slice(0, 3);
  const stamp = new Date().toLocaleString('zh-CN', { hour12: false });
  const lines = [
    `### 微信公众号（自动抓取 · ${stamp}）`,
    '',
    `- **粉丝量级**：${fans}（${fanTier(fans)}）`,
    `- **内容形式**：图文推文（公众号文章）；已发表 ${posts} 篇${reads ? `，累计阅读 ${reads}` : ''}`,
  ];
  if (a.metrics?.length) {
    lines.push(`- **后台数据**：${a.metrics.map((m) => `${m.label} ${m.value}`).join(' · ')}`);
  }
  if (tops.length) lines.push(`- **代表内容**：${tops.join('｜')}`);
  return lines.join('\n');
}

function mergeWechatBlock(existing: string, block: string): string {
  const wrapped = `${WX_START}\n${block}\n${WX_END}`;
  const s = existing.indexOf(WX_START);
  const e = existing.indexOf(WX_END);
  if (s !== -1 && e !== -1 && e > s) {
    return existing.slice(0, s) + wrapped + existing.slice(e + WX_END.length);
  }
  const head = existing.replace(/\s+$/, '');
  return head ? `${head}\n\n${wrapped}` : wrapped;
}

interface ProfilePageProps {
  persona: string;
  onNewProfile: () => void;
  onDeleted: (name: string) => void;
}

const DIM_META: Record<string, { label: string; description: string; crop: readonly [number, number, number, number] }> = {
  'identity.md': { label: '身份定位', description: '我是谁、做什么、为谁解决什么问题', crop: [295, 271, 70, 70] },
  'style.md': { label: '内容风格', description: '表达方式、语气与整体调性', crop: [295, 415, 70, 70] },
  'audience.md': { label: '目标受众', description: '我主要服务的读者是谁', crop: [295, 552, 70, 70] },
  'platforms.md': { label: '平台运营', description: '在小红书上的定位与使用方式', crop: [295, 681, 70, 70] },
  'preferences.md': { label: '偏好与红线', description: '内容倾向与不做的边界', crop: [295, 800, 70, 70] },
  'memory.md': { label: '经验沉淀', description: '我在创作中积累的思路与方法', crop: [295, 918, 70, 70] },
};

/** 收起态直接取原文第一个正文段落，仅去除 Markdown 标记，不生成摘要。 */
function firstBodyParagraph(markdown: string): string {
  const plainInline = (tokens: Token[]): string => tokens.map((token) => {
    if (token.type === 'html') return '';
    if (token.type === 'br') return ' ';
    if ('tokens' in token && Array.isArray(token.tokens)) return plainInline(token.tokens);
    return 'text' in token && typeof token.text === 'string' ? token.text : '';
  }).join('');
  const withoutComments = markdown.replace(/<!--[\s\S]*?-->/g, '');
  for (const token of marked.lexer(withoutComments)) {
    if (token.type === 'paragraph' || token.type === 'text') {
      const text = plainInline(marked.Lexer.lexInline(token.text)).trim();
      if (text) return text;
    }
    if (token.type === 'list') {
      for (const item of token.items) {
        const text = firstBodyParagraph(item.text);
        if (text) return text;
      }
    }
    if (token.type === 'blockquote') {
      const text = firstBodyParagraph(token.text);
      if (text) return text;
    }
  }
  return '';
}

export default function ProfilePage({ persona, onNewProfile, onDeleted }: ProfilePageProps) {
  const [files, setFiles] = useState<PersonaFile[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [savingFile, setSavingFile] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState('');
  const [fetchingWx, setFetchingWx] = useState(false);
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [editingFiles, setEditingFiles] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!persona) { setFiles([]); return; }
    let ignore = false;   // 切 persona 丢弃旧请求（F2）
    setLoading(true);
    setError('');
    setEditing(false);
    setExpandedFiles(new Set());
    setEditingFiles(new Set());
    fetchPersonaFiles(persona)
      .then((d) => {
        if (ignore) return;
        setFiles(d.files);
        setDrafts(Object.fromEntries(d.files.map((f) => [f.filename, f.content])));
      })
      .catch(() => { if (!ignore) setError('加载画像失败'); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [persona]);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 2500); };

  const handleSave = async (filename: string) => {
    setSavingFile(filename);
    try {
      await savePersonaFile(persona, filename, drafts[filename] ?? '');
      setFiles((prev) => prev.map((f) => f.filename === filename ? { ...f, content: drafts[filename] ?? '' } : f));
      showToast(`已保存 ${DIM_META[filename]?.label || filename} ✓`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSavingFile('');
    }
  };

  // 「平台运营」一键抓取公众号数据：复用数据中心同一个 mp 后台会话接口，把粉丝量级/内容形式
  // 抓完“直接落盘”并刷新视图——不进编辑态、不用用户点保存（否则会露出原始 markdown 破坏观感）。
  const handleFetchWechat = async (filename: string) => {
    setFetchingWx(true);
    try {
      const a = await fetchAccountAnalytics('wechat-oa');
      if (!a.loggedIn) {
        showToast('公众号后台未登录：请先到「账号」页扫码登录后再抓取');
        return;
      }
      const base = files.find((f) => f.filename === filename)?.content ?? '';
      const merged = mergeWechatBlock(base, buildWechatBlock(a));
      await savePersonaFile(persona, filename, merged);
      // 同步更新 files（视图）与 drafts（若正在编辑也一致），无需手动保存
      setFiles((prev) => prev.map((f) => f.filename === filename ? { ...f, content: merged } : f));
      setDrafts((p) => ({ ...p, [filename]: merged }));
      showToast(`已抓取并更新画像（粉丝 ${a.followers ?? 0}，已发表 ${a.posts ?? 0} 篇）✓`);
    } catch (e) {
      showToast(e instanceof Error ? `抓取失败：${e.message}` : '抓取失败（可能未登录或平台改版）');
    } finally {
      setFetchingWx(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`确定删除画像「${persona}」吗？\n此操作不可恢复，将删除该画像的全部六维文件。`)) return;
    setDeleting(true);
    try {
      await deletePersona(persona);
      onDeleted(persona);
      showToast(`已删除画像「${persona}」`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  const toggleExpanded = (filename: string) => setExpandedFiles((previous) => {
    const next = new Set(previous);
    if (next.has(filename)) next.delete(filename);
    else next.add(filename);
    return next;
  });

  const toggleFileEditing = (filename: string) => setEditingFiles((previous) => {
    const next = new Set(previous);
    if (next.has(filename)) next.delete(filename);
    else next.add(filename);
    return next;
  });

  if (!persona) {
    return (
      <div className="profile-page my-profile-page">
        <PageBanner page="profile" title="我的 IP" subtitle="六个维度构成一个完整人设，可随时编辑保存。"
          actions={<button className="btn btn-primary" onClick={onNewProfile}><IconPlus size={22} />新建画像</button>} />
        <section className="my-profile-empty">
          <ReferenceArtwork page="profile" crop={[295, 271, 70, 70]} className="my-profile-dim-art" />
          <div><h2>还没有选择画像</h2><p>画像沉淀你的定位、风格、受众与红线，生成内容会更贴合你的人设。</p>
            <button className="btn btn-primary" onClick={onNewProfile}><IconPlus size={20} />新建画像</button></div>
        </section>
      </div>
    );
  }

  return (
    <div className="profile-page my-profile-page">
      <PageBanner page="profile" title={persona} subtitle="六个维度构成一个完整人设，可随时编辑保存。"
        actions={<>
          <button className="btn btn-primary" onClick={() => {
            if (editing || editingFiles.size > 0) { setEditing(false); setEditingFiles(new Set()); }
            else setEditing(true);
          }}>
            {editing || editingFiles.size > 0 ? <IconCheck size={24} /> : <IconEdit size={24} />}
            {editing || editingFiles.size > 0 ? '完成编辑' : '编辑资料'}
          </button>
          <button className="btn my-profile-delete" disabled={deleting} onClick={handleDelete}>
            <IconTrash size={24} />{deleting ? '删除中…' : '删除画像'}
          </button>
        </>} />

      {error && <div className="my-profile-error" role="alert">{error}</div>}

      {loading ? (
        <div className="loading"><div className="spinner" />加载中…</div>
      ) : (
        <div className="my-profile-dimensions">
          {files.map((f) => {
            const meta = DIM_META[f.filename];
            const label = meta?.label || f.filename;
            const isEditing = editing || editingFiles.has(f.filename);
            const expanded = expandedFiles.has(f.filename);
            const dirty = isEditing && (drafts[f.filename] ?? '') !== f.content;
            const hasContent = f.content.trim().length > 0;
            return (
              <section key={f.filename} className={`my-profile-row${expanded || isEditing ? ' is-expanded' : ''}`}>
                <div className="my-profile-label">
                  {meta ? <ReferenceArtwork page="profile" crop={meta.crop} className="my-profile-dim-art" />
                    : <span className="my-profile-dim-fallback"><IconFile size={42} /></span>}
                  <div className="my-profile-label-copy"><h2><span className="my-profile-gold-rule" aria-hidden="true" />{label}</h2>
                    {meta && <p>{meta.description}</p>}</div>
                </div>
                <div className="my-profile-document">
                  <button className="my-profile-pencil" onClick={() => toggleFileEditing(f.filename)}
                    disabled={editing} aria-label={`编辑${label}`} title={`编辑${label}`}>
                    {editingFiles.has(f.filename) ? <IconCheck size={24} /> : <IconEdit size={24} />}
                  </button>
                  {isEditing ? <>
                    <textarea className="field my-profile-editor" aria-label={`${label}完整内容`}
                      value={drafts[f.filename] ?? ''}
                      onChange={(e) => setDrafts((p) => ({ ...p, [f.filename]: e.target.value }))} />
                    <div className="my-profile-document-actions">
                      <button className="btn btn-primary btn-sm" disabled={!dirty || savingFile === f.filename}
                        onClick={() => handleSave(f.filename)}><IconCheck size={17} />
                        {savingFile === f.filename ? '保存中…' : dirty ? '保存' : '已保存'}
                      </button>
                    </div>
                  </> : <>
                    {expanded ? <div className="profile-content my-profile-content is-expanded"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(f.content || '_（空）_') }} />
                      : <p className="my-profile-excerpt">{firstBodyParagraph(f.content) || '（暂无正文段落）'}</p>}
                    {(hasContent || f.filename === 'platforms.md') && <button className="my-profile-read-more" onClick={() => toggleExpanded(f.filename)}
                      aria-expanded={expanded}>{expanded ? '收起全文' : '阅读全文'}</button>}
                  </>}
                  {f.filename === 'platforms.md' && (expanded || isEditing) && <details className="my-profile-platform-tools">
                    <summary>公众号数据</summary>
                    <button className="btn btn-sm" disabled={fetchingWx}
                      title="用已登录的公众号后台会话抓取粉丝/内容数据，写入本栏（与数据中心同源）"
                      onClick={() => handleFetchWechat(f.filename)}><IconChart size={17} />
                      {fetchingWx ? '抓取中…' : '抓取公众号数据'}</button>
                  </details>}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {toast && <div className="toast ok" role="status"><IconCheck size={18} />{toast}</div>}
    </div>
  );
}
