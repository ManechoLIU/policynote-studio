"""Offline checks for the community login wait before QR capture. No browser or network."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/shared/scripts"))
import xhs_publish as xhs


class _El:
    def __init__(self, box=None, visible=True):
        self.box = box
        self.visible = visible

    def is_visible(self):
        return self.visible

    def bounding_box(self):
        return self.box


class _Page:
    def __init__(self):
        self.url = "https://www.xiaohongshu.com/explore"
        self.selectors = {}
        self.waits = []
        self.title_text = ""
        self.body = ""
        self.ready_after = None
        self.boom = False

    def title(self):
        return self.title_text

    def inner_text(self, selector, timeout=0):
        return self.body

    def query_selector_all(self, selector):
        if self.boom:
            raise RuntimeError("Execution context was destroyed")
        return list(self.selectors.get(selector, []))

    def wait_for_timeout(self, ms):
        self.waits.append(ms)
        if self.ready_after is not None and len(self.waits) >= self.ready_after:
            self.selectors[".login-container .qrcode-img"] = [
                _El({"width": 96, "height": 120})
            ]


def _qr(page, width, height):
    page.selectors[".login-container .qrcode-img"] = [_El({"width": width, "height": height})]


def test_visible_qr_at_least_96px_skips_the_fixed_wait():
    page = _Page()
    _qr(page, 96, 96)
    assert xhs._community_login_surface_ready(page) is True
    assert xhs._is_logged_in(page) is False
    xhs._wait_community_login_surface(page)
    assert page.waits == []


def test_login_box_without_qr_is_ready_but_not_logged_in():
    page = _Page()
    box = _El()
    page.selectors[".login-container"] = [box]
    page.selectors[xhs.SELECTORS["logged_out"]] = [box]
    assert xhs._community_login_surface_ready(page) is True
    assert xhs._is_logged_in(page) is False
    xhs._wait_community_login_surface(page)
    assert page.waits == []


def test_small_qr_is_not_treated_as_ready():
    page = _Page()
    _qr(page, 95, 96)
    assert xhs._community_login_surface_ready(page) is False
    xhs._wait_community_login_surface(page)
    assert page.waits == [800] * 10


def test_delayed_qr_waits_only_completed_rounds():
    page = _Page()
    page.ready_after = 3
    xhs._wait_community_login_surface(page)
    assert page.waits == [800, 800, 800]


def test_unready_page_is_bounded_to_ten_probes():
    page = _Page()
    xhs._wait_community_login_surface(page)
    assert page.waits == [800] * 10


def test_login_error_stops_before_any_wait_and_is_not_success():
    page = _Page()
    page.url = "https://www.xiaohongshu.com/website-login/error?error_code=300012"
    page.body = "IP存在风险"
    assert xhs._login_error(page)
    assert xhs._is_logged_in(page) is False
    xhs._wait_community_login_surface(page)
    assert page.waits == []


def test_already_logged_in_stops_before_any_wait():
    page = _Page()
    page.selectors[xhs.SELECTORS["login_ok"]] = [_El()]
    assert xhs._is_logged_in(page) is True
    xhs._wait_community_login_surface(page)
    assert page.waits == []


def test_query_errors_keep_the_bounded_fallback():
    page = _Page()
    page.boom = True
    xhs._wait_community_login_surface(page)
    assert page.waits == [800] * 10
