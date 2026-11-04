"""Bounded official insurance news feed; publication dates are never heat scores."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import re
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import certifi

ASSOCIATION_URL = 'https://www.iachina.cn/'
MEDICAL_URL = 'https://ybj.sh.gov.cn/ybxw/index.html'
_SOURCES = {ASSOCIATION_URL: ('insurance', 'regulation'), MEDICAL_URL: ('medical',)}
_LABELS = {'insurance': '保险行业', 'regulation': '监管动态', 'medical': '医保动态'}
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_SECONDS = 300
_MAX_BYTES = 2 * 1024 * 1024
_RELEVANT = re.compile(r'保险|保单|寿险|财险|车险|再保|理赔|监管|养老|医保|医疗|医药|护理|骗保|保障')
_ADMINISTRATIVE = re.compile(r'招聘|设备采购|比选公告|中标结果|成交公告|OA系统|平台建设项目')
_POLITICAL = re.compile(r'党委|党组|全会精神|传达学习|习近平|总书记|党建|主题教育|从严治党')
_FULL_DATE = re.compile(r'(?<!\d)(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})(?:日)?(?!\d)')


def _today() -> date:
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _fetch_html(url: str) -> str:
    """Only two fixed entry points, verified TLS, no redirects, bounded bodies."""
    if url not in _SOURCES:
        raise ValueError('unsupported source')
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context()), _NoRedirect())
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
    with opener.open(req, timeout=8) as response:
        if response.geturl() != url or response.status != 200:
            raise ValueError('unexpected source response')
        if response.headers.get_content_type() not in ('text/html', 'application/xhtml+xml'):
            raise ValueError('unexpected source content type')
        raw = response.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError('source response too large')
        encoding = response.headers.get_content_charset() or 'utf-8'
        return raw.decode(encoding, errors='replace')


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None = None
    children: list = field(default_factory=list)

    def text(self) -> str:
        if self.tag in ('script', 'style', 'template', 'noscript'):
            return ''
        return ' '.join(x.text() if isinstance(x, _Node) else x for x in self.children)


class _Document(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node('root', {})
        self.current = self.root
        self.anchors: list[_Node] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        if tag == 'a':
            self.anchors.append(node)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def _clean(text: str) -> str:
    return ' '.join(text.split()).strip()


def _article_url(raw: str, source: str) -> tuple[str, str]:
    if not raw or re.search(r'[\x00-\x20\\]', raw):
        return '', ''
    try:
        parsed = urlsplit(urljoin(source, raw))
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return '', ''
        host = (parsed.hostname or '').lower()
        path = parsed.path
        if source == ASSOCIATION_URL:
            if host in ('iachina.cn', 'www.iachina.cn') and re.fullmatch(r'/art/20\d{2}/\d{1,2}/\d{1,2}/art_\d+_\d+\.html', path):
                return urlunsplit(('https', 'www.iachina.cn', path, '', '')), 'insurance'
            if host in ('nfra.gov.cn', 'www.nfra.gov.cn') and path == '/cn/view/pages/ItemDetail.html':
                query = parse_qs(parsed.query)
                if not re.fullmatch(r'\d+', query.get('docId', [''])[0]):
                    return '', ''
                values = {key: query[key][0] for key in ('docId', 'itemId', 'generaltype') if key in query}
                if any(not re.fullmatch(r'\d+', value) for value in values.values()):
                    return '', ''
                return urlunsplit(('https', 'www.nfra.gov.cn', path, urlencode(values), '')), 'regulation'
        elif host == 'ybj.sh.gov.cn' and re.fullmatch(r'/(?:ybdt|qtwj|zxzc)/20\d{6}/[A-Za-z0-9_-]+\.html', path):
            return urlunsplit(('https', host, path, '', '')), 'medical'
    except ValueError:
        pass
    return '', ''


def _container(anchor: _Node) -> _Node:
    node = anchor
    while node.parent is not None:
        if node.tag == 'li' or 'content-news-item' in (node.attrs.get('class') or '').split():
            return node
        node = node.parent
    return anchor


def _date_string(anchor: _Node, url: str, group: str) -> str | None:
    """Use publication paths or standalone displayed dates; no year inference."""
    # Association article paths carry the publication date; summaries mention events.
    if group == 'insurance':
        candidate = re.search(r'/art/(20\d{2})/(\d{1,2})/(\d{1,2})/', url)
        parts = candidate.groups() if candidate else None
    else:
        # Restrict displayed dates to standalone elements, never prose in summaries.
        def displayed_date(node):
            if node.tag in ('a', 'script', 'style', 'template', 'noscript'):
                return None
            match = _FULL_DATE.fullmatch(_clean(node.text()))
            if match:
                return match
            for child in node.children:
                if isinstance(child, _Node):
                    match = displayed_date(child)
                    if match:
                        return match
            return None
        candidate = displayed_date(_container(anchor))
        parts = candidate.groups() if candidate else None
        if parts is None and group == 'medical':
            candidate = re.search(r'/(20\d{2})(\d{2})(\d{2})/', url)
            parts = candidate.groups() if candidate else None
    if parts is None:
        return ''
    try:
        return date(*(int(p) for p in parts)).isoformat()
    except ValueError:
        return None


def _recent(items: list[dict], today: date) -> list[dict]:
    earliest = (today - timedelta(days=90)).isoformat()
    return [item.copy() for item in items if not item['published_at'] or earliest <= item['published_at'] <= today.isoformat()]


def _ordered(items: list[dict], today: date) -> list[dict]:
    # Sort ties by canonical URL so responsive duplicate markup cannot reorder results.
    ordered = sorted(_recent(items, today), key=lambda item: (item['url'], item['title']))
    ordered.sort(key=lambda item: item['published_at'], reverse=True)
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    result = []
    for item in ordered:
        title_key = item['title'].casefold()
        if item['url'] not in seen_urls and title_key not in seen_titles:
            seen_urls.add(item['url'])
            seen_titles.add(title_key)
            result.append(item)
    return result


def _parse(html: str, source: str, today: date) -> dict[str, list[dict]]:
    groups = {key: [] for key in _SOURCES[source]}
    for anchor in _Document(html).anchors:
        node: _Node | None = anchor
        hidden = False
        while node is not None:
            if node.tag in ('script', 'style', 'template', 'noscript'):
                hidden = True
                break
            node = node.parent
        if hidden:
            continue
        title = _clean(anchor.attrs.get('title') or anchor.text())
        if len(title) < 3 or len(title) > 500 or not _RELEVANT.search(title) or _POLITICAL.search(title) or _ADMINISTRATIVE.search(title):
            continue
        url, group = _article_url(anchor.attrs.get('href') or '', source)
        if not url:
            continue
        published = _date_string(anchor, url, group)
        if published is None:
            continue
        publisher = {'insurance': '中国保险行业协会', 'regulation': '国家金融监督管理总局', 'medical': '上海市医保局'}[group]
        groups[group].append({'title': title, 'url': url, 'hot': '', 'source': publisher, 'published_at': published})
    return {key: _ordered(items, today) for key, items in groups.items()}


def _parse_association(html: str, today: date) -> dict[str, list[dict]]:
    return _parse(html, ASSOCIATION_URL, today)


def _parse_medical(html: str, today: date) -> dict[str, list[dict]]:
    return _parse(html, MEDICAL_URL, today)


def _safe_error(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f'来源暂不可用（HTTP {error.code}）'
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return '来源请求超时'
    return '来源暂不可用，请稍后刷新'


async def insurance_trends(query: str = '', limit: int = 30) -> dict:
    """Return all three groups, including explicit partial-failure/stale states."""
    now, today = time.time(), _today()
    query = str(query or '')[:200].strip().casefold()
    try:
        limit = max(1, min(60, int(limit)))
    except (TypeError, ValueError, OverflowError):
        limit = 30
    needed = [source for source, keys in _SOURCES.items()
              if any(key not in _CACHE or now - _CACHE[key][0] >= _CACHE_SECONDS for key in keys)]
    responses = await asyncio.gather(*(asyncio.to_thread(_fetch_html, source) for source in needed), return_exceptions=True)
    errors: dict[str, str] = {}
    for source, response in zip(needed, responses):
        if isinstance(response, BaseException):
            errors.update({key: _safe_error(response) for key in _SOURCES[source]})
            continue
        try:
            parsed = _parse(response, source, today)
        except Exception:
            errors.update({key: '来源内容暂时无法解析' for key in _SOURCES[source]})
            continue
        for key, items in parsed.items():
            if items:
                _CACHE[key] = (time.time(), items)
            else:
                errors[key] = '来源未返回可用的近期行业资讯'
    groups = []
    for key, label in _LABELS.items():
        cached = _CACHE.get(key)
        items = _recent(cached[1], today) if cached else []
        if query:
            items = [item for item in items if query in f"{item['title']} {item['source']}".casefold()]
        error = errors.get(key)
        groups.append({'platform': key, 'label': label, 'items': items[:limit],
                       'ok': error is None and cached is not None,
                       'stale': error is not None and cached is not None,
                       'updated_at': cached[0] if cached else None, 'error': error})
    # Aggregate freshness cannot be newer than its oldest required source.
    timestamps = [group['updated_at'] for group in groups if group['updated_at'] is not None]
    updated = min(timestamps) if len(timestamps) == len(groups) else 0
    return {'trends': groups, 'updated': updated}
