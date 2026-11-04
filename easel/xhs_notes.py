"""Read-only keyword search/detail adapter for the xhs CLI JSON envelope.

Only status/search/read argv are permitted. Search order is upstream relevance,
not a platform popularity ranking. Images are URLs, not evidence of image analysis.
No CLI, network or account files are touched until a public function is called.
The adapter never retries; the installed CLI can retry/fall back internally.
Risk-control latching covers errors visible in the final CLI envelope only.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

_SOURCE = 'xiaohongshu_cli'
_TIMEOUT_SECONDS = 30
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_CACHE_SECONDS = 60
_CACHE_MAX_ENTRIES = 64
_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, dict]] = {}
_STOP_ERROR: str | None = None
_NOTE_ID = re.compile(r'[0-9a-fA-F]{24}')
_MESSAGES = {
    'invalid_query': '关键词须为1至100个字符，且不能以选项符号开头。',
    'invalid_limit': '数量须为1至100的整数。',
    'invalid_url': '请使用HTTPS小红书官方笔记链接；当前不支持短链。',
    'cli_not_configured': '未找到可执行的xhs CLI，请配置EASEL_XHS_CLI。',
    'auth_required': '小红书CLI尚未登录或登录已失效，请重新登录。',
    'risk_control': '小红书触发验证或风控，已停止本进程后续请求；请处理后重启服务。',
    'rate_limited': '小红书接口暂时不可用或限流；本应用未重跑CLI。',
    'invalid_response': '小红书CLI返回的数据格式无法识别。',
    'cli_failed': '小红书CLI执行失败；本应用未重跑CLI。',
    'timeout': '小红书读取超时；本应用未重跑CLI。',
    'output_too_large': '小红书CLI返回数据超过大小限制。',
    'busy': '已有小红书读取正在执行，请稍后再试。',
}


class _CliError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(_MESSAGES.get(code, _MESSAGES['cli_failed']))


def _resolve_cli() -> str:
    """Resolve one executable, never a command string and never install anything."""
    configured = os.environ.get('EASEL_XHS_CLI', 'xhs').strip()
    if not configured or '\x00' in configured:
        raise _CliError('cli_not_configured')
    candidate = shutil.which(configured)
    if not candidate or not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
        raise _CliError('cli_not_configured')
    return str(Path(candidate).absolute())


def _run_cli(executable: str, args: list[str], deadline: float):
    """Bound both streams while reading; discard stderr rather than leaking secrets."""
    valid = args == ['status', '--json'] or (
        len(args) == 3 and args[0] in ('search', 'read') and args[-1] == '--json'
        and isinstance(args[1], str) and args[1] and not args[1].startswith('-')
        and '\x00' not in args[1])
    if not valid or not os.path.isabs(executable) or '\x00' in executable:
        raise _CliError('cli_failed')
    if deadline <= time.monotonic():
        raise _CliError('timeout')
    process = None
    try:
        process = subprocess.Popen([executable, *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=True)
        output = bytearray()
        total = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _CliError('timeout')
                for key, _ in selector.select(min(remaining, .1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > _MAX_OUTPUT_BYTES:
                        raise _CliError('output_too_large')
                    if key.data:
                        output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _CliError('timeout')
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise _CliError('timeout') from None
        try:
            envelope = json.loads(output.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise _CliError('invalid_response') from None
        if not isinstance(envelope, dict):
            raise _CliError('invalid_response')
        error = envelope.get('error')
        if error is not None:
            if not isinstance(error, dict) or not isinstance(error.get('code'), str):
                raise _CliError('invalid_response')
            external = error['code']
            mapped = ('auth_required' if external == 'not_authenticated' else
                      'risk_control' if external in ('verification_required', 'ip_blocked', 'signature_error') else
                      'rate_limited' if external in ('api_error', 'rate_limited') else 'cli_failed')
            raise _CliError(mapped)
        if code != 0 or envelope.get('ok') is False:
            raise _CliError('cli_failed')
        if 'data' not in envelope:
            raise _CliError('invalid_response')
        return envelope['data']
    except OSError:
        raise _CliError('cli_failed') from None
    finally:
        if process is not None:
            # Also kill descendants retaining pipes after the main process exits.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def _run_cli_stage(executable: str, args: list[str], deadline: float):
    """Emit a fixed diagnostic schema to server stderr, without command inputs."""
    phase = args[0] if args[0] in ('status', 'search', 'read') else 'unknown'
    started = time.monotonic()
    error_code = 'cli_failed'
    try:
        result = _run_cli(executable, args, deadline)
        error_code = None
        return result
    except _CliError as exc:
        error_code = exc.code if exc.code in _MESSAGES else 'cli_failed'
        raise
    finally:
        event = {'event': 'xhs_cli_stage', 'phase': phase,
                 'elapsed_ms': max(0, round((time.monotonic() - started) * 1000)),
                 'error_code': error_code}
        try:
            print(json.dumps(event), file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass  # A closed log stream must not change the business outcome.


def _text(value, limit=50_000):
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= limit else None


def _count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 10**15 else None
    if isinstance(value, str):
        match = re.fullmatch(r'(\d+(?:\.\d+)?)(万|亿)?', value.strip())
        if match:
            try:
                count = Decimal(match[1]) * {'万': 10000, '亿': 100000000, None: 1}[match[2]]
                if count == count.to_integral_value() and 0 <= count <= 10**15:
                    return int(count)
            except InvalidOperation:
                pass
    return None


def _image_url(value):
    if not isinstance(value, str) or len(value) > 4096 or re.search(r'[\x00-\x20\\]', value):
        return None
    try:
        p = urlsplit(value)
        host = p.hostname or ''
        if (p.scheme == 'https' and not p.username and not p.password and p.port in (None, 443)
                and (host == 'xhscdn.com' or host.endswith('.xhscdn.com'))):
            return value
    except ValueError:
        pass
    return None


def _images(note):
    candidates = []
    cover = note.get('cover')
    if isinstance(cover, dict):
        candidates.extend([cover.get('url_default'), cover.get('url_pre')])
    image_list = note.get('image_list')
    if isinstance(image_list, list):
        for image in image_list:
            if not isinstance(image, dict):
                continue
            candidates.extend([image.get('url_default'), image.get('url_pre')])
            if isinstance(image.get('info_list'), list):
                candidates.extend(info.get('url') for info in image['info_list'] if isinstance(info, dict))
    return list(dict.fromkeys(url for value in candidates if (url := _image_url(value))))[:20]


def _published_at(value):
    # Search labels such as '昨天' do not establish an absolute publication date.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(value)
        if numeric <= 0:
            return None
        if numeric >= 1_000_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _canonical_url(url):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\x00-\x20\\]', url):
        raise _CliError('invalid_url')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in ('www.xiaohongshu.com', 'xiaohongshu.com')
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise _CliError('invalid_url')
        match = re.fullmatch(r'/(?:explore|discovery/item)/([0-9a-fA-F]{24})/?', parsed.path)
        if not match:
            raise _CliError('invalid_url')
        note_id = match[1].lower()
        return f'https://www.xiaohongshu.com/explore/{note_id}', note_id
    except ValueError:
        raise _CliError('invalid_url') from None


def _map_item(item, detail=False, expected_id=None):
    if not isinstance(item, dict) or not isinstance(item.get('note_card'), dict):
        raise _CliError('invalid_response')
    note = item['note_card']
    note_id = item.get('id') or note.get('note_id')
    if not isinstance(note_id, str) or not _NOTE_ID.fullmatch(note_id):
        raise _CliError('invalid_response')
    note_id = note_id.lower()
    detail_id = note.get('note_id')
    if detail_id is not None and (not isinstance(detail_id, str) or detail_id.lower() != note_id):
        raise _CliError('invalid_response')
    if expected_id and note_id != expected_id:
        raise _CliError('invalid_response')
    title = _text(note.get('title') if detail else note.get('display_title'), 300)
    if title is None:
        raise _CliError('invalid_response')
    body = _text(note.get('desc')) if detail else None
    user = note.get('user') if isinstance(note.get('user'), dict) else {}
    metrics = note.get('interact_info') if isinstance(note.get('interact_info'), dict) else {}
    return {'id': note_id, 'title': title, 'body': body,
        'content_status': ('full' if body else 'unavailable') if detail else 'summary',
        'images': _images(note), 'url': f'https://www.xiaohongshu.com/explore/{note_id}',
        'author': _text(user.get('nickname'), 200) or _text(user.get('nick_name'), 200),
        'metrics': {'likes': _count(metrics.get('liked_count')), 'saves': _count(metrics.get('collected_count')),
                    'comments': _count(metrics.get('comment_count'))},
        'published_at': _published_at(note.get('time')) if detail else None}


def _html_note_item(note):
    """HTML read returns entry['note'] directly, without a feed items wrapper.

    Keep only known fields and require its own ID: the upstream HTML parser can
    return another map entry when the requested note is missing.
    """
    note_id = note.get('note_id', note.get('noteId'))
    if ('note_id' in note and 'noteId' in note
            and note['note_id'] != note['noteId']):
        raise _CliError('invalid_response')
    interact = note.get('interact_info', note.get('interactInfo'))
    if isinstance(interact, dict):
        interact = {snake: interact.get(snake, interact.get(camel)) for snake, camel in (
            ('liked_count', 'likedCount'), ('collected_count', 'collectedCount'),
            ('comment_count', 'commentCount'))}
    images = note.get('image_list', note.get('imageList'))
    normalized_images = []
    if isinstance(images, list):
        for image in images:
            if not isinstance(image, dict):
                continue
            normalized_images.append({
                'url_default': image.get('url_default', image.get('urlDefault')),
                'url_pre': image.get('url_pre', image.get('urlPre')),
                'info_list': image.get('info_list', image.get('infoList'))})
    return {'id': note_id, 'note_card': {
        'note_id': note_id, 'title': note.get('title'), 'desc': note.get('desc'),
        'time': note.get('time'), 'user': note.get('user'), 'cover': note.get('cover'),
        'interact_info': interact, 'image_list': normalized_images}}


def _parse_items(data, operation, expected_id):
    if not isinstance(data, dict):
        raise _CliError('invalid_response')
    if operation == 'read':
        if 'items' not in data:
            return [_map_item(_html_note_item(data), True, expected_id)]
        if not isinstance(data['items'], list):
            raise _CliError('invalid_response')
        if len(data['items']) != 1:
            raise _CliError('invalid_response')
        return [_map_item(data['items'][0], True, expected_id)]
    if not isinstance(data.get('items'), list):
        raise _CliError('invalid_response')
    result = []
    seen = set()
    for item in data['items']:
        if not isinstance(item, dict):
            raise _CliError('invalid_response')
        if not _text(item.get('model_type'), 100):
            raise _CliError('invalid_response')
        if item.get('model_type') != 'note':
            continue
        normalized = _map_item(item)
        if normalized['id'] not in seen:
            result.append(normalized)
            seen.add(normalized['id'])
    return result


def _failure(query, code, phase=None):
    message = _MESSAGES[code]
    if code == 'timeout':
        message = {'status': '小红书登录状态检查超时。',
                   'search': '小红书笔记搜索超时。',
                   'read': '小红书笔记详情读取超时。'}.get(phase, message)
    return {'query': query, 'items': [], 'source': _SOURCE, 'fetched_at': None, 'cached': False,
            'error': {'code': code, 'message': message, 'retryable': False}}


def _execute(operation, target, limit, expected_id=None):
    global _STOP_ERROR
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    if not _LOCK.acquire(timeout=_TIMEOUT_SECONDS):
        return _failure(target, 'busy')
    phase = None
    try:
        if _STOP_ERROR:
            raise _CliError(_STOP_ERROR)
        cli = _resolve_cli()
        phase = 'status'
        status = _run_cli_stage(cli, ['status', '--json'], deadline)
        if not isinstance(status, dict):
            raise _CliError('invalid_response')
        if (status.get('authenticated') is False or status.get('isAuthenticated') is False
                or (status.get('authenticated') is not True and status.get('isAuthenticated') is not True)):
            raise _CliError('auth_required')
        user = status.get('user')
        if status.get('guest') is True or (isinstance(user, dict) and user.get('guest') is True):
            raise _CliError('auth_required')
        account = _text(user.get('id'), 200) if isinstance(user, dict) else None
        key = (cli, account, operation, target)
        now = time.monotonic()
        # Without a stable account identity, never reuse cached data.
        cached = _CACHE.get(key) if account else None
        if cached and cached[0] > now:
            result = deepcopy(cached[1])
            result['cached'] = True
        else:
            phase = operation
            data = _run_cli_stage(cli, [operation, target, '--json'], deadline)
            result = {'query': target, 'items': _parse_items(data, operation, expected_id),
                      'source': _SOURCE, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                      'cached': False, 'error': None}
            if account:
                for old in [k for k, v in _CACHE.items() if v[0] <= now]:
                    del _CACHE[old]
                if len(_CACHE) >= _CACHE_MAX_ENTRIES:
                    del _CACHE[next(iter(_CACHE))]
                _CACHE[key] = (time.monotonic() + _CACHE_SECONDS, deepcopy(result))
        result['items'] = result['items'][:limit]
        return result
    except _CliError as exc:
        if exc.code in ('auth_required', 'risk_control'):
            _CACHE.clear()
        if exc.code == 'risk_control':
            _STOP_ERROR = exc.code
        return _failure(target, exc.code, phase)
    finally:
        _LOCK.release()


def search_notes(query: str, limit: int = 20) -> dict:
    """Return one CLI keyword-search page; limit only truncates local results."""
    if (not isinstance(query, str) or not query.strip() or len(query) > 100
            or re.search(r'[\x00-\x1f\x7f]', query) or query.strip().startswith('-')):
        return _failure('', 'invalid_query')
    query = ' '.join(query.split())
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        return _failure(query, 'invalid_limit')
    return _execute('search', query, limit)


def read_note(url: str) -> dict:
    """Read one official note URL; remove token/query/hash before CLI execution."""
    try:
        canonical, note_id = _canonical_url(url)
    except _CliError as exc:
        return _failure('', exc.code)
    return _execute('read', canonical, 1, note_id)
