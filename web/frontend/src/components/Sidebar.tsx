import { useState } from 'react';
import type { ChatSession } from '../lib/store';
import type { PersonaItem } from '../lib/api';
import {
  IconNewChat, IconEdit, IconArchive, IconUnarchive, IconTrash, IconChevron,
} from './icons';
import { IconGear } from './settingsIcons';
import ThemeToggle from './ThemeToggle';
import { ReferenceArtwork, WorkspaceGlyph } from './MingyaPrimitives';

export type Page = 'dashboard' | 'chat' | 'trends' | 'ideas' | 'discovery' | 'calendar' | 'publish' | 'breakdown' | 'skills' | 'outputs' | 'accounts' | 'profile';

interface SidebarProps {
  currentPage: Page;
  onPageChange: (page: Page) => void;
  personas: PersonaItem[];
  selectedPersona: string;
  onPersonaChange: (persona: string) => void;
  onNewProfile: () => void;
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSessionSelect: (id: string) => void;
  onSessionDelete: (id: string) => void;
  onSessionRename: (id: string, title: string) => void;
  onSessionArchive: (id: string, archived: boolean) => void;
  onNewChat: () => void;
  gatewayStatus: string;
  apiDirect: boolean;
  agentRuntime: string;
  onOpenSettings: () => void;
}

// 保留既有六个主导航，选题等功能仍从工作台进入。
const NAV: { page: Page; glyph: string; label: string }[] = [
  { page: 'dashboard', glyph: 'home', label: '创作工作台' },
  { page: 'chat', glyph: 'write', label: '笔记创作' },
  { page: 'skills', glyph: 'layers', label: '技能库' },
  { page: 'outputs', glyph: 'layers', label: '内容库' },
  { page: 'accounts', glyph: 'user', label: '账号管理' },
  { page: 'profile', glyph: 'star', label: '我的 IP' },
];

export default function Sidebar({
  currentPage,
  onPageChange,
  personas,
  selectedPersona,
  onPersonaChange,
  onNewProfile,
  sessions,
  activeSessionId,
  onSessionSelect,
  onSessionDelete,
  onSessionRename,
  onSessionArchive,
  onNewChat,
  gatewayStatus,
  apiDirect,
  agentRuntime,
  onOpenSettings,
}: SidebarProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [showArchived, setShowArchived] = useState(false);

  const startRename = (s: ChatSession) => { setRenamingId(s.id); setRenameValue(s.title); };
  const commitRename = () => {
    if (renamingId) onSessionRename(renamingId, renameValue);
    setRenamingId(null);
  };

  const active = sessions.filter((s) => !s.archived && (s.messages.length > 0 || s.id === activeSessionId));
  const archived = sessions.filter((s) => s.archived);

  const renderItem = (s: ChatSession, isArchived: boolean) => {
    if (renamingId === s.id) {
      return (
        <div key={s.id} className="session-item">
          <input
            className="session-rename-input"
            value={renameValue}
            autoFocus
            onChange={(e) => setRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename();
              else if (e.key === 'Escape') setRenamingId(null);
            }}
            onBlur={commitRename}
          />
        </div>
      );
    }
    return (
      <div
        key={s.id}
        className={`session-item ${s.id === activeSessionId ? 'active' : ''}`}
        onClick={() => onSessionSelect(s.id)}
      >
        <span className="session-item-title">{s.title}</span>
        <div className="session-actions">
          <button className="session-act" title="重命名"
            onClick={(e) => { e.stopPropagation(); startRename(s); }}><IconEdit size={14} /></button>
          <button className="session-act" title={isArchived ? '取消归档' : '归档'}
            onClick={(e) => { e.stopPropagation(); onSessionArchive(s.id, !isArchived); }}>
            {isArchived ? <IconUnarchive size={14} /> : <IconArchive size={14} />}
          </button>
          <button className="session-act danger" title="删除"
            onClick={(e) => { e.stopPropagation(); onSessionDelete(s.id); }}><IconTrash size={14} /></button>
        </div>
      </div>
    );
  };

  return (
    <div className="sidebar strict-sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <ReferenceArtwork page="home" crop={[20, 14, 186, 66]} className="sidebar-reference-logo" label="明亚 小红书工作台" />
          <h1 className="sr-only">明亚小红书工作台</h1>
          <ThemeToggle />
        </div>
        <select
          className="persona-select"
          value={selectedPersona}
          onChange={(e) => {
            if (e.target.value === '__new__') { onNewProfile(); return; }
            onPersonaChange(e.target.value);
          }}
          title="选择用户画像（一个对话对应一个画像：切换会新建一个对话）"
        >
          <option value="">通用模式</option>
          {personas.map((p) => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
          <option value="__new__">+ 新建画像…</option>
        </select>
      </div>

      <nav className="sidebar-nav" aria-label="工作台导航">
        {NAV.map(({ page, glyph, label }) => (
          <button
            key={page}
            className={`nav-item ${currentPage === page ? 'active' : ''}`}
            aria-current={currentPage === page ? 'page' : undefined}
            onClick={() => onPageChange(page)}
          >
            <span className="nav-icon"><WorkspaceGlyph kind={glyph} /></span>
            {label}
          </button>
        ))}
      </nav>

      <div className="sidebar-section" id="sidebar-sessions">
        {currentPage === 'chat' && (
          <>
            <div className="sidebar-section-header">
              <span className="sidebar-section-title">创作历史</span>
              <button className="new-chat-btn" onClick={onNewChat} title="新建创作对话">
                <IconNewChat size={13} /> 新创作
              </button>
            </div>
            {active.map((s) => renderItem(s, false))}

            {archived.length > 0 && (
              <>
                <div className="archived-header" onClick={() => setShowArchived((v) => !v)}>
                  <span className={`archived-chevron ${showArchived ? 'open' : ''}`}><IconChevron size={12} /></span>
                  已归档 · {archived.length}
                </div>
                {showArchived && archived.map((s) => renderItem(s, true))}
              </>
            )}
          </>
        )}
      </div>

      <button className="sidebar-settings" onClick={onOpenSettings}><IconGear size={23} /> 设置</button>
      <div className="sidebar-workspace-note">▱ <span>本地工作空间</span></div>
      <div className="sidebar-status">
        <span className={`status-dot ${gatewayStatus === 'connected' ? '' : 'offline'}`} />
        <span className="sidebar-status-text">{gatewayStatus === 'connected'
          ? (apiDirect ? '仅聊天模式已配置' : agentRuntime !== 'openclaw' ? `${agentRuntime} CLI 已安装` : '网关已连接')
          : gatewayStatus === 'disconnected'
            ? (apiDirect ? 'API 配置不完整' : agentRuntime !== 'openclaw' ? `${agentRuntime} CLI 未配置` : '网关离线')
            : '连接中…'}</span>

      </div>
    </div>
  );
}
