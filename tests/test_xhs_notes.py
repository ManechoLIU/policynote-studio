"""Offline contracts for the read-only CLI adapter; never invoke a real xhs CLI."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
NOTE_ID = '64abcdef1234567890abcdef'
URL = f'https://www.xiaohongshu.com/explore/{NOTE_ID}'


def module():
    return importlib.import_module('easel.xhs_notes')


@pytest.fixture
def adapter(monkeypatch):
    m = module()
    monkeypatch.setattr(m, '_CACHE', {})
    monkeypatch.setattr(m, '_STOP_ERROR', None)
    monkeypatch.setattr(m, '_resolve_cli', lambda: '/offline/xhs')
    return m


def search_payload():
    return {'items': [{'id': NOTE_ID, 'model_type': 'note', 'xsec_token': 'PRIVATE_TOKEN',
                      'note_card': {'display_title': '  保险知识  ', 'desc': 'search is not full text',
                                    'user': {'nickname': '作者'},
                                    'interact_info': {'liked_count': '1.2万', 'collected_count': '30'},
                                    'cover': {'url_default': 'https://sns-webpic-qc.xhscdn.com/cover.jpg'}}}]}


def fake_cli(monkeypatch, m, payload, status=None):
    calls = []
    def run(executable, args, deadline):
        calls.append(args)
        if args[0] == 'status':
            return status if status is not None else {'authenticated': True, 'user': {'id': 'account-a'}}
        return payload
    monkeypatch.setattr(m, '_run_cli', run)
    return calls


def test_public_adapter_exists():
    assert callable(module().search_notes)
    assert callable(module().read_note)


def test_search_preserves_unknowns_and_never_claims_full_text(adapter, monkeypatch):
    calls = fake_cli(monkeypatch, adapter, search_payload())
    result = adapter.search_notes('  保险  ', limit=1)
    assert result['error'] is None and result['query'] == '保险'
    assert result['cached'] is False and result['fetched_at']
    assert result['source'] == 'xiaohongshu_cli'
    assert result['items'] == [{'id': NOTE_ID, 'title': '保险知识', 'body': None,
        'content_status': 'summary', 'images': ['https://sns-webpic-qc.xhscdn.com/cover.jpg'],
        'url': URL, 'author': '作者', 'metrics': {'likes': 12000, 'saves': 30, 'comments': None},
        'published_at': None}]
    assert calls == [['status', '--json'], ['search', '保险', '--json']]
    assert 'PRIVATE_TOKEN' not in json.dumps(result)


@pytest.mark.parametrize('query', ['', ' ', '--private', '\x00x', 'a'*101, None])
def test_invalid_keyword_does_not_start_cli(adapter, monkeypatch, query):
    monkeypatch.setattr(adapter, '_run_cli', lambda *a: pytest.fail('invalid input must not execute'))
    assert adapter.search_notes(query)['error']['code'] == 'invalid_query'


@pytest.mark.parametrize('limit', [0, -1, 101, True, '20'])
def test_invalid_limit_does_not_start_cli(adapter, monkeypatch, limit):
    monkeypatch.setattr(adapter, '_run_cli', lambda *a: pytest.fail('invalid input must not execute'))
    assert adapter.search_notes('保险', limit)['error']['code'] == 'invalid_limit'


def test_empty_search_is_distinct_from_invalid_payload(adapter, monkeypatch):
    fake_cli(monkeypatch, adapter, {'items': []})
    assert adapter.search_notes('保险')['error'] is None
    fake_cli(monkeypatch, adapter, {'unexpected': []})
    assert adapter.search_notes('other')['error']['code'] == 'invalid_response'


def test_malformed_note_is_not_successful_empty_list(adapter, monkeypatch):
    fake_cli(monkeypatch, adapter, {'items': [{'model_type': 'note', 'id': NOTE_ID}]})
    assert adapter.search_notes('保险')['error']['code'] == 'invalid_response'


def test_non_note_search_cards_are_skipped(adapter, monkeypatch):
    fake_cli(monkeypatch, adapter, {'items': [{'model_type': 'query', 'id': 'irrelevant'}]})
    assert adapter.search_notes('保险')['items'] == []


@pytest.mark.parametrize('desc,status', [('正文内容', 'full'), ('  ', 'unavailable'), (None, 'unavailable')])
def test_only_nonempty_detail_desc_is_full(adapter, monkeypatch, desc, status):
    fake_cli(monkeypatch, adapter, {'items': [{'id': NOTE_ID, 'note_card': {
        'note_id': NOTE_ID, 'title': '详情', 'desc': desc, 'time': 1762232400000,
        'image_list': [{'url_default': 'https://sns-img-qc.xhscdn.com/full.jpg'},
                       {'url_default': 'http://sns-img-qc.xhscdn.com/plain.jpg'},
                       {'url_default': 'https://evil.example/image.jpg'}]}}]})
    result = adapter.read_note(URL + '?xsec_token=PRIVATE#top')
    item = result['items'][0]
    assert item['content_status'] == status
    assert item['body'] == ('正文内容' if status == 'full' else None)
    assert item['images'] == ['https://sns-img-qc.xhscdn.com/full.jpg']
    assert item['published_at'] == '2025-11-04T05:00:00+00:00'
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('url', ['http://www.xiaohongshu.com/explore/'+NOTE_ID,
    'https://www.xiaohongshu.com.evil.example/explore/'+NOTE_ID,
    'https://www.xiaohongshu.com@127.0.0.1/explore/'+NOTE_ID,
    'https://www.xiaohongshu.com:444/explore/'+NOTE_ID,
    'https://127.0.0.1/explore/'+NOTE_ID, 'file:///etc/passwd',
    'https://xhslink.com/a/abc', 'https://www.xiaohongshu.com/user/profile/'+NOTE_ID])
def test_unsafe_and_unsupported_urls_do_not_execute(adapter, monkeypatch, url):
    monkeypatch.setattr(adapter, '_run_cli', lambda *a: pytest.fail('unsafe URL must not execute'))
    assert adapter.read_note(url)['error']['code'] == 'invalid_url'


def test_detail_id_mismatch_is_not_accepted(adapter, monkeypatch):
    fake_cli(monkeypatch, adapter, {'items': [{'note_card': {'note_id': 'a'*24, 'title': 'wrong', 'desc': 'wrong'}}]})
    assert adapter.read_note(URL)['error']['code'] == 'invalid_response'


def test_cache_revalidates_account_and_returns_independent_values(adapter, monkeypatch):
    calls = fake_cli(monkeypatch, adapter, search_payload())
    first = adapter.search_notes('保险')
    first['items'][0]['title'] = 'changed'
    second = adapter.search_notes('保险')
    assert second['cached'] is True and second['items'][0]['title'] == '保险知识'
    assert calls == [['status', '--json'], ['search', '保险', '--json'], ['status', '--json']]
    calls = fake_cli(monkeypatch, adapter, {'items': []}, {'authenticated': True, 'user': {'id': 'account-b'}})
    assert adapter.search_notes('保险')['cached'] is False
    assert calls[-1] == ['search', '保险', '--json']


def test_status_without_stable_id_never_caches(adapter, monkeypatch):
    calls = fake_cli(monkeypatch, adapter, search_payload(), {'authenticated': True, 'user': {}})
    adapter.search_notes('保险')
    assert adapter.search_notes('保险')['cached'] is False
    assert len(calls) == 4


def test_auth_failure_blocks_operation_and_clears_old_cache(adapter, monkeypatch):
    fake_cli(monkeypatch, adapter, search_payload())
    adapter.search_notes('保险')
    calls = fake_cli(monkeypatch, adapter, {'items': []}, {'authenticated': False})
    result = adapter.search_notes('保险')
    assert result['items'] == [] and result['error']['code'] == 'auth_required'
    assert calls == [['status', '--json']] and adapter._CACHE == {}


def test_risk_control_latches_and_never_retries(adapter, monkeypatch):
    calls = []
    def blocked(*args):
        calls.append(1)
        raise adapter._CliError('risk_control')
    monkeypatch.setattr(adapter, '_run_cli', blocked)
    assert adapter.search_notes('保险')['error']['code'] == 'risk_control'
    assert adapter.read_note(URL)['error']['code'] == 'risk_control'
    assert len(calls) == 1


def test_missing_cli_is_clear_configuration_error(adapter, monkeypatch):
    monkeypatch.setattr(adapter, '_resolve_cli', lambda: (_ for _ in ()).throw(adapter._CliError('cli_not_configured')))
    result = adapter.search_notes('保险')
    assert result['error']['code'] == 'cli_not_configured' and result['fetched_at'] is None


def test_serial_lock_includes_status_and_search(adapter, monkeypatch):
    events = []
    def run(executable, args, deadline):
        events.append(args[0])
        time.sleep(.01)
        return {'authenticated': True} if args[0] == 'status' else {'items': []}
    monkeypatch.setattr(adapter, '_run_cli', run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(adapter.search_notes, ['one', 'two']))
    assert events == ['status', 'search', 'status', 'search']


def offline_executable(tmp_path, body):
    f = tmp_path/'offline-xhs'
    f.write_text('#!'+sys.executable+'\n'+body)
    f.chmod(0o700)
    return str(f)


@pytest.mark.parametrize('code,want', [('not_authenticated', 'auth_required'),
    ('verification_required', 'risk_control'), ('ip_blocked', 'risk_control'),
    ('signature_error', 'risk_control'), ('api_error', 'rate_limited'), ('unknown_error', 'cli_failed')])
def test_cli_error_envelopes_are_sanitized(tmp_path, code, want):
    m = module()
    cli = offline_executable(tmp_path, 'print('+repr(json.dumps({'ok': False, 'error': {'code': code, 'message': 'cookie=SECRET /private/path'}}))+')\n')
    with pytest.raises(m._CliError) as exc:
        m._run_cli(cli, ['status', '--json'], time.monotonic()+5)
    assert exc.value.code == want
    assert 'SECRET' not in str(exc.value) and '/private/path' not in str(exc.value)


def test_runner_rejects_write_commands_before_spawn():
    m = module()
    with pytest.raises(m._CliError):
        m._run_cli('/never/execute', ['publish', '--private', '--json'], time.monotonic()+1)


def test_runner_bounds_output_and_timeout(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, '_MAX_OUTPUT_BYTES', 1024)
    cli = offline_executable(tmp_path, "print('x'*4096)\n")
    with pytest.raises(m._CliError) as exc:
        m._run_cli(cli, ['status', '--json'], time.monotonic()+5)
    assert exc.value.code == 'output_too_large'
    cli = offline_executable(tmp_path, 'import time\ntime.sleep(5)\n')
    with pytest.raises(m._CliError) as exc:
        m._run_cli(cli, ['status', '--json'], time.monotonic()+.05)
    assert exc.value.code == 'timeout'


def test_resolver_never_treats_env_as_shell_command(monkeypatch):
    m = module()
    monkeypatch.setenv('EASEL_XHS_CLI', 'xhs --private')
    with pytest.raises(m._CliError) as exc:
        m._resolve_cli()
    assert exc.value.code == 'cli_not_configured'


def test_expired_cache_fetches_again(adapter, monkeypatch):
    calls = fake_cli(monkeypatch, adapter, search_payload())
    monkeypatch.setattr(adapter, '_CACHE_SECONDS', -1)
    adapter.search_notes('保险')
    assert adapter.search_notes('保险')['cached'] is False
    assert len(calls) == 4


def test_conflicting_auth_flags_fail_closed(adapter, monkeypatch):
    calls = fake_cli(monkeypatch, adapter, {'items': []},
                     {'authenticated': False, 'isAuthenticated': True, 'user': {'id': 'account-a'}})
    result = adapter.search_notes('保险')
    assert result['error']['code'] == 'auth_required'
    assert calls == [['status', '--json']]


def test_detail_conflicting_ids_fail_closed(adapter, monkeypatch):
    fake_cli(monkeypatch, adapter, {'items': [{'id': NOTE_ID, 'note_card': {
        'note_id': 'a'*24, 'title': 'wrong', 'desc': 'wrong'}}]})
    assert adapter.read_note(URL)['error']['code'] == 'invalid_response'


def test_read_uses_canonical_url_without_credentials(adapter, monkeypatch):
    calls = fake_cli(monkeypatch, adapter, {'items': [{'note_card': {
        'note_id': NOTE_ID, 'title': '详情', 'desc': '正文'}}]})
    result = adapter.read_note(URL+'?xsec_token=SECRET&xsec_source=pc_search#top')
    assert result['query'] == URL
    assert calls[-1] == ['read', URL, '--json']


def test_expired_deadline_never_spawns(monkeypatch):
    m = module()
    monkeypatch.setattr(m.subprocess, 'Popen', lambda *a, **k: pytest.fail('expired deadline must not execute'))
    with pytest.raises(m._CliError) as exc:
        m._run_cli('/offline/xhs', ['status', '--json'], time.monotonic()-1)
    assert exc.value.code == 'timeout'


def test_runner_success_and_sanitized_stderr(tmp_path):
    m = module()
    cli = offline_executable(tmp_path, 'import sys\nsys.stderr.write("cookie=SECRET /private/path")\nprint(\'{"schema_version":"1", "ok":true,"data":{"items":[]}}\')\n')
    assert m._run_cli(cli, ['search', '保险', '--json'], time.monotonic()+5) == {'items': []}


@pytest.mark.parametrize('raw', ['{}', '[]', '{"ok": false}', '{"data":{}}garbage'])
def test_runner_never_treats_unknown_response_as_empty(tmp_path, raw):
    m = module()
    cli = offline_executable(tmp_path, 'print('+repr(raw)+')\n')
    with pytest.raises(m._CliError):
        m._run_cli(cli, ['status', '--json'], time.monotonic()+5)


def test_runner_limits_stderr_too(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, '_MAX_OUTPUT_BYTES', 1024)
    cli = offline_executable(tmp_path, 'import sys\nsys.stderr.write("x"*4096)\n')
    with pytest.raises(m._CliError) as exc:
        m._run_cli(cli, ['status', '--json'], time.monotonic()+5)
    assert exc.value.code == 'output_too_large'


def test_search_missing_card_discriminator_is_not_successful_empty(adapter, monkeypatch):
    fake_cli(monkeypatch, adapter, {'items': [{'unexpected': 'upstream schema changed'}]})
    result = adapter.search_notes('保险')
    assert result['error']['code'] == 'invalid_response'


@pytest.mark.parametrize('raw_note', [
    {'note_id': NOTE_ID, 'title': 'HTML详情', 'desc': 'HTML正文',
     'user': {'nickname': 'HTML作者'}, 'interact_info': {'liked_count': '1.2万'},
     'image_list': [{'url_default': 'https://sns-img-qc.xhscdn.com/html.jpg'}]},
    {'noteId': NOTE_ID, 'title': 'HTML详情', 'desc': 'HTML正文',
     'user': {'nickname': 'HTML作者'}, 'interactInfo': {'likedCount': '1.2万'},
     'imageList': [{'urlDefault': 'https://sns-img-qc.xhscdn.com/html.jpg'}]},
])
def test_read_accepts_html_fallback_bare_note(adapter, monkeypatch, raw_note):
    # client_mixins.get_note_detail -> html_parser.entry['note']; JSON data is bare.
    fake_cli(monkeypatch, adapter, raw_note)
    result = adapter.read_note(URL)
    assert result['error'] is None
    assert result['items'][0]['id'] == NOTE_ID
    assert result['items'][0]['body'] == 'HTML正文'
    assert result['items'][0]['content_status'] == 'full'
    assert result['items'][0]['images'] == ['https://sns-img-qc.xhscdn.com/html.jpg']
    assert result['items'][0]['metrics'] == {'likes': 12000, 'saves': None, 'comments': None}
    assert result['items'][0]['published_at'] is None


@pytest.mark.parametrize('raw_note', [
    {'noteId': 'a'*24, 'title': '其他详情', 'desc': '不能冒用'},
    {'title': '缺ID', 'desc': '不能猜请求ID'},
    {'noteId': NOTE_ID, 'note_id': 'a'*24, 'title': '冲突ID', 'desc': '不能猜'},
])
def test_html_fallback_requires_explicit_matching_note_id(adapter, monkeypatch, raw_note):
    # html_parser can select first entry when target is absent; never trust that fallback.
    fake_cli(monkeypatch, adapter, raw_note)
    result = adapter.read_note(URL)
    assert result['error']['code'] == 'invalid_response'
    assert result['items'] == []


@pytest.mark.parametrize('guest_status', [
    {'authenticated': True, 'user': {'id': 'account-a', 'guest': True}},
    {'authenticated': True, 'guest': True, 'user': {'id': 'account-a'}},
])
def test_guest_status_clears_cache_and_stops_before_search(adapter, monkeypatch, guest_status):
    fake_cli(monkeypatch, adapter, search_payload())
    adapter.search_notes('保险')
    calls = fake_cli(monkeypatch, adapter, {'items': []}, guest_status)
    result = adapter.search_notes('保险')
    assert result['error']['code'] == 'auth_required'
    assert result['items'] == [] and adapter._CACHE == {}
    assert calls == [['status', '--json']]


def test_read_bare_html_note_through_cli_json_envelope(adapter, monkeypatch, tmp_path):
    bare = {'noteId': NOTE_ID, 'title': 'HTML原路径', 'desc': '直接来自HTML note对象'}
    script = 'import json, sys\n' + (
        "data = {'authenticated': True, 'user': {'id': 'offline-account'}} if sys.argv[1] == 'status' else "
        + repr(bare) + '\n') + "print(json.dumps({'ok': True, 'schema_version': '1', 'data': data}))\n"
    cli = offline_executable(tmp_path, script)
    monkeypatch.setattr(adapter, '_resolve_cli', lambda: cli)
    result = adapter.read_note(URL)
    assert result['error'] is None
    assert result['items'][0]['body'] == '直接来自HTML note对象'
    assert result['items'][0]['content_status'] == 'full'


def test_status_timeout_logs_only_safe_phase_and_elapsed(adapter, monkeypatch, capsys):
    clock = {'now': 100.0}
    monkeypatch.setattr(adapter.time, 'monotonic', lambda: clock['now'])
    def timeout(*args):
        clock['now'] += 30
        raise adapter._CliError('timeout')
    monkeypatch.setattr(adapter, '_run_cli', timeout)
    result = adapter.search_notes('SECRET关键词')
    assert result['error']['code'] == 'timeout'
    assert '登录状态检查超时' in result['error']['message']
    lines = capsys.readouterr().err.splitlines()
    events = [json.loads(line) for line in lines]
    assert events == [{'event': 'xhs_cli_stage', 'phase': 'status',
                       'elapsed_ms': 30000, 'error_code': 'timeout'}]
    assert 'SECRET' not in '\n'.join(lines)


def test_shared_budget_exhaustion_is_observable_without_extending_deadline(adapter, monkeypatch, capsys):
    clock = {'now': 100.0}
    calls = []
    monkeypatch.setattr(adapter.time, 'monotonic', lambda: clock['now'])
    def run(executable, args, deadline):
        calls.append((args[0], deadline-clock['now']))
        if args[0] == 'status':
            clock['now'] += 29
            return {'authenticated': True, 'user': {'id': 'account-a'}}
        clock['now'] += 1
        raise adapter._CliError('timeout')
    monkeypatch.setattr(adapter, '_run_cli', run)
    result = adapter.search_notes('SECRET关键词')
    assert result['error']['code'] == 'timeout'
    assert '搜索超时' in result['error']['message']
    assert calls == [('status', 30), ('search', 1)]
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert events == [
        {'event': 'xhs_cli_stage', 'phase': 'status', 'elapsed_ms': 29000, 'error_code': None},
        {'event': 'xhs_cli_stage', 'phase': 'search', 'elapsed_ms': 1000, 'error_code': 'timeout'},
    ]


def test_detail_phase_log_never_contains_url_account_or_raw_error(adapter, monkeypatch, capsys):
    def run(executable, args, deadline):
        if args[0] == 'status':
            return {'authenticated': True, 'user': {'id': 'SECRET_ACCOUNT'}}
        raise adapter._CliError('risk_control')
    monkeypatch.setattr(adapter, '_run_cli', run)
    result = adapter.read_note(URL+'?xsec_token=SECRET_TOKEN')
    assert result['error']['code'] == 'risk_control'
    raw = capsys.readouterr().err
    events = [json.loads(line) for line in raw.splitlines()]
    assert [event['phase'] for event in events] == ['status', 'read']
    assert events[0]['error_code'] is None and events[1]['error_code'] == 'risk_control'
    assert all(isinstance(event['elapsed_ms'], int) and event['elapsed_ms'] >= 0 for event in events)
    assert 'SECRET' not in raw and NOTE_ID not in raw and 'https' not in raw


def test_unexpected_stage_error_logs_generic_code_only(adapter, monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError('cookie=SECRET /private/path')
    monkeypatch.setattr(adapter, '_run_cli', fail)
    with pytest.raises(RuntimeError):
        adapter.search_notes('保险')
    raw = capsys.readouterr().err
    assert json.loads(raw)['error_code'] == 'cli_failed'
    assert 'SECRET' not in raw and '/private/' not in raw
