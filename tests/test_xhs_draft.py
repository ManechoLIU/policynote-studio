"""Draft-only Xiaohongshu paths: no live browser or platform requests."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'shared' / 'scripts'))
import xhs_publish as xhs


class Locator:
    def __init__(self, nodes):
        self.nodes = nodes

    def count(self):
        return len(self.nodes)

    def nth(self, index):
        return self.nodes[index]

    def element_handles(self):
        return self.nodes[:]

    def text_content(self, **kwargs):
        assert len(self.nodes) == 1
        return self.nodes[0].text_content()


class Element:
    def __init__(self, text, page=None, visible=True, enabled=True):
        self.text, self.page, self.visible, self.enabled = text, page, visible, enabled
        self.clicks = 0

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def inner_text(self):
        return self.text

    def text_content(self):
        return self.text

    def get_attribute(self, key):
        return None

    def query_selector(self, selector):
        return None

    def evaluate(self, expression, other):
        if expression == '(node, selector) => node.matches(selector)':
            return False
        if expression == xhs._DRAFT_NOTICE_DOCUMENT:
            return 'same'
        assert expression == '(node, previous) => node === previous'
        return self is other

    def click(self, **kwargs):
        assert not kwargs.get('force')
        self.clicks += 1
        if self.page:
            self.page.clicked = True


class Page:
    url = xhs.PUBLISH_URL

    def __init__(self, labels=('存草稿',), success='草稿保存成功', preexisting=False):
        self.clicked = False
        self.buttons = [Element(label, self) for label in labels]
        self.success, self.preexisting = success, preexisting
        self.uploads = []
        self.waits = []
        self.mouse = SimpleNamespace(click=lambda *a: pytest.fail('No coordinate clicks'))

    def get_by_role(self, role, *, name):
        assert role == 'button'
        return Locator([b for b in self.buttons if name.fullmatch(b.text)])

    def evaluate(self, expression, marker):
        assert expression in (xhs._DRAFT_DOCUMENT_MARK, xhs._DRAFT_DOCUMENT_CURRENT, xhs._DRAFT_DOCUMENT_STATUS)
        assert marker['key'].startswith('_xhs_draft_receipt_')
        return 'same' if expression == xhs._DRAFT_DOCUMENT_STATUS else True

    def locator(self, selector):
        if selector == 'body':
            return Locator([Element(self.success if self.preexisting else '')])
        if selector == "div.creator-publish-popover, [role='dialog']":
            return Locator([])
        if (self.clicked or self.preexisting) and self.success:
            return Locator([Element(self.success)])
        return Locator([])

    def query_selector(self, selector):
        return object()

    def set_input_files(self, selector, path):
        self.uploads.append(path)

    def wait_for_selector(self, selector, **kwargs):
        self.waits.append(selector)
        return object()

    def wait_for_timeout(self, milliseconds):
        pass

    def goto(self, *a, **kwargs):
        pass

    def set_default_timeout(self, timeout):
        pass


class UploadStatePage:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.current = []
        self.clock, self.inputs, self.waits, self.reads = 0, [], [], 0

    def set_input_files(self, selector, path):
        self.inputs.append(path)

    def query_selector_all(self, selector):
        # The old waiter sees a local preview even when its upload is failed.
        return [Element('上传失败')] * len(self.inputs)

    def evaluate(self, expression):
        self.reads += 1
        self.current = next(self.snapshots, self.current)
        return self.current

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        self.clock += milliseconds / 1000


def upload_slot(**changes):
    state = dict(visible=True, loaded=True, ready=True, pending=False, failed=False, retry=False)
    state.update(changes)
    return state


def mock_upload_clock(page, monkeypatch):
    monkeypatch.setattr(xhs, 'time', SimpleNamespace(monotonic=lambda: page.clock, time=lambda: page.clock))


def test_image_upload_waits_for_pending_slot_to_become_ready(monkeypatch):
    pending = upload_slot()
    pending.update(pending=True, ready=False)
    page = UploadStatePage([[pending], [upload_slot()]])
    mock_upload_clock(page, monkeypatch)
    xhs._upload_images(page, ['fixture.jpg'])
    assert page.reads == 2 and page.waits == [500]
    assert page.inputs == ['fixture.jpg']


def test_image_upload_ready_read_after_deadline_is_not_accepted(monkeypatch, capsys):
    page = UploadStatePage([[upload_slot()]])
    mock_upload_clock(page, monkeypatch)
    original_evaluate = page.evaluate
    def late_evaluate(expression):
        page.clock = 61
        return original_evaluate(expression)
    page.evaluate = late_evaluate
    with pytest.raises(SystemExit):
        xhs._upload_images(page, ['fixture.jpg'])
    assert page.inputs == ['fixture.jpg'] and page.reads == 1
    assert 'XHS_IMAGE_UPLOAD_UNCONFIRMED' in capsys.readouterr().err


@pytest.mark.parametrize('bad_state', [
    {'failed': True}, {'retry': True}, {'pending': True}, {'ready': False},
    {'loaded': False}, {'visible': False},
])
def test_image_upload_local_preview_cannot_override_failed_or_unready_slot(bad_state, monkeypatch, capsys):
    state = upload_slot()
    state.update(bad_state)
    page = UploadStatePage([[state]])
    mock_upload_clock(page, monkeypatch)
    with pytest.raises(SystemExit):
        xhs._upload_images(page, ['fixture.jpg'])
    assert page.inputs == ['fixture.jpg']
    if state['failed'] or state['retry']:
        assert page.waits == []
        assert 'XHS_IMAGE_UPLOAD_FAILED' in capsys.readouterr().err


def test_image_upload_requires_every_image_ready_and_rechecks_prior_slots(monkeypatch):
    pending = upload_slot()
    pending.update(pending=True, ready=False)
    page = UploadStatePage([[upload_slot()], [upload_slot(), pending], [upload_slot(), upload_slot()]])
    mock_upload_clock(page, monkeypatch)
    xhs._upload_images(page, ['one.jpg', 'two.jpg'])
    assert page.inputs == ['one.jpg', 'two.jpg']
    assert page.reads == 3 and page.waits == [500]


def test_image_upload_prior_image_failure_stops_before_next_upload(monkeypatch):
    failed = upload_slot()
    failed['failed'] = True
    page = UploadStatePage([[upload_slot()], [failed, upload_slot()]])
    mock_upload_clock(page, monkeypatch)
    with pytest.raises(SystemExit):
        xhs._upload_images(page, ['one.jpg', 'two.jpg', 'three.jpg'])
    assert page.inputs == ['one.jpg', 'two.jpg']


def test_image_upload_failure_prevents_editor_fill_and_save(tmp_path, monkeypatch, capsys):
    from tests.test_xhs_auth_diagnostics import playwright_double
    forbid_public(monkeypatch)
    page = Page()
    page.evaluate = lambda *args: [upload_slot(failed=True)]
    playwright_double(monkeypatch, SimpleNamespace(pages=[page], close=lambda: None))
    monkeypatch.setattr(xhs.content_guard, 'guard_or_die', lambda *a, **kw: None)
    monkeypatch.setattr(xhs, '_click_publish_tab', lambda *a: None)
    monkeypatch.setattr(xhs, '_fill_and_save_draft', lambda *a, **kw: pytest.fail('upload failure reached save'))
    media = tmp_path / 'image.jpg'
    media.write_bytes(b'fixture')
    args = SimpleNamespace(title='标题', content='secret-body', tags='', images=str(media), exec=True,
                           profile_base=None, proxy=None, no_proxy=True, headed=False, keep_open=False)
    with pytest.raises(SystemExit):
        xhs._publish(args, 'image')
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.out.splitlines()[-1])['failureDiagnostic']
    assert diagnostic['stage'] == 'upload' and diagnostic['action_sent'] is False
    assert diagnostic['published'] is False and not page.clicked
    assert 'secret-body' not in captured.out + captured.err
    assert 'XHS_IMAGE_UPLOAD_FAILED' in captured.err


def test_image_upload_actual_dom_state_function_guards():
    import subprocess
    script = r'''
const assert = require('node:assert/strict');
const inspect = FUNCTION;
const node = (values={}) => ({isConnected:true, getClientRects(){return this.hidden ? [] : [1]},
    getAttribute(){return null}, ...values});
global.getComputedStyle = el => ({visibility:el.hidden ? 'hidden' : 'visible', display:'block'});
function snapshot(options={}) {
    const image = node({complete:true,naturalWidth:640,naturalHeight:480,...options.image});
    const control = node({disabled:false,...options.control});
    const slot = node({innerText:options.text || '', querySelector:s=> options.pending ? {} : null,
        querySelectorAll:s=>s==='img.img.preview' ? [image] : options.noControl ? [] : [control]});
    global.document = {querySelectorAll:s=>{assert.equal(s,'.img-preview-area .pr');return [slot]}};
    return inspect()[0];
}
assert.deepEqual(snapshot(),{visible:true,loaded:true,ready:true,pending:false,failed:false,retry:false});
assert.equal(snapshot({pending:true}).pending,true);
assert.equal(snapshot({noControl:true}).ready,false);
assert.equal(snapshot({control:{hidden:true}}).ready,false);
assert.equal(snapshot({control:{disabled:true}}).ready,false);
assert.equal(snapshot({image:{complete:false}}).loaded,false);
assert.equal(snapshot({image:{naturalWidth:0}}).loaded,false);
assert.equal(snapshot({image:{naturalHeight:0}}).loaded,false);
assert.equal(snapshot({text:'上传失败'}).failed,true);
assert.equal(snapshot({text:'重试'}).retry,true);
'''.replace('FUNCTION', xhs._IMAGE_UPLOAD_STATES)
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


class DraftDialogPage(Page):
    def __init__(self, prompt='是否保存草稿并离开？', confirm_labels=('离开',), receipt=True,
                 disabled=False, click_error=False, preexisting_dialog=False):
        super().__init__()
        self.confirmed, self.receipt = False, receipt
        self.preexisting_dialog = preexisting_dialog
        self.confirm_buttons = [Element(label, enabled=not disabled) for label in confirm_labels]
        page = self
        for button in self.confirm_buttons:
            def click(*, timeout, target=button):
                target.clicks += 1
                page.confirmed = True
                if click_error:
                    raise RuntimeError('secret-confirmation-transport')
            button.click = click

        class Dialog(Element):
            def inner_text(self):
                return prompt + ' ' + ' '.join(button.inner_text() for button in self.query_selector_all('button,[role="button"]'))

            def query_selector_all(self, selector):
                assert selector == 'button,[role="button"]'
                return [Element('取消'), *page.confirm_buttons]

        self.dialog = Dialog('')

    def locator(self, selector):
        if selector == "div.creator-publish-popover, [role='dialog']":
            return Locator([self.dialog] if (self.clicked or self.preexisting_dialog) and not self.confirmed else [])
        if selector == xhs._DRAFT_NOTICES and not (self.confirmed and self.receipt):
            return Locator([])
        return super().locator(selector)


@pytest.mark.parametrize('prompt,label', [
    ('是否保存草稿并离开？', '离开'),
    ('是否暂存并离开？', '确认离开'),
    ('是否保存草稿?', '保存草稿'),
])
def test_draft_confirmation_then_independent_receipt(prompt, label, monkeypatch):
    forbid_public(monkeypatch)
    page = DraftDialogPage(prompt=prompt, confirm_labels=(label,))
    xhs._save_draft(page, timeout_s=0.1)
    assert page.buttons[0].clicks == 1
    assert page.confirm_buttons[0].clicks == 1


@pytest.mark.parametrize('options', [
    {'prompt': '未保存的内容将丢失，是否离开？'},
    {'prompt': '是否不保存草稿并离开？'},
    {'prompt': '是否放弃草稿并离开？'},
    {'prompt': '是否保存草稿并离开？请公开发布'},
    {'prompt': '是否确认操作？', 'confirm_labels': ('确认',)},
    {'prompt': '是否公开发布？', 'confirm_labels': ('确定',)},
    {'confirm_labels': ('离开', '保存并离开')},
    {'disabled': True},
])
def test_unsafe_ambiguous_or_disabled_confirmation_is_not_clicked(options, monkeypatch):
    forbid_public(monkeypatch)
    page = DraftDialogPage(**options)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.buttons[0].clicks == 1
    assert all(button.clicks == 0 for button in page.confirm_buttons)


@pytest.mark.parametrize('click_error', [False, True])
def test_confirmation_without_receipt_is_unknown_and_never_repeated(click_error, monkeypatch, capsys):
    forbid_public(monkeypatch)
    page = DraftDialogPage(receipt=False, click_error=click_error)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0.01)
    assert page.buttons[0].clicks == 1
    assert page.confirm_buttons[0].clicks == 1
    captured = capsys.readouterr()
    assert '草稿保存结果未确认' in captured.err
    assert 'secret-confirmation-transport' not in captured.err
    assert 'draft_saved' not in captured.out


def test_preexisting_confirmation_blocks_initial_draft_action(monkeypatch):
    forbid_public(monkeypatch)
    page = DraftDialogPage(preexisting_dialog=True)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.buttons[0].clicks == 0
    assert all(button.clicks == 0 for button in page.confirm_buttons)


class PictureGuidePage(Page):
    def __init__(self, *, title='图片可以编辑啦，快来试试吧', labels=('我知道了', ''),
                 description='支持裁剪图片大小、选择模版或滤镜、添加贴纸和文字', close_aria='关闭新功能引导',
                 ack_class='feature-guide__btn', close_class='feature-guide__close',
                 classes='feature-guide feature-guide--with-arrow', guides=1,
                 enabled=True, hides=True, live=True, extra_dialog=False):
        super().__init__()
        self.guide_hidden = False
        class GuideControl(Element):
            def get_attribute(self, key):
                if key == 'class':
                    return ack_class if self.text == '我知道了' else close_class
                if key == 'aria-label' and self.text == '':
                    return close_aria
                return None
        self.guide_buttons = [GuideControl(label, enabled=enabled) for label in labels]
        page = self
        for button in self.guide_buttons:
            def click(*, timeout, target=button):
                target.clicks += 1
                page.guide_hidden = hides
            button.click = click

        class Guide(Element):
            def get_attribute(self, key):
                return {'class': classes, 'role': 'dialog'}.get(key)

            def inner_text(self):
                return title + '\n' + description.replace('和文字', '\n和文字') + '\n' + ' '.join(labels)

            def query_selector_all(self, selector):
                assert selector == 'button,[role="button"]'
                return page.guide_buttons

            def evaluate(self, expression, other=None):
                if expression == '(node, previous) => node === previous':
                    return self is other
                assert 'ownerDocument === document' in expression
                return live

            def wait_for_element_state(self, state, *, timeout):
                assert state == 'hidden' and timeout == 5000
                if not page.guide_hidden:
                    raise RuntimeError('guide did not close')

        self.guide = Guide('')
        self.guides = [self.guide] * guides
        if extra_dialog:
            self.guides.append(Element('unrelated dialog'))

    def locator(self, selector):
        if selector == xhs._DRAFT_DIALOGS:
            return Locator([] if self.guide_hidden else self.guides)
        return super().locator(selector)


def test_exact_picture_guide_is_dismissed_once_before_strict_draft_receipt(monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage()
    xhs._save_draft(page, timeout_s=0)
    assert page.guide_buttons[0].clicks == 1
    assert page.guide_buttons[1].clicks == 0
    assert page.guide_hidden
    assert page.buttons[0].clicks == 1


@pytest.mark.parametrize('options', [
    {'title': '图片可以编辑啦，快来试试吧，发布吧'},
    {'description': '不同的引导说明'},
    {'description': ''},
    {'labels': ('知道了',)},
    {'labels': ('我知道了', '发布')},
    {'labels': ('我知道了', '我知道了')},
    {'labels': ('我知道了', '', '')},
    {'close_aria': '发布'},
    {'close_class': 'other-close'},
    {'ack_class': 'other-action'},
    {'classes': 'feature-guide'},
    {'guides': 2},
    {'enabled': False},
    {'live': False},
    {'extra_dialog': True},
])
def test_untrusted_picture_guide_keeps_original_baseline_veto(options, monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage(**options)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert all(button.clicks == 0 for button in page.guide_buttons)
    assert page.buttons[0].clicks == 0


def test_picture_guide_must_hide_before_any_draft_action(monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage(hides=False)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.guide_buttons[0].clicks == 1
    assert page.buttons[0].clicks == 0


def test_picture_guide_dismissal_is_not_a_save_receipt(monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage()
    page.success = ''
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.guide_buttons[0].clicks == 1
    assert page.buttons[0].clicks == 1


@pytest.mark.parametrize('url', [
    'https://www.xiaohongshu.com/publish/publish',
    'http://creator.xiaohongshu.com/publish/publish',
    'https://creator.xiaohongshu.com/other',
])
def test_picture_guide_requires_official_publish_scope(url, monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage()
    page.url = url
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.guide_buttons[0].clicks == page.buttons[0].clicks == 0


@pytest.mark.parametrize('change', ['dialog', 'button'])
def test_picture_guide_changed_identity_is_not_clicked(change, monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage()
    if change == 'dialog':
        dialogs = iter([[page.guide], [Element('replacement')]])
        monkeypatch.setattr(xhs, '_visible_draft_dialogs', lambda page: next(dialogs))
    else:
        page.guide_buttons[0].evaluate = lambda *args: False
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.guide_buttons[0].clicks == page.buttons[0].clicks == 0


def test_picture_guide_click_error_is_never_retried(monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage()
    def fail_click(**kwargs):
        page.guide_buttons[0].clicks += 1
        raise RuntimeError('uncertain guide dismissal')
    page.guide_buttons[0].click = fail_click
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.guide_buttons[0].clicks == 1
    assert page.buttons[0].clicks == 0


@pytest.mark.parametrize('close_aria', [None, '关闭', '关闭新功能引导'])
def test_picture_guide_ignores_only_known_icon_close_control(close_aria, monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage(close_aria=close_aria)
    xhs._save_draft(page, timeout_s=0)
    assert [button.clicks for button in page.guide_buttons] == [1, 0]
    assert page.buttons[0].clicks == 1


def test_picture_guide_conflicting_close_labels_are_not_treated_as_empty(monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage()
    close = page.guide_buttons[1]
    close.text = '发布'
    close.get_attribute = lambda key: {'aria-label': '关闭', 'class': 'feature-guide__close'}.get(key)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert all(button.clicks == 0 for button in page.guide_buttons)
    assert page.buttons[0].clicks == 0


def test_picture_guide_real_close_aria_and_ack_classes(monkeypatch):
    forbid_public(monkeypatch)
    page = PictureGuidePage(labels=('', '我知道了'))
    xhs._save_draft(page, timeout_s=0)
    assert [button.clicks for button in page.guide_buttons] == [0, 1]
    assert page.buttons[0].clicks == 1


def forbid_public(monkeypatch):
    for name in ('_wait_publish_clickable', '_confirm_publish_dialog', '_wait_publish_success', '_fill_and_submit'):
        monkeypatch.setattr(xhs, name, lambda *a, _name=name, **kw: pytest.fail(f'Called public helper {_name}'))


@pytest.mark.parametrize('label', ['暂存离开', '存草稿', '保存草稿'])
def test_unique_explicit_draft_button_gets_clicked_and_confirmed(label, monkeypatch):
    forbid_public(monkeypatch)
    page = Page((label, '发布', '确定'))
    xhs._save_draft(page, timeout_s=0)
    assert page.buttons[0].clicks == 1
    assert all(b.clicks == 0 for b in page.buttons[1:])


def test_receipt_timeout_diagnostic_redacts_urls_and_nonallowlisted_labels(monkeypatch, capsys):
    forbid_public(monkeypatch)

    class DiagnosticPage(Page):
        url = 'https://creator.xiaohongshu.com/publish/publish?token=secret-query#secret-hash'

        def get_by_role(self, role, *, name):
            if role == 'dialog':
                return Locator([Element(label) for label in ('是否保存草稿', 'secret-dialog') if name.fullmatch(label)])
            if name is xhs._DRAFT_BUTTON_NAME:
                return super().get_by_role(role, name=name)
            return Locator([Element(label) for label in ('存草稿', 'secret-button') if name.fullmatch(label)])

        def locator(self, selector):
            if selector == xhs._DRAFT_NOTICES and self.clicked:
                return Locator([Element('草稿保存失败'), Element('secret-notice')])
            return super().locator(selector)

    page = DiagnosticPage(success='')
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    captured = capsys.readouterr()
    rows = [json.loads(line.split(' ', 1)[1]) for line in captured.err.splitlines()
            if line.startswith('XHS_DRAFT_DIAGNOSTIC ')]
    diagnostic = next(row for row in rows if row['stage'] == 'receipt_timeout')
    assert diagnostic['action_sent'] is True and diagnostic['notice_confirmed'] is False
    assert diagnostic['url'] == 'https://creator.xiaohongshu.com/publish/publish'
    assert diagnostic['buttons'] == ['存草稿']
    assert diagnostic['dialogs'] == ['是否保存草稿']
    assert diagnostic['notices'] == ['草稿保存失败']
    assert 'secret-' not in captured.err
    assert page.buttons[0].clicks == 1


@pytest.mark.parametrize('labels', [(), ('发布', '确认'), ('存草稿', '保存草稿')])
def test_missing_closed_shadow_or_ambiguous_button_fails_closed(labels):
    page = Page(labels)
    with pytest.raises(SystemExit) as error:
        xhs._save_draft(page, timeout_s=0)
    assert error.value.code != 0
    assert not page.clicked


@pytest.mark.parametrize('message', ['', '发布成功', '保存成功', '自动保存中', '草稿保存失败'])
def test_navigation_reset_or_generic_success_is_not_draft_receipt(message):
    page = Page(success=message)
    page.url = 'https://creator.xiaohongshu.com/new/home'
    with pytest.raises(SystemExit) as error:
        xhs._save_draft(page, timeout_s=0)
    assert error.value.code != 0
    assert page.clicked


def test_preexisting_success_does_not_confirm_current_save():
    page = Page(preexisting=True)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert not page.clicked


def test_hidden_button_and_disabled_button_cannot_be_clicked():
    for visible, enabled in [(False, True), (True, False)]:
        page = Page()
        page.buttons[0].visible, page.buttons[0].enabled = visible, enabled
        with pytest.raises(SystemExit):
            xhs._save_draft(page, timeout_s=0)
        assert not page.clicked


@pytest.mark.parametrize('kind,explicit_draft', [('image', True), ('video', True), ('image', False), ('video', False)])
@pytest.mark.parametrize('with_confirmation', [False, True])
def test_execution_only_saves_draft_and_never_records_publish(kind, explicit_draft, with_confirmation, tmp_path, monkeypatch, capsys):
    forbid_public(monkeypatch)
    media = tmp_path / ('media.png' if kind == 'image' else 'media.mp4')
    media.write_bytes(b'fixture')
    page = DraftDialogPage() if with_confirmation else Page()
    ctx = SimpleNamespace(pages=[page], close=Mock())
    session = SimpleNamespace(__enter__=lambda self: self, __exit__=lambda *args: None)
    class Manager:
        def __enter__(self):
            return session
        def __exit__(self, *args):
            pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=Manager, TimeoutError=TimeoutError))
    monkeypatch.setattr(xhs, '_launch', lambda *a, **kw: ctx)
    monkeypatch.setattr(xhs, '_ProfileLock', lambda *a: SimpleNamespace(acquire=lambda *a: None, release=lambda: None))
    monkeypatch.setattr(xhs, '_proxy', lambda *a: None)
    monkeypatch.setattr(xhs, '_click_publish_tab', lambda *a: None)
    monkeypatch.setattr(xhs, '_upload_images', lambda *a: None)
    monkeypatch.setattr(xhs, '_fill_editor', lambda *a: None)
    monkeypatch.setattr(xhs.content_guard, 'guard_or_die', lambda *a, **kw: None)
    monkeypatch.setitem(sys.modules, 'calendar_ops', SimpleNamespace(record_publish=lambda *a, **kw: pytest.fail('draft is not published')))
    args = SimpleNamespace(title='草稿标题', content='正文', tags='', images=str(media), video=str(media), exec=True,
                           draft=explicit_draft, profile_base=None, proxy=None, no_proxy=True, headed=False, keep_open=False)
    assert xhs._publish(args, kind) == 0
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1]) == {'outcome': 'draft_saved', 'published': False}
    assert page.clicked
    if with_confirmation:
        assert page.confirm_buttons[0].clicks == 1
    if kind == 'video':
        assert page.uploads == [str(media)]
        assert xhs.SELECTORS['title_input'] in page.waits


def test_plan_and_dry_run_describe_draft_only(tmp_path, capsys, monkeypatch):
    lines = xhs._plan_lines('video', '标题', '正文', ['/a.mp4'], [])
    assert any('草稿' in line for line in lines)
    assert not any('等发布按钮' in line or 'URL 离开' in line for line in lines)
    media = tmp_path / 'a.png'
    media.write_bytes(b'x')
    args = SimpleNamespace(title='草稿', content='', tags='', images=str(media), exec=False, draft=False)
    monkeypatch.setattr(xhs.content_guard, 'guard_or_die', lambda *a, **kw: None)
    assert xhs._publish(args, 'image') == 0
    assert '真正发布' not in capsys.readouterr().out


def test_cli_accepts_draft_flag_but_omission_is_also_draft(monkeypatch):
    monkeypatch.setattr(xhs.login_state, 'configure_stdio', lambda: None)
    for flags in ([], ['--draft']):
        captured = []
        monkeypatch.setattr(xhs, 'cmd_publish', lambda args: captured.append(args.draft) or 0)
        monkeypatch.setattr(sys, 'argv', ['xhs_publish', 'publish', *flags])
        assert xhs.main() == 0
        assert captured == [True]


# Static HTML DOM with CSS matching, not a locator that ignores its selector.
from html.parser import HTMLParser
import re


class DOMElement(Element):
    def __init__(self, tag, attrs, parent=None):
        super().__init__('')
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text_content(self):
        return ''.join(child.text_content() if isinstance(child, DOMElement) else child for child in self.children)

    def inner_text(self):
        return self.text_content()

    def is_visible(self):
        current = self
        while current:
            if 'hidden' in current.attrs or 'display:none' in current.attrs.get('style', '').replace(' ', ''):
                return False
            current = current.parent
        return True

    def get_attribute(self, key):
        return self.attrs.get(key)

    def query_selector(self, selector):
        def descendants(node):
            for child in node.children:
                if isinstance(child, DOMElement):
                    yield child
                    yield from descendants(child)
        return next((node for node in descendants(self) if any(simple_css_matches(node, part) for part in selector.split(','))), None)

    def evaluate(self, expression, other):
        if expression == '(node, selector) => node.matches(selector)':
            return any(simple_css_matches(self, part) for part in other.split(','))
        if expression == xhs._DRAFT_NOTICE_DOCUMENT:
            return 'same'
        assert expression == '(node, previous) => node === previous'
        return self is other


class HTMLFixture(HTMLParser):
    VOID = {'input', 'br', 'img', 'meta', 'link', 'hr'}

    def __init__(self, html, parent=None):
        super().__init__(convert_charrefs=True)
        self.root = parent or DOMElement('body', {})
        self.current = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = DOMElement(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                break
            node = node.parent

    def handle_data(self, value):
        self.current.children.append(value)

    def nodes(self):
        def walk(node):
            yield node
            for child in node.children:
                if isinstance(child, DOMElement):
                    yield from walk(child)
        return list(walk(self.root))


def simple_css_matches(node, selector):
    selector = selector.strip()
    if '>' in selector:
        parent, child = selector.split('>', 1)
        return bool(node.parent and simple_css_matches(node.parent, parent) and simple_css_matches(node, child))
    if re.fullmatch(r'\[[\w-]+\]', selector):
        return selector[1:-1] in node.attrs
    if selector.startswith('.'):
        return selector[1:] in node.attrs.get('class', '').split()
    match = re.fullmatch(r'\[([\w-]+)(\*?)=[\"\']?([^\"\'\]]+)[\"\']?\]', selector)
    if match:
        key, partial, value = match.groups()
        actual = node.attrs.get(key, '')
        return value in actual if partial else value == actual
    return node.tag == selector


class DOMLocator(Locator):
    def element_handles(self):
        return self.nodes[:]

    def text_content(self, **kwargs):
        assert len(self.nodes) == 1
        return self.nodes[0].text_content()


class DOMPage(Page):
    def __init__(self, initial='', after='', mutate=None):
        super().__init__(success='')
        self.fixture = HTMLFixture(initial)
        self.after, self.mutate = after, mutate
        original_click = self.buttons[0].click
        def click(**kwargs):
            original_click(**kwargs)
            HTMLFixture(self.after, self.fixture.root)
            if self.mutate:
                self.mutate(self.fixture)
        self.buttons[0].click = click

    def locator(self, selector):
        parts = selector.split(',')
        return DOMLocator([node for node in self.fixture.nodes() if any(simple_css_matches(node, part) for part in parts)])


@pytest.mark.parametrize('fragment', [
    '<div class="message-preview">草稿保存成功</div>',
    '<div role="status">草稿保存成功</div>',
    '<section class="preview"><div class="d-message">草稿保存成功</div></section>',
    '<div contenteditable="true"><div class="d-toast">草稿保存成功</div></div>',
])
def test_post_click_editor_preview_text_cannot_be_platform_receipt(fragment):
    page = DOMPage(after=fragment)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.clicked


def test_new_root_platform_notice_is_accepted_from_static_dom():
    page = DOMPage(after='<div class="d-message"><span>草稿保存成功</span></div>')
    xhs._save_draft(page, timeout_s=0)
    assert page.clicked


@pytest.mark.parametrize('initial', [
    '<textarea>草稿保存成功</textarea>',
    '<div hidden class="d-toast">草稿保存成功</div>',
    '<div class="preview" hidden><div class="d-message">草稿保存成功</div></div>',
])
def test_existing_body_text_or_hidden_old_notice_cannot_be_copied_as_receipt(initial):
    page = DOMPage(initial=initial, after='<div class="d-toast">草稿保存成功</div>')
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert not page.clicked


def test_existing_notice_node_changed_after_click_is_not_new_save_evidence():
    def change_existing(fixture):
        node = next(n for n in fixture.nodes() if n.attrs.get('class') == 'd-message')
        node.children = ['草稿保存成功']
    page = DOMPage(initial='<div class="d-message">处理中</div>', mutate=change_existing)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.clicked


@pytest.mark.parametrize('fragment', [
    '<div class="d-message" contenteditable="true">草稿保存成功</div>',
    '<textarea class="d-message">草稿保存成功</textarea>',
    '<div class="d-toast" role="textbox">草稿保存成功</div>',
    '<div class="d-message editor-preview">草稿保存成功</div>',
    '<div class="d-message"><textarea>草稿保存成功</textarea></div>',
    '<div class="d-toast"><span contenteditable="true">草稿保存成功</span></div>',
])
def test_root_notice_that_is_editor_or_wraps_editable_content_is_not_receipt(fragment):
    page = DOMPage(after=fragment)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.clicked


# Synthetic CDP DOM trees model the protocol boundary; no Chromium is launched.
def cdp_node(node_id, name, label='', attrs=None, children=None, shadow=None):
    attributes = []
    for key, value in (attrs or {}).items():
        attributes.extend([key, value])
    nodes = list(children or [])
    if label:
        nodes.append({'nodeId': node_id + 10000, 'nodeType': 3, 'nodeName': '#text', 'nodeValue': label})
    value = {'nodeId': node_id, 'backendNodeId': node_id + 1000, 'nodeType': 1,
             'nodeName': name, 'localName': name.lower(), 'attributes': attributes, 'children': nodes}
    if shadow is not None:
        value['shadowRoots'] = [{'nodeId': 90, 'nodeType': 11, 'nodeName': '#document-fragment',
                                 'shadowRootType': 'closed', 'children': shadow}]
    return value


def cdp_tree(buttons=None, host='XHS-PUBLISH-BTN'):
    buttons = buttons if buttons is not None else [cdp_node(3, 'BUTTON', '暂存离开'), cdp_node(4, 'BUTTON', '发布')]
    return {'root': {'nodeId': 80, 'nodeType': 9, 'nodeName': '#document', 'documentURL': xhs.PUBLISH_URL,
                     'children': [cdp_node(1, 'HTML', children=[cdp_node(2, host, shadow=buttons)])]}}


class CDPSession:
    def __init__(self, page, tree=None, fresh_tree=None, states=None, click_result=None, fail_method=None, click_error=False, cleanup_error=False):
        self.page, self.tree, self.fresh_tree = page, tree or cdp_tree(), fresh_tree
        self.states, self.click_result = states or {}, click_result
        self.fail_method, self.click_error, self.cleanup_error = fail_method, click_error, cleanup_error
        self.calls, self.reads, self.detached = [], 0, False

    def send(self, method, params=None):
        self.calls.append((method, params or {}))
        if method == self.fail_method:
            raise RuntimeError('secret-protocol-detail')
        if method == 'DOM.getDocument':
            assert params == {'depth': -1, 'pierce': True}
            self.reads += 1
            return self.fresh_tree if self.reads > 1 and self.fresh_tree is not None else self.tree
        if method == 'DOM.resolveNode':
            return {'object': {'objectId': f"node-{params['backendNodeId']}"}}
        if method == 'Runtime.callFunctionOn':
            assert params['returnByValue'] is True
            args = [value.get('value', value.get('objectId')) for value in params['arguments']]
            assert args[2] == 'node-1002'  # Bind the live button to the exact scanned host object.
            backend_id = int(params['objectId'].split('-')[1])
            state = {'valid': True, 'label': args[0], 'visible': True, 'enabled': True, 'clicked': False}
            state.update(self.states.get(backend_id, {}))
            if args[1]:
                if self.click_error:
                    raise RuntimeError('unknown-action-detail')
                if self.click_result is not None:
                    state.update(self.click_result)
                elif state['valid'] and state['visible'] and state['enabled'] and state['label'] == args[0]:
                    state['clicked'] = True
                    self.page.clicked = True
            return {'result': {'value': state}}
        if method == 'Runtime.releaseObject':
            if self.cleanup_error:
                raise RuntimeError('cleanup-detail')
            return {}
        pytest.fail(f'Unexpected CDP operation {method}')

    def detach(self):
        self.detached = True


class CDPPage(Page):
    def __init__(self, **session_options):
        super().__init__(labels=())
        self.session = CDPSession(self, **session_options)
        self.context = SimpleNamespace(new_cdp_session=lambda page: self.session)


def test_closed_shadow_semantic_draft_click_then_explicit_receipt(monkeypatch):
    forbid_public(monkeypatch)
    page = CDPPage()
    xhs._save_draft(page, timeout_s=0)
    assert page.clicked and page.session.detached
    assert page.session.reads == 2  # Fresh DOM identity check immediately before action.
    clicks = [args for method, args in page.session.calls
              if method == 'Runtime.callFunctionOn' and args['arguments'][1]['value']]
    assert len(clicks) == 1
    assert clicks[0]['objectId'] == 'node-1003'
    assert clicks[0]['arguments'] == [{'value': '暂存离开'}, {'value': True}, {'objectId': 'node-1002'}]


@pytest.mark.parametrize('options', [
    {'tree': cdp_tree([cdp_node(3, 'BUTTON', '存草稿'), cdp_node(5, 'DIV', '保存草稿', attrs={'role': 'button'})])},
    {'states': {1003: {'enabled': False}}},
    {'states': {1003: {'visible': False}}},
    {'states': {1003: {'valid': False}}},
    {'fresh_tree': cdp_tree([cdp_node(3, 'BUTTON', '发布')])},
    {'fresh_tree': cdp_tree([cdp_node(7, 'BUTTON', '暂存离开')])},
    {'tree': cdp_tree(host='UNTRUSTED-EDITOR')},
    {'tree': cdp_tree([cdp_node(3, 'SPAN', '暂存离开')])},
    {'tree': cdp_tree([cdp_node(3, 'BUTTON', '发布'), cdp_node(4, 'BUTTON', '确认')])},
    {'click_result': {'clicked': False, 'valid': False}},
])
def test_closed_shadow_ambiguous_disabled_changed_or_untrusted_fails_closed(options, monkeypatch):
    forbid_public(monkeypatch)
    page = CDPPage(**options)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert not page.clicked
    assert page.session.detached


def test_closed_shadow_click_alone_is_not_success_receipt(monkeypatch, capsys):
    forbid_public(monkeypatch)
    page = CDPPage()
    page.success = ''
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.clicked
    assert 'draft_saved' not in capsys.readouterr().out


def test_closed_shadow_diagnostic_does_not_log_arbitrary_labels(capsys):
    page = CDPPage(tree=cdp_tree([cdp_node(4, 'BUTTON', 'cookie=private-token-title')]))
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    diagnostic = capsys.readouterr().err
    assert 'private-token-title' not in diagnostic
    assert 'closed_shadow' in diagnostic
    assert 'closed' in diagnostic


@pytest.mark.parametrize('method', ['DOM.getDocument', 'DOM.resolveNode', 'Runtime.callFunctionOn'])
def test_closed_shadow_protocol_exception_fails_closed_and_is_redacted(method, capsys):
    page = CDPPage(fail_method=method)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.session.detached and not page.clicked
    assert 'secret-protocol-detail' not in capsys.readouterr().err


def test_closed_shadow_unknown_action_is_not_retried_or_misreported_as_no_button(capsys):
    page = CDPPage(click_error=True)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    clicks = [params for method, params in page.session.calls
              if method == 'Runtime.callFunctionOn' and params['arguments'][1]['value']]
    assert len(clicks) == 1
    error = capsys.readouterr().err
    assert '草稿保存结果未确认' in error
    assert 'unknown-action-detail' not in error
    assert page.session.detached


def test_closed_shadow_cleanup_error_does_not_overwrite_action_or_receipt():
    page = CDPPage(cleanup_error=True)
    xhs._save_draft(page, timeout_s=0)
    assert page.clicked and page.session.detached


def test_closed_shadow_rejects_foreign_top_level_document():
    tree = cdp_tree()
    tree['root']['documentURL'] = 'https://evil.example/publish/publish'
    page = CDPPage(tree=tree)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert not page.clicked
    assert not any(method == 'DOM.resolveNode' for method, _ in page.session.calls)


def test_closed_shadow_does_not_traverse_iframe_document():
    inner = cdp_tree()['root']
    frame = cdp_node(8, 'IFRAME')
    frame['contentDocument'] = inner
    tree = cdp_tree([])
    tree['root']['children'] = [frame]
    page = CDPPage(tree=tree)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert not page.clicked
    assert not any(method == 'DOM.resolveNode' for method, _ in page.session.calls)


def test_closed_shadow_rejects_fake_host_name_with_wrong_local_name():
    tree = cdp_tree()
    tree['root']['children'][0]['children'][0]['localName'] = 'div'
    page = CDPPage(tree=tree)
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert not page.clicked


def test_closed_shadow_runtime_guard_executes_offline_javascript():
    """Execute the actual validation/click function against DOM doubles, not its return mock."""
    import subprocess
    script = r'''
const assert = require('node:assert/strict');
const action = eval('(' + ACTION + ')');
let calls;
globalThis.innerHeight = 800; globalThis.innerWidth = 1200;
globalThis.location = {origin:'https://creator.xiaohongshu.com', pathname:'/publish/publish'};
globalThis.getComputedStyle = node => ({display:'block', visibility:'visible', opacity:'1', pointerEvents:'auto', ...node.style});
globalThis.HTMLElement = {prototype: {click() { calls++; }}};
function fixture(change = () => {}) {
  calls = 0;
  const document = {};
  const make = (tagName, label) => ({
    tagName, innerText:label, textContent:label, isConnected:true, attrs:{}, style:{}, parentElement:null,
    rect:{width:100,height:30,left:1,top:1,right:101,bottom:31},
    getAttribute(key) { return this.attrs[key] ?? null; },
    hasAttribute(key) { return key in this.attrs; },
    matches(selector) { return ['INPUT','TEXTAREA'].includes(this.tagName) || 'contenteditable' in this.attrs; },
    getBoundingClientRect() { return this.rect; }, getClientRects() { return [this.rect]; },
    getRootNode() { return document; },
  });
  const host = make('XHS-PUBLISH-BTN','');
  const button = make('BUTTON','暂存离开');
  const publish = make('BUTTON','发布');
  const root = {host, mode:'closed', querySelectorAll() { return this.buttons; }, buttons:[button,publish]};
  button.getRootNode = publish.getRootNode = () => root;
  const setup = {button, host, root, expectedHost:host};
  change(setup);
  return action.call(button, '暂存离开', true, setup.expectedHost);
}
assert.equal(fixture().clicked, true); assert.equal(calls, 1);
for (const change of [
  s => { s.expectedHost = {...s.host}; },
  s => { s.button.innerText = s.button.textContent = '发布'; },
  s => { s.button.attrs['aria-label'] = '发布'; },
  s => { s.button.disabled = true; },
  s => { s.host.attrs['aria-disabled'] = 'true'; },
  s => { s.host.isConnected = false; },
  s => { s.root.mode = 'open'; },
  s => { s.button.rect.width = 0; },
  s => { s.button.rect.top = 1000; },
  s => { s.button.style.visibility = 'hidden'; },
  s => { s.root.buttons.push(s.button); },
  s => { globalThis.location.origin = 'https://evil.example'; },
]) {
  assert.equal(fixture(change).clicked, false); assert.equal(calls, 0);
}
console.log('offline CDP runtime guards passed');
'''.replace('ACTION', json.dumps(xhs._CDP_DRAFT_ACTION, ensure_ascii=False), 1)
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'offline CDP runtime guards passed'


@pytest.mark.parametrize('boundary', ['element_handles', 'is_visible', 'old_handle_comparison'])
def test_receipt_navigation_race_waits_for_explicit_notice_without_reclick(boundary, monkeypatch):
    from playwright.sync_api import Error
    forbid_public(monkeypatch)
    old = Element('处理中')
    pending = True

    def navigation_race():
        nonlocal pending
        if pending:
            pending = False
            raise Error('Execution context was destroyed, most likely because of a navigation; private-detail')

    class Notice(Element):
        def is_visible(self):
            if boundary == 'is_visible':
                navigation_race()
            return super().is_visible()

        def evaluate(self, expression, other):
            if boundary == 'old_handle_comparison' and other is old:
                navigation_race()
            return super().evaluate(expression, other)

    class Notices(Locator):
        def element_handles(self):
            if boundary == 'element_handles':
                navigation_race()
            return super().element_handles()

    class NavigatingPage(Page):
        def locator(self, selector):
            if selector == xhs._DRAFT_NOTICE_HISTORY:
                return Locator([old])
            if selector == xhs._DRAFT_NOTICES and self.clicked:
                return Notices([Notice('草稿保存成功')])
            return super().locator(selector)

    page = NavigatingPage()
    xhs._save_draft(page, timeout_s=1)
    assert page.buttons[0].clicks == 1
    assert pending is False


def test_receipt_navigation_without_notice_ends_unknown_without_reclick(monkeypatch, capsys):
    from playwright.sync_api import Error
    forbid_public(monkeypatch)

    class NavigatingPage(Page):
        def locator(self, selector):
            if selector == xhs._DRAFT_NOTICES and self.clicked:
                self.url = 'https://creator.xiaohongshu.com/new/home?token=private-detail'
                raise Error('Execution context was destroyed; private-detail')
            return super().locator(selector)

    page = NavigatingPage()
    with pytest.raises(SystemExit) as error:
        xhs._save_draft(page, timeout_s=0)
    assert error.value.code != 0
    assert page.buttons[0].clicks == 1
    captured = capsys.readouterr()
    assert '草稿保存结果未确认' in captured.err
    assert 'private-detail' not in captured.err
    assert 'draft_saved' not in captured.out


@pytest.mark.parametrize('navigation', ['new_document', 'hash', 'same_context_error', 'unreadable_identity'])
def test_receipt_permanent_old_context_requires_proven_new_document(navigation, monkeypatch, capsys):
    from playwright.sync_api import Error
    forbid_public(monkeypatch)

    class Document:
        def __init__(self):
            self.marker = None

    class ContextNotice(Element):
        def __init__(self, text, document):
            super().__init__(text)
            self.document = document

        def evaluate(self, expression, other):
            if expression == '(node, previous) => node === previous':
                if self.document is not other.document or navigation == 'same_context_error':
                    raise Error('JSHandles can be evaluated only in the context they were created!')
                return self is other
            if expression == xhs._DRAFT_NOTICE_DOCUMENT:
                if navigation == 'unreadable_identity' and self.text == '草稿保存成功':
                    raise Error('Execution context was destroyed')
                return 'same' if self.document.marker == other else 'new'
            return super().evaluate(expression, other)

    class NavigatingPage(Page):
        def __init__(self):
            super().__init__()
            self.document = Document()
            self.old = ContextNotice('处理中', self.document)

        def evaluate(self, expression, key):
            if 'defineProperty' in expression:
                self.document.marker = key
                return True
            return self.document.marker == key

        def locator(self, selector):
            if selector == xhs._DRAFT_NOTICE_HISTORY:
                return Locator([self.old])
            if selector == xhs._DRAFT_NOTICES and self.clicked:
                if navigation == 'new_document':
                    if self.document is self.old.document:
                        self.document = Document()
                    return Locator([ContextNotice('草稿保存成功', self.document)])
                self.url += '#hash-only'
                if navigation == 'hash':
                    self.old.text = '草稿保存成功'
                    return Locator([self.old])
                return Locator([ContextNotice('草稿保存成功', self.document)])
            return super().locator(selector)

    page = NavigatingPage()
    if navigation == 'new_document':
        xhs._save_draft(page, timeout_s=0)
    else:
        with pytest.raises(SystemExit):
            xhs._save_draft(page, timeout_s=0)
        assert '草稿保存结果未确认' in capsys.readouterr().err
    assert page.buttons[0].clicks == 1


def test_receipt_baseline_document_change_blocks_action(monkeypatch, capsys):
    forbid_public(monkeypatch)

    class BaselineRacePage(Page):
        def evaluate(self, expression, marker):
            if expression == xhs._DRAFT_DOCUMENT_CURRENT:
                return False
            return super().evaluate(expression, marker)

    page = BaselineRacePage()
    with pytest.raises(SystemExit):
        xhs._save_draft(page, timeout_s=0)
    assert page.buttons[0].clicks == 0
    assert '未执行保存' in capsys.readouterr().err


def test_document_marker_guards_execute_offline_javascript():
    import subprocess
    script = r'''
const assert = require('node:assert/strict');
const mark = eval('(' + MARK + ')');
const current = eval('(' + CURRENT + ')');
const state = eval('(' + STATE + ')');
const marker = {key:'_xhs_draft_receipt_test', value:'separate-random-value'};
const oldDocument = {};
globalThis.document = oldDocument;
const oldNotice = {isConnected:true, ownerDocument:oldDocument};
assert.equal(mark(marker), true);
assert.deepEqual(Object.getOwnPropertyDescriptor(oldDocument, marker.key), {
  value:marker.value, writable:false, enumerable:false, configurable:false,
});
assert.equal(mark(marker), false);
assert.equal(current(marker), true);
assert.equal(state(oldNotice, marker), 'same');
globalThis.location = {hash:'#same-document', pathname:'/different-path'};
assert.equal(state(oldNotice, marker), 'same');
assert.throws(() => Object.defineProperty(oldDocument, marker.key, {value:'changed'}), TypeError);
globalThis.document = {};
const newNotice = {isConnected:true, ownerDocument:document};
assert.equal(current(marker), false);
assert.equal(state(newNotice, marker), 'new');
assert.equal(state(oldNotice, marker), 'unknown');
assert.equal(state({...newNotice, isConnected:false}, marker), 'unknown');
Object.defineProperty(document, marker.key, {value:marker.value, configurable:true});
assert.equal(state(newNotice, marker), 'unknown');
delete document[marker.key];
Object.defineProperty(document, marker.key, {get() { throw Error('must not read accessor'); }});
assert.equal(state(newNotice, marker), 'unknown');
console.log('document identity guards passed');
'''
    for key, value in [('MARK', xhs._DRAFT_DOCUMENT_MARK), ('CURRENT', xhs._DRAFT_DOCUMENT_CURRENT),
                       ('STATE', xhs._DRAFT_NOTICE_DOCUMENT)]:
        script = script.replace(key, json.dumps(value), 1)
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'document identity guards passed'
