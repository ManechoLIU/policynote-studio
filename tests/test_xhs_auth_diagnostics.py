"""Authentication failures stay observable and inconclusive; no live browser calls."""
import asyncio
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shared/scripts'))
import xhs_publish as xhs
from web import app as web
from tests.test_xhs_draft_api import setup as publish_setup


def playwright_double(monkeypatch, context):
    class Manager:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            pass

    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(
        sync_playwright=Manager, TimeoutError=TimeoutError))
    monkeypatch.setattr(xhs, '_launch', lambda *a, **kw: context)
    monkeypatch.setattr(xhs, '_ProfileLock', lambda *a: SimpleNamespace(
        acquire=lambda *a: None, release=lambda: None))


@pytest.mark.parametrize('kind', ['image', 'video'])
@pytest.mark.parametrize('url,wall,outcome,navigation_error,probe_error', [
    ('https://creator.xiaohongshu.com/login?token=secret-token#secret-hash', True, 'auth_required', False, False),
    ('https://creator.xiaohongshu.com/login?token=secret-token', False, 'auth_unknown', False, False),
    ('https://creator.xiaohongshu.com/login?token=secret-token', None, 'auth_unknown', False, False),
    ('https://creator.xiaohongshu.com/publish/publish?next=login&token=secret-token', True, 'auth_unknown', False, False),
    ('https://untrusted.example/login?token=secret-token', True, 'auth_unknown', False, False),
    ('https://creator.xiaohongshu.com/login?token=secret-token', True, 'auth_unknown', True, False),
    ('https://creator.xiaohongshu.com/login?token=secret-token', True, 'auth_unknown', False, True),
])
def test_publish_auth_refusal_requires_official_login_path_and_visible_wall(
        kind, url, wall, outcome, navigation_error, probe_error, tmp_path, monkeypatch, capsys):
    class Page:
        def __init__(self):
            self.url = url

        def goto(self, *a, **kw):
            if navigation_error:
                raise TimeoutError('secret-navigation-url?token=secret-token')

        def set_default_timeout(self, *a):
            pass

        def wait_for_timeout(self, *a):
            pass

        def query_selector(self, selector):
            if probe_error:
                raise RuntimeError('secret-probe-context-destroyed')
            return None

        def query_selector_all(self, selector):
            if wall is None:
                raise RuntimeError('secret-context-detail')
            return [SimpleNamespace(is_visible=lambda: True)] if wall else []

        def screenshot(self, *, path, full_page, timeout):
            assert full_page is False and timeout == 3000
            Path(path).write_bytes(b'offline-viewport')

    page = Page()
    playwright_double(monkeypatch, SimpleNamespace(pages=[page], close=lambda: None))
    monkeypatch.setattr(xhs, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(xhs.content_guard, 'guard_or_die', lambda *a, **kw: None)
    monkeypatch.setattr(xhs, '_click_publish_tab', lambda *a: pytest.fail('auth refusal precedes media'))
    media = tmp_path / ('cover.png' if kind == 'image' else 'video.mp4')
    media.write_bytes(b'media')
    args = SimpleNamespace(title='标题', content='正文', tags='', images=str(media), video=str(media),
                           exec=True, profile_base=None, proxy=None, no_proxy=True, headed=False, keep_open=False)
    with pytest.raises(SystemExit):
        xhs._publish(args, kind)
    captured = capsys.readouterr()
    receipt = json.loads(captured.out.splitlines()[-1])
    assert receipt['outcome'] == outcome and receipt['published'] is False
    rows = [json.loads(line.split(' ', 1)[1]) for line in captured.err.splitlines()
            if line.startswith('XHS_AUTH_DIAGNOSTIC ')]
    assert rows[-1]['stage'] == ('publish_navigation' if navigation_error else 'publish_auth')
    assert rows[-1]['login_ok'] is (None if navigation_error else False)
    assert rows[-1]['login_wall'] is wall
    assert rows[-1]['action_sent'] is False
    if probe_error:
        assert rows[-1]['error_type'] == 'RuntimeError'
    assert 'secret-' not in captured.out + captured.err
    assert len(list((tmp_path / 'outputs/_runtime/draft-r1').glob('*.png'))) == 1


@pytest.mark.parametrize('failure_stage', ['whoami_launch', 'whoami_page'])
def test_whoami_failure_stdout_contains_only_controlled_error_metadata(failure_stage, monkeypatch, capsys):
    context = SimpleNamespace(pages=[object()], close=lambda: None)
    playwright_double(monkeypatch, context)

    def fail(*a, **kw):
        raise RuntimeError('secret-url?token=secret-cookie /private/secret-path')

    if failure_stage == 'whoami_launch':
        monkeypatch.setattr(xhs, '_launch', fail)
    else:
        monkeypatch.setattr(xhs, '_authenticated_account', lambda *a: None)
        monkeypatch.setattr(xhs, '_check_account_login', fail)
    args = SimpleNamespace(profile_base=None, proxy=None, no_proxy=True)
    assert xhs.cmd_whoami(args) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result['error'] == 'XHS_WHOAMI_UNCONFIRMED'
    assert result['stage'] == failure_stage and result['error_type'] == 'RuntimeError'
    assert 'secret-' not in captured.out + captured.err


def test_whoami_profile_lock_failure_has_controlled_reason(monkeypatch, capsys):
    playwright_double(monkeypatch, object())

    def busy(*args):
        raise TimeoutError('secret-lock-owner')

    monkeypatch.setattr(xhs, '_ProfileLock', lambda *a: SimpleNamespace(acquire=busy))
    assert xhs.cmd_whoami(SimpleNamespace(profile_base=None, proxy=None, no_proxy=True)) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result['error'] == 'XHS_PROFILE_BUSY' and result['stage'] == 'whoami_profile_lock'
    assert 'secret-' not in captured.out + captured.err


@pytest.mark.parametrize('unsafe', [False, True])
def test_backend_whoami_reports_safe_cause_without_invalidating_last_state(unsafe, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(web, 'LOGIN_DIR', tmp_path)
    monkeypatch.setattr(web, 'LOGIN_PROCESSES', {})
    monkeypatch.setattr(web, '_WHOAMI_TASKS', {})
    monkeypatch.setattr(web, '_WHOAMI_LOCK', threading.Lock())
    identity = {'loggedIn': True, 'name': 'Own identity', 'avatar': 'https://self.test/avatar'}
    monkeypatch.setattr(web, '_WHOAMI_CACHE', {'xiaohongshu': (web.time.time(), identity)})
    web._write_login_marker('xiaohongshu', 'success')
    initial = (tmp_path / 'xiaohongshu.json').read_bytes()
    payload = {'loggedIn': False, 'error': 'secret-error' if unsafe else 'XHS_PROFILE_BUSY',
               'stage': 'secret-stage' if unsafe else 'whoami_profile_lock',
               'error_type': 'secret-type' if unsafe else 'TimeoutError'}
    monkeypatch.setattr(web.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(payload), stderr='secret-stderr'))
    with pytest.raises(web.HTTPException) as error:
        asyncio.run(web.api_account_whoami('xiaohongshu', force=True))
    assert error.value.status_code == 502
    expected = 'XHS_WHOAMI_UNCONFIRMED' if unsafe else 'XHS_PROFILE_BUSY'
    assert error.value.headers['X-XHS-Auth-Code'] == expected
    assert (tmp_path / 'xiaohongshu.json').read_bytes() == initial
    assert web._WHOAMI_CACHE['xiaohongshu'][1] == identity
    assert 'secret-' not in str(error.value.detail) + json.dumps(error.value.headers) + capsys.readouterr().err


@pytest.mark.parametrize('outcome', ['auth_required', 'auth_unknown'])
def test_publish_backend_preserves_marker_and_redacts_content_from_auth_failure_log(
        outcome, publish_setup, monkeypatch):
    client, _, result, root = publish_setup
    monkeypatch.setattr(web, 'LOGIN_DIR', root / '_login')
    web._write_login_marker('xiaohongshu', 'success')
    initial = (web.LOGIN_DIR / 'xiaohongshu.json').read_bytes()
    (root / 'cover.png').write_bytes(b'media')
    result.returncode = 1
    result.stdout = json.dumps({'outcome': outcome, 'published': False,
                               'error': 'XHS_AUTH_REQUIRED' if outcome == 'auth_required' else 'XHS_AUTH_UNKNOWN',
                               'stage': 'publish_auth', 'error_type': 'RuntimeError'})
    result.stderr = 'ERROR: secret-stderr?token=secret-token /private/secret-path\nXHS_AUTH_DIAGNOSTIC ' + json.dumps({
        'stage': 'publish_auth', 'state': outcome, 'login_ok': False, 'login_wall': None,
        'action_sent': False, 'url': 'https://creator.xiaohongshu.com/login?token=secret-token#secret-hash',
        'error_type': 'secret-error-type', 'unknown': 'secret-field'})
    response = client.post('/api/publish/xiaohongshu', json={
        'title': 'secret-title', 'body': 'secret-body', 'media': ['cover.png'],
    }).json()
    assert response['ok'] is False and response['published'] is False
    assert response['authDiagnostic']['state'] == outcome
    assert response['authDiagnostic']['stage'] == 'publish_auth'
    assert 'secret-' not in json.dumps(response) + (root / '_publish.log').read_text()
    assert 'https://creator.xiaohongshu.com/login' in (root / '_publish.log').read_text()
    assert (web.LOGIN_DIR / 'xiaohongshu.json').read_bytes() == initial
