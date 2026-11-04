"""Offline community login and identity boundary regressions."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shared/scripts'))
import redbook_read as read
import xhs_publish as xhs


@pytest.fixture
def login(monkeypatch, tmp_path):
    import playwright.sync_api as playwright
    clock = [0.0]
    monkeypatch.setattr(xhs.time, 'monotonic', lambda: clock[0])
    page = Mock()
    page.wait_for_timeout.side_effect = lambda ms: clock.__setitem__(0, clock[0] + ms / 1000)
    ctx = Mock(pages=[page])
    manager = Mock()
    manager.__enter__ = Mock(return_value=object())
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(playwright, 'sync_playwright', lambda: manager)
    monkeypatch.setattr(xhs, '_ProfileLock', lambda _: Mock())
    monkeypatch.setattr(xhs, '_launch', lambda *a, **k: ctx)
    monkeypatch.setattr(xhs, '_proxy', lambda *a: None)
    creator = Mock(return_value={'userId': 'creator'})
    monkeypatch.setattr(xhs, '_creator_account', creator)
    monkeypatch.setattr(xhs, '_wait_community_login_surface', lambda p: None)
    monkeypatch.setattr(xhs, '_is_logged_in', lambda p: True)
    monkeypatch.setattr(xhs, '_login_error', lambda p: '')
    monkeypatch.setattr(xhs, '_qr_state', lambda p: '')
    capture = Mock(return_value=True)
    monkeypatch.setattr(xhs, '_capture_login_qr', capture)
    monkeypatch.setattr(xhs.login_qr, 'capture_qr', lambda *a, **k: None)
    states = []
    monkeypatch.setattr(xhs.login_state, 'write_status', lambda _, state, *a, **k: states.append(state))
    args = SimpleNamespace(qr_out=str(tmp_path/'qr.png'), timeout=12, status_file=None,
                           profile_base=None, headed=False, proxy=None, no_proxy=True, community_only=True)
    return args, page, ctx, creator, capture, states


def test_community_valid_fast_path(login, monkeypatch):
    args, page, ctx, creator, capture, states = login
    probe = Mock(return_value=True)
    monkeypatch.setattr(read, 'community_status_from_context', probe)
    assert xhs.cmd_login(args) == 0
    assert states[-1] == 'success'
    probe.assert_called_once_with(ctx, timeout_s=6)
    creator.assert_not_called()
    capture.assert_not_called()
    page.goto.assert_not_called()


def test_creator_and_page_login_do_not_prove_community(login, monkeypatch):
    args, page, ctx, creator, capture, states = login
    probe = Mock(return_value=False)
    monkeypatch.setattr(read, 'community_status_from_context', probe)
    assert xhs.cmd_login(args) == 1
    assert 'success' not in states
    creator.assert_not_called()
    assert capture.call_args.kwargs['community_only'] is True
    assert probe.call_count <= 2


def test_success_after_scan_requires_community_probe(login, monkeypatch):
    args, _, _, creator, _, states = login
    probe = Mock(side_effect=[False, True])
    monkeypatch.setattr(read, 'community_status_from_context', probe)
    assert xhs.cmd_login(args) == 0
    assert states[-1] == 'success' and probe.call_count == 2
    creator.assert_not_called()


def test_default_creator_fast_path_unchanged(login):
    args, _, _, creator, capture, states = login
    args.community_only = False
    assert xhs.cmd_login(args) == 0
    assert states[-1] == 'success'
    creator.assert_called_once()
    capture.assert_not_called()


def test_community_navigation_failure_never_falls_back(login, monkeypatch):
    args, page, _, creator, capture, states = login
    monkeypatch.setattr(read, 'community_status_from_context', lambda *a, **k: False)
    page.goto.side_effect = RuntimeError('SECRET')
    assert xhs.cmd_login(args) == 1
    assert states[-1] == 'error'
    creator.assert_not_called()
    capture.assert_not_called()


def test_probe_validates_identity_and_never_exposes_cookie(monkeypatch, capsys):
    ctx = Mock()
    ctx.cookies.return_value = [{'name': k, 'value': 'SECRET', 'domain': '.xiaohongshu.com', 'path': '/'} for k in ('a1', 'web_session')]
    monkeypatch.setattr(read, '_node', lambda: '/node')
    run = Mock(return_value=(0, json.dumps({'guest': False, 'user_id': 'a'*24}).encode(), b''))
    monkeypatch.setattr(read, '_run', run)
    assert read.community_status_from_context(ctx)
    assert 'SECRET' not in repr(run.call_args.args[0])
    run.return_value = (0, b'{"guest":true,"user_id":"aaaaaaaaaaaaaaaaaaaaaaaa"}', b'SECRET')
    assert not read.community_status_from_context(ctx)
    run.side_effect = RuntimeError('SECRET')
    with pytest.raises(read._SessionError, match='unknown_error'):
        read.community_status_from_context(ctx)
    assert 'SECRET' not in str(capsys.readouterr())


def test_community_capture_never_navigates_creator(monkeypatch, tmp_path):
    page = Mock()
    monkeypatch.setattr(xhs, '_is_logged_in', lambda p: True)
    monkeypatch.setattr(xhs, '_login_error', lambda p: 'failure')
    with pytest.raises(RuntimeError):
        xhs._capture_login_qr(page, tmp_path/'qr', community_only=True)
    page.goto.assert_not_called()


@pytest.mark.parametrize('stderr,code', [
    (b'Code: 300012\nSECRET', 'ip_blocked'),
    (b'Code: 300015\nSECRET', 'signature_error'),
    (b'Error: Captcha required: SECRET', 'verification_required'),
    (b'Code: 429\nSECRET', 'api_error'),
    (b'network SECRET', 'unknown_error'),
])
def test_risk_stops_without_retry_or_secret(login, monkeypatch, capsys, stderr, code):
    args, page, ctx, creator, capture, states = login
    ctx.cookies.return_value = [{'name': k, 'value': 'SECRET', 'domain': '.xiaohongshu.com', 'path': '/'} for k in ('a1', 'web_session')]
    monkeypatch.setattr(read, '_node', lambda: '/node')
    run = Mock(return_value=(1, b'', stderr))
    monkeypatch.setattr(read, '_run', run)
    with pytest.raises(read._SessionError, match=code):
        read.community_status_from_context(ctx)
    run.reset_mock()
    assert xhs.cmd_login(args) == 1
    run.assert_called_once()
    assert states[-1] == 'error' and 'success' not in states
    creator.assert_not_called()
    capture.assert_not_called()
    page.goto.assert_not_called()
    assert 'SECRET' not in str(capsys.readouterr())


@pytest.mark.parametrize('failure,expected', [
    ('cookie_read', 'cookie_read'), ('cookie_select', 'cookie_select'),
    ('node_resolve', 'node_resolve'), ('node_exit', 'node_exit'),
    ('json_parse', 'json_parse'), ('identity_shape', 'identity_shape'),
])
def test_diagnostics_are_fixed_and_do_not_leak(monkeypatch, capsys, failure, expected):
    ctx = Mock()
    ctx.cookies.return_value = [{'name': k, 'value': 'SECRET', 'domain': '.xiaohongshu.com', 'path': '/'} for k in ('a1', 'web_session')]
    if failure == 'cookie_read': ctx.cookies.side_effect = RuntimeError('SECRET')
    if failure == 'cookie_select': monkeypatch.setattr(read, '_select_cookies', Mock(side_effect=RuntimeError('SECRET')))
    monkeypatch.setattr(read, '_node', Mock(side_effect=RuntimeError('SECRET')) if failure == 'node_resolve' else Mock(return_value='/node'))
    if failure == 'node_exit': outcome = (1, b'SECRET', b'unsafe SECRET')
    elif failure == 'json_parse': outcome = (0, b'SECRET', b'')
    elif failure == 'identity_shape': outcome = (0, b'{"guest":false,"user_id":"SECRET"}', b'')
    else: outcome = (0, b'{}', b'')
    run = Mock(return_value=outcome)
    monkeypatch.setattr(read, '_run', run)
    with pytest.raises(read._SessionError) as error:
        read.community_status_from_context(ctx)
    assert error.value.code == 'unknown_error' and error.value.stage == expected
    with pytest.raises(RuntimeError) as shown:
        xhs._community_identity(ctx)
    assert '阶段：' in str(shown.value) and 'unknown_error' in str(shown.value)
    assert 'SECRET' not in str(shown.value) + str(capsys.readouterr())
    assert run.call_count <= 2


def test_failure_stage_and_code_are_allowlisted():
    error = read._SessionError('SECRET', 'SECRET')
    assert error.code == 'unknown_error' and error.stage is None
