export const CLIENT_BROWSER_STATES = [
  'unavailable', 'connected', 'opened', 'working', 'saved',
  'record_found', 'review_ready', 'reviewed', 'auth_required', 'unconfirmed', 'error', 'busy',
] as const;

export type ClientBrowserState = typeof CLIENT_BROWSER_STATES[number];

export interface ClientBrowserResult {
  ok: boolean;
  state: ClientBrowserState;
  message: string;
  published: false;
  receiptKind?: string;
  reviewToken?: string;
}

export interface ClientDraftImage {
  name: string;
  mime: 'image/png' | 'image/jpeg' | 'image/webp';
  base64: string;
}

export interface ClientDraftPayload {
  title: string;
  body: string;
  tags: string[];
  images: ClientDraftImage[];
}

const STATE_SET = new Set<string>(CLIENT_BROWSER_STATES);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TIMEOUT_MS = { status: 3000, open: 10000, draft: 190000, review_prepare: 10000, review: 10000 } as const;
type ClientAction = keyof typeof TIMEOUT_MS;
type ClientReviewPayload = { reviewToken: string };

const MISSING = '未检测到浏览器连接器。请在你平时使用的 Chrome 中安装「明亚工作台浏览器连接」，在扩展设置中连接此工作台地址，然后刷新页面。';

function resultKeysOk(value: Record<string, unknown>): boolean {
  return Object.keys(value).every((key) => key === 'ok' || key === 'state' || key === 'message' || key === 'published' || key === 'receiptKind' || key === 'reviewToken');
}

export function parseClientBrowserResult(value: unknown): ClientBrowserResult | null {
  if (!value || typeof value !== 'object') return null;
  const result = value as Record<string, unknown>;
  if (!resultKeysOk(result)) return null;
  if (typeof result.ok !== 'boolean' || typeof result.state !== 'string' || !STATE_SET.has(result.state)) return null;
  if (typeof result.message !== 'string' || result.published !== false) return null;
  if ('receiptKind' in result && typeof result.receiptKind !== 'string') return null;
  if ('reviewToken' in result && typeof result.reviewToken !== 'string') return null;
  const successfulStates = new Set(['connected', 'opened', 'saved', 'record_found', 'review_ready', 'reviewed']);
  if (result.ok !== successfulStates.has(result.state)) return null;
  if (result.state === 'saved' && result.receiptKind !== 'fresh_notice') return null;
  if (result.state === 'record_found' && (result.ok !== true || result.receiptKind !== 'new_record')) return null;
  if (result.state !== 'saved' && result.state !== 'record_found' && 'receiptKind' in result) return null;
  if (result.state === 'review_ready' && (typeof result.reviewToken !== 'string' || !UUID.test(result.reviewToken))) return null;
  if (result.state !== 'review_ready' && 'reviewToken' in result) return null;
  return result as unknown as ClientBrowserResult;
}

function timeoutResult(action: ClientAction): ClientBrowserResult {
  if (action === 'draft') {
    return { ok: false, state: 'unconfirmed', message: '保存结果未确认。请先查看小红书草稿，勿重复提交。', published: false };
  }
  if (action === 'review_prepare' || action === 'review') {
    return { ok: false, state: 'unconfirmed', message: '解除保护结果未确认，请刷新状态核对；不会自动重试', published: false };
  }
  if (action === 'status') return { ok: false, state: 'unavailable', message: MISSING, published: false };
  return { ok: false, state: 'error', message: '打开小红书草稿超时，未保存或发布。', published: false };
}

function requestClient(action: ClientAction, payload: ClientDraftPayload | ClientReviewPayload | undefined, signal?: AbortSignal): Promise<ClientBrowserResult> {
  const isReview = action === 'review_prepare' || action === 'review';
  if (signal?.aborted) return Promise.resolve(isReview ? timeoutResult(action) : { ok: false, state: 'error', message: '已取消', published: false });
  const id = crypto.randomUUID();
  return new Promise((resolve) => {
    let settled = false;
    let sent = false;
    const finish = (result: ClientBrowserResult) => {
      if (settled) return;
      settled = true;
      window.removeEventListener('message', onMessage);
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      resolve({ ...result, published: false });
    };
    const onMessage = (event: MessageEvent) => {
      if (event.source !== window || event.origin !== location.origin) return;
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      const message = data as Record<string, unknown>;
      if (Object.keys(message).some((key) => key !== 'channel' && key !== 'version' && key !== 'id' && key !== 'result')) return;
      if (message.channel !== 'mingya-browser' || message.version !== 1 || message.id !== id) return;
      const result = parseClientBrowserResult(message.result);
      if (!result) return;
      finish(result);
    };
    const onAbort = () => finish(isReview ? timeoutResult(action) : action === 'draft' && sent ? timeoutResult('draft') : { ok: false, state: 'error', message: '已取消', published: false });
    const timer = setTimeout(() => finish(timeoutResult(action)), TIMEOUT_MS[action]);
    window.addEventListener('message', onMessage);
    signal?.addEventListener('abort', onAbort);
    const request: { channel: string; version: number; id: string; action: ClientAction; payload?: ClientDraftPayload | ClientReviewPayload } = {
      channel: 'mingya-workbench', version: 1, id, action,
    };
    if (payload) request.payload = payload;
    if (signal?.aborted) { onAbort(); return; }
    try {
      window.postMessage(request, location.origin);
      sent = true;
    } catch {
      finish({ ok: false, state: 'error', message: '未发送请求', published: false });
    }
  });
}

export function getClientBrowserStatus(signal?: AbortSignal): Promise<ClientBrowserResult> {
  return requestClient('status', undefined, signal);
}

export function openClientXhsDrafts(signal?: AbortSignal): Promise<ClientBrowserResult> {
  return requestClient('open', undefined, signal);
}

export function saveClientXhsDraft(payload: ClientDraftPayload, signal?: AbortSignal): Promise<ClientBrowserResult> {
  return requestClient('draft', payload, signal);
}

export function prepareClientDraftReview(signal?: AbortSignal): Promise<ClientBrowserResult> {
  return requestClient('review_prepare', undefined, signal);
}

export function confirmClientDraftReview(reviewToken: string, signal?: AbortSignal): Promise<ClientBrowserResult> {
  if (!UUID.test(reviewToken)) return Promise.resolve({ok:false,state:'error',message:'核对令牌无效',published:false});
  return requestClient('review', {reviewToken}, signal);
}
