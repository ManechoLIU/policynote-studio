"""离线核对只读草稿 CLI。不启动浏览器、不访问网络。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xhs_draft_readback as rb  # noqa: E402


NOTE_URL = "https://creator.xiaohongshu.com/new/note-manager"


class Boom(Exception):
    def __init__(self):
        super().__init__("https://creator.xiaohongshu.com/login?secret=token-value")


def _card(title, text, href="", note_id="", status_markers=None):
    return {"title": title, "text": text, "href": href, "noteId": note_id,
            "statusMarkers": status_markers or []}


class FakePage:
    def __init__(self, cards=None, url=NOTE_URL, body="", goto_error=None):
        self.cards = cards or []
        self.url = url
        self.body = body
        self.goto_error = goto_error
        self.gotos = []
        self.forbidden = []

    def goto(self, url, timeout=None, wait_until=None):
        self.gotos.append({"url": url, "timeout": timeout, "wait_until": wait_until})
        if self.goto_error:
            raise self.goto_error
        if "login" in url:
            self.url = url

    def evaluate(self, script):
        assert "cookie" not in script.lower()
        assert "localStorage" not in script
        return {"cards": self.cards, "bodyHint": self.body}

    def click(self, *a, **k):
        self.forbidden.append("click")
        raise AssertionError("click")

    def fill(self, *a, **k):
        self.forbidden.append("fill")
        raise AssertionError("fill")

    def type(self, *a, **k):
        self.forbidden.append("type")
        raise AssertionError("type")

    def screenshot(self, *a, **k):
        raise AssertionError("screenshot")


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.pages = [page]
        self.closed = False
        self.cookies_read = False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True

    def cookies(self):
        self.cookies_read = True
        raise AssertionError("cookies")

    def storage_state(self, **k):
        raise AssertionError("storage")


class FakeLock:
    def __init__(self):
        self.acquired = None
        self.released = False
        self.fail = False

    def acquire(self, timeout_s):
        self.acquired = timeout_s
        if self.fail:
            raise TimeoutError("busy")

    def release(self):
        self.released = True


@pytest.fixture
def harness(monkeypatch):
    lock = FakeLock()
    page = FakePage()
    ctx = FakeContext(page)
    calls = {}

    def launch(p, headed, base, proxy, login_assets=True, timeout_ms=None, retry_target_closed=True):
        calls["launch"] = dict(
            headed=headed, base=base, proxy=proxy, login_assets=login_assets,
            timeout_ms=timeout_ms, retry_target_closed=retry_target_closed,
        )
        return ctx

    monkeypatch.setattr(rb, "_ProfileLock", lambda profile: lock)
    monkeypatch.setattr(rb, "_profile_dir", lambda base: Path("/tmp/unused-xhs-profile"))
    monkeypatch.setattr(rb, "_launch", launch)
    class _PW:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(rb, "sync_playwright", _PW)
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(rb, 'time', SimpleNamespace(
        monotonic=lambda: clock.now, sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds)))
    return SimpleNamespace(lock=lock, page=page, ctx=ctx, calls=calls)


def _run(argv):
    with pytest.raises(SystemExit) as ei:
        rb.main(argv)
    return ei.value.code


def _json_of(capsys):
    out = capsys.readouterr().out
    assert len(out.encode()) < 2 * 1024 * 1024
    return json.loads(out)


def test_exact_title_strips_query_and_marks_draft(harness, capsys):
    harness.page.cards = [
        _card(
            "晚霞草稿",
            "草稿 2024-05-01 19:57 编辑",
            "https://creator.xiaohongshu.com/publish/publish?noteId=abcDEF12&xsec_token=SECRET",
            "abcDEF12",
            status_markers=['草稿'],
        ),
        _card("另一篇", "已发布 2024-01-01", "https://www.xiaohongshu.com/explore/zzzzzzzz?xsec_token=NO"),
    ]
    code = _run(["--title", "晚霞草稿", "--json"])
    payload = _json_of(capsys)
    assert code == 0
    assert payload["absence_conclusive"] is False
    assert payload["visible_page_examined"] is True
    assert payload["error_type"] is None
    assert len(payload["matches"]) == 1
    match = payload["matches"][0]
    assert match == {
        "title": "晚霞草稿",
        "draft_status": "draft",
        "published_at_text": "2024-05-01 19:57",
        "href": "https://creator.xiaohongshu.com/publish/publish",
        "note_id": "abcDEF12",
    }
    assert "SECRET" not in capsys.readouterr().out if False else "SECRET" not in json.dumps(payload)
    assert "另一篇" not in json.dumps(payload)
    assert harness.page.gotos[0]["url"] == NOTE_URL
    assert harness.page.gotos[0]["timeout"] <= 8000
    assert harness.calls["launch"] == {
        "headed": False, "base": None, "proxy": None, "login_assets": False,
        "timeout_ms": 8000, "retry_target_closed": False,
    }
    assert harness.lock.acquired == 1
    assert harness.ctx.closed is True
    assert harness.lock.released is True
    assert harness.page.forbidden == []
    assert harness.ctx.cookies_read is False


def test_other_title_is_not_a_body_match(harness, capsys):
    harness.page.cards = [_card("封面", "正文里出现目标标题但标题不同")]
    code = _run(["--title", "目标标题", "--json"])
    payload = _json_of(capsys)
    assert code == 0
    assert payload["matches"] == []
    assert payload["page_state"] == "cards"
    assert payload["absence_conclusive"] is False
    assert payload["visible_page_examined"] is True
    assert "count" not in payload


def test_published_ambiguity_and_edit_href_are_unknown(harness, capsys):
    harness.page.cards = [
        _card("歧义", "草稿 已发布", "https://creator.xiaohongshu.com/publish/publish?xsec_token=T", status_markers=['草稿', '已发布']),
        _card("仅链接", "编辑", "https://www.xiaohongshu.com/explore/noteid01"),
    ]
    _run(["--title", "歧义", "--json"])
    first = _json_of(capsys)
    assert first["matches"][0]["draft_status"] == "unknown"
    assert first["matches"][0]["href"] == "https://creator.xiaohongshu.com/publish/publish"
    assert "xsec_token" not in json.dumps(first)
    assert first["matches"][0]["note_id"] is None

    harness.page.cards = [_card("仅链接", "编辑", "https://www.xiaohongshu.com/explore/noteid01")]
    _run(["--title", "仅链接", "--json"])
    second = json.loads(capsys.readouterr().out)
    assert second["matches"][0]["draft_status"] == "unknown"
    assert second["matches"][0]["note_id"] == "noteid01"
    assert second["matches"][0]["published_at_text"] is None


def test_login_wall(harness, capsys):
    harness.page.url = "https://creator.xiaohongshu.com/login"
    harness.page.body = "扫码登录"
    code = _run(["--title", "晚霞草稿", "--json"])
    payload = _json_of(capsys)
    assert code == 0
    assert payload["error_type"] == "auth_required"
    assert payload["page_state"] == "login"
    assert payload["matches"] == []
    assert payload["visible_page_examined"] is True
    assert payload["absence_conclusive"] is False


def test_empty_state_is_not_conclusive(harness, capsys):
    harness.page.body = "暂无笔记"
    code = _run(["--title", "晚霞草稿", "--json"])
    payload = _json_of(capsys)
    assert code == 0
    assert payload["page_state"] == "empty"
    assert payload["matches"] == []
    assert payload["absence_conclusive"] is False
    assert payload["visible_page_examined"] is True


def test_loading_is_distinct_from_empty(harness, capsys):
    harness.page.body = "加载中"
    _run(["--title", "晚霞草稿", "--json"])
    payload = _json_of(capsys)
    assert payload["page_state"] == "loading"
    assert payload["absence_conclusive"] is False
    assert payload["matches"] == []


def test_navigation_error_hides_raw_and_still_cleans_up(harness, capsys):
    harness.page.goto_error = Boom()
    code = _run(["--title", "晚霞草稿", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == 0
    assert payload["error_type"] == "navigation_failed"
    assert payload["visible_page_examined"] is False
    assert payload["absence_conclusive"] is False
    assert "token-value" not in out
    assert "login?" not in out
    assert harness.ctx.closed is True
    assert harness.lock.released is True


def test_lock_timeout_does_not_launch(harness, capsys):
    harness.lock.fail = True
    _run(["--title", "晚霞草稿", "--json"])
    payload = _json_of(capsys)
    assert payload["error_type"] == "lock_timeout"
    assert "launch" not in harness.calls
    assert harness.lock.released is True
    assert payload["visible_page_examined"] is False


def test_rejects_control_and_empty_title(capsys):
    assert _run(["--title", "", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error_type"] == "invalid_title"
    assert _run(["--title", "a\nb", "--json"]) == 2
    assert _run(["--title", "x" * 101, "--json"]) == 2


def test_unpublished_marker(harness, capsys):
    harness.page.cards = [_card("未发", "未发布", "https://creator.xiaohongshu.com/publish/publish", status_markers=['未发布'])]
    _run(["--title", "未发", "--json"])
    payload = _json_of(capsys)
    assert payload["matches"][0]["draft_status"] == "draft"
    assert payload["matches"][0]["note_id"] is None


@pytest.mark.parametrize('title,text,markers', [
    ('讨论草稿', '讨论草稿 编辑', []),
    ('目标', '正文里提到未发布', []),
    ('目标', '编辑', ['草稿', '已发布']),
])
def test_title_or_body_is_not_a_visible_draft_status(title, text, markers, harness):
    harness.page.cards = [_card(title, text, status_markers=markers)]
    result = rb.readback(title)
    assert result['matches'][0]['draft_status'] == 'unknown'


def test_waits_for_spa_cards_instead_of_returning_first_unknown(harness):
    snapshots = iter([
        {'cards': [], 'bodyHint': '加载中'},
        {'cards': [_card('目标', '编辑', status_markers=['草稿'])], 'bodyHint': ''},
    ])
    harness.page.evaluate = lambda script: next(snapshots)
    result = rb.readback('目标')
    assert result['page_state'] == 'cards'
    assert result['matches'][0]['draft_status'] == 'draft'


def test_zero_cards_without_terminal_condition_times_out_inconclusively(harness):
    result = rb.readback('目标')
    assert result['error_type'] == 'timeout'
    assert result['page_state'] == 'unknown'
    assert result['absence_conclusive'] is False


@pytest.mark.parametrize('navigation_timeout', [False, True])
def test_context_closes_before_driver_and_cleanup_keeps_result(navigation_timeout, harness, monkeypatch):
    events = []
    harness.page.cards = [_card('目标', '编辑', status_markers=['草稿'])]
    if navigation_timeout:
        harness.page.goto_error = TimeoutError('secret-navigation')

    def close():
        events.append('context_close')
        raise Boom()

    class Manager:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *args):
            events.append('driver_stop')
            raise Boom()

    harness.ctx.close = close
    monkeypatch.setattr(rb, 'sync_playwright', Manager)
    original_release = harness.lock.release
    def release():
        events.append('lock_release')
        original_release()
    harness.lock.release = release
    result = rb.readback('目标')
    assert events == ['context_close', 'driver_stop', 'lock_release']
    assert result['error_type'] == ('timeout' if navigation_timeout else None)
    if not navigation_timeout:
        assert result['matches'][0]['draft_status'] == 'draft'


def test_unexpected_origin_is_not_examined(harness):
    harness.page.url = 'https://untrusted.example/new/note-manager'
    harness.page.evaluate = lambda script: pytest.fail('must not read another origin')
    result = rb.readback('目标')
    assert result['error_type'] == 'unexpected_location'
    assert result['visible_page_examined'] is False
    assert result['absence_conclusive'] is False


def test_card_text_does_not_become_a_login_wall(harness):
    harness.page.body = '手机号登录说明'
    harness.page.cards = [_card('目标', '编辑', status_markers=['草稿'])]
    assert rb.readback('目标')['page_state'] == 'cards'


def test_empty_words_in_page_instructions_are_not_an_empty_result(harness):
    harness.page.body = '页面使用说明：若暂无笔记，请查看帮助'
    result = rb.readback('目标')
    assert result['page_state'] == 'unknown'
    assert result['error_type'] == 'timeout'
    assert result['absence_conclusive'] is False


def test_actual_dom_reader_excludes_title_and_hidden_status_markers():
    import subprocess
    script = r'''
const assert = require('node:assert/strict');
const read = eval('(' + SOURCE + ')');
globalThis.getComputedStyle = node => ({display:'block', visibility:'visible', opacity:'1', ...node.style});
const make = (text, style={}) => ({textContent:text, innerText:text, style, parentElement:null,
  getClientRects() { return [{}]; }, contains(node) { return this === node; }});
const title = make('草稿');
const hidden = make('草稿', {visibility:'hidden'});
const actual = make('已发布');
const card = make('草稿 已发布');
title.parentElement = hidden.parentElement = actual.parentElement = card;
card.querySelector = selector => selector === '.note-card__title' ? title : null;
card.getAttribute = () => null;
card.querySelectorAll = () => [title, hidden, actual];
globalThis.document = {body:{innerText:''}, querySelectorAll() { return [card]; }};
assert.deepEqual(read().cards[0].statusMarkers, ['已发布']);
actual.innerText = actual.textContent = '草稿';
assert.deepEqual(read().cards[0].statusMarkers, ['草稿']);
console.log('visible status markers verified');
'''.replace('SOURCE', json.dumps(rb._READ_JS), 1)
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'visible status markers verified'
