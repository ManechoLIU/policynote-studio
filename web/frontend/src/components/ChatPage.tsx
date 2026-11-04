import { useState, useRef, useEffect } from 'react';
import MessageBubble from './MessageBubble';
import QuestionCards from './QuestionCards';
import BrushEntry from './BrushEntry';
import type { ChatSession, ChatMessage, StreamState } from '../lib/store';
import { uploadFiles, adoptOversize } from '../lib/api';
import type { UploadedFile } from '../lib/api';
import { IconStop, IconFile, IconSend, IconProfile, IconChevron, IconCompass, IconEdit } from './icons';
import { BannerBackground, ReferenceArtwork, SectionMark } from './MingyaPrimitives';
import '../styles/mingya-chat.css';

interface ChatPageProps {
  apiDirect: boolean;
  session: ChatSession;
  stream?: StreamState;          // 进行中的流式态（来自 App，切页也不丢）
  onSend: (displayText: string, attachments?: UploadedFile[]) => void;
  onStop: () => void;
  onResend: (
    userIndex: number,
    displayText: string,
    attachments?: UploadedFile[],
    legacyAgentText?: string,
  ) => void; // 重试：仅对最后一轮
  onQuestionAnswered?: (questionId: string) => void;   // 某道问答题提交成功（App 记录答过，重放不再出现）
}

// 保留既有快捷任务与发送内容，仅调整品牌视觉。
const SUGGESTIONS = [
  { icon: '🔥', title: '蹭个热点', prompt: '看看现在微博和抖音有什么热搜，挑几个适合我做二创的选题' },
  { icon: '✍️', title: '写小红书文案', prompt: '帮我写一条小红书种草文案，主题先问我' },
  { icon: '🎴', title: '做金句卡片', prompt: '把一句走心的话做成一张适合发朋友圈的金句卡片' },
  { icon: '🎬', title: '口播脚本', prompt: '帮我写一条 60 秒的口播短视频脚本，主题先问我' },
];

const QUICK_TASKS = [
  { title: '帮我选题', description: '从真实问题中找到合适的选题', prompt: '帮我策划一条小红书选题，主题先问我', Icon: IconCompass },
  { title: '帮我写稿', description: '围绕主题生成清晰的笔记文案', prompt: '帮我写一条小红书种草文案，主题先问我', Icon: IconFile },
  { title: '帮我改稿', description: '优化表达、结构与可读性', prompt: '帮我修改一篇小红书文案，先问我要原稿', Icon: IconEdit },
];

export default function ChatPage({ apiDirect, session, stream, onSend, onStop, onResend, onQuestionAnswered }: ChatPageProps) {
  const [input, setInput] = useState('');
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [maxMb, setMaxMb] = useState(50);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);

  const isStreaming = !!stream;
  const isEmpty = session.messages.length === 0 && !isStreaming;

  useEffect(() => {
    fetch('/api/upload/limits').then((r) => r.json())
      .then((d) => { if (d?.max_mb) setMaxMb(d.max_mb); }).catch(() => {});
  }, []);

  const doUpload = async (fs: FileList | File[]) => {
    const arr = Array.from(fs);
    if (!arr.length) return;
    const cap = maxMb * 1024 * 1024;
    const big = arr.filter((f) => f.size > cap);
    const small = arr.filter((f) => f.size <= cap);

    if (big.length) {
      // 超限：不走上传通道，复制进收件箱后作为普通附件（界面零新增元素）
      setUploading(true);
      try {
        const saved = await adoptOversize(big, session.id);
        setAttachments((a) => [...a, ...saved]);
      } catch (err) {
        alert((err as Error).message || '超限文件处理失败');
      } finally {
        setUploading(false);
      }
    }
    if (!small.length) return;
    setUploading(true);
    try {
      const saved = await uploadFiles(small, session.id);
      setAttachments((a) => [...a, ...saved]);
    } catch (err) {
      alert((err as Error).message || '上传失败');
    } finally {
      setUploading(false);
    }
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false);
    if (e.dataTransfer.files?.length) doUpload(e.dataTransfer.files);
  };
  const onPaste = (e: React.ClipboardEvent) => {
    if (e.clipboardData.files?.length) { e.preventDefault(); doUpload(e.clipboardData.files); }
  };
  const removeAttachment = (path: string) => setAttachments((a) => a.filter((x) => x.path !== path));

  useEffect(() => {
    if (!isEmpty) messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [session.messages, stream?.content, stream?.thinking, stream?.activity, stream?.stillWorking, isEmpty]);

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 180) + 'px';
    }
  }, [input]);

  const handleSend = () => {
    const trimmed = input.trim();
    if ((!trimmed && attachments.length === 0) || isStreaming || uploading) return;
    // 附件通过结构化字段发送；用户消息气泡只显示用户实际输入的文字。
    onSend(trimmed, attachments);
    setInput('');
    setAttachments([]);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法确认候选词/英文的回车交给输入法处理，不能触发发送。
    // 部分浏览器先触发 compositionend，此时仍需用 229 识别输入法按键。
    if (isComposingRef.current || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const inputBox = (hero: boolean) => (
    <div className={`composer ${hero ? 'composer-hero' : ''} ${dragOver ? 'composer-drag' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
      onDrop={onDrop}>
      {attachments.length > 0 && (
        <div className="composer-attachments">
          {attachments.map((a) => (
            <span key={a.path} className="attach-chip" title={a.path}>
              <IconFile size={12} /> <span className="attach-name">{a.name}</span>
              <button className="attach-x" onClick={() => removeAttachment(a.path)} title="移除">×</button>
            </span>
          ))}
        </div>
      )}
      <div className="composer-top">
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder={dragOver ? '松手上传素材…' : hero ? '写下主题，或添加你的创作素材…' : '发消息…（Enter 发送，Shift+Enter 换行，可拖入/粘贴素材）'}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onCompositionStart={() => { isComposingRef.current = true; }}
          onCompositionEnd={() => { isComposingRef.current = false; }}
          onBlur={() => { isComposingRef.current = false; }}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          rows={1}
          autoFocus={hero}
        />
      </div>
      <input ref={fileInputRef} type="file" multiple hidden
        onChange={(e) => { if (e.target.files) doUpload(e.target.files); e.target.value = ''; }} />
      <div className="composer-bar">
        <button className="composer-attach-btn" onClick={() => fileInputRef.current?.click()}
          disabled={isStreaming || uploading} title={`添加素材（图片/文档）；超过 ${maxMb}MB 的大文件将自动存为本地素材（不走上传）`}>
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m8 12 6.5-6.5a3.2 3.2 0 0 1 4.5 4.5L10 19a5 5 0 0 1-7-7l9-9" /><path d="m6 14 8-8" /></svg> {uploading ? '上传中…' : '上传附件'}
        </button>
        {!apiDirect && <BrushEntry onPick={(t) => { setInput(t); requestAnimationFrame(() => textareaRef.current?.focus()); }} />}
        <span className="composer-hint">{isStreaming ? '生成中…' : '按 Enter 发送，Shift + Enter 换行'}</span>
        {isStreaming ? (
          <button className="send-btn" onClick={onStop} title="停止生成"><IconStop size={15} /></button>
        ) : (
          <button className="send-btn" onClick={handleSend} disabled={(!input.trim() && !attachments.length) || uploading} title="发送"><IconSend size={28} /></button>
        )}
      </div>
    </div>
  );

  // ---- 空态：居中欢迎页 ----
  if (isEmpty) {
    return (
      <div className="chat-page strict-chat-page strict-chat-empty">
        <div className="chat-hero">
          <BannerBackground page="chat" />
          <div className="chat-workspace-label"><SectionMark>内部创作工作台</SectionMark></div>
          <div className="chat-hero-brand">
            <ReferenceArtwork page="chat" crop={[323, 155, 37, 35]} className="chat-identity-logo" />
            <strong>明亚</strong><span>· 小红书工作台</span>
            <span className="chat-brand-divider" aria-hidden="true" />
            <span className="chat-current-persona"><IconProfile size={24} /><span title={session.persona || '未选择 IP'}>{session.persona || '未选择 IP'}</span><span className="chat-persona-chevron" aria-hidden="true">⌄</span></span>
          </div>
          <h1 className="chat-hero-title">今天，想把哪个<span className="chat-title-question">保险问题讲清楚？<span className="chat-title-underline" aria-hidden="true" /></span></h1>
          <p className="chat-hero-sub">从真实问题出发，让每一次表达都更清楚。</p>
          {inputBox(true)}
          <div className="suggestions">
            {QUICK_TASKS.map((s) => (
              <button key={s.title} className="strict-chat-task" onClick={() => { if (!isStreaming) onSend(s.prompt); }}>
                <span className="suggestion-icon"><s.Icon size={42} /></span>
                <span className="suggestion-body"><span className="suggestion-title">{s.title}</span><span className="suggestion-text">{s.description}</span></span>
                <IconChevron size={22} />
              </button>
            ))}
          </div>
          <details className="chat-more-tasks">
            <summary>更多原有任务</summary>
            <div className="chat-original-tasks">
              {SUGGESTIONS.filter((s) => !apiDirect || ['写小红书文案', '口播脚本'].includes(s.title)).map((s) => (
                <button key={s.title} title={s.prompt} onClick={() => { if (!isStreaming) onSend(s.prompt); }}><span>{s.icon}</span>{s.title}</button>
              ))}
            </div>
          </details>
          <div className="chat-mode-status"><span className="chat-status-rule" /><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 12a4 4 0 0 0 6 0l5-5a4 4 0 0 0-6-6l-1 1" /></svg><span>{apiDirect ? 'API 直连 · 文字与附件分析' : '本机 Agent · 创作工具模式'}</span><span className="chat-status-rule" /></div>
        </div>
      </div>
    );
  }

  // ---- 对话态 ----
  const displayMessages: ChatMessage[] = [...session.messages];
  if (isStreaming) displayMessages.push({ role: 'assistant', content: stream!.content || '' });

  return (
    <div className="chat-page strict-chat-page">
      <div className="chat-messages">
        <div className="chat-thread">
          {displayMessages.map((msg, i) => {
            const isLast = i === displayMessages.length - 1;
            const live = isStreaming && isLast && msg.role === 'assistant';
            const isFinal = !live && i < session.messages.length;
            let actions;
            if (isFinal) {
              const copy = () => navigator.clipboard?.writeText(msg.content);
              // 只允许对「最后一轮」重试，契合 OpenClaw append-only 模型（不改写历史）；已移除编辑
              const isLastFinal = i === session.messages.length - 1;
              if (msg.role === 'user') {
                actions = {
                  onCopy: copy,
                  onRetry: isLastFinal
                    ? () => onResend(i, msg.content, msg.attachments, msg.agentContent)
                    : undefined,
                  canModify: !isStreaming,
                };
              } else {
                const pi = i - 1;
                const prevUser = pi >= 0 && session.messages[pi]?.role === 'user' ? session.messages[pi] : null;
                actions = {
                  onCopy: copy,
                  onRetry: (isLastFinal && prevUser)
                    ? () => onResend(pi, prevUser.content, prevUser.attachments, prevUser.agentContent)
                    : undefined,
                  canModify: !isStreaming,
                };
              }
            }
            return (
              <MessageBubble
                key={`${i}-${msg.role}`}
                message={msg}
                isStreaming={live}
                thinking={live ? stream!.thinking : ''}
                activity={live ? stream!.activity : ''}
                stillWorking={live ? stream!.stillWorking : ''}
                actions={actions}
              />
            );
          })}
          {isStreaming && (stream!.questions?.length ?? 0) > 0 && (
            <QuestionCards questions={stream!.questions || []} onDone={(qid) => onQuestionAnswered?.(qid)} />
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="chat-input-area">
        <div className="chat-input-inner">{inputBox(false)}</div>
      </div>
    </div>
  );
}
