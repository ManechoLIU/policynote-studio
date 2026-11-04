"""Actual HTTP boundary with offline adapter doubles; no xhs/platform calls."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from easel import xhs_notes
from web import app as web

NOTE_ID = '64abcdef1234567890abcdef'
URL = f'https://www.xiaohongshu.com/explore/{NOTE_ID}'
SEARCH = '/api/xhs/notes/search'
DETAIL = '/api/xhs/notes/detail'
ORIGIN = 'http://localhost:7860'


@pytest.fixture(autouse=True)
def no_real_cli(monkeypatch):
    monkeypatch.setattr(xhs_notes, '_run_cli', lambda *a, **k: pytest.fail('API test must never execute CLI'))


def client(peer=('127.0.0.1', 12345), origin=ORIGIN, base_url=ORIGIN):
    return TestClient(web.app, base_url=base_url, client=peer,
                      headers={'Origin': origin} if origin else {})


def payload(query='保险', error=None):
    return {'query': query, 'items': [], 'source': 'xiaohongshu_cli',
            'fetched_at': None if error else '2026-11-04T10:00:00+00:00',
            'cached': False, 'error': error}


def test_search_returns_adapter_envelope_unchanged(monkeypatch):
    expected = payload()
    expected['cached'] = True
    calls = []
    def search(query, limit=20):
        calls.append((query, limit))
        return deepcopy(expected)
    monkeypatch.setattr(xhs_notes, 'search_notes', search)
    response = client().get(SEARCH, params={'q': '保险'})
    assert response.status_code == 200
    assert response.json() == expected
    assert calls == [('保险', 20)]


def test_detail_returns_adapter_envelope_unchanged(monkeypatch):
    expected = payload(URL)
    calls = []
    monkeypatch.setattr(xhs_notes, 'read_note', lambda url: (calls.append(url), deepcopy(expected))[1])
    response = client().get(DETAIL, params={'url': URL})
    assert response.status_code == 200 and response.json() == expected
    assert calls == [URL]


@pytest.mark.parametrize('error_code', ['auth_required', 'cli_not_configured', 'risk_control', 'timeout'])
def test_business_failure_stays_200_with_adapter_error(monkeypatch, error_code):
    expected = payload(error={'code': error_code, 'message': '安全业务错误', 'retryable': False})
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: expected)
    response = client().get(SEARCH, params={'q': '保险'})
    assert response.status_code == 200 and response.json() == expected


@pytest.mark.parametrize('params', [{}, {'q': ''}, {'q': ' '}, {'q': 'a'*101},
                                    {'q': '--private'}, {'q': 'SECRET\x00'}, {'q': 'line\nfeed'}])
def test_bad_search_input_is_400_and_never_calls_adapter(monkeypatch, params):
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: pytest.fail('invalid request must not call adapter'))
    response = client().get(SEARCH, params=params)
    assert response.status_code == 400
    assert isinstance(response.json()['detail'], str)
    assert 'SECRET' not in response.text


@pytest.mark.parametrize('url', [None, '', 'file:///etc/SECRET', 'https://127.0.0.1/note',
    'https://www.xiaohongshu.com.evil.example/explore/'+NOTE_ID,
    'https://xhslink.com/a/SECRET', 'http://www.xiaohongshu.com/explore/'+NOTE_ID])
def test_bad_detail_url_is_400_and_never_calls_adapter(monkeypatch, url):
    monkeypatch.setattr(xhs_notes, 'read_note', lambda *a, **k: pytest.fail('invalid request must not call adapter'))
    response = client().get(DETAIL, params={} if url is None else {'url': url})
    assert response.status_code == 400
    assert isinstance(response.json()['detail'], str)
    assert 'SECRET' not in response.text


@pytest.mark.parametrize('route,key,value', [(SEARCH, 'q', '保险'), (DETAIL, 'url', URL)])
def test_duplicate_query_parameters_are_rejected(monkeypatch, route, key, value):
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: pytest.fail('duplicate input'))
    monkeypatch.setattr(xhs_notes, 'read_note', lambda *a, **k: pytest.fail('duplicate input'))
    response = client().get(route, params=[(key, value), (key, value)])
    assert response.status_code == 400


@pytest.mark.parametrize('route,params,method', [(SEARCH, {'q': '保险'}, 'search_notes'),
                                               (DETAIL, {'url': URL}, 'read_note')])
@pytest.mark.parametrize('peer,origin,fetch_site', [
    (('127.0.0.1', 12345), 'https://evil.example', None),
    (('192.168.1.2', 12345), None, None),
    (('127.0.0.1', 12345), None, 'cross-site'),
])
def test_untrusted_requests_cannot_start_account_cli(monkeypatch, route, params, method, peer, origin, fetch_site):
    monkeypatch.setattr(xhs_notes, method, lambda *a, **k: pytest.fail('permission rejected before adapter'))
    headers = {'Sec-Fetch-Site': fetch_site} if fetch_site else {}
    response = client(peer, origin).get(route, params=params, headers=headers)
    assert response.status_code == 403
    assert response.json()['detail'] == '仅允许从本机工作台操作。'


def test_loopback_without_origin_can_read(monkeypatch):
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: payload())
    assert client(origin=None).get(SEARCH, params={'q': '保险'}).status_code == 200


def test_custom_port_same_origin_retains_existing_allowance(monkeypatch):
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: payload())
    custom = 'http://localhost:7862'
    response = client(origin=custom, base_url=custom).get(SEARCH, params={'q': '保险'})
    assert response.status_code == 200


@pytest.mark.parametrize('bad', [None, {}, {'error': None},
    {'query': '保险', 'items': [], 'source': 'xiaohongshu_cli', 'fetched_at': None, 'cached': False},
    {'query': '保险', 'items': None, 'source': 'xiaohongshu_cli', 'fetched_at': None, 'cached': False, 'error': None}])
def test_missing_or_invalid_adapter_fields_fail_safely(monkeypatch, bad):
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: bad)
    response = client().get(SEARCH, params={'q': '保险'})
    assert response.status_code == 502
    assert response.json() == {'detail': '小红书笔记读取暂时不可用。'}


def test_unexpected_adapter_exception_never_leaks_details(monkeypatch):
    def fail(*a, **k):
        raise RuntimeError('cookie=SECRET /private/local/path')
    monkeypatch.setattr(xhs_notes, 'read_note', fail)
    response = client().get(DETAIL, params={'url': URL})
    assert response.status_code == 502
    assert 'SECRET' not in response.text and '/private/' not in response.text


def test_only_get_endpoints_are_registered(monkeypatch):
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: pytest.fail('POST must not call adapter'))
    assert client().post(SEARCH, json={'q': '保险'}).status_code == 405


def test_blocking_adapter_does_not_block_event_loop(monkeypatch):
    async def scenario():
        loop_thread = threading.get_ident()
        started = threading.Event()
        release = threading.Event()
        def search(*a, **k):
            assert threading.get_ident() != loop_thread
            started.set()
            assert release.wait(2)
            return payload()
        monkeypatch.setattr(xhs_notes, 'search_notes', search)
        transport = httpx.ASGITransport(app=web.app, client=('127.0.0.1', 12345))
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers={'Origin': ORIGIN}) as c:
            pending = asyncio.create_task(c.get(SEARCH, params={'q': '保险'}))
            try:
                for _ in range(50):
                    if started.is_set():
                        break
                    await asyncio.sleep(.01)
                assert started.is_set()
                assert not pending.done()
            finally:
                release.set()
            assert (await pending).status_code == 200
    asyncio.run(scenario())


def test_account_read_response_is_not_browser_cached(monkeypatch):
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: payload())
    response = client().get(SEARCH, params={'q': '保险'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'


def test_partial_note_cannot_escape_contract_guard(monkeypatch):
    bad = payload()
    bad['items'] = [{'id': NOTE_ID, 'title': 'missing body/status'}]
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: bad)
    response = client().get(SEARCH, params={'q': '保险'})
    assert response.status_code == 502
    assert response.json() == {'detail': '小红书笔记读取暂时不可用。'}


def test_adapter_validation_error_is_mapped_to_safe_400(monkeypatch):
    bad = payload(error={'code': 'invalid_query', 'message': 'SECRET /private/source', 'retryable': False})
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: bad)
    response = client().get(SEARCH, params={'q': '保险'})
    assert response.status_code == 400
    assert 'SECRET' not in response.text and '/private/' not in response.text


@pytest.mark.parametrize('route,params,method', [(SEARCH, {'q': '保险'}, 'search_notes'),
                                               (DETAIL, {'url': URL}, 'read_note')])
def test_same_site_without_origin_cannot_trigger_account_cli(monkeypatch, route, params, method):
    monkeypatch.setattr(xhs_notes, method, lambda *a, **k: pytest.fail('same-site resource must be refused'))
    response = client(origin=None).get(route, params=params, headers={'Sec-Fetch-Site': 'same-site'})
    assert response.status_code == 403
    assert response.json() == {'detail': '仅允许从本机工作台操作。'}


def test_same_origin_browser_without_origin_can_read(monkeypatch):
    monkeypatch.setattr(xhs_notes, 'search_notes', lambda *a, **k: payload())
    response = client(origin=None).get(SEARCH, params={'q': '保险'},
                                       headers={'Sec-Fetch-Site': 'same-origin'})
    assert response.status_code == 200
