"""No platform calls: exercise the actual HTTP boundary with a fake runner."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from web import app as web


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setattr(web, 'SCHEDULE_FILE', tmp_path / '_schedule.json')
    env = tmp_path / '.env'
    env.write_text('EASEL_PUBLISH_MODE=automated\n', encoding='utf-8')
    monkeypatch.setattr(web, 'ENV_FILE', env)
    monkeypatch.delenv('EASEL_PUBLISH_MODE', raising=False)
    calls = []
    result = SimpleNamespace(returncode=0, stdout='', stderr='')

    def run(command, **kwargs):
        calls.append(command)
        return result

    monkeypatch.setattr(web.subprocess, 'run', run)
    client = TestClient(web.app, base_url='http://localhost:7860',
                        headers={'Origin': 'http://localhost:7860'})
    return client, calls, result, tmp_path


@pytest.mark.parametrize('media', ['cover.png', 'clip.mp4'])
def test_xhs_forces_draft_and_never_records_published(setup, media):
    client, calls, result, root = setup
    (root / media).write_bytes(b'test-media')
    result.stdout = 'platform log\n' + json.dumps({'outcome': 'draft_saved', 'published': False})
    response = client.post('/api/publish/xiaohongshu', json={
        'title': '测试稿', 'body': '测试正文', 'media': [media],
        'draft': False, 'publish': True,  # old/untrusted clients cannot opt out
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['ok'] is True
    assert data['outcome'] == 'draft_saved'
    assert data['published'] is False
    assert '--draft' in calls[0] and '--exec' in calls[0]
    assert calls[0][2] == ('publish-video' if media.endswith('.mp4') else 'publish')
    assert not (root / '_schedule.json').exists()


@pytest.mark.parametrize('stdout', [
    '', '发布成功', '{}', '{"outcome":"draft_saved","published":true}',
    '{"outcome":"published","published":false}',
    '{"outcome":"draft_saved","published":0}', '[]',
    '{"outcome":"draft_saved","published":false}\nnot a terminal receipt',
])
def test_xhs_exit_zero_without_valid_draft_receipt_is_not_success(setup, stdout):
    client, _, result, root = setup
    (root / 'cover.png').write_bytes(b'image')
    result.stdout = stdout
    data = client.post('/api/publish/xiaohongshu', json={
        'title': '测试', 'media': ['cover.png'],
    }).json()
    assert data['ok'] is False
    assert data['published'] is False
    assert data['outcome'] == 'failed'
    assert not (root / '_schedule.json').exists()


def test_nonzero_process_cannot_claim_draft_success(setup):
    client, _, result, root = setup
    (root / 'cover.png').write_bytes(b'image')
    result.returncode = 1
    result.stdout = '{"outcome":"draft_saved","published":false}'
    data = client.post('/api/publish/xiaohongshu', json={
        'title': '测试', 'media': ['cover.png'],
    }).json()
    assert data['ok'] is False
    assert data['published'] is False


@pytest.mark.parametrize('stderr,expected_code,expected_message', [
    ('上传图片：/private/local/path.png\nERROR: 未找到唯一可见且可用的草稿保存按钮',
     'DRAFT_BUTTON_NOT_FOUND', '草稿保存按钮'),
    ('ERROR: 检测到多个可见草稿保存按钮，无法唯一确认；未执行保存',
     'DRAFT_BUTTON_NOT_FOUND', '草稿保存按钮'),
    ('ERROR: 草稿保存结果未确认：未出现明确的平台草稿保存成功提示',
     'DRAFT_SAVE_UNCONFIRMED', '保存结果尚未确认'),
    ('ERROR: 未登录，请先login', 'DRAFT_SAVE_FAILED', '登录状态'),
    ('Traceback /private/local/source.py: an unexpected failure',
     'DRAFT_SAVE_FAILED', '保存草稿失败'),
])
def test_xhs_reports_safe_specific_failure_without_internal_details(setup, stderr, expected_code, expected_message):
    client, _, result, root = setup
    (root / 'cover.png').write_bytes(b'image')
    result.returncode, result.stderr = 1, stderr
    data = client.post('/api/publish/xiaohongshu', json={
        'title': '测试', 'media': ['cover.png'],
    }).json()
    assert data['ok'] is False and data['published'] is False
    assert data['errorCode'] == expected_code
    assert expected_message in data['message']
    assert '/private' not in data['message'] and 'Traceback' not in data['message']
    assert not (root / '_schedule.json').exists()


def test_exit_zero_without_receipt_reports_unknown_draft_outcome(setup):
    client, _, result, root = setup
    (root / 'cover.png').write_bytes(b'image')
    result.stdout = 'done without receipt'
    data = client.post('/api/publish/xiaohongshu', json={'title': '测试', 'media': ['cover.png']}).json()
    assert data['errorCode'] == 'DRAFT_SAVE_UNCONFIRMED'
    assert '保存结果尚未确认' in data['message']

