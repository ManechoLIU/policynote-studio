"""Stage evidence survives failures without exposing content or replaying actions."""
import json
import sys
from types import SimpleNamespace

import pytest

from tests.test_xhs_draft import Page, forbid_public
from tests.test_xhs_auth_diagnostics import playwright_double
from tests.test_xhs_draft_api import setup as publish_setup
import xhs_publish as xhs


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    forbid_public(monkeypatch)
    page = Page()
    context = SimpleNamespace(pages=[page], close=lambda: None)
    playwright_double(monkeypatch, context)
    monkeypatch.setattr(xhs, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(xhs.content_guard, 'guard_or_die', lambda *a, **kw: None)
    for helper in ('_click_publish_tab', '_upload_images', '_fill_editor'):
        monkeypatch.setattr(xhs, helper, lambda *a, **kw: None)
    media = tmp_path / 'cover.png'
    media.write_bytes(b'media')
    args = SimpleNamespace(title='标题', content='secret-body', tags='', images=str(media), exec=True,
                           profile_base=None, proxy=None, no_proxy=True, headed=False, keep_open=False)
    return page, context, args


@pytest.mark.parametrize('stage', ['prepare', 'runtime', 'profile_lock', 'launch', 'tab', 'upload', 'fill', 'draft_prepare', 'draft_action'])
def test_failure_reports_stage_and_action_uncertainty(stage, publisher, monkeypatch, capsys):
    page, context, args = publisher

    def fail(*a, **kw):
        raise ValueError('secret-account secret-body?token=secret-token')

    if stage == 'prepare':
        args.title = ''
    elif stage == 'runtime':
        monkeypatch.setitem(sys.modules, 'playwright.sync_api', None)
    elif stage == 'profile_lock':
        def busy(*args):
            raise TimeoutError('secret-profile-owner')
        monkeypatch.setattr(xhs, '_ProfileLock', lambda *a: SimpleNamespace(acquire=busy))
    elif stage == 'draft_prepare':
        page.evaluate = fail
    elif stage == 'draft_action':
        def click(**kwargs):
            page.buttons[0].clicks += 1
            page.clicked = True
            fail()
        page.buttons[0].click = click
    else:
        target = {'launch': '_launch', 'tab': '_click_publish_tab', 'upload': '_upload_images', 'fill': '_fill_editor'}[stage]
        monkeypatch.setattr(xhs, target, fail)
    with pytest.raises(SystemExit):
        xhs._publish(args, 'image')
    captured = capsys.readouterr()
    result = json.loads(captured.out.splitlines()[-1])
    diagnostic = result['failureDiagnostic']
    assert diagnostic['stage'] == stage
    assert diagnostic['published'] is False
    assert diagnostic['action_sent'] is (None if stage == 'draft_action' else False)
    expected_type = {'prepare': 'SystemExit', 'runtime': 'ModuleNotFoundError', 'profile_lock': 'TimeoutError'}
    assert diagnostic['error_type'] == expected_type.get(stage, 'ValueError')
    assert 'secret-' not in captured.out + captured.err
    assert page.buttons[0].clicks == (1 if stage == 'draft_action' else 0)
    if stage == 'draft_action':
        assert 'draft_action_attempt' in captured.err


@pytest.mark.parametrize('primary_failure', [False, True])
def test_cleanup_does_not_replace_primary_failure_or_confirmed_receipt(primary_failure, publisher, monkeypatch, capsys):
    page, context, args = publisher

    def close():
        raise RuntimeError('secret-cleanup')

    context.close = close
    if primary_failure:
        def fail(*args, **kwargs):
            raise ValueError('secret-primary')
        monkeypatch.setattr(xhs, '_fill_editor', fail)
        with pytest.raises(SystemExit):
            xhs._publish(args, 'image')
    else:
        assert xhs._publish(args, 'image') == 0
    captured = capsys.readouterr()
    terminal = json.loads(captured.out.splitlines()[-1])
    if primary_failure:
        diagnostic = terminal['failureDiagnostic']
        assert diagnostic['stage'] == 'fill' and diagnostic['error_type'] == 'ValueError'
        assert diagnostic['cleanup_error_type'] == 'RuntimeError'
    else:
        assert terminal == {'outcome': 'draft_saved', 'published': False}
    assert 'secret-' not in captured.out + captured.err


def test_auth_terminal_is_not_overwritten_by_cleanup_failure(publisher, monkeypatch, capsys):
    page, context, args = publisher
    page.url = 'https://creator.xiaohongshu.com/login'
    page.query_selector = lambda selector: None
    page.query_selector_all = lambda selector: [SimpleNamespace(is_visible=lambda: True)]
    def close():
        raise RuntimeError('secret-cleanup')
    context.close = close
    with pytest.raises(SystemExit):
        xhs._publish(args, 'image')
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['outcome'] == 'auth_required'
    assert result['error'] == 'XHS_AUTH_REQUIRED'


def test_playwright_exit_cleanup_keeps_original_failure(publisher, monkeypatch, capsys):
    _, _, args = publisher

    class Manager:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            raise RuntimeError('secret-manager-cleanup')

    def fail(*args, **kwargs):
        raise ValueError('secret-original')

    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(
        sync_playwright=Manager, TimeoutError=TimeoutError))
    monkeypatch.setattr(xhs, '_fill_editor', fail)
    with pytest.raises(SystemExit):
        xhs._publish(args, 'image')
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.out.splitlines()[-1])['failureDiagnostic']
    assert diagnostic['stage'] == 'fill' and diagnostic['error_type'] == 'ValueError'
    assert diagnostic['cleanup_error_type'] == 'RuntimeError'
    assert 'secret-' not in captured.out + captured.err


@pytest.mark.parametrize('boundary', ['launch', 'pages', 'new_page', 'default_timeout'])
def test_launch_and_page_init_error_survive_manager_cleanup(boundary, publisher, monkeypatch, capsys):
    page, context, args = publisher

    class Manager:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            raise RuntimeError('secret-manager-cleanup')

    def fail(*args, **kwargs):
        raise ValueError('secret-initialization')

    if boundary == 'launch':
        monkeypatch.setattr(xhs, '_launch', fail)
    elif boundary == 'pages':
        class Context:
            @property
            def pages(self):
                return fail()
        monkeypatch.setattr(xhs, '_launch', lambda *a, **kw: Context())
    elif boundary == 'new_page':
        context.pages = []
        context.new_page = fail
    else:
        page.set_default_timeout = fail
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(
        sync_playwright=Manager, TimeoutError=TimeoutError))
    with pytest.raises(SystemExit):
        xhs._publish(args, 'image')
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.out.splitlines()[-1])['failureDiagnostic']
    assert diagnostic['stage'] == 'launch' and diagnostic['error_type'] == 'ValueError'
    assert diagnostic['cleanup_error_type'] == 'RuntimeError'
    assert diagnostic['action_sent'] is False
    assert 'secret-' not in captured.out + captured.err


@pytest.mark.parametrize('action_sent', [False, None, True])
def test_backend_persists_only_fixed_failure_fields(action_sent, publish_setup):
    client, _, process, root = publish_setup
    (root / 'cover.png').write_bytes(b'media')
    process.returncode = 1
    diagnostic = {'stage': 'draft_action' if action_sent is not False else 'upload',
                  'error_type': 'ValueError', 'errorCode': 'XHS_DRAFT_STAGE_UNCONFIRMED' if action_sent is not False else 'XHS_DRAFT_STAGE_FAILED',
                  'action_sent': action_sent, 'confirmation_sent': False, 'published': False,
                  'content': 'secret-body', 'url': 'https://creator.xiaohongshu.com/?token=secret-token'}
    process.stdout = json.dumps({'outcome': 'failed', 'published': False, 'failureDiagnostic': diagnostic})
    process.stderr = 'secret-raw\nXHS_FAILURE_DIAGNOSTIC ' + json.dumps(diagnostic)
    response = client.post('/api/publish/xiaohongshu', json={
        'title': 'secret-title', 'body': 'secret-body', 'media': ['cover.png'],
    }).json()
    assert response['ok'] is False and response['published'] is False
    assert response['failureDiagnostic']['action_sent'] is action_sent
    assert response['failureDiagnostic']['stage'] == diagnostic['stage']
    assert 'secret-' not in json.dumps(response) + (root / '_publish.log').read_text()
    assert 'failureDiagnostic' in (root / '_publish.log').read_text()
