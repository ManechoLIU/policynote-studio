import { useState, useEffect, useMemo } from 'react';
import type { ComponentType } from 'react';
import { fetchSkills } from '../lib/api';
import type { SkillItem } from '../lib/api';
import SkillDrawer from './SkillDrawer';
import { PageBanner } from './MingyaPrimitives';
import '../styles/mingya-skills.css';
import { displayName } from '../lib/skillDisplayNames';
import {
  IconSearch, IconCompass, IconSkills, IconSend, IconChart, IconLayers,
  IconVideo, IconImage, IconMusic, IconMic, IconText, IconLayout, IconProfile, IconOutputs, IconChevron, IconCheck,
} from './icons';

interface SkillPageProps {
  persona: string;
}

type IconC = ComponentType<{ size?: number }>;
type LayerMeta = { key: string; label: string; Icon: IconC; color: string };

const LAYERS: LayerMeta[] = [
  { key: 'discover', label: '发现', Icon: IconSearch, color: 'var(--layer-discover)' },
  { key: 'plan', label: '策划', Icon: IconCompass, color: 'var(--layer-plan)' },
  { key: 'produce', label: '制作', Icon: IconSkills, color: 'var(--layer-produce)' },
  { key: 'publish', label: '发布', Icon: IconSend, color: 'var(--layer-publish)' },
  { key: 'attribute', label: '归因', Icon: IconChart, color: 'var(--layer-attribute)' },
  { key: 'general', label: '通用', Icon: IconLayers, color: 'var(--layer-general)' },
];
const LAYER_META: Record<string, LayerMeta> = Object.fromEntries(LAYERS.map((l) => [l.key, l]));
const OTHER: LayerMeta = { key: 'other', label: '其他', Icon: IconLayers, color: 'var(--layer-general)' };

// 按 skill 名关键词映射线性图标（无匹配退回层图标）
function iconFor(name: string, LayerIcon: IconC): IconC {
  const n = name.toLowerCase();
  const map: [RegExp, IconC][] = [
    [/video|clip|reframe|highlight|beat|slideshow|intro|chapter/, IconVideo],
    [/image|img|photo|poster|infographic|comparison|meme|remove-bg|green-screen|enhance/, IconImage],
    [/music|audio|bgm|mix|denoise/, IconMusic],
    [/voice|tts|clone|subtitle/, IconMic],
    [/card|xhs|xiaohongshu|note/, IconLayout],
    [/copy|writ|text|polish|condens|style|format|content/, IconText],
    [/chart|data|report|roi|analy|insight|scor/, IconChart],
    [/publish|upload|wechat|zhihu|bilibili|douyin|kuaishou|channel/, IconSend],
    [/rss|trend|news|discover|ugc|competitor|hot/, IconSearch],
    [/profile|persona|brand|position|audience/, IconProfile],
    [/schedul|calendar|plan|strategy|campaign|matrix/, IconCompass],
    [/asset|template|batch|doc|mindmap|link/, IconOutputs],
  ];
  for (const [re, C] of map) if (re.test(n)) return C;
  return LayerIcon;
}

const LAYER_DESC: Record<string, string> = {
  discover: '发现层技能', plan: '策划层技能', produce: '制作层技能',
  publish: '发布层技能', attribute: '归因层技能', general: '通用技能', other: '技能',
};

export default function SkillPage({ persona }: SkillPageProps) {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [activeLayer, setActiveLayer] = useState('discover');

  const load = () => {
    fetchSkills().then(setSkills).catch(() => setError('加载 SKILL 列表失败'));
  };
  useEffect(load, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((s) =>
      s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q) || displayName(s.name).toLowerCase().includes(q));
  }, [skills, query]);

  const grouped = useMemo(() => {
    const g: Record<string, SkillItem[]> = {};
    for (const s of filtered) {
      const key = LAYER_META[s.layer] ? s.layer : 'other';
      (g[key] ||= []).push(s);
    }
    return g;
  }, [filtered]);

  const orderedLayers = [...LAYERS, OTHER].filter((l) => grouped[l.key]?.length);
  const layerHelp: Record<string, string> = {
    discover: '找趋势、找话题、找灵感', plan: '明确方向、拆解选题', produce: '产出图文、图片与视频',
    publish: '高效发布、规范管理', attribute: '复盘内容、沉淀经验', general: '日常创作、通用工具', other: '更多创作辅助工具',
  };

  return (
    <div className="skills-page strict-skills-page">
      <PageBanner page="skills" title="技能库" subtitle="为内容创作，找到顺手的工具">
        <form className="strict-skill-search" onSubmit={(e) => e.preventDefault()} role="search">
          <label className="strict-skill-search-field"><IconSearch size={25} /><input aria-label="搜索技能名或描述" placeholder="搜索技能名或描述…" value={query} onChange={(e) => setQuery(e.target.value)} />{query && <button type="button" className="strict-search-clear" onClick={() => setQuery('')}>清除</button>}</label>
          <button type="submit" className="strict-skill-search-submit">搜索</button>
        </form>
      </PageBanner>

      {error && <div className="strict-skills-error">{error}</div>}

      <div className="strict-skills-layout">
        <nav className="strict-skills-layers" aria-label="技能层级">
          {[...LAYERS, ...(grouped.other?.length ? [OTHER] : [])].map((layer) => (
            <button key={layer.key} className={`strict-layer-item ${activeLayer === layer.key ? 'is-active' : ''}`} onClick={() => { setActiveLayer(layer.key); document.getElementById(`skill-layer-${layer.key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>
              <span className="strict-layer-icon"><layer.Icon size={37} /></span>
              <span className="strict-layer-copy"><strong>{layer.label}{layer.key === 'general' || layer.key === 'other' ? '' : '层'}</strong><span>{layerHelp[layer.key]}</span></span>
              <IconChevron size={21} />
            </button>
          ))}
        </nav>
        <div className="strict-skills-results">
          {orderedLayers.length === 0 && !error && <div className="empty-state"><IconSearch size={40} /><p>没有匹配「{query}」的技能</p></div>}
          <div className="strict-skills-grid">
            {orderedLayers.flatMap((layer) => grouped[layer.key].map((s, index) => {
              const meta = LAYER_META[s.layer] || OTHER;
              const Icon = iconFor(s.name, meta.Icon);
              const alert = s.needsApi && !s.apiConfigured;
              const statusClass = !s.needsApi ? 'not-required' : alert ? 'needs-api' : 'configured';
              const statusLabel = !s.needsApi ? '无需 API' : alert ? '需 API' : '已配置';
              return (
                <button type="button" key={s.name} id={index === 0 ? `skill-layer-${layer.key}` : undefined} className="strict-skill-card" onClick={() => setSelected(s.name)} title={`${displayName(s.name)} · ${s.name}`}>
                  <span className="strict-skill-card-head"><span className={`strict-skill-icon ${['discover', 'produce'].includes(s.layer) ? 'is-warm' : ''}`}><Icon size={38} /></span><span className="strict-skill-heading"><strong>{displayName(s.name)}</strong><span>技术名：{s.name}</span></span></span>
                  <span className="strict-skill-description">{s.description?.trim() || LAYER_DESC[layer.key]}</span>
                  <span className="strict-skill-footer"><span><span className={`strict-skill-status ${statusClass}`}>{s.needsApi ? (alert ? <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M7 18H5a4 4 0 0 1-.5-8A6 6 0 0 1 16 7a5 5 0 0 1 3 9h-2M12 21V11m-4 4 4-4 4 4" /></svg> : <span className="strict-configured-check"><IconCheck size={13} /></span>) : null}{statusLabel}</span></span><span className="strict-skill-details">查看详情<IconChevron size={16} /></span></span>
                </button>
              );
            }))}
          </div>
        </div>
      </div>

      {selected && <SkillDrawer skillName={selected} persona={persona} onClose={() => setSelected(null)} onConfigured={load} />}
    </div>
  );
}
