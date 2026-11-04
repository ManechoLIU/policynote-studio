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
    monkeypatch.setattr(m, '_NOTE_CONTEXTS', {})
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


def test_search_accepts_original_cli_title_field_without_display_title(adapter, monkeypatch):
    payload = search_payload()
    card = payload['items'][0]['note_card']
    card['title'] = card.pop('display_title')
    fake_cli(monkeypatch, adapter, payload)
    result = adapter.search_notes('家庭保险')
    assert result['error'] is None
    assert result['items'][0]['title'] == '保险知识'
    assert result['items'][0]['body'] is None


def test_untitled_image_note_does_not_break_other_search_results(adapter, monkeypatch):
    payload = search_payload()
    payload['items'][0]['note_card'].pop('display_title')
    fake_cli(monkeypatch, adapter, payload)
    result = adapter.search_notes('家庭保险')
    assert result['error'] is None
    assert result['items'][0]['title'] == '无标题笔记'
    assert result['items'][0]['images']
    assert result['items'][0]['body'] is None


def test_official_cdn_http_image_is_upgraded_to_https(adapter, monkeypatch):
    payload = search_payload()
    payload['items'][0]['note_card']['cover']['url_default'] = 'http://sns-webpic-qc.xhscdn.com/cover.jpg'
    fake_cli(monkeypatch, adapter, payload)
    result = adapter.search_notes('家庭保险')
    assert result['error'] is None
    assert result['items'][0]['images'] == ['https://sns-webpic-qc.xhscdn.com/cover.jpg']


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
    assert item['images'] == ['https://sns-img-qc.xhscdn.com/full.jpg',
                              'https://sns-img-qc.xhscdn.com/plain.jpg']
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
    ('profile_busy', 'busy'), ('read_timeout', 'timeout'),
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


def test_bundled_reader_receives_remaining_budget_without_mutating_parent(monkeypatch, tmp_path):
    m = module()
    cli = offline_executable(tmp_path,
        "import os,json\nprint(json.dumps({'data':{'remaining_ms':os.environ.get('EASEL_XHS_READ_BUDGET_MS')}}))\n")
    monkeypatch.setattr(m, '_BUNDLED_SHIM', Path(cli))
    monkeypatch.setenv('EASEL_XHS_READ_BUDGET_MS', '999999')
    result = m._run_cli(cli, ['status', '--json'], time.monotonic()+5)
    assert 0 < int(result['remaining_ms']) <= 4000
    import os
    assert os.environ['EASEL_XHS_READ_BUDGET_MS'] == '999999'


def test_custom_reader_keeps_existing_environment_contract(monkeypatch, tmp_path):
    m = module()
    cli = offline_executable(tmp_path,
        "import os,json\nprint(json.dumps({'data':{'remaining_ms':os.environ.get('EASEL_XHS_READ_BUDGET_MS')}}))\n")
    monkeypatch.delenv('EASEL_XHS_READ_BUDGET_MS', raising=False)
    assert m._run_cli(cli, ['status', '--json'], time.monotonic()+5) == {'remaining_ms': None}


def test_bundled_reader_uses_server_python_not_ambient_shebang(monkeypatch, tmp_path):
    m = module()
    cli = tmp_path/'bundled-reader'
    cli.write_text('#!/does/not/exist/python\nimport json,sys\nprint(json.dumps({"data":{"python":sys.executable}}))\n')
    cli.chmod(0o700)
    monkeypatch.setattr(m, '_BUNDLED_SHIM', cli)
    assert m._run_cli(str(cli), ['status', '--json'], time.monotonic()+5) == {'python': sys.executable}


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


def test_default_resolver_uses_bundled_shim_without_external_cli(monkeypatch):
    m=module()
    monkeypatch.delenv('EASEL_XHS_CLI',raising=False)
    resolved=m._resolve_cli()
    assert resolved==str(Path(__file__).resolve().parents[1]/'skills/shared/scripts/redbook_read.py')


def test_explicit_override_keeps_external_cli_route(monkeypatch,tmp_path):
    m=module()
    cli=Path(offline_executable(tmp_path,'pass\n'))
    monkeypatch.setenv('EASEL_XHS_CLI',str(cli))
    assert m._resolve_cli()==str(cli)


def test_missing_bundled_entry_is_configuration_error_not_external_fallback(monkeypatch,tmp_path):
    m=module()
    monkeypatch.delenv('EASEL_XHS_CLI',raising=False)
    monkeypatch.setattr(m,'_REDBOOK_ENTRY',tmp_path/'missing-dist.js')
    with pytest.raises(m._CliError) as exc:m._resolve_cli()
    assert exc.value.code=='cli_not_configured'


def test_missing_node_is_configuration_error_without_external_fallback(monkeypatch):
    m=module()
    monkeypatch.delenv('EASEL_XHS_CLI',raising=False)
    monkeypatch.setattr(m.shutil,'which',lambda name:None)
    with pytest.raises(m._CliError) as exc:m._resolve_cli()
    assert exc.value.code=='cli_not_configured'


def test_empty_explicit_override_does_not_silently_use_bundled_reader(monkeypatch):
    m=module()
    monkeypatch.setenv('EASEL_XHS_CLI','')
    with pytest.raises(m._CliError) as exc:m._resolve_cli()
    assert exc.value.code=='cli_not_configured'


def test_bundled_search_locator_reaches_read_only_inside_scoped_context(adapter,monkeypatch,capsys):
    m=adapter
    monkeypatch.setattr(m,'_resolve_cli',lambda:str(m._BUNDLED_SHIM))
    account={'id':'account-a'}
    seen=[]
    def run(executable,args,deadline):
        if args[0]=='status':return {'authenticated':True,'user':account}
        if args[0]=='search':return search_payload()
        locator=m._READ_CONTEXT.get()
        seen.append(locator)
        if not locator or locator['token']!='PRIVATE_TOKEN':raise m._CliError('cli_failed')
        return {'note_id':NOTE_ID,'title':'保险知识','desc':'实际详情正文'}
    monkeypatch.setattr(m,'_run_cli',run)
    result=m.search_notes('保险')
    assert 'PRIVATE_TOKEN' not in json.dumps(result)
    read=m.read_note(URL+'?xsec_token=UNTRUSTED_USER_TOKEN')
    assert read['error'] is None and read['items'][0]['content_status']=='full'
    assert seen==[{'note_id':NOTE_ID,'token':'PRIVATE_TOKEN','source':'pc_search','account_id':'account-a'}]
    assert m._READ_CONTEXT.get() is None
    assert 'PRIVATE_TOKEN' not in json.dumps(read)+capsys.readouterr().err


def test_locator_is_account_scoped_expires_and_never_applies_to_custom_cli(adapter,monkeypatch):
    m=adapter
    monkeypatch.setattr(m,'_resolve_cli',lambda:str(m._BUNDLED_SHIM))
    clock={'now':100.0};account={'id':'account-a'};seen=[]
    monkeypatch.setattr(m.time,'monotonic',lambda:clock['now'])
    def run(executable,args,deadline):
        if args[0]=='status':return {'authenticated':True,'user':account}
        if args[0]=='search':return search_payload()
        seen.append(m._READ_CONTEXT.get())
        raise m._CliError('cli_failed')
    monkeypatch.setattr(m,'_run_cli',run)
    m.search_notes('保险')
    account['id']='account-b';m.read_note(URL)
    assert seen[-1] is None
    account['id']='account-a';clock['now']=100+m._NOTE_CONTEXT_SECONDS+1;m.read_note(URL)
    assert seen[-1] is None
    clock['now']=2000;m.search_notes('保险')
    monkeypatch.setattr(m,'_resolve_cli',lambda:'/offline/custom-xhs')
    m.read_note(URL)
    assert seen[-1] is None


def test_authentication_failure_clears_private_note_locators(adapter,monkeypatch):
    m=adapter
    monkeypatch.setattr(m,'_resolve_cli',lambda:str(m._BUNDLED_SHIM))
    calls=fake_cli(monkeypatch,m,search_payload())
    m.search_notes('保险')
    assert m._NOTE_CONTEXTS
    fake_cli(monkeypatch,m,{}, {'authenticated':False})
    assert m.read_note(URL)['error']['code']=='auth_required'
    assert m._NOTE_CONTEXTS=={}


def test_private_locator_cache_has_capacity_and_requires_account(adapter,monkeypatch):
    m=adapter
    monkeypatch.setattr(m,'_NOTE_CONTEXT_MAX',1)
    cli=str(m._BUNDLED_SHIM)
    raw=search_payload()
    items=m._parse_items(raw,'search',None)
    m._remember_note_contexts(cli,None,raw,items)
    assert m._NOTE_CONTEXTS=={}
    m._remember_note_contexts(cli,'account-a',raw,items)
    other='a'*24
    raw2={'items':[{'id':other,'model_type':'note','xsec_token':'OTHER_TOKEN','note_card':{'display_title':'另一篇'}}]}
    m._remember_note_contexts(cli,'account-a',raw2,m._parse_items(raw2,'search',None))
    assert len(m._NOTE_CONTEXTS)==1
    assert m._read_locator(cli,'account-a',NOTE_ID) is None
    assert m._read_locator(cli,'account-a',other)['token']=='OTHER_TOKEN'


def test_bundled_locator_is_environment_only_and_inherited_override_is_removed(monkeypatch,tmp_path):
    m=module()
    import os
    f=tmp_path/'safe-bundle.py'
    f.write_text('import json,os,sys\n'
                 +'ctx=os.environ.get("EASEL_XHS_NOTE_CONTEXT")\n'
                 +'print(json.dumps({"data":{"has_context":bool(ctx),"secret_in_argv":any("PRIVATE" in a for a in sys.argv)}}))\n')
    f.chmod(0o700)
    monkeypatch.setattr(m,'_BUNDLED_SHIM',f)
    monkeypatch.setenv('EASEL_XHS_NOTE_CONTEXT','PARENT_UNTRUSTED')
    token=m._READ_CONTEXT.set({'note_id':NOTE_ID,'token':'PRIVATE_TOKEN','source':'pc_search'})
    try:
        result=m._run_cli(str(f),['read',URL,'--json'],m.time.monotonic()+5)
        assert result=={'has_context':True,'secret_in_argv':False}
        status=m._run_cli(str(f),['status','--json'],m.time.monotonic()+5)
        assert status=={'has_context':False,'secret_in_argv':False}
    finally:m._READ_CONTEXT.reset(token)
    assert os.environ['EASEL_XHS_NOTE_CONTEXT']=='PARENT_UNTRUSTED'


def test_locator_can_be_recovered_from_official_search_web_url_without_public_leak(adapter):
    m=adapter
    raw={'items':[{'id':NOTE_ID,'model_type':'note','webUrl':URL+'?xsec_token=WEB_ONLY_TOKEN&xsec_source=pc_search',
                   'note_card':{'display_title':'标题'}}]}
    public=m._parse_items(raw,'search',None)
    m._remember_note_contexts(str(m._BUNDLED_SHIM),'account-a',raw,public)
    assert m._read_locator(str(m._BUNDLED_SHIM),'account-a',NOTE_ID)['token']=='WEB_ONLY_TOKEN'
    assert 'WEB_ONLY_TOKEN' not in json.dumps(public)


def test_conflicting_search_locator_fields_are_not_trusted(adapter):
    m=adapter
    raw=search_payload()
    raw['items'][0]['webUrl']=URL+'?xsec_token=CONFLICT'
    m._remember_note_contexts(str(m._BUNDLED_SHIM),'account-a',raw,m._parse_items(raw,'search',None))
    assert m._read_locator(str(m._BUNDLED_SHIM),'account-a',NOTE_ID) is None


def test_search_expected_account_is_scoped_and_failed_search_cannot_cache(adapter,monkeypatch):
    m=adapter
    monkeypatch.setattr(m,'_resolve_cli',lambda:str(m._BUNDLED_SHIM))
    def run(executable,args,deadline):
        if args[0]=='status':return {'authenticated':True,'user':{'id':'account-a'}}
        assert m._EXPECTED_ACCOUNT.get()=='account-a'
        raise m._CliError('auth_required')
    monkeypatch.setattr(m,'_run_cli',run)
    result=m.search_notes('保险')
    assert result['error']['code']=='auth_required'
    assert m._CACHE=={} and m._NOTE_CONTEXTS=={}
    assert m._EXPECTED_ACCOUNT.get() is None


def test_note_locator_outlives_search_cache_but_expires_at_its_own_bound(adapter,monkeypatch):
    m=adapter
    clock={'now':100.0}
    monkeypatch.setattr(m.time,'monotonic',lambda:clock['now'])
    raw=search_payload();cli=str(m._BUNDLED_SHIM)
    m._remember_note_contexts(cli,'account-a',raw,m._parse_items(raw,'search',None))
    clock['now']=100+m._CACHE_SECONDS+1
    assert m._read_locator(cli,'account-a',NOTE_ID) is not None
    clock['now']=100+m._NOTE_CONTEXT_SECONDS+1
    assert m._read_locator(cli,'account-a',NOTE_ID) is None


def test_expected_search_account_is_only_derived_for_bundled_search(monkeypatch,tmp_path):
    m=module()
    import os
    f=tmp_path/'expected-account-bundle.py'
    f.write_text('import json,os\nprint(json.dumps({"data":{'
        +'"expected":os.environ.get("EASEL_XHS_EXPECTED_ACCOUNT"),'
        +'"inherited_keyword":os.environ.get("EASEL_XHS_SEARCH_KEYWORD")}}))\n')
    monkeypatch.setattr(m,'_BUNDLED_SHIM',f)
    monkeypatch.setenv('EASEL_XHS_EXPECTED_ACCOUNT','PARENT_UNTRUSTED')
    monkeypatch.setenv('EASEL_XHS_SEARCH_KEYWORD','PARENT_QUERY')
    token=m._EXPECTED_ACCOUNT.set('1234567890abcdef12345678')
    try:
        search=m._run_cli(str(f),['search','保险','--json'],m.time.monotonic()+5)
        status=m._run_cli(str(f),['status','--json'],m.time.monotonic()+5)
    finally:m._EXPECTED_ACCOUNT.reset(token)
    assert search=={'expected':'1234567890abcdef12345678','inherited_keyword':None}
    assert status=={'expected':None,'inherited_keyword':None}
    assert os.environ['EASEL_XHS_EXPECTED_ACCOUNT']=='PARENT_UNTRUSTED'
    assert os.environ['EASEL_XHS_SEARCH_KEYWORD']=='PARENT_QUERY'


def test_original_url_uses_fresh_current_account_locator_and_encoded_official_target(adapter,monkeypatch):
    from urllib.parse import parse_qs,urlsplit
    m=adapter
    cli=str(m._BUNDLED_SHIM)
    monkeypatch.setattr(m,'_resolve_cli',lambda:cli)
    raw=search_payload();raw['items'][0]['xsec_token']='NOTE+/='
    m._remember_note_contexts(cli,'account-a',raw,m._parse_items(raw,'search',None))
    calls=[]
    def run(executable,args,deadline):
        calls.append(args)
        return {'authenticated':True,'user':{'id':'account-a'}}
    monkeypatch.setattr(m,'_run_cli',run)
    result=m.original_note_url(URL)
    parsed=urlsplit(result)
    assert parsed.scheme=='https' and parsed.netloc=='www.xiaohongshu.com'
    assert parsed.path=='/explore/'+NOTE_ID
    assert parse_qs(parsed.query)=={'xsec_token':['NOTE+/='],'xsec_source':['pc_search']}
    assert '%2B%2F%3D' in result and 'web_session' not in result and 'a1=' not in result
    assert calls==[['status','--json']]


def test_original_url_rejects_missing_expired_or_other_account_context(adapter,monkeypatch):
    m=adapter;cli=str(m._BUNDLED_SHIM)
    monkeypatch.setattr(m,'_resolve_cli',lambda:cli)
    clock={'now':100.0};account={'id':'account-a'}
    monkeypatch.setattr(m.time,'monotonic',lambda:clock['now'])
    monkeypatch.setattr(m,'_run_cli',lambda *a:{'authenticated':True,'user':account})
    with pytest.raises(m._CliError) as exc:m.original_note_url(URL)
    assert exc.value.code=='original_unavailable'
    raw=search_payload();m._remember_note_contexts(cli,'account-a',raw,m._parse_items(raw,'search',None))
    account['id']='account-b'
    with pytest.raises(m._CliError) as exc:m.original_note_url(URL)
    assert exc.value.code=='original_unavailable'
    account['id']='account-a';clock['now']=100+m._NOTE_CONTEXT_SECONDS+1
    with pytest.raises(m._CliError) as exc:m.original_note_url(URL)
    assert exc.value.code=='original_unavailable'


@pytest.mark.parametrize('url',[URL+'?xsec_token=USER',URL+'#fragment',URL+'/',
    URL.replace('www.xiaohongshu.com','evil.example'),URL.replace('/explore/','/discovery/item/')])
def test_original_only_accepts_exact_public_canonical_url(adapter,monkeypatch,url):
    monkeypatch.setattr(adapter,'_run_cli',lambda *a:pytest.fail('invalid original URL must not execute'))
    with pytest.raises(adapter._CliError) as exc:adapter.original_note_url(url)
    assert exc.value.code=='invalid_url'


def test_custom_cli_original_context_is_explicitly_unsupported(adapter,monkeypatch):
    monkeypatch.setattr(adapter,'_resolve_cli',lambda:'/offline/custom')
    monkeypatch.setattr(adapter,'_run_cli',lambda *a:pytest.fail('unsupported reader must not execute'))
    with pytest.raises(adapter._CliError) as exc:adapter.original_note_url(URL)
    assert exc.value.code=='original_unsupported'
