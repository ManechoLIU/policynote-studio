"""Bounded launch options are opt-in; all browser operations are mocked."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shared/scripts'))
import xhs_publish as xhs


class TargetClosedError(Exception):
    pass


@pytest.fixture
def launch_env(tmp_path, monkeypatch):
    context = object()
    launch = Mock(return_value=context)
    sleep, clear, install = Mock(), Mock(), Mock()
    profile = tmp_path / 'offline-profile'
    monkeypatch.setattr(xhs, '_profile_dir', lambda base: profile)
    monkeypatch.setattr(xhs, '_cloak_executable', lambda: None)
    monkeypatch.setattr(xhs, '_browser_channel', lambda: None)
    monkeypatch.setattr(xhs, 'time', SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(xhs, '_clear_stale_chrome_locks', clear)
    monkeypatch.setattr(xhs.login_transport, 'install', install)
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    return playwright, context, profile, launch, sleep, clear, install


def test_default_launch_preserves_one_target_closed_retry(launch_env):
    p, context, profile, launch, sleep, clear, install = launch_env
    launch.side_effect = [TargetClosedError('closed'), context]
    assert xhs._launch(p, False, None, None) is context
    assert launch.call_count == 2
    assert 'timeout' not in launch.call_args.kwargs
    sleep.assert_called_once_with(1.2)
    clear.assert_called_once_with(profile)
    install.assert_called_once_with(context, 'xiaohongshu')


def test_bounded_no_retry_launch_never_sleeps_or_clears_locks(launch_env):
    p, _, _, launch, sleep, clear, install = launch_env
    launch.side_effect = TargetClosedError('closed')
    with pytest.raises(TargetClosedError):
        xhs._launch(p, False, None, None, timeout_ms=1200, retry_target_closed=False)
    launch.assert_called_once()
    assert launch.call_args.kwargs['timeout'] == 1200
    sleep.assert_not_called()
    clear.assert_not_called()
    install.assert_not_called()


@pytest.mark.parametrize('timeout_ms', [None, 2500])
def test_optional_timeout_and_no_retry_preserve_other_launch_arguments(timeout_ms, launch_env):
    p, context, profile, launch, sleep, clear, install = launch_env
    assert xhs._launch(p, True, None, 'http://offline-proxy', login_assets=False,
                       timeout_ms=timeout_ms, retry_target_closed=False) is context
    launch.assert_called_once()
    assert launch.call_args.args == (str(profile),)
    kwargs = launch.call_args.kwargs
    assert kwargs['headless'] is False and kwargs['locale'] == 'zh-CN'
    assert kwargs['proxy'] == {'server': 'http://offline-proxy'}
    assert ('timeout' in kwargs) is (timeout_ms is not None)
    if timeout_ms is not None:
        assert kwargs['timeout'] == timeout_ms
    sleep.assert_not_called()
    clear.assert_not_called()
    install.assert_not_called()


def test_default_launch_does_not_retry_other_errors(launch_env):
    p, _, _, launch, sleep, clear, _ = launch_env
    launch.side_effect = ValueError('unavailable')
    with pytest.raises(ValueError):
        xhs._launch(p, False, None, None)
    launch.assert_called_once()
    sleep.assert_not_called()
    clear.assert_not_called()
