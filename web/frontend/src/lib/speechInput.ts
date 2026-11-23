export interface SpeechRecognitionAlternativeLike {
  readonly transcript: string;
  readonly confidence?: number;
}

export interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  readonly [index: number]: SpeechRecognitionAlternativeLike;
}

export interface SpeechRecognitionResultListLike {
  readonly length: number;
  readonly [index: number]: SpeechRecognitionResultLike;
}

export interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultListLike;
}

export interface SpeechRecognitionErrorEventLike {
  readonly error: string;
  readonly message?: string;
}

export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onstart: ((event: Event) => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: ((event: Event) => void) | null;
}

export type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

export interface SpeechResultChunks {
  finalText: string;
  interimText: string;
  finalIndexes: number[];
}

export interface TranscriptInsertion {
  value: string;
  caret: number;
}

interface SpeechWindow extends Window {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
}

/** Chrome/Edge 使用 unprefixed 或 webkit 前缀构造器，其余浏览器返回 null。 */
export function getSpeechRecognitionCtor(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null;
  const speechWindow = window as SpeechWindow;
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null;
}

/**
 * 只收集本次事件新增的 final 片段，并汇总当前 interim 预览。
 * committedFinalIndexes 用来防止部分浏览器在后续事件里重复上报已经定稿的结果。
 */
export function collectSpeechResults(
  results: SpeechRecognitionResultListLike,
  resultIndex: number,
  committedFinalIndexes: ReadonlySet<number> = new Set(),
): SpeechResultChunks {
  const finalParts: string[] = [];
  const interimParts: string[] = [];
  const finalIndexes: number[] = [];
  const start = Math.max(0, Math.min(resultIndex, results.length));

  for (let index = start; index < results.length; index += 1) {
    const result = results[index];
    if (!result || result.length === 0) continue;

    const transcript = result[0]?.transcript ?? '';
    if (!transcript.trim()) continue;

    if (result.isFinal) {
      if (!committedFinalIndexes.has(index)) {
        finalParts.push(transcript);
        finalIndexes.push(index);
      }
    } else {
      interimParts.push(transcript);
    }
  }

  return {
    finalText: finalParts.join('').trim(),
    interimText: interimParts.join('').trim(),
    finalIndexes,
  };
}

/** 按普通键盘输入语义插入文本；有选区时替换选区。 */
export function insertTranscript(
  value: string,
  start: number,
  end: number,
  transcript: string,
): TranscriptInsertion {
  const text = transcript.trim();
  const safeStart = Math.max(0, Math.min(start, value.length));
  const safeEnd = Math.max(safeStart, Math.min(end, value.length));

  if (!text) return { value, caret: safeStart };

  const nextValue = value.slice(0, safeStart) + text + value.slice(safeEnd);
  return { value: nextValue, caret: safeStart + text.length };
}

export function speechErrorMessage(error: string, detail = ''): string {
  switch (error) {
    case 'not-allowed':
    case 'service-not-allowed':
      return '麦克风权限被拒绝，请在地址栏权限中允许后重试。';
    case 'audio-capture':
      return '未检测到可用麦克风，请检查设备连接。';
    case 'no-speech':
      return '没听到内容，请再试一次。';
    case 'network':
      return '语音识别服务网络异常，请稍后重试。';
    case 'language-not-supported':
      return '当前浏览器不支持中文语音识别。';
    case 'bad-grammar':
      return '语音识别配置异常，请刷新后重试。';
    default: {
      const message = detail.trim();
      return message ? `语音识别失败：${message}` : '语音识别失败，请重试。';
    }
  }
}
