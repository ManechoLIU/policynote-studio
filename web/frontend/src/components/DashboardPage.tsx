import { useState, useEffect, useRef } from 'react';
import '../styles/mingya-dashboard.css';
import {
  fetchTrends, fetchSchedule, fetchOutputs, fetchAccounts, fetchIdeas,
  fetchAnalyticsPlatforms, fetchAccountAnalytics,
} from '../lib/api';
import type {
  TrendGroup, ScheduleItem, OutputNode, AccountItem, Idea,
  AnalyticsPlatform, AccountAnalytics, AccountWhoami,
} from '../lib/api';
import type { Page } from './Sidebar';
import { BannerBackground, ReferenceArtwork, SectionMark, WorkspaceGlyph } from './MingyaPrimitives';
import { getWhoamiCache, verifyStale } from '../lib/whoami';
import {
  IconFire, IconCalendar, IconOutputs, IconChat, IconSkills, IconAccounts,
  IconIdea, IconPublish, IconPlus, IconFile, IconEdit, IconChevron,
} from './icons';

/** 大数格式化：12000 → 1.2万。 */
function fmtNum(n: number | null): string {
  if (n == null) return '—';
  const a = Math.abs(n);
  if (a >= 10000) return (n / 10000).toFixed(a >= 100000 ? 0 : 1) + '万';
  return String(n);
}
/** 增长量渲染信息：正=绿↑，负=红↓，0/缺失=不显示。 */
function growthInfo(n: number | null): { text: string; color: string } | null {
  if (n == null || n === 0) return null;
  return n > 0
    ? { text: `▲+${fmtNum(n)}`, color: 'var(--trend-up)' }
    : { text: `▼${fmtNum(Math.abs(n))}`, color: 'var(--trend-down)' };
}

interface DashboardProps {
  persona: string;
  gatewayStatus: string;
  apiDirect: boolean;
  agentRuntime: string;
  onNavigate: (page: Page) => void;
  onUseTopic: (title: string) => void;
}

const STATUS_LABEL: Record<string, string> = { idea: '选题', draft: '草稿', scheduled: '待发', published: '已发' };

export default function DashboardPage({ persona, gatewayStatus, apiDirect, agentRuntime, onNavigate, onUseTopic }: DashboardProps) {
  const [trends, setTrends] = useState<TrendGroup[]>([]);
  const [schedule, setSchedule] = useState<ScheduleItem[]>([]);
  const [outputs, setOutputs] = useState<OutputNode[]>([]);
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [loaded, setLoaded] = useState({ schedule: false, outputs: false, accounts: false, ideas: false });
  // 归因层：账号创作数据
  const [anaPlats, setAnaPlats] = useState<AnalyticsPlatform[]>([]);
  const [anaSel, setAnaSel] = useState('');
  const [anaData, setAnaData] = useState<Record<string, AccountAnalytics | 'loading' | 'error'>>(() => {
    try {
      const cache = JSON.parse(localStorage.getItem('easel_analytics') || '{}') as Record<string, AccountAnalytics | 'loading' | 'error'>;
      return Object.fromEntries(Object.entries(cache).filter(([platform, value]) =>
        value && typeof value === 'object' && value.platform === platform
        && typeof value.loggedIn === 'boolean'));
    } catch { return {}; }
  });
  const anaPending = useRef(new Set<string>());
  const [anaErrors, setAnaErrors] = useState<Record<string, string>>({});
  const [anaWin, setAnaWin] = useState<'last' | 'day' | 'week' | 'month' | 'year'>('week');
  // whoami 自愈：登录态以真实 profile 为准（与账号页共享 localStorage 缓存）
  const [whoamiMap, setWhoamiMap] = useState<Record<string, AccountWhoami>>(() => getWhoamiCache());

  useEffect(() => {
    fetchTrends('weibo,douyin', 6).then((d) => setTrends(d.trends)).catch(() => {});
    fetchSchedule().then((data) => { setSchedule(data); setLoaded((state) => ({ ...state, schedule: true })); }).catch(() => {});
    fetchOutputs().then((data) => { setOutputs(data); setLoaded((state) => ({ ...state, outputs: true })); }).catch(() => {});
    fetchAccounts().then((data) => { setAccounts(data); setLoaded((state) => ({ ...state, accounts: true })); }).catch(() => {});
    fetchIdeas().then((data) => { setIdeas(data); setLoaded((state) => ({ ...state, ideas: true })); }).catch(() => {});
    fetchAnalyticsPlatforms().then((ps) => {
      setAnaPlats(ps);
      const cache = getWhoamiCache();
      const isLog = (p: AnalyticsPlatform) => p.loggedIn || !!cache[p.platform]?.loggedIn;
      const first = ps.find(isLog);
      if (first) setAnaSel((s) => s || first.platform);
      // 开页后台自愈：对非 API 式的归因平台真校验（whoami），刷新登录态；
      // B 站走 cookie、公众号走凭证/官方 API 判定，都不起浏览器。
      const API_BASED = new Set(['bilibili', 'wechat-oa']);
      verifyStale(ps.filter((p) => !API_BASED.has(p.platform)).map((p) => p.platform), {
        onUpdate: (platform, r) => {
          setWhoamiMap((m) => ({ ...m, [platform]: r }));
          if (r.loggedIn) setAnaSel((s) => s || platform);
        },
      });
    }).catch(() => {});
  }, []);

  const runAna = (platform: string) => {
    setAnaSel(platform);
    if (anaPending.current.has(platform)) return;
    anaPending.current.add(platform);
    setAnaErrors((errors) => ({ ...errors, [platform]: '' }));
    setAnaData((d) => ({ ...d, [platform]: 'loading' }));
    fetchAccountAnalytics(platform)
      .then((r) => setAnaData((d) => {
        const next = { ...d, [platform]: r };
        const cache = Object.fromEntries(Object.entries(next).filter(([, value]) =>
          value && typeof value === 'object'));
        try { localStorage.setItem('easel_analytics', JSON.stringify(cache)); } catch { /* quota */ }
        return next;
      }))
      .catch((error: unknown) => {
        setAnaErrors((errors) => ({ ...errors, [platform]: error instanceof Error
          ? error.message : '抓取失败，请稍后重试' }));
        setAnaData((d) => ({ ...d, [platform]: 'error' as const }));
      })
      .finally(() => anaPending.current.delete(platform));
  };

  const runtimeNote = gatewayStatus === 'connecting' ? '连接中…'
    : apiDirect ? (gatewayStatus === 'connected' ? '文本对话 API 已配置 · 当前为仅聊天模式' : '文本对话 API 待配置')
    : agentRuntime === 'openclaw' ? (gatewayStatus === 'connected' ? '创作网关已连接' : '创作网关未连接')
    : gatewayStatus === 'connected' ? `${agentRuntime} CLI 已安装，模型可用性待实际对话验证` : `${agentRuntime} CLI 待配置`;
  const todayStr = new Date().toISOString().slice(0, 10);
  const upcoming = [...schedule]
    .filter((s) => s.date >= todayStr && s.status !== 'published')
    .sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time)).slice(0, 5);
  const recent = outputs.slice(0, 5);
  const pendingIdeas = ideas.filter((i) => i.status === 'pending');
  const loggedIn = accounts.filter((a) => a.loggedIn).length;

  const quick: { label: string; page: Page; Icon: typeof IconChat }[] = [
    { label: '外部选题', page: 'discovery', Icon: IconIdea },
    { label: '开始对话', page: 'chat', Icon: IconChat },
    { label: '看热点', page: 'trends', Icon: IconFire },
    { label: '拆爆款', page: 'breakdown', Icon: IconSkills },
    { label: '记选题', page: 'ideas', Icon: IconIdea },
    { label: '排日历', page: 'calendar', Icon: IconCalendar },
    { label: '去发布', page: 'publish', Icon: IconPublish },
  ];

  const stats: { label: string; value: string; page: Page }[] = [
    { label: '待做选题', value: loaded.ideas ? String(pendingIdeas.length) : '—', page: 'ideas' },
    { label: '内容项目', value: loaded.outputs ? String(outputs.length) : '—', page: 'outputs' },
    { label: '待发排期', value: loaded.schedule ? String(upcoming.length) : '—', page: 'calendar' },
    { label: '已登录账号', value: loaded.accounts && accounts.length ? `${loggedIn}/${accounts.length}` : '—', page: 'accounts' },
  ];
  const exampleQuestions = [
    '第一次整理家庭保障，该从哪开始？',
    '拿到一份保险方案，先看什么？',
    '保单里的这些词，你真的看懂了吗？',
  ];

  return (
    <div className="page-scroll dash-page mingya-dash">
      <section className="my-hero">
        <BannerBackground page="home" />
        <div className="my-hero-copy">
          <p className="my-eyebrow"><SectionMark>让专业，被更多人看见</SectionMark></p>
          <h1>把专业，写进<br /><span className="my-title-line">每一篇笔记。<span className="my-title-stroke" aria-hidden="true" /></span></h1>
          <p className="my-hero-description">从一个真实问题出发，写出有依据、有温度的保险内容。</p>
          <div className="my-hero-actions">
            <button className="btn btn-primary" onClick={() => onNavigate('chat')}><IconPlus size={23} />开始创作</button>
            <button className="btn" onClick={() => onNavigate('profile')}><WorkspaceGlyph kind="user" size={23} />完善我的 IP</button>
          </div>
        </div>
      </section>

      <section className="my-course-path" aria-label="课程主线">
        <div className="my-course-step"><ReferenceArtwork page="home" crop={[328, 448, 60, 58]} className="my-course-icon" /><div><h2>被看见</h2><p>找到客户真正关心的问题</p></div></div>
        <span className="my-course-dots" aria-hidden="true">··········</span>
        <div className="my-course-step"><ReferenceArtwork page="home" crop={[743, 448, 60, 58]} className="my-course-icon" /><div><h2>被相信</h2><p>用真实经历与依据建立信任</p></div></div>
        <span className="my-course-dots" aria-hidden="true">··········</span>
        <div className="my-course-step"><ReferenceArtwork page="home" crop={[1159, 448, 60, 58]} className="my-course-icon" /><div><h2>被选择</h2><p>把专业判断写成一篇好内容</p></div></div>
      </section>

      <div className="my-business-grid">
        <section className="my-question-card">
          <h2><SectionMark>今天，从一个好问题开始</SectionMark></h2>
          <p className="my-question-caption">问题示例 · 可继续补充素材</p>
          <div className="my-example-questions">
            {exampleQuestions.map((question, index) => <button key={question} onClick={() => onUseTopic(question)}>
              <span className="my-question-number">{index + 1}</span><span>{question}</span><IconChevron size={17} />
            </button>)}
          </div>
          <button className="my-topic-library" onClick={() => onNavigate('ideas')}><IconOutputs size={18} />去选题库</button>
        </section>
        <section className="my-space-card">
          <h2><SectionMark>我的创作空间</SectionMark></h2>
          <div className="my-space-stats">
            {stats.map((stat) => <button key={stat.label} onClick={() => onNavigate(stat.page)}><span>{stat.label}</span><strong>{stat.value}</strong></button>)}
          </div>
          <ReferenceArtwork page="home" crop={[1139, 685, 224, 115]} className="my-notebook-art" />
          <p className="my-space-caption">第一篇好内容，从这里开始</p>
        </section>
      </div>

      <section className="my-continue">
        <h2><SectionMark>继续创作</SectionMark></h2>
        <div className="my-continue-cards">
          <button className="my-recent-content" onClick={() => onNavigate('outputs')}><IconFile size={25} /><span>{recent.length ? (recent[0].meta?.title || recent[0].name) : loaded.outputs ? '还没有内容草稿' : '内容草稿尚未读取'}</span></button>
          <div className="my-start-note"><IconEdit size={25} /><span>把真实问题，变成你的第一篇笔记</span><button className="btn btn-primary" onClick={() => onNavigate('chat')}>开始写笔记<span aria-hidden="true">→</span></button></div>
        </div>
      </section>

      <section className="my-toolbox">
        <div className="my-toolbox-head"><h2><SectionMark>创作工具</SectionMark></h2><span>{persona ? `当前 IP · ${persona}` : '选择一个 IP，让表达更贴近你'}</span></div>
        <div className="dash-quick">{quick.map((q) => <button key={q.page} className="dash-quick-btn" onClick={() => onNavigate(q.page)}><q.Icon size={16} /><span>{q.label}</span></button>)}</div>
        <p className="my-runtime-note">{runtimeNote}</p>
      </section>

      <div className="dash-grid">
        {/* 今日热点 */}
        <div className="card dash-card">
          <div className="dash-card-head">
            <span><IconFire size={16} /> 热点参考</span>
            <button className="dash-more" onClick={() => onNavigate('trends')}>热点雷达 →</button>
          </div>
          {trends.length === 0 && <div className="dash-empty">热点加载中 / 需配置代理</div>}
          {trends.map((g) => (
            <div key={g.platform} className="dash-trend-group">
              <div className="dash-trend-plat">{g.label}</div>
              {g.items.slice(0, 3).map((it, i) => (
                <div key={i} className="dash-trend-item" title={`${it.title}（点击做成内容）`}>
                  <span className="dash-trend-title" onClick={() => onUseTopic(it.title)}>{it.title}</span>
                </div>
              ))}
            </div>
          ))}
        </div>

        {/* 选题库 */}
        <div className="card dash-card">
          <div className="dash-card-head">
            <span><IconIdea size={16} /> 选题库 · 待做</span>
            <button className="dash-more" onClick={() => onNavigate('ideas')}>全部 →</button>
          </div>
          {pendingIdeas.length === 0 && <div className="dash-empty">还没有待做选题。先记下一个读者真正关心的问题。</div>}
          {pendingIdeas.slice(0, 5).map((it) => (
            <div key={it.id} className="dash-idea" onClick={() => onUseTopic(it.title)} title="点击做成内容">
              <span className="dash-idea-title">{it.title}</span>
              {it.source && <span className="badge">{it.source}</span>}
            </div>
          ))}
        </div>

        {/* 近期排期 */}
        <div className="card dash-card">
          <div className="dash-card-head">
            <span><IconCalendar size={16} /> 近期排期</span>
            <button className="dash-more" onClick={() => onNavigate('calendar')}>日历 →</button>
          </div>
          {upcoming.length === 0 && <div className="dash-empty">暂无排期，去日历安排一条吧</div>}
          {upcoming.map((s) => (
            <div key={s.id} className="dash-sched" onClick={() => onNavigate('calendar')}>
              <span className="dash-sched-date">{s.date.slice(5)}</span>
              <span className="dash-sched-title">{s.platform ? `[${s.platform}] ` : ''}{s.title}</span>
              <span className="badge">{STATUS_LABEL[s.status] || s.status}</span>
            </div>
          ))}
        </div>

        {/* 最近产物 */}
        <div className="card dash-card dash-card-top">
          <div className="dash-card-head">
            <span><IconOutputs size={16} /> 最近产物</span>
            <button className="dash-more" onClick={() => onNavigate('outputs')}>内容库 →</button>
          </div>
          {recent.length === 0 && <div className="dash-empty">还没有内容文件。开始创作，逐步整理你的第一篇笔记。</div>}
          {recent.map((g) => (
            <div key={g.name} className="dash-output" onClick={() => onNavigate('outputs')}>
              <span className="dash-output-name">{g.meta?.title || g.name}</span>
              <span className="badge">{g.meta?.platform || (g.type === 'dir' ? `${g.fileCount ?? 0} 文件` : '单文件')}</span>
            </div>
          ))}
        </div>

        {/* 创作数据（归因层）：选平台自动拉取登录账号的粉丝/获赞/关注 + 多窗口增长 + 近7日环比 + 最新笔记 */}
        <div className="card dash-card dash-card-wide">
          <div className="dash-card-head">
            <span><IconAccounts size={16} /> 创作数据</span>
            {anaSel && anaData[anaSel] && anaData[anaSel] !== 'loading' && (
              <button className="dash-more" onClick={() => runAna(anaSel)}>刷新 →</button>
            )}
          </div>
          {(() => {
            const logged = anaPlats.filter((p) => p.loggedIn || whoamiMap[p.platform]?.loggedIn);
            if (anaPlats.length === 0) return <div className="dash-empty">加载中 / 需配置代理</div>;
            if (logged.length === 0) {
              return (
                <div className="dash-empty" onClick={() => onNavigate('accounts')} style={{ cursor: 'pointer' }}>
                  去账号页登录后，这里看各平台粉丝 / 获赞 / 关注、增长趋势与最新笔记 →
                </div>
              );
            }
            const d = anaSel ? anaData[anaSel] : undefined;
            const WIN: [typeof anaWin, string][] = [
              ['last', '较上次'], ['day', '较昨日'], ['week', '较上周'], ['month', '较上月'], ['year', '较去年'],
            ];
            return (
              <>
                <div className="ana-plats">
                  {logged.map((p) => (
                    <button key={p.platform} className={`chip ${anaSel === p.platform ? 'active' : ''}`}
                      onClick={() => runAna(p.platform)}>{p.name}</button>
                  ))}
                </div>
                {!d && <div className="dash-empty">点上方平台查看该账号数据</div>}
                {d === 'loading' && (
                  <div className="loading" style={{ padding: '28px 0' }}><div className="spinner" />抓取中…正在校验登录态并读取平台数据</div>
                )}
                {d === 'error' && (
                  <div className="dash-empty" style={{ color: 'var(--red)' }}>{anaErrors[anaSel] || '抓取失败，请稍后重试'}；点平台重试</div>
                )}
                {d && d !== 'loading' && d !== 'error' && (!d.loggedIn ? (
                  <div className="dash-empty" onClick={() => onNavigate('accounts')} style={{ cursor: 'pointer' }}>
                    该平台登录态已失效，去账号页重登 →
                  </div>
                ) : (
                  <div className="ana-body">
                    {d.warning && <div className="dash-empty" role="status" style={{ gridColumn: '1 / -1' }}>{d.warning}</div>}
                    {/* 概览 + 增长对比 */}
                    <div className="ana-col ana-col-main">
                      <div className="ana-id">{d.nickname ? `@${d.nickname}` : d.name}</div>
                      <div className="ana-overview">
                        {([['粉丝', 'followers'], ['获赞', 'likes'], ['关注', 'following']] as const).map(([label, key]) => {
                          const w = d.growth?.[anaWin] ?? null;
                          const g = w ? growthInfo(w[key as 'followers' | 'likes']) : null;
                          return (
                            <div key={key} className="ana-stat">
                              <div className="ana-stat-val">{fmtNum(d[key])}</div>
                              <div className="ana-stat-label">{label}</div>
                              {g ? <div className="ana-stat-delta" style={{ color: g.color }}>{g.text}</div>
                                 : <div className="ana-stat-delta ana-muted">—</div>}
                            </div>
                          );
                        })}
                      </div>
                      <div className="ana-wins">
                        {WIN.map(([k, lab]) => (
                          <button key={k} className={`ana-win ${anaWin === k ? 'on' : ''}`}
                            onClick={() => setAnaWin(k)}>{lab}</button>
                        ))}
                      </div>
                      <div className="ana-wins-note">
                        {d.growth?.[anaWin]?.since_days != null
                          ? `对比 ${d.growth[anaWin]!.since_days} 天前的快照`
                          : '暂无该时段历史快照，多刷新几次即可积累对比'}
                      </div>
                    </div>

                    {/* 近7日平台指标 + 环比 */}
                    <div className="ana-col ana-col-metrics">
                      <div className="ana-sub">近 7 日 · 环比</div>
                      {(d.metrics ?? []).length === 0 ? (
                        <div className="dash-empty">该平台未提供近 7 日指标</div>
                      ) : (
                        <div className="ana-metrics">
                          {(d.metrics ?? []).map((m) => {
                            const vs = m.vs ?? '';
                            const up = vs.startsWith('+');
                            const has = vs && vs !== '-';
                            return (
                              <div key={m.label} className="ana-metric">
                                <div className="ana-metric-val">{m.value}</div>
                                <div className="ana-metric-label">{m.label}</div>
                                {has && <div className="ana-metric-vs" style={{ color: up ? 'var(--trend-up)' : 'var(--trend-down)' }}>环比{vs}</div>}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>

                    {/* 最新笔记（可点进原文） */}
                    <div className="ana-col ana-col-notes">
                      <div className="ana-sub">最新笔记</div>
                      {(d.notes ?? []).length === 0 ? (
                        <div className="dash-empty">该账号暂无可读取的已发布笔记</div>
                      ) : (
                        <div className="ana-notes">
                          {(d.notes ?? []).slice(0, 6).map((n, i) => (
                            <a key={i} className="ana-note" href={n.url} target="_blank" rel="noreferrer" title={n.title}>
                              {n.cover
                                ? <img className="ana-note-cover" src={n.cover} alt="" referrerPolicy="no-referrer" />
                                : <span className="ana-note-cover ana-note-cover-ph">📝</span>}
                              <span className="ana-note-main">
                                <span className="ana-note-title">{n.title || '(无标题)'}</span>
                                {n.stat && <span className="ana-note-stat">{n.stat}</span>}
                              </span>
                              <span className="ana-note-go">↗</span>
                            </a>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
