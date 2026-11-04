"""Offline feed contracts; no platform, browser or credential access."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/shared/scripts'))
import redbook_read as shim
from easel import xhs_notes as notes

ID = '64abcdef1234567890abcdef'
ACCOUNT = 'a' * 24
URL = f'https://www.xiaohongshu.com/explore/{ID}'

@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(notes, '_CACHE', {})
    monkeypatch.setattr(notes, '_NOTE_CONTEXTS', {})
    monkeypatch.setattr(notes, '_STOP_ERROR', None)
    monkeypatch.setattr(notes, '_resolve_cli', lambda: str(notes._BUNDLED_SHIM))
    account = [ACCOUNT]
    calls = []
    def run(cli, args, deadline):
        calls.append(args)
        if args[0] == 'status': return {'authenticated': True, 'user': {'id': account[0]}}
        if args[0] == 'feed':
            assert notes._EXPECTED_ACCOUNT.get() == account[0]
            return {'items': [{'id': ID, 'model_type': 'note', 'xsec_token': 'SECRET',
                               'note_card': {'display_title': '推荐内容'}}]}
        if args[0] == 'search': return {'items': []}
        assert notes._READ_CONTEXT.get()['source'] == 'pc_feed'
        return {'note_id': ID, 'title': '推荐内容', 'desc': '全文'}
    monkeypatch.setattr(notes, '_run_cli', run)
    return account, calls


def test_feed_cache_account_isolation_and_locator_read_original(state):
    account, calls = state
    result = notes.feed_notes()
    assert result['query'] == '' and result['source'] == 'xiaohongshu_homefeed'
    assert result['items'][0]['title'] == '推荐内容' and result['items'][0]['metrics']['likes'] is None
    assert 'SECRET' not in json.dumps(result)
    assert notes.feed_notes()['cached'] is True
    assert notes.search_notes('保险')['items'] == []
    assert notes.read_note(URL)['items'][0]['body'] == '全文'
    assert notes.original_note_url(URL).endswith('xsec_token=SECRET&xsec_source=pc_feed')
    account[0] = 'b' * 24
    assert notes.feed_notes()['cached'] is False
    assert calls.count(['feed', '--json']) == 2
    assert calls.count(['search', '保险', '--json']) == 1


def test_feed_fail_no_retry_or_cache(state, monkeypatch):
    _, calls = state
    original = notes._run_cli
    def run(cli, args, deadline):
        if args[0] == 'feed':
            calls.append(args)
            raise notes._CliError('risk_control')
        return original(cli, args, deadline)
    monkeypatch.setattr(notes, '_run_cli', run)
    result = notes.feed_notes()
    assert result['source'] == 'xiaohongshu_homefeed' and result['error']['code'] == 'risk_control'
    assert calls.count(['feed', '--json']) == 1 and not notes._CACHE
    notes.feed_notes()
    assert calls.count(['feed', '--json']) == 1


def test_context_source_strict_and_direct_token_rejected(monkeypatch):
    base = {'note_id': ID, 'token': 'SECRET', 'source': 'pc_feed', 'account_id': ACCOUNT}
    monkeypatch.setenv('EASEL_XHS_NOTE_CONTEXT', json.dumps(base))
    assert shim._note_context(URL)['source'] == 'pc_feed'
    base['source'] = 'evil'
    monkeypatch.setenv('EASEL_XHS_NOTE_CONTEXT', json.dumps(base))
    with pytest.raises(shim._Invalid): shim._note_context(URL)
    with pytest.raises(shim._Invalid): shim._note_context(URL + '?xsec_token=OTHER')
    assert shim._arguments(['feed', '--json']) == ('feed', [])
    with pytest.raises(shim._Invalid): shim._arguments(['feed', '--category', 'insurance', '--json'])


def test_bootstrap_checks_same_account_before_feed(tmp_path):
    (tmp_path / 'package.json').write_text('{"type":"module"}')
    lib = tmp_path / 'lib'; lib.mkdir()
    (lib / 'client.js').write_text('export class XhsClient { async getSelfInfo(){return {guest:false,user_id:process.env.FAKE_ACCOUNT}} async getHomeFeed(c){return {category:c}} }')
    (lib / 'cookies.js').write_text('export function parseCookieString(){return {}}')
    (lib / 'platform.js').write_text('export const PLATFORMS={xhs:{}}')
    entry = tmp_path / 'cli.js'; entry.touch()
    env = dict(os.environ, FAKE_ACCOUNT=ACCOUNT, EASEL_XHS_EXPECTED_ACCOUNT=ACCOUNT, REDBOOK_COOKIE_STRING='SECRET')
    def run(): return subprocess.run(['node', '--input-type=module', '--eval', shim._FEED_BOOTSTRAP, str(entry)], env=env, capture_output=True, text=True, timeout=5)
    valid = run()
    assert valid.returncode == 0 and json.loads(valid.stdout) == {'category':'homefeed_recommend'}
    env['FAKE_ACCOUNT'] = 'b'*24
    mismatch = run()
    assert mismatch.returncode != 0 and mismatch.stdout == '' and 'Code: -100' in mismatch.stderr
    assert 'SECRET' not in mismatch.stderr


def test_feed_is_limited_and_locator_is_not_created_for_hidden_rows(state, monkeypatch):
    _, calls = state
    def run(cli, args, deadline):
        calls.append(args)
        if args[0] == 'status': return {'authenticated': True, 'user': {'id': ACCOUNT}}
        return {'items': [{'id': f'{i:024x}', 'model_type': 'note', 'xsec_token': 'SECRET',
                           'note_card': {'display_title': f'内容{i}'}} for i in range(30)]}
    monkeypatch.setattr(notes, '_run_cli', run)
    result = notes.feed_notes()
    assert len(result['items']) == 20 and len(notes._NOTE_CONTEXTS) == 20
    assert notes._read_locator(str(notes._BUNDLED_SHIM), ACCOUNT, f'{20:024x}') is None


def test_original_rejects_untrusted_source(state):
    notes.feed_notes()
    key = next(iter(notes._NOTE_CONTEXTS))
    expiry, context = notes._NOTE_CONTEXTS[key]
    notes._NOTE_CONTEXTS[key] = (expiry, {**context, 'source': 'evil'})
    with pytest.raises(notes._CliError) as error:
        notes.original_note_url(URL)
    assert error.value.code == 'original_unavailable'
