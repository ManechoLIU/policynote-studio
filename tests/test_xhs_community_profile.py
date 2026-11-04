"""Community profile routing, with only fake browser and lock objects."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shared/scripts'))
import xhs_publish as publish
import redbook_read as read


def test_community_profile_is_separate_from_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    assert publish._profile_dir(None) == tmp_path / '.easel-browser-profiles' / 'XiaohongshuProfile'
    assert publish._profile_dir(publish._community_profile_base()) == tmp_path / '.easel-browser-profiles' / 'community-reader' / 'XiaohongshuProfile'
    assert publish._profile_dir(None) != publish._profile_dir(publish._community_profile_base())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('community,explicit', [(True, None), (True, '/chosen'), (False, None), (False, '/chosen')])
def test_login_lock_and_launch_share_exact_base(monkeypatch, tmp_path, community, explicit):
    from playwright import sync_api
    profile = []
    launches = []
    ctx = Mock(pages=[Mock()])
    manager = Mock()
    manager.__enter__ = Mock(return_value=object())
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(sync_api, 'sync_playwright', lambda: manager)
    monkeypatch.setattr(publish, '_community_profile_base', lambda: '/community')
    monkeypatch.setattr(publish, '_ProfileLock', lambda p: profile.append(p) or Mock())
    monkeypatch.setattr(publish, '_launch', lambda *a, **kw: launches.append(kw) or ctx)
    monkeypatch.setattr(publish, '_proxy', lambda *a: None)
    monkeypatch.setattr(publish.login_state, 'write_status', lambda *a, **kw: None)
    monkeypatch.setattr(read, 'community_status_from_context', lambda *a, **kw: True)
    monkeypatch.setattr(publish, '_creator_account', lambda *a: {'userId': 'creator'})
    args = SimpleNamespace(qr_out=str(tmp_path/'qr'), timeout=1, status_file=None,
                           profile_base=explicit, headed=False, proxy=None, no_proxy=True,
                           community_only=community)
    assert publish.cmd_login(args) == 0
    base = explicit if explicit is not None else '/community' if community else None
    assert profile == [publish._profile_dir(base)]
    assert launches[0]['base'] == base
    assert not list(tmp_path.iterdir())


def test_cookie_bridge_uses_identical_community_base(monkeypatch):
    calls = []
    monkeypatch.setattr(publish, '_community_profile_base', lambda: '/community')
    class Lock:
        def __init__(self, path): calls.append(('profile', path))
        def acquire(self, timeout): pass
        def release(self): calls.append(('release',))
    class Context:
        def cookies(self, url):
            return [{'name': n, 'value': 'FAKE', 'path': '/', 'domain': '.xiaohongshu.com'} for n in ('a1','web_session')]
        def close(self): calls.append(('close',))
    class Manager:
        def __enter__(self): return None
        def __exit__(self, *a): pass
    def launch(*a, **kw): calls.append(('launch', kw['base'])); return Context()
    monkeypatch.setattr(read, '_dependencies', lambda: (Manager, Lock, launch, publish._profile_dir, publish._community_profile_base))
    assert read._profile_cookie_string(read.time.monotonic() + 30).startswith('a1=FAKE')
    assert calls[0] == ('profile', publish._profile_dir('/community'))
    assert ('launch', '/community') in calls and calls[-1] == ('release',)
