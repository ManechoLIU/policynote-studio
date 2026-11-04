#!/usr/bin/env python3
"""xhs_publish.py — 小红书平台草稿保存（Playwright）。

publish / publish-video 执行时一律仅保存平台草稿，省略 --draft 也不会公开发布。
--draft 是显式兼容参数；没有公开发布开关。必须获得平台明确草稿保存成功提示，
才输出 {"outcome":"draft_saved","published":false}，否则非零退出。

check 检查环境；login 登录；plan 离线预览草稿步骤；selftest 离线自检。
旧公开发布辅助函数保留供代码兼容，但不接入任何 CLI 执行入口。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import login_state  # noqa: E402
import login_qr
import login_transport
import content_guard  # noqa: E402  出站内容安全闸门

# --------------------------------------------------------------------------- #
# 选择器集中维护（小红书改版时单点更新）。REF = xiaohongshu-mcp 对应源。
# --------------------------------------------------------------------------- #
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
EXPLORE_URL = "https://www.xiaohongshu.com/explore"
CREATOR_LOGIN_URL = "https://creator.xiaohongshu.com/login"
CREATOR_HOME_URL = "https://creator.xiaohongshu.com/new/home"
CREATOR_ACCOUNT_URL = "https://creator.xiaohongshu.com/api/galaxy/user/info"
TITLE_MAX = 20  # 小红书标题上限（全角计法，见 calc_title_length）

SELECTORS = {
    # 登录（REF login.go）
    "login_ok": ".main-container .user .link-wrapper .channel",
    "qrcode": ".login-container .qrcode-img, .qrcode-img, [class*='qrcode' i] img, "
              "[class*='qrcode' i] canvas, [class*='qr-code' i], img[src*='qrcode' i], "
              ".sso-login-wrapper img, .sso-login-wrapper canvas, "
              ".login-container img[alt*='二维码']",
    "logged_out": ".login-container, .login-modal, .login-box, "
                  ".sso-login-wrapper, .qrcode-img, [class*='qrcode' i], [class*='qr-code' i]",
    "login_entry": ".side-bar-component.login-btn, button.login-btn",
    "qr_switch": "button:has-text('扫码登录'), [role='tab']:has-text('扫码登录'), "
                 ".login-type:has-text('扫码登录'), .sso-login-wrapper > div > div > img",
    # 进页/切 tab（REF publish.go mustClickPublishTab）
    "upload_content": "div.upload-content",
    "creator_tab": "div.creator-tab",
    "pop_cover": "div.d-popover",
    # 上传（REF publish.go uploadImages / publish_video.go uploadVideo）
    "upload_input_first": ".upload-input",
    "upload_input_more": "input[type=file]",
    "img_preview": ".img-preview-area .pr",
    # 标题/正文（REF publish.go submitPublish / getContentElement）
    "title_input": "div.d-input input, input[placeholder*='标题']",
    "content_quill": "div.ql-editor",
    "content_editable": "div.editor-container [contenteditable='true'], [contenteditable='true']",
    "content_placeholder": "p[data-placeholder*='输入正文描述']",
    "title_overflow": "div.title-container div.max_suffix",
    "content_overflow": "div.edit-container div.length-error",
    # 话题（REF publish.go inputTag）
    "topic_container": "#creator-editor-topic-container",
    "topic_item": "#creator-editor-topic-container .item",
    # 发布按钮（REF publish.go findPublishButton）
    "publish_btn_new": "xhs-publish-btn",
    "publish_btn_old": ".publish-page-publish-btn button.bg-red",
}

PROFILE_NAME = "XiaohongshuProfile"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_QR_OUT = PROJECT_ROOT / "outputs" / "_login" / "xhs-login-qrcode.png"

# Chromium 启动性能参数（提速冷启动；勿禁用图片——二维码是图片）
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--disable-extensions", "--disable-background-networking",
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
    "--disable-features=TranslateUI,BackForwardCache",
    "--mute-audio", "--no-first-run", "--no-default-browser-check",
]


# --------------------------------------------------------------------------- #
# 纯函数（可离线自测）
# --------------------------------------------------------------------------- #
def calc_title_length(s: str) -> int:
    """小红书标题长度：UTF-16 码元非 ASCII 计 2、ASCII 计 1，再 (n+1)//2 上取整。
    REF pkg/xhsutil/title.go CalcTitleLength。20 全角字 → 20。"""
    utf16 = s.encode("utf-16-le")
    byte_len = 0
    for i in range(0, len(utf16), 2):
        code = utf16[i] | (utf16[i + 1] << 8)
        byte_len += 2 if code > 127 else 1
    return (byte_len + 1) // 2


def _die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _abspaths(csv: str | None) -> list[str]:
    """逗号分隔路径 → 绝对路径列表，校验存在。"""
    if not csv:
        return []
    out = []
    for raw in csv.split(","):
        raw = raw.strip()
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if not p.is_file():
            _die(f"文件不存在：{p}")
        out.append(str(p))
    return out


def _profile_dir(base: str | None) -> Path:
    root = Path(base).expanduser() if base else Path.home() / ".easel-browser-profiles"
    return root / PROFILE_NAME


def _community_profile_base() -> str:
    """Separate community-reader browser state from the publishing profile."""
    return str(Path.home() / ".easel-browser-profiles" / "community-reader")


def _cloak_executable() -> Path | None:
    """本机已装的 CloakBrowser 内核。只用 ~/.cloakbrowser，不用调研 profile / 9344 / 9345。

    无头 Playwright Chromium 会被小红书 300012；同一出口下 Cloak 无头已实测能出码。
    """
    override = (os.environ.get("EASEL_CLOAK_BROWSER") or "").strip()
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    root = Path.home() / ".cloakbrowser"
    if not root.is_dir():
        return None
    choices = sorted(root.glob("chromium-*/chrome.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    return choices[0] if choices else None


class _ProfileLock:
    """同一份 XiaohongshuProfile 不能同时开两个 Cloak：账号页 whoami 和登录会把内核打成 exit 21。"""

    def __init__(self, profile: Path):
        self.path = profile / ".easel.lock"
        self.fd: int | None = None

    def acquire(self, timeout_s: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + timeout_s
        last: OSError | None = None
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.lseek(self.fd, 0, os.SEEK_SET)
                os.write(self.fd, f"{os.getpid()}\n".encode("ascii"))
                return
            except OSError as e:
                last = e
                if self.fd is not None:
                    try:
                        os.close(self.fd)
                    except OSError:
                        pass
                    self.fd = None
                if time.time() >= deadline:
                    raise TimeoutError(f"登录目录正被占用：{self.path}") from last
                time.sleep(0.4)

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = None


def _clear_stale_chrome_locks(profile: Path) -> None:
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = profile / name
        try:
            p.unlink()
        except OSError:
            pass


def _browser_channel() -> str | None:
    """Cloak 找不到时，有头登录才退回本机 Chrome/Edge。"""
    if os.name != "nt":
        return None
    pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    chrome = (
        pf / "Google" / "Chrome" / "Application" / "chrome.exe",
        pf86 / "Google" / "Chrome" / "Application" / "chrome.exe",
        local / "Google" / "Chrome" / "Application" / "chrome.exe",
    )
    if any(p.is_file() for p in chrome):
        return "chrome"
    edge = (
        pf / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    )
    if any(p.is_file() for p in edge):
        return "msedge"
    return None


def _login_error(page) -> str:
    """区分明确 IP 风险和其它错误页，不泄露 URL 中的登录参数。"""
    try:
        url = page.url or ""
        title = page.title() or ""
        try:
            text = page.inner_text("body", timeout=2000)[:3000]
        except Exception:
            text = ""
        if any(message in title + text for message in (
                "工程师正在", "工程师在紧急", "系统维护中", "系统正在维护", "页面维护中")):
            return ("小红书当前入口显示维护页面，暂时无法完成登录校验。"
                    "这不是二维码过期或账号退出，请稍后重试。")
        codes = parse_qs(urlparse(url).query).get("error_code", [])
        code = codes[0] if codes and codes[0].isdigit() and len(codes[0]) <= 32 else ""
        error_page = "website-login/error" in url or "安全限制" in title or bool(code)
        if not error_page:
            return ""
        if code == "300012" or "IP存在风险" in re.sub(r"\s+", "", text).upper():
            detail = f"（错误码 {code}）" if code else ""
            return (f"小红书返回 IP 风险限制{detail}，二维码无法获取。"
                    "请在可信网络重试；服务器/机房出口可能被平台限制，Easel 无法保证解除风控。")
        detail = f"（错误码 {code}）" if code else ""
        return f"小红书登录被平台拦截{detail}，不一定是 IP 问题。请检查网络，或使用 --headed 查看平台提示。"
    except Exception:
        return ""


def _risk_blocked(page) -> bool:
    return "IP 风险限制" in _login_error(page)


def _visible_login_element(page, selectors: str):
    for selector in selectors.split(","):
        for element in page.query_selector_all(selector.strip()):
            if element.is_visible():
                return element
    return None


def _auth_error_type(error) -> str:
    name = type(error).__name__ if error is not None else 'UnknownError'
    return name if name in {
        'Error', 'TimeoutError', 'RuntimeError', 'ValueError', 'TypeError', 'OSError',
        'ImportError', 'ModuleNotFoundError', 'PermissionError', 'ConnectionError', 'TargetClosedError', 'SystemExit',
    } else 'UnknownError'


def _safe_auth_location(raw_url):
    try:
        url = urlparse(raw_url or '')
        if url.scheme != 'https' or url.hostname not in ('creator.xiaohongshu.com', 'www.xiaohongshu.com') or url.port not in (None, 443):
            return '<non_official>', False
        return 'https://' + url.hostname + url.path, url.hostname == 'creator.xiaohongshu.com' and url.path.rstrip('/') == '/login'
    except Exception:
        return '<unavailable>', False


def _failure_screenshot(page, prefix):
    if not callable(getattr(page, 'screenshot', None)):
        return '<unavailable>'
    try:
        directory = PROJECT_ROOT / 'outputs' / '_runtime' / 'draft-r1'
        directory.mkdir(parents=True, exist_ok=True)
        screenshot = directory / f'{prefix}-{time.time_ns()}.png'
        page.screenshot(path=str(screenshot), full_page=False, timeout=3000)
        return str(screenshot)
    except Exception:
        return '<unavailable>'


def _auth_snapshot(page, stage, *, login_ok=None, error=None, screenshot=False):
    try:
        location, login_path = _safe_auth_location(page.url)
    except Exception as location_error:
        location, login_path = '<unavailable>', False
        error = error or location_error
    try:
        wall = _visible_login_element(page, SELECTORS['logged_out']) is not None
    except Exception as probe_error:
        wall = None
        error = error or probe_error
    state = 'auth_required' if login_path and wall is True and error is None else 'auth_unknown'
    fields = {'stage': stage, 'state': state, 'url': location, 'login_ok': login_ok,
              'login_wall': wall, 'action_sent': False, 'error_type': _auth_error_type(error)}
    if screenshot:
        fields['screenshot'] = _failure_screenshot(page, 'auth-refusal')
    print('XHS_AUTH_DIAGNOSTIC ' + json.dumps(fields, ensure_ascii=False), file=sys.stderr)
    return fields


def _stop_publish_auth(page, *, login_ok, stage='publish_auth', error=None, attempt=None):
    diagnostic = _auth_snapshot(page, stage, login_ok=login_ok, error=error, screenshot=True)
    code = 'XHS_AUTH_REQUIRED' if diagnostic['state'] == 'auth_required' else 'XHS_AUTH_UNKNOWN'
    if attempt is not None:
        attempt['terminal_reported'] = True
    print(json.dumps({'outcome': diagnostic['state'], 'published': False, 'error': code,
                      'stage': stage, 'error_type': diagnostic['error_type']}))
    _die('小红书创作平台要求登录，请在账号管理完成登录后再保存草稿' if code == 'XHS_AUTH_REQUIRED'
         else '小红书认证状态未确认；本次未执行草稿保存，请稍后校验账号')


def _whoami_failure(result, stage, error, code='XHS_WHOAMI_UNCONFIRMED'):
    fields = {'error': code, 'stage': stage, 'error_type': _auth_error_type(error)}
    result.update(fields)
    print('XHS_AUTH_DIAGNOSTIC ' + json.dumps(fields), file=sys.stderr)


def _publish_failure(attempt, error, *, cleanup=False):
    """Only fixed metadata crosses the runner boundary; never exception text."""
    action = attempt.get('action_sent', False)
    safe = {'stage': 'cleanup' if cleanup else attempt['stage'], 'published': False,
            'action_sent': action, 'confirmation_sent': attempt.get('confirmation_sent', False),
            'error_type': _auth_error_type(error),
            'errorCode': ('XHS_CLEANUP_FAILED' if cleanup else
                          'XHS_DRAFT_STAGE_FAILED' if action is False else 'XHS_DRAFT_STAGE_UNCONFIRMED')}
    if not cleanup and attempt.get('cleanup_error_type'):
        safe['cleanup_error_type'] = attempt['cleanup_error_type']
    if attempt.get('page') is not None:
        safe['screenshot'] = _failure_screenshot(attempt['page'], 'stage-failure')
    print('XHS_FAILURE_DIAGNOSTIC ' + json.dumps(safe), file=sys.stderr)
    if not cleanup:
        print(json.dumps({'outcome': 'failed', 'published': False, 'failureDiagnostic': safe}))


def _publish_cleanup_error(attempt, error):
    attempt['cleanup_error_type'] = _auth_error_type(error)
    _publish_failure(attempt, error, cleanup=True)


def _draft_action_attempt(attempt, *, confirmation=False):
    if attempt is not None:
        attempt['stage'] = 'confirmation' if confirmation else 'draft_action'
        attempt['confirmation_sent' if confirmation else 'action_sent'] = None
    _draft_button_diagnostic('draft_confirmation_attempt' if confirmation else 'draft_action_attempt',
                             **{'confirmation_sent' if confirmation else 'action_sent': None})


def _open_login_dialog(page) -> None:
    creator = urlparse(page.url or "").hostname == "creator.xiaohongshu.com"
    if not _visible_login_element(page, SELECTORS["logged_out"]):
        try:
            page.wait_for_selector(
                f"{SELECTORS['logged_out']}, {SELECTORS['login_entry']}, {SELECTORS['login_ok']}",
                state="visible", timeout=45000 if creator else 15000)
        except Exception:
            pass
    if creator:
        try:
            page.wait_for_selector(".sso-login-wrapper", state="visible", timeout=15000)
            page.wait_for_selector(".sso-login-wrapper [data-testid='loading']",
                                   state="hidden", timeout=15000)
        except Exception:
            pass
    if _visible_login_element(page, SELECTORS["logged_out"]):
        return
    if urlparse(page.url or "").hostname == "www.xiaohongshu.com":
        button = _visible_login_element(page, SELECTORS["login_entry"])
        if button:
            button.click(timeout=3000, no_wait_after=True)


def _missing_qr_reason(page) -> str:
    try:
        text = page.inner_text("body", timeout=2000).strip()
    except Exception:
        return "登录页面尚未就绪，请检查网络连接后重试"
    if not text:
        return "登录页面空白，平台脚本未完成加载，请检查网络连接后重试"
    if "扫一扫" in text or "扫码登录" in text:
        return "已切换扫码登录，但二维码未加载或无法识别，请稍后重试"
    if "短信登录" in text or "发送验证码" in text:
        return "页面仍停留在短信登录，扫码切换控件未就绪或失效；可用 --headed 查看平台提示"
    return "未找到可识别的登录二维码，登录弹窗未打开或页面结构已变化"


def _capture_login_qr(page, qr_out: Path, *, creator_only: bool = False, community_only: bool = False) -> bool:
    """首页无法出码时只尝试一次官方创作平台入口；False 表示已登录。"""
    failures = []
    for url in ((None,) if community_only else (CREATOR_LOGIN_URL,) if creator_only else (None, CREATOR_LOGIN_URL)):
        entry = "创作平台" if url else "小红书首页"
        try:
            if url:
                page.goto(url, wait_until="commit", timeout=30000)
        except Exception as error:
            reason = f"打不开小红书创作平台（{type(error).__name__}），请检查网络连接后重试"
            failures.append(f"{entry}：{reason}")
            continue
        if not community_only and _is_logged_in(page):
            return False
        reason = _login_error(page)
        if not reason:
            try:
                _open_login_dialog(page)
                reason = _login_error(page)
                if reason:
                    failures.append(f"{entry}：{reason}")
                    continue
                login_qr.prepare_qr(page, qr_out, SELECTORS["qrcode"],
                                    SELECTORS["qr_switch"], minimum_size=96)
                return True
            except Exception as error:
                if not community_only and _is_logged_in(page):
                    return False
                reason = _login_error(page) or _missing_qr_reason(page)
                print(f"小红书抓码失败（{entry}/{type(error).__name__}）：{reason}", file=sys.stderr)
        failures.append(f"{entry}：{reason}")
    raise RuntimeError("；".join(failures))


def _is_logged_in(page) -> bool:
    if _login_error(page):
        return False
    try:
        for element in page.query_selector_all(SELECTORS["logged_out"]):
            if element.is_visible():
                return False
    except Exception:
        pass
    try:
        for element in page.query_selector_all(SELECTORS["login_ok"]):
            if element.is_visible():
                return True
    except Exception:
        pass
    try:
        url = urlparse(page.url or "")
        if (url.hostname == "creator.xiaohongshu.com"
                and not parse_qs(url.query).get("error_code")
                and (url.path.startswith(("/publish/", "/new/", "/home/")) or url.path == "/home")):
            for account in page.query_selector_all(".user-info"):
                if not account.is_visible():
                    continue
                avatar = account.query_selector("img.user_avatar")
                name = account.query_selector("span")
                if (avatar and avatar.is_visible() and avatar.get_attribute("src")
                        and name and name.is_visible() and name.inner_text().strip()):
                    return True
    except Exception:
        pass
    return False


def _qr_state(page) -> str:
    try:
        text = page.inner_text(
            ".sso-login-wrapper, .login-container, .login-modal, .login-box",
            timeout=1000)
    except Exception:
        return ""
    compact = re.sub(r"\s+", "", text)
    if any(message in compact for message in (
            "扫码成功", "扫描成功", "已扫码", "请在手机上确认", "请在APP上确认")):
        return "scanned"
    if any(message in compact for message in (
            "二维码已失效", "二维码失效", "二维码已过期", "二维码过期", "二维码无效")):
        return "expired"
    return ""


def _refresh_expired_qr(page, qr_out: Path) -> bool:
    if _qr_state(page) != "expired":
        return False
    selectors = (
        ".sso-login-wrapper button:has-text('刷新'), "
        ".sso-login-wrapper [role='button']:has-text('刷新'), "
        ".sso-login-wrapper a:text-is('点击刷新'), "
        ".sso-login-wrapper span:text-is('点击刷新'), "
        ".sso-login-wrapper div:text-is('点击刷新'), "
        ".login-container button:has-text('刷新'), "
        ".login-container span:text-is('刷新二维码'), "
        ".login-container div:text-is('刷新二维码'), "
        ".login-container a:text-is('点击刷新'), "
        ".login-container span:text-is('点击刷新'), "
        ".login-container div:text-is('点击刷新')"
    )
    button = _visible_login_element(page, selectors)
    if button is None:
        return False
    button.click(timeout=3000)
    page.wait_for_timeout(800)
    if _qr_state(page) == "scanned":
        return False
    login_qr.capture_qr(page, qr_out, SELECTORS["qrcode"], minimum_size=96)
    return True


def _verify_login(page, timeout_s: float = 6) -> bool:
    deadline = time.monotonic() + timeout_s
    confirmed = 0
    while time.monotonic() < deadline:
        if _is_logged_in(page):
            confirmed += 1
            if confirmed >= 2:
                return True
        else:
            confirmed = 0
        if _login_error(page) or _visible_login_element(page, SELECTORS["logged_out"]):
            return False
        page.wait_for_timeout(500)
    return False


def _check_account_login(page) -> bool:
    outcomes = []
    deadline = time.monotonic() + 14
    for destination in (CREATOR_HOME_URL, EXPLORE_URL):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcomes.append("unknown")
                break
            page.goto(destination, wait_until="commit", timeout=min(4000, remaining * 1000))
            remaining = deadline - time.monotonic()
            if remaining > 0 and _verify_login(page, timeout_s=min(4, remaining)):
                return True
            outcomes.append("unknown" if _login_error(page) or not _visible_login_element(
                page, SELECTORS["logged_out"]) else "logged_out")
            diagnostic = _auth_snapshot(page, 'whoami_creator_page' if destination == CREATOR_HOME_URL else 'whoami_web_page')
            # Account management connects publishing accounts. A proven creator
            # login wall must not be masked by an unrelated web-site fallback.
            if (destination == CREATOR_HOME_URL and outcomes[-1] == 'logged_out'
                    and diagnostic['state'] == 'auth_required'):
                return False
        except Exception as error:
            outcomes.append("unknown")
            _auth_snapshot(page, 'whoami_creator_page' if destination == CREATOR_HOME_URL else 'whoami_web_page', error=error)
    if any(outcome != "logged_out" for outcome in outcomes):
        raise RuntimeError("小红书登录校验暂未完成：网络超时、维护/风控页面或账号界面未就绪；保留上次登录状态")
    return False


def _creator_account(context) -> dict | None:
    try:
        response = context.request.get(
            CREATOR_ACCOUNT_URL, headers={"Referer": CREATOR_HOME_URL}, timeout=5000)
        if response.status != 200:
            return None
        payload = response.json()
        account = payload.get("data")
        if (payload.get("success") is True and isinstance(account, dict)
                and isinstance(account.get("userId"), str) and account["userId"].strip()):
            return account
    except Exception:
        pass
    return None


def _web_account(context) -> dict | None:
    try:
        response = context.request.get(EXPLORE_URL, timeout=4000)
        if response.status != 200:
            return None
        return _web_account_html(response.text())
    except Exception:
        return None


def _initial_state(html: str) -> dict:
    if len(html) > 2_000_000:
        return {}
    match = re.search(
        r"<script\b[^>]*>\s*window\.__INITIAL_STATE__\s*=\s*([\s\S]*?)</script>",
        html, re.IGNORECASE)
    if not match:
        return {}
    serialized = re.sub(
        r'"(?:\\.|[^"\\])*"|\bundefined\b',
        lambda token: "null" if token.group() == "undefined" else token.group(),
        match.group(1).strip().rstrip(";"))
    try:
        state = json.loads(serialized)
        return state if isinstance(state, dict) else {}
    except ValueError:
        return {}


def _unwrap_state(value):
    if isinstance(value, dict) and "userId" not in value and "basicInfo" not in value:
        if "value" in value:
            return value["value"]
        if "_value" in value:
            return value["_value"]
    return value


def _state_account(state: dict) -> dict | None:
    user = state.get("user")
    account = _unwrap_state(user.get("userInfo")) if isinstance(user, dict) else None
    if (not isinstance(account, dict) or _unwrap_state(user.get("loggedIn")) is not True
            or not isinstance(account.get("userId"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", account["userId"])):
        return None
    identity = _account_identity(account)
    return {**account, "userName": identity["name"], "userAvatar": identity["avatar"]}


def _web_account_html(html: str) -> dict | None:
    return _state_account(_initial_state(html))


def _self_profile_identity(state: dict, user_id: str) -> dict:
    identity = {"name": "", "avatar": ""}
    account = _state_account(state)
    if account is None or account["userId"] != user_id:
        return identity
    profile = _unwrap_state(state["user"].get("userPageData"))
    if not isinstance(profile, dict) or profile.get("userId", user_id) != user_id:
        return identity
    basic = profile.get("basicInfo")
    return _account_identity(basic) if isinstance(basic, dict) else identity


def _self_profile_account(context, account: dict) -> dict:
    user_id = account.get("userId")
    if not isinstance(user_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", user_id):
        return account
    destination = f"https://www.xiaohongshu.com/user/profile/{user_id}"
    try:
        response = context.request.get(
            destination, headers={"Referer": EXPLORE_URL}, timeout=4000)
        current_url = urlparse(response.url)
        if (response.status != 200 or current_url.hostname != "www.xiaohongshu.com"
                or current_url.path.rstrip("/") != urlparse(destination).path):
            return account
        fallback = _self_profile_identity(_initial_state(response.text()), user_id)
        identity = _account_identity(account)
        return {**account, "userName": fallback["name"] or identity["name"],
                "userAvatar": fallback["avatar"] or identity["avatar"]}
    except Exception:
        return account


def _authenticated_account(context) -> dict | None:
    account = _web_account(context)
    if account is not None and all(_account_identity(account).values()):
        return account
    creator = _creator_account(context)
    if account is None:
        return creator
    if creator is None or creator.get("userId") != account.get("userId"):
        return account
    identity = _account_identity(account)
    fallback = _account_identity(creator)
    return {
        **account,
        "userName": identity["name"] or fallback["name"],
        "userAvatar": identity["avatar"] or fallback["avatar"],
    }


def _account_identity(account: dict) -> dict:
    name = next((account[key].strip()[:40] for key in ("userName", "nickname", "user_name", "name")
                 if isinstance(account.get(key), str) and account[key].strip()), "")
    avatar = ""
    for key in ("userAvatar", "imageb", "images", "avatar", "user_avatar"):
        candidate = account.get(key)
        if not isinstance(candidate, str):
            continue
        candidate = candidate.strip()
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        try:
            parsed = urlparse(candidate)
            if (parsed.scheme in ("https", "http") and parsed.hostname
                    and not parsed.username and not parsed.password
                    and not any(ord(character) < 32 for character in candidate)):
                avatar = candidate
                break
        except ValueError:
            continue
    return {"name": name, "avatar": avatar}


def _page_account_identity(page, user_id: str | None = None) -> dict:
    identity = {"name": "", "avatar": ""}
    if urlparse(page.url or "").hostname == "creator.xiaohongshu.com":
        for account in page.query_selector_all(".user-info"):
            if not account.is_visible():
                continue
            avatar = account.query_selector("img.user_avatar")
            name = account.query_selector("span")
            if avatar and avatar.is_visible() and name and name.is_visible():
                return _account_identity({
                    "userName": name.inner_text(),
                    "userAvatar": avatar.get_attribute("src"),
                })
        return identity
    profile_url = (f"https://www.xiaohongshu.com/user/profile/{user_id}"
                   if isinstance(user_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", user_id) else "")
    for channel in ([] if profile_url else page.query_selector_all(SELECTORS["login_ok"])):
        if not channel.is_visible():
            continue
        account = channel.evaluate("""element => {
            const anchor = element.closest('a[href]') || element.querySelector('a[href]')
                || element.closest('.user')?.querySelector('a[href*="/user/profile/"]');
            return {
                href: anchor?.getAttribute('href') || '',
                avatar: anchor?.querySelector('img')?.getAttribute('src') || ''
            };
        }""")
        if not isinstance(account, dict):
            continue
        href = account.get("href")
        if not isinstance(href, str):
            continue
        candidate = urlparse(href)
        if (candidate.scheme or candidate.netloc
                or not re.fullmatch(r"/user/profile/[A-Za-z0-9_-]+/?", candidate.path)):
            continue
        profile_url = "https://www.xiaohongshu.com" + candidate.path
        identity["avatar"] = _account_identity({"userAvatar": account.get("avatar")})["avatar"]
        break
    if not profile_url:
        return identity
    try:
        page.goto(profile_url, wait_until="commit", timeout=4000)
        current_url = urlparse(page.url or "")
        if (current_url.hostname != "www.xiaohongshu.com"
                or current_url.path.rstrip("/") != urlparse(profile_url).path.rstrip("/")):
            return identity
        try:
            page.wait_for_function(
                """() => {
                    const profile = window.__INITIAL_STATE__?.user?.userPageData;
                    return !!(profile?.basicInfo || profile?.value?.basicInfo || profile?._value?.basicInfo);
                }""",
                timeout=1500)
        except Exception:
            pass
        try:
            state = page.evaluate("""() => {
                const user = window.__INITIAL_STATE__?.user;
                return user ? JSON.parse(JSON.stringify({user: {
                    loggedIn: user.loggedIn, userInfo: user.userInfo, userPageData: user.userPageData
                }})) : {};
            }""")
            if isinstance(state, dict):
                own_account = _state_account(state)
                if own_account is not None and own_account["userId"] != urlparse(profile_url).path.rstrip("/").split("/")[-1]:
                    return {"name": "", "avatar": ""}
                extracted = _self_profile_identity(state, urlparse(profile_url).path.rstrip("/").split("/")[-1])
                if all(extracted.values()):
                    return extracted
                identity.update({key: value for key, value in extracted.items() if value})
        except Exception:
            pass
        selectors = ".user-profile .user-info, .user-page .user-info, .user .user-info, .user-profile .info-part"
        page.wait_for_selector(selectors, state="visible", timeout=3000)
        for account in page.query_selector_all(selectors):
            if not account.is_visible():
                continue
            name = account.query_selector(".user-name, .username, .name")
            avatar = account.query_selector("img.avatar, img.user-avatar, img.avatar-item, .avatar img")
            if name and name.is_visible():
                text = name.inner_text().strip()
                if text:
                    identity["name"] = text.splitlines()[0][:40]
            if avatar and avatar.is_visible():
                identity["avatar"] = _account_identity({"userAvatar": avatar.get_attribute("src")})["avatar"]
            if identity["name"]:
                break
    except Exception:
        pass
    return identity


def _proxy(explicit: str | None, disable: bool) -> str | None:
    """外网代理：--no-proxy 关；--proxy 显式；否则取 env（小红书是外网，默认需代理）。"""
    if disable:
        return None
    if explicit:
        return explicit
    return os.environ.get("https_proxy") or os.environ.get("http_proxy") \
        or os.environ.get("EASEL_PROXY")


# --------------------------------------------------------------------------- #
# 浏览器动作（移植自 xiaohongshu-mcp，需 playwright）
# --------------------------------------------------------------------------- #
def _human_type(page, locator, text: str) -> None:
    """逐字符输入 + 随机间隔（反检测，REF humanize/input.go Type）。"""
    locator.click()
    for ch in text:
        page.keyboard.type(ch)
        page.wait_for_timeout(random.randint(30, 110))


def _click_publish_tab(page, tabname: str) -> None:
    """点「上传图文/上传视频」tab：重试 15s + 遮挡检测 + 移弹层。REF mustClickPublishTab。"""
    page.wait_for_selector(SELECTORS["upload_content"], timeout=15000)
    deadline = time.time() + 15
    while time.time() < deadline:
        tabs = page.query_selector_all(SELECTORS["creator_tab"])
        for tab in tabs:
            try:
                if not tab.is_visible():
                    continue
                if (tab.inner_text() or "").strip() != tabname:
                    continue
            except Exception:
                continue
            # 遮挡检测（REF isElementBlocked：elementFromPoint 命中的是不是自己）
            blocked = tab.evaluate(
                """(el) => { const r = el.getBoundingClientRect();
                    if (!r.width || !r.height) return true;
                    const t = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
                    return !(t === el || el.contains(t)); }""")
            if blocked:
                cover = page.query_selector(SELECTORS["pop_cover"])
                if cover:
                    cover.evaluate("el => el.remove()")
                page.wait_for_timeout(200)
                continue
            tab.click()
            return
        page.wait_for_timeout(200)
    _die(f"未找到发布 TAB：{tabname}（页面结构可能已变，检查 SELECTORS.creator_tab）")


_IMAGE_UPLOAD_STATES = r'''() => {
    const visible = node => !!node && node.isConnected && node.getClientRects().length > 0
        && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none';
    return [...document.querySelectorAll('.img-preview-area .pr')].map(slot => {
        const text = (slot.innerText || '').replace(/\s+/g, '');
        const images = [...slot.querySelectorAll('img.img.preview')];
        const controls = [...slot.querySelectorAll('.image-editor-control .image-editor-actions.has-editor-info button.edit-btn')];
        const image = images.length === 1 ? images[0] : null;
        const control = controls.length === 1 ? controls[0] : null;
        return {
            visible: visible(slot),
            loaded: visible(image) && image.complete === true && image.naturalWidth > 0 && image.naturalHeight > 0,
            ready: visible(control) && control.disabled === false && control.getAttribute('aria-disabled') !== 'true',
            pending: !!slot.querySelector('.processing-container, .progress-container, .spinner-red, .prerender, .mask.uploading'),
            failed: text.includes('上传失败'), retry: text.includes('重试')
        };
    });
}'''


def _upload_images(page, paths: list[str]) -> None:
    """Wait for every observed image slot to finish processing; never retry an upload."""
    for i, path in enumerate(paths):
        sel = SELECTORS["upload_input_first"] if i == 0 else SELECTORS["upload_input_more"]
        page.set_input_files(sel, path)
        print(f"  等待图片 {i+1}/{len(paths)} 上传完成", file=sys.stderr)
        deadline = time.monotonic() + 60
        while True:
            try:
                states = page.evaluate(_IMAGE_UPLOAD_STATES)
                fields = ('visible', 'loaded', 'ready', 'pending', 'failed', 'retry')
                if not isinstance(states, list) or any(not isinstance(state, dict) or any(
                        type(state.get(field)) is not bool for field in fields) for state in states):
                    raise ValueError('image upload state unavailable')
            except Exception as error:
                _draft_button_diagnostic('image_upload_unconfirmed', errorCode='XHS_IMAGE_UPLOAD_UNCONFIRMED',
                                         error_type=_auth_error_type(error))
                _die('XHS_IMAGE_UPLOAD_UNCONFIRMED：无法核验图片上传状态；未执行保存')
            if any(state['failed'] or state['retry'] for state in states):
                _draft_button_diagnostic('image_upload_failed', errorCode='XHS_IMAGE_UPLOAD_FAILED', states=states[:18])
                _die('XHS_IMAGE_UPLOAD_FAILED：平台显示图片上传失败或待重试；未执行保存')
            if time.monotonic() >= deadline:
                _draft_button_diagnostic('image_upload_timeout', errorCode='XHS_IMAGE_UPLOAD_UNCONFIRMED',
                                         required_count=i + 1, states=states[:18])
                _die('XHS_IMAGE_UPLOAD_UNCONFIRMED：图片上传完成状态未确认（60s）；未执行保存')
            if len(states) == i + 1 and all(state['visible'] and state['loaded'] and state['ready']
                                            and not state['pending'] for state in states):
                break
            page.wait_for_timeout(500)


def _upload_video(page, path: str) -> None:
    """上传视频并等待编辑器就绪；草稿流程不探测公开发布按钮。"""
    sel = SELECTORS["upload_input_first"]
    if not page.query_selector(sel):
        sel = SELECTORS["upload_input_more"]
    page.set_input_files(sel, path)
    print(f"  上传视频：{path}（等待处理，最长 10min）", file=sys.stderr)
    page.wait_for_selector(SELECTORS["title_input"], state="visible", timeout=600000)


def _content_element(page):
    """正文输入框：先 ql-editor（旧版），再 contenteditable（新版），最后退回 placeholder 定位。
    REF getContentElement。小红书已把正文编辑器从 Quill 换成 contenteditable。"""
    el = page.query_selector(SELECTORS["content_quill"])
    if el:
        return el
    el = page.query_selector(SELECTORS["content_editable"])
    if el:
        return el
    ph = page.query_selector(SELECTORS["content_placeholder"])
    if ph:
        cur = ph
        for _ in range(5):
            parent = cur.evaluate_handle("el => el.parentElement").as_element()
            if not parent:
                break
            role = parent.get_attribute("role") or ""
            if role == "textbox" or parent.get_attribute("contenteditable") == "true":
                return parent
            cur = parent
    return None


def _check_overflow(page) -> None:
    """标题/正文超限：读平台自身的溢出提示元素。REF checkTitleMaxLength/checkContentMaxLength。"""
    for key, name in (("title_overflow", "标题"), ("content_overflow", "正文")):
        el = page.query_selector(SELECTORS[key])
        if el and el.is_visible():
            _die(f"{name}超出平台长度限制：{(el.inner_text() or '').strip()}")


def _input_tags(page, content_el, tags: list[str]) -> None:
    """话题：正文末尾输 # + 联想下拉点第一项，真绑话题。REF inputTag。

    注意：必须把 # 与话题名**连续输入**、中途不能再 click 正文——否则光标会被挪走，
    # 和话题名被拆开，小红书识别不到连续的 #话题 token，联想不出、绑不上话题。
    """
    if not tags:
        return
    content_el.click()
    page.keyboard.press("Control+End")   # 光标移到正文末尾，避免 # 插到正文中间
    page.wait_for_timeout(400)
    for tag in tags:
        tag = tag.lstrip("#").strip()
        if not tag:
            continue
        page.keyboard.type(" ")          # 与正文/上一话题分隔，确保 # 起一个新 token
        page.keyboard.type("#")
        page.wait_for_timeout(300)
        for ch in tag:                   # 逐字输入话题名——不再 click（避免移光标拆开 #话题）
            page.keyboard.type(ch)
            page.wait_for_timeout(random.randint(30, 110))
        page.wait_for_timeout(1000)
        item = page.query_selector(SELECTORS["topic_item"])
        if item:
            item.click()                 # 点联想第一项 = 真正绑定话题
        else:
            page.keyboard.type(" ")      # 无联想则退化为空格分隔（至少保留 #文字）
        page.wait_for_timeout(500)


def _wait_publish_clickable(page, timeout_s: int = 15):
    """等发布按钮可点击（新旧兼容）。REF findPublishButton/waitForPublishButtonClickable。"""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        for w in page.query_selector_all(SELECTORS["publish_btn_new"]):
            if not w.is_visible():
                continue
            if (w.get_attribute("is-publish") or "") == "false":
                continue
            if (w.get_attribute("submit-disabled") or "") == "true":
                last = "新版发布按钮不可点击"
                continue
            return ("new", w)
        for b in page.query_selector_all(SELECTORS["publish_btn_old"]):
            if not b.is_visible():
                continue
            if b.get_attribute("disabled") is not None:
                last = "旧版发布按钮 disabled"
                continue
            if (b.get_attribute("aria-disabled") or "") == "true":
                last = "旧版发布按钮 aria-disabled"
                continue
            return ("old", b)
        page.wait_for_timeout(1000)
    _die(f"等待发布按钮可点击超时{('：' + last) if last else ''}")


def _diag_after_publish(page) -> str:
    """点发布后失败时，抓屏幕上可见的弹框/报错/toast 文案，供排错。
    截图默认不存（正常使用不截图）；排错时设 EASEL_PUBLISH_DEBUG=1 才会存失败截图。"""
    bits = []
    for sel in (".d-modal", "[role=dialog]", "[class*=modal]", "[class*=dialog]",
                ".d-message", ".d-toast", "[class*=toast]", "[class*=error]", "[class*=tip]"):
        try:
            for el in page.query_selector_all(sel):
                if el.is_visible():
                    t = (el.inner_text() or "").strip().replace("\n", " / ")
                    if t and t not in " ".join(bits):
                        bits.append(f"[{sel}] {t[:120]}")
        except Exception:
            pass
    shot = ""
    if os.environ.get("EASEL_PUBLISH_DEBUG"):
        try:
            out = PROJECT_ROOT / "outputs" / "_login" / "xhs-publish-fail.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            shot = f"（调试截图见 {out}）"
        except Exception:
            pass
    return ("屏幕可见元素：" + " | ".join(bits[:6]) if bits else "屏幕无可识别弹框/报错") + shot


def _confirm_publish_dialog(page) -> None:
    """点发布后若弹二次确认框，点其确认按钮（保守：仅当可见且文案匹配）。"""
    deadline = time.time() + 5
    while time.time() < deadline:
        for b in page.query_selector_all("button, .d-button, [role=button]"):
            try:
                if not b.is_visible():
                    continue
                tx = (b.inner_text() or "").strip()
                if tx in ("确认发布", "确定发布", "继续发布", "立即发布", "确认", "确定"):
                    b.click()
                    page.wait_for_timeout(1000)
                    return
            except Exception:
                pass
        page.wait_for_timeout(500)


def _wait_publish_success(page, timeout_s: int = 40) -> None:
    """发布成功校验：小红书发布成功后**原地清空表单回到上传页**（不换 URL）。
    成功信号任一：跳离 /publish/publish、出现「成功」toast、或编辑表单已重置（标题框+图片预览消失）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if "/publish/publish" not in page.url:
            print(f"✅ 发布成功，已跳转：{page.url}")
            return
        for sel in (".d-message", ".d-toast", "[class*=toast]", "[class*=message]"):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible() and "成功" in (el.inner_text() or ""):
                    print("✅ 发布成功（检测到成功提示）")
                    return
            except Exception:
                pass
        # 表单已重置：填过的标题框 + 上传的图片预览都消失 = 已提交回到空上传页
        try:
            if (not page.query_selector(SELECTORS["title_input"])
                    and not page.query_selector(SELECTORS["img_preview"])):
                print("✅ 发布成功（编辑表单已清空复位）")
                return
        except Exception:
            pass
        page.wait_for_timeout(500)
    _die("发布未确认成功：点击发布后未跳离发布页/未见成功提示。" + _diag_after_publish(page))


def _normalize_content(text: str) -> str:
    """小红书编辑器「不支持连续空行输入」——把 2+ 连续空行压成单个空行，行尾空白清掉。"""
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)      # 行尾空白
    text = re.sub(r"\n{3,}", "\n\n", text)       # 连续空行（3+ 换行）→ 一个空行
    return text.strip("\n")


def _fill_editor(page, title, content, tags):
    """仅填写编辑器与长度校验；不点击提交。"""
    content = _normalize_content(content)         # 修连续空行导致的发布失败
    title_el = page.query_selector(SELECTORS["title_input"])
    if not title_el:
        _die("未找到标题输入框（检查 SELECTORS.title_input）")
    _human_type(page, title_el, title)
    page.wait_for_timeout(400)

    content_el = _content_element(page)
    if not content_el:
        _die("未找到正文输入框（检查 SELECTORS.content_*）")
    _human_type(page, content_el, content)
    page.wait_for_timeout(500)
    title_el.click()  # REF waitAndClickTitleInput：回点标题增强稳定性
    _input_tags(page, content_el, tags)

    _check_overflow(page)


def _fill_and_submit(page, title, content, tags):
    """旧公开发布helper；当前CLI不会调用。"""
    _fill_editor(page, title, content, tags)
    kind, btn = _wait_publish_clickable(page, 15)
    btn.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    box = btn.bounding_box()
    if kind == "new" and box:
        # xhs-publish-btn 是宽横条(闭合 Shadow DOM)，内含[暂存离开][发布]两个按钮；
        # 点 host 中心会落在两按钮间隙→无效。发布按钮在右侧约 62% 处（实测像素为品牌红），按坐标点它。
        page.mouse.click(box["x"] + box["width"] * 0.62, box["y"] + box["height"] / 2)
    else:
        try:
            btn.click(force=True)
        except Exception:
            btn.click()
    page.wait_for_timeout(1000)
    _confirm_publish_dialog(page)   # 若弹二次确认框，点确认
    _wait_publish_success(page, 40)


_DRAFT_BUTTON_NAME = re.compile(r"^(?:暂存离开|存草稿|保存草稿)$")
_DRAFT_SUCCESS_TEXTS = (
    "草稿保存成功", "保存草稿成功", "草稿已保存", "已保存草稿", "已存为草稿", "已保存至草稿箱", "草稿保存完成",
)
_DRAFT_SUCCESS = re.compile(r"^(?:" + "|".join(_DRAFT_SUCCESS_TEXTS) + r")[！!。]?$")
# Deliberately narrow: nested editor/preview components and generic ARIA messages
# are not platform receipts. Unrecognised live structures must fail closed.
_DRAFT_NOTICES = "body > .d-message, body > .d-toast"
_DRAFT_NOTICE_HISTORY = ".d-message, .d-toast"
_DRAFT_EDITABLE = "input, textarea, [contenteditable], [role='textbox']"
# Structure from soup-song/xhs-refactored b848941, internal/action/publish.go:677.
# Its broad non-red button and sleep-based completion are deliberately not used.
_DRAFT_DIALOGS = "div.creator-publish-popover, [role='dialog']"
_DRAFT_CONFIRM_LABELS = frozenset(('离开', '确认离开', '保存并离开', '暂存离开', '存草稿', '保存草稿'))
_DRAFT_CANCEL_LABELS = frozenset(('取消', '继续编辑', '返回'))
_DRAFT_CONFIRM_PROMPTS = (
    '是否保存草稿', '是否保存草稿并离开', '是否将当前内容保存为草稿并离开',
    '当前内容将保存至草稿箱，是否离开', '当前内容将保存为草稿，是否离开',
    '是否暂存并离开', '是否暂存当前内容并离开',
)
# A non-configurable own property survives same-document URL changes. Only its
# proven absence on a live notice's document can retire old-context handles.
_DRAFT_DOCUMENT_CHECK = r'''(doc, marker) => {
    const own = Object.getOwnPropertyDescriptor(doc, marker.key);
    if (own === undefined) return 'new';
    return own.value === marker.value && own.configurable === false
        && own.writable === false && own.enumerable === false ? 'same' : 'unknown';
}'''
_DRAFT_DOCUMENT_MARK = r'''marker => {
    if (Object.getOwnPropertyDescriptor(document, marker.key) !== undefined) return false;
    Object.defineProperty(document, marker.key, {value: marker.value});
    return (CHECK)(document, marker) === 'same';
}'''.replace('CHECK', _DRAFT_DOCUMENT_CHECK)
_DRAFT_DOCUMENT_CURRENT = 'marker => (' + _DRAFT_DOCUMENT_CHECK + ")(document, marker) === 'same'"
_DRAFT_DOCUMENT_STATUS = 'marker => (' + _DRAFT_DOCUMENT_CHECK + ')(document, marker)'
_DRAFT_NOTICE_DOCUMENT = r'''(node, marker) => {
    if (!node.isConnected || node.ownerDocument !== document) return 'unknown';
    return (CHECK)(document, marker);
}'''.replace('CHECK', _DRAFT_DOCUMENT_CHECK)


def _draft_success_visible(page, previous, document_marker) -> bool:
    """Accept only a new root platform notification, never an existing DOM node."""
    for notice in page.locator(_DRAFT_NOTICES).element_handles():
        if not notice.is_visible():
            continue
        if re.search(r"editor|preview", notice.get_attribute("class") or "", re.IGNORECASE):
            continue
        if notice.evaluate("(node, selector) => node.matches(selector)", _DRAFT_EDITABLE) or notice.query_selector(_DRAFT_EDITABLE):
            continue
        document_state = notice.evaluate(_DRAFT_NOTICE_DOCUMENT, document_marker)
        if document_state not in ('same', 'new'):
            continue
        if document_state == 'same' and any(notice.evaluate("(node, previous) => node === previous", old) for old in previous):
            continue
        if _DRAFT_SUCCESS.fullmatch(" ".join((notice.inner_text() or "").split())):
            return True
    return False


# CDP is used only inside the product's already-running Playwright context.
# It never clicks a host box: the closed root must expose a real semantic button.
_CDP_DRAFT_ACTION = r'''function(expectedLabel, click, expectedHost) {
    const allowed = new Set(['暂存离开', '存草稿', '保存草稿']);
    const clean = value => (value || '').replace(/\s+/g, ' ').trim();
    const labelOf = node => {
        const text = clean(node.innerText || node.textContent);
        const aria = clean(node.getAttribute('aria-label'));
        return text && aria && text !== aria ? '' : (text || aria);
    };
    const root = this.getRootNode();
    const host = root && root.host;
    const label = labelOf(this);
    const semantic = this.tagName === 'BUTTON' || this.getAttribute('role') === 'button';
    const valid = this.isConnected && location.origin === 'https://creator.xiaohongshu.com'
        && location.pathname === '/publish/publish' && semantic && allowed.has(label)
        && label === expectedLabel && root.mode === 'closed'
        && host && host === expectedHost && host.tagName === 'XHS-PUBLISH-BTN' && host.isConnected
        && !this.matches('input,textarea,[contenteditable]');
    const visible = node => {
        const rect = node.getBoundingClientRect();
        if (!rect.width || !rect.height || !node.getClientRects().length
                || rect.bottom <= 0 || rect.right <= 0
                || rect.top >= innerHeight || rect.left >= innerWidth) return false;
        for (let el = node; el; el = el.parentElement || el.getRootNode().host) {
            const style = getComputedStyle(el);
            if (el.hidden || style.display === 'none' || style.visibility !== 'visible'
                    || Number(style.opacity) === 0) return false;
        }
        return true;
    };
    let enabled = valid;
    for (let el = this; el; el = el.parentElement || el.getRootNode().host) {
        if (el.disabled || el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'
                || el.hasAttribute('inert') || getComputedStyle(el).pointerEvents === 'none') enabled = false;
    }
    const state = {valid: !!valid, label, visible: !!valid && visible(this), enabled: !!enabled, clicked: false};
    if (!click || !state.valid || !state.visible || !state.enabled) return state;
    // Check again in the same synchronous call that invokes the semantic action.
    const matches = [...root.querySelectorAll('button,[role="button"]')]
        .filter(node => allowed.has(labelOf(node)) && visible(node));
    if (matches.length !== 1 || matches[0] !== this) return {...state, valid: false};
    HTMLElement.prototype.click.call(this);
    return {...state, clicked: true};
}'''


def _draft_button_diagnostic(stage: str, **safe_fields) -> None:
    """Only controlled labels and structural metadata, never page text or secrets."""
    print('XHS_DRAFT_DIAGNOSTIC ' + json.dumps({'stage': stage, **safe_fields}, ensure_ascii=False), file=sys.stderr)


def _draft_timeout_diagnostic(page, document_marker, confirmation_sent=False) -> None:
    """Best-effort, read-only evidence; log only a path and controlled labels."""
    fields = {'action_sent': True, 'confirmation_sent': confirmation_sent, 'notice_confirmed': False,
              'document_state': 'unknown', 'url': '<unavailable>', 'buttons': [], 'dialogs': [],
              'notices': [], 'confirmation_dialog_count': 0, 'confirmation_buttons': []}
    allowed = set(_DRAFT_SUCCESS_TEXTS) | set(_DRAFT_CONFIRM_PROMPTS) | {
        '暂存离开', '存草稿', '保存草稿', '是否保存草稿', '草稿保存失败', '保存草稿失败',
        '草稿保存中', '正在保存草稿', '保存并离开', '确认离开', '继续编辑', '取消', '确定',
        '离开', '不保存', '不保存离开', '放弃', '直接离开', '确认', '确认发布',
    }
    names = re.compile(r'^(?:' + '|'.join(re.escape(label) for label in sorted(allowed)) + r')[！!。]?$')
    try:
        url = urlparse(page.url or '')
        fields['url'] = ('https://creator.xiaohongshu.com' + url.path
                         if url.scheme == 'https' and url.hostname == 'creator.xiaohongshu.com'
                         else '<non_official>')
    except Exception:
        pass
    try:
        state = page.evaluate(_DRAFT_DOCUMENT_STATUS, document_marker)
        if state in ('same', 'new', 'unknown'):
            fields['document_state'] = state
    except Exception:
        pass
    for role, field in (('button', 'buttons'), ('dialog', 'dialogs'), (None, 'notices')):
        try:
            locator = page.get_by_role(role, name=names) if role else page.locator(_DRAFT_NOTICES)
            # Handles avoid locator auto-waits extending an already-expired gate.
            for item in locator.element_handles()[:20]:
                if not item.is_visible():
                    continue
                label = ' '.join((item.inner_text() or item.get_attribute('aria-label') or '').split())
                if names.fullmatch(label) and label not in fields[field]:
                    fields[field].append(label)
        except Exception:
            pass
    try:
        dialogs = _visible_draft_dialogs(page)
        fields['confirmation_dialog_count'] = len(dialogs)
        for dialog in dialogs[:10]:
            for button in dialog.query_selector_all('button,[role="button"]')[:20]:
                if button.is_visible():
                    label = _draft_control_label(button)
                    safe = label if names.fullmatch(label) else '<other>'
                    if safe not in fields['confirmation_buttons']:
                        fields['confirmation_buttons'].append(safe)
    except Exception:
        pass
    if callable(getattr(page, 'screenshot', None)):
        fields['screenshot'] = _failure_screenshot(page, 'draft-timeout')
    _draft_button_diagnostic('receipt_timeout', **fields)


def _cdp_nodes(root):
    stack, count = [root], 0
    while stack:
        node = stack.pop()
        count += 1
        if count > 50000:
            raise ValueError('DOM tree limit')
        yield node
        stack.extend(reversed(node.get('children', []) + node.get('shadowRoots', [])))


def _cdp_draft_candidates(session):
    document = session.send('DOM.getDocument', {'depth': -1, 'pierce': True})
    candidates, diagnostics = [], []
    root_document = document.get('root', {})
    if root_document.get('nodeType') != 9 or root_document.get('nodeName') != '#document' or root_document.get('documentURL', '').split('?', 1)[0] != PUBLISH_URL.split('?', 1)[0]:
        raise ValueError('not the official top-level document')
    for host in _cdp_nodes(root_document):
        if host.get('nodeType') != 1 or host.get('nodeName') != 'XHS-PUBLISH-BTN' or host.get('localName') != 'xhs-publish-btn':
            continue
        roots = host.get('shadowRoots', [])
        entry = {'closed': any(root.get('shadowRootType') == 'closed' for root in roots), 'buttons': []}
        diagnostics.append(entry)
        for root in roots:
            if root.get('shadowRootType') != 'closed':
                continue
            for node in _cdp_nodes(root):
                attrs = dict(zip(node.get('attributes', [])[::2], node.get('attributes', [])[1::2]))
                if node.get('nodeName', '').upper() != 'BUTTON' and attrs.get('role') != 'button':
                    continue
                text = ' '.join(''.join(n.get('nodeValue', '') for n in _cdp_nodes(node)
                                      if n.get('nodeType') == 3).split())
                aria = ' '.join(attrs.get('aria-label', '').split())
                label = '' if text and aria and text != aria else (text or aria)
                safe_label = label if label in ('暂存离开', '存草稿', '保存草稿', '发布', '确认', '确定', '确认发布', '立即发布', '取消', '返回') else '<other>'
                if len(entry['buttons']) < 20:
                    entry['buttons'].append({'label': safe_label, 'semantic': 'BUTTON' if node.get('nodeName', '').upper() == 'BUTTON' else 'role=button'})
                if _DRAFT_BUTTON_NAME.fullmatch(label):
                    backend, host_backend = node.get('backendNodeId'), host.get('backendNodeId')
                    if type(backend) is not int or type(host_backend) is not int:
                        raise ValueError('missing stable node identity')
                    candidates.append((backend, host_backend, label))
    _draft_button_diagnostic('closed_shadow_scan', hosts=diagnostics[:10], candidates=len(candidates))
    return candidates


class _DraftActionUnknown(RuntimeError):
    """A dispatched action has no trustworthy response; never repeat it."""


def _visible_draft_dialogs(page):
    return [dialog for dialog in page.locator(_DRAFT_DIALOGS).element_handles() if dialog.is_visible()]


def _draft_control_label(button):
    text = ' '.join((button.inner_text() or '').split())
    aria = ' '.join((button.get_attribute('aria-label') or '').split())
    return '' if text and aria and text != aria else (text or aria)


def _picture_edit_guide_button(dialog):
    """Recognise only the observed image-edit onboarding, never a save dialog."""
    if (dialog.get_attribute('role') != 'dialog'
            or set((dialog.get_attribute('class') or '').split()) != {'feature-guide', 'feature-guide--with-arrow'}):
        return None
    if dialog.evaluate('node => node.isConnected && node.ownerDocument === document && window === window.top') is not True:
        return None
    if dialog.query_selector(_DRAFT_EDITABLE):
        return None
    buttons = [button for button in dialog.query_selector_all('button,[role="button"]') if button.is_visible()]
    labels = [_draft_control_label(button) for button in buttons]
    if len(buttons) not in (1, 2) or labels.count('我知道了') != 1:
        return None
    for button, label in zip(buttons, labels):
        if label == '我知道了':
            continue
        if (button.get_attribute('class') != 'feature-guide__close'
                or label not in ('', '关闭', '关闭新功能引导')) or (label == '' and (
                (button.inner_text() or '').strip() or (button.get_attribute('aria-label') or '').strip())):
            return None
    acknowledgement = buttons[labels.index('我知道了')]
    if acknowledgement.get_attribute('class') != 'feature-guide__btn' or not acknowledgement.is_enabled():
        return None
    expected = '图片可以编辑啦，快来试试吧支持裁剪图片大小、选择模版或滤镜、添加贴纸和文字我知道了'
    if re.sub(r'\s+', '', dialog.inner_text() or '') != expected:
        return None
    return acknowledgement


def _dismiss_picture_edit_guide(page):
    # This one-time onboarding click precedes the receipt baseline. It is not a
    # draft action or receipt; all unrecognised dialogs retain the baseline veto.
    dialogs = _visible_draft_dialogs(page)
    if len(dialogs) != 1:
        return
    url = urlparse(page.url or '')
    if url.scheme != 'https' or url.netloc != 'creator.xiaohongshu.com' or url.path != '/publish/publish':
        return
    dialog = dialogs[0]
    button = _picture_edit_guide_button(dialog)
    if button is None:
        return
    fresh_dialogs = _visible_draft_dialogs(page)
    if (len(fresh_dialogs) != 1
            or fresh_dialogs[0].evaluate('(node, previous) => node === previous', dialog) is not True):
        raise ValueError('picture guide changed before dismissal')
    fresh_button = _picture_edit_guide_button(fresh_dialogs[0])
    if fresh_button is None or fresh_button.evaluate('(node, previous) => node === previous', button) is not True:
        raise ValueError('picture guide control changed before dismissal')
    # Native click retains actionability/hit checks. Never retry an uncertain click.
    fresh_button.click(timeout=5000)
    dialog.wait_for_element_state('hidden', timeout=5000)
    _draft_button_diagnostic('picture_edit_guide_dismissed')


def _draft_confirmation_button(dialog, document_marker):
    if dialog.evaluate(_DRAFT_NOTICE_DOCUMENT, document_marker) not in ('same', 'new'):
        raise ValueError('confirmation document unavailable')
    if dialog.evaluate('(node, selector) => node.matches(selector)', _DRAFT_EDITABLE) or dialog.query_selector(_DRAFT_EDITABLE):
        raise ValueError('editable confirmation is not trusted')
    buttons = [button for button in dialog.query_selector_all('button,[role="button"]') if button.is_visible()]
    labels = [_draft_control_label(button) for button in buttons]
    if not labels or any(label not in _DRAFT_CONFIRM_LABELS | _DRAFT_CANCEL_LABELS for label in labels):
        raise ValueError('unsupported confirmation controls')
    # Match the complete prompt and all visible controls, never a draft substring.
    compact = re.sub(r'\s+', '', dialog.inner_text() or '')
    suffix = ''.join(labels)
    prompt = next((prompt for prompt in _DRAFT_CONFIRM_PROMPTS
                   if any(compact == prompt + ending + suffix for ending in ('', '？', '?', '。'))), None)
    if prompt is None:
        raise ValueError('unsupported confirmation intent')
    affirmative = [(button, label) for button, label in zip(buttons, labels) if label in _DRAFT_CONFIRM_LABELS]
    if len(affirmative) != 1 or not affirmative[0][0].is_enabled():
        raise ValueError('confirmation is ambiguous or disabled')
    return affirmative[0][0], affirmative[0][1], prompt


def _confirm_draft_dialog(page, document_marker, attempt=None) -> bool:
    """One explicit save-draft confirmation; this is never a save receipt."""
    dialogs = _visible_draft_dialogs(page)
    if not dialogs:
        return False
    url = urlparse(page.url or '')
    if len(dialogs) != 1 or url.scheme != 'https' or url.hostname != 'creator.xiaohongshu.com' or url.path != '/publish/publish':
        raise ValueError('untrusted confirmation scope')
    button, label, prompt = _draft_confirmation_button(dialogs[0], document_marker)
    fresh, fresh_label, fresh_prompt = _draft_confirmation_button(dialogs[0], document_marker)
    if (fresh_label, fresh_prompt) != (label, prompt) or fresh.evaluate('(node, previous) => node === previous', button) is not True:
        raise ValueError('confirmation changed before action')
    try:
        # Native semantic element click retains Playwright's overlay/hit checks.
        _draft_action_attempt(attempt, confirmation=True)
        fresh.click(timeout=5000)
    except Exception as error:
        if attempt is not None:
            attempt['primary_error'] = error
        raise _DraftActionUnknown('confirmation response unavailable') from None
    _draft_button_diagnostic('draft_confirmation_sent', label=label)
    if attempt is not None:
        attempt.update(stage='receipt', confirmation_sent=True)
    return True


def _cdp_draft_state(session, candidate, *, click=False, attempt=None):
    backend, host_backend, label = candidate
    objects = []
    try:
        for identity in (backend, host_backend):
            remote = session.send('DOM.resolveNode', {'backendNodeId': identity}).get('object', {})
            if not remote.get('objectId'):
                raise ValueError('node is unavailable')
            objects.append(remote['objectId'])
        try:
            if click:
                _draft_action_attempt(attempt)
            result = session.send('Runtime.callFunctionOn', {
                'objectId': objects[0], 'functionDeclaration': _CDP_DRAFT_ACTION,
                'arguments': [{'value': label}, {'value': click}, {'objectId': objects[1]}], 'returnByValue': True,
            })
        except Exception as error:
            if click:
                if attempt is not None:
                    attempt['primary_error'] = error
                raise _DraftActionUnknown('action response unavailable') from None
            raise
        if result.get('exceptionDetails'):
            if click:
                raise _DraftActionUnknown('action execution result unavailable')
            raise ValueError('live node validation failed')
        value = result.get('result', {}).get('value')
        if not isinstance(value, dict) or value.get('valid') is not True or value.get('label') != label:
            if click and (not isinstance(value, dict) or value.get('clicked') is not False):
                raise _DraftActionUnknown('action result unavailable')
            raise ValueError('live node identity changed')
        return value
    finally:
        # Cleanup failure must never overwrite the action result or trigger a retry.
        for object_id in objects:
            try:
                session.send('Runtime.releaseObject', {'objectId': object_id})
            except Exception as error:
                _draft_button_diagnostic('closed_shadow_cleanup_failed', error_type=type(error).__name__)


def _cdp_pick_draft(session):
    visible = []
    for candidate in _cdp_draft_candidates(session):
        state = _cdp_draft_state(session, candidate)
        if state.get('visible') is True:
            visible.append((candidate, state))
    if len(visible) != 1 or visible[0][1].get('enabled') is not True:
        raise ValueError('draft button is absent, ambiguous or disabled')
    return visible[0][0]


def _click_closed_shadow_draft(page, attempt=None) -> None:
    """Fail closed unless two fresh pierced trees and live button checks agree."""
    session = None
    try:
        if page.url.split('?', 1)[0] != PUBLISH_URL.split('?', 1)[0]:
            raise ValueError('not the platform editor')
        session = page.context.new_cdp_session(page)
        candidate = _cdp_pick_draft(session)
        if _cdp_pick_draft(session) != candidate:
            raise ValueError('draft node changed before click')
        state = _cdp_draft_state(session, candidate, click=True, attempt=attempt)
        if state.get('visible') is not True or state.get('enabled') is not True or state.get('clicked') is not True:
            raise ValueError('draft action was not verified')
        _draft_button_diagnostic('closed_shadow_clicked', label=candidate[2])
    except _DraftActionUnknown:
        _draft_button_diagnostic('closed_shadow_result_unknown')
        _die('草稿保存结果未确认：安全草稿操作已发送但结果未知，请先核对平台草稿；不会自动重试')
    except Exception as error:
        if attempt is not None:
            attempt['primary_error'] = error
        _draft_button_diagnostic('closed_shadow_blocked', error_type=type(error).__name__)
        _die('未能安全确认闭合 Shadow DOM 内唯一可用的草稿按钮；已停止，不会公开发布')
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


def _save_draft(page, timeout_s: float = 30, *, attempt=None) -> None:
    """Semantic light/closed-shadow draft action followed by a separate receipt gate."""
    # Whole-body text is ONLY a veto. Hidden or copied old success text cannot
    # become fresh evidence when made visible, moved, or cloned after the click.
    document_marker = {'key': '_xhs_draft_receipt_' + os.urandom(16).hex(), 'value': os.urandom(16).hex()}
    try:
        _dismiss_picture_edit_guide(page)
        if page.evaluate(_DRAFT_DOCUMENT_MARK, document_marker) is not True:
            raise ValueError('document marker unavailable')
        before_text = page.locator("body").text_content(timeout=5000) or ""
        if any(text in re.sub(r"\s+", "", before_text) for text in _DRAFT_SUCCESS_TEXTS):
            _die("点击前页面已包含草稿保存成功文案，无法核验本次通知来源；未执行保存")
        previous = page.locator(_DRAFT_NOTICE_HISTORY).element_handles()
        if _visible_draft_dialogs(page):
            _die("点击前存在确认对话框，无法核验本次暂存来源；未执行保存")
        if page.evaluate(_DRAFT_DOCUMENT_CURRENT, document_marker) is not True or any(
                old.evaluate(_DRAFT_NOTICE_DOCUMENT, document_marker) != 'same' for old in previous):
            raise ValueError('baseline document changed')
    except Exception as error:
        if attempt is not None:
            attempt['primary_error'] = error
        _draft_button_diagnostic('receipt_baseline_unavailable', error_type=type(error).__name__)
        _die("点击前无法确认草稿通知所属文档；未执行保存")
    deadline = time.monotonic() + timeout_s
    button = None
    while True:
        buttons = page.get_by_role("button", name=_DRAFT_BUTTON_NAME)
        visible = [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()]
        if len(visible) > 1:
            _die("检测到多个可见草稿保存按钮，无法唯一确认；未执行保存")
        if len(visible) == 1 and visible[0].is_enabled():
            button = visible[0]
            break
        if time.monotonic() >= deadline:
            break
        page.wait_for_timeout(200)
    try:
        if page.evaluate(_DRAFT_DOCUMENT_CURRENT, document_marker) is not True:
            raise ValueError('action document changed')
    except Exception as error:
        if attempt is not None:
            attempt['primary_error'] = error
        _draft_button_diagnostic('receipt_baseline_unavailable', error_type=type(error).__name__)
        _die("点击前草稿编辑文档已改变或不可确认；未执行保存")
    if button is not None:
        _draft_action_attempt(attempt)
        button.click(timeout=5000)
    else:
        _click_closed_shadow_draft(page, attempt=attempt)
    if attempt is not None:
        attempt.update(stage='receipt', action_sent=True)
    _draft_button_diagnostic('draft_action_sent')
    deadline = time.monotonic() + timeout_s
    receipt_error_logged = False
    confirmation_sent = False
    while True:
        try:
            if _draft_success_visible(page, previous, document_marker):
                print("✅ 已检测到平台明确草稿保存成功提示", file=sys.stderr)
                return
            if not confirmation_sent and _confirm_draft_dialog(page, document_marker, attempt=attempt):
                confirmation_sent = True
                continue
        except _DraftActionUnknown:
            _draft_button_diagnostic('draft_confirmation_result_unknown')
            _die("草稿保存结果未确认：草稿确认操作已发送但结果未知；不会自动重试")
        except Exception as error:
            # Navigation can destroy a notice query or an old handle comparison.
            # An unreadable receipt is never success; retry only this read gate.
            if not receipt_error_logged:
                _draft_button_diagnostic('receipt_unavailable', error_type=type(error).__name__)
                receipt_error_logged = True
        if time.monotonic() >= deadline:
            _draft_timeout_diagnostic(page, document_marker, confirmation_sent)
            _die("草稿保存结果未确认：未出现明确的平台草稿保存成功提示；不会回退公开发布")
        # Local pacing also works while the page's execution context is absent.
        time.sleep(0.2)


def _fill_and_save_draft(page, title, content, tags, *, attempt=None):
    _fill_editor(page, title, content, tags)
    if attempt is not None:
        attempt['stage'] = 'draft_prepare'
    _save_draft(page, attempt=attempt)
    if attempt is not None:
        attempt['receipt_confirmed'] = True


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def _launch(p, headed: bool, base: str | None, proxy: str | None, *, login_assets: bool = True,
            timeout_ms: int | None = None, retry_target_closed: bool = True):
    profile = _profile_dir(base)
    profile.mkdir(parents=True, exist_ok=True)
    args = list(LAUNCH_ARGS)
    kwargs = dict(headless=not headed,
                  locale="zh-CN",
                  args=args)
    if timeout_ms is not None:
        kwargs['timeout'] = timeout_ms
    if proxy:
        kwargs["proxy"] = {"server": proxy}
    else:
        # 显式直连：Chromium 级屏蔽系统/环境代理（开 VPN 也能用）——同抖音链兜底
        args.append("--no-proxy-server")
    cloak = _cloak_executable()
    if cloak:
        kwargs["executable_path"] = str(cloak)
    elif headed:
        channel = _browser_channel()
        if channel:
            kwargs["channel"] = channel
    last: Exception | None = None
    for attempt in range(2 if retry_target_closed else 1):
        try:
            context = p.chromium.launch_persistent_context(str(profile), **kwargs)
            if login_assets:
                login_transport.install(context, "xiaohongshu")
            return context
        except Exception as e:
            last = e
            if retry_target_closed and attempt == 0 and "TargetClosed" in type(e).__name__:
                time.sleep(1.2)
                _clear_stale_chrome_locks(profile)
                continue
            raise
    raise last  # pragma: no cover


def cmd_check(_a) -> int:
    ok = True
    try:
        from playwright.sync_api import sync_playwright
        print("✅ playwright 已安装")
        with sync_playwright() as p:
            path = p.chromium.executable_path
            if path and Path(path).exists():
                print(f"✅ chromium 内核：{path}")
            else:
                print("❌ 未安装浏览器内核（playwright install chromium）"); ok = False
    except Exception as e:
        print(f"❌ playwright/内核不可用：{e}"); ok = False
    cloak = _cloak_executable()
    if cloak:
        print(f"✅ CloakBrowser 内核：{cloak}")
    else:
        print("⚠️ 未找到 CloakBrowser（~/.cloakbrowser）；小红书登录可能被 300012")
    print(f"登录态目录：{_profile_dir(None)}")
    return 0 if ok else 3


def _wait_sel(page, selector: str, timeout_ms: int = 15000, what: str = "元素") -> object:
    """等待选择器出现，容忍登录页导航竞态。

    小红书登录页会多次跳转（探索页 → 登录引导），Playwright 在导航瞬间
    查询旧 DOM 会抛 "Execution context was destroyed"——这不是选择器错误，
    是页面在跳。重试直到 deadline 而非一次失败就报错。
    """
    deadline = time.time() + timeout_ms / 1000
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            el = page.wait_for_selector(selector, timeout=2500)
            if el is not None:
                return el
        except Exception as e:  # navigation / context destroyed / timeout
            last_err = e
        page.wait_for_timeout(800)
    if last_err is not None and "Timeout" not in type(last_err).__name__:
        raise last_err
    raise TimeoutError(f"等待 {what} 超时（{timeout_ms}ms）：{selector}")


def _query_safe(page, selector: str):
    """query_selector 的容错版：导航瞬间 context 被销毁会抛异常，这里吞掉返回
    None。裸 query_selector 在页面跳转期会崩，用它做一次性登录态探测更稳。"""
    try:
        return page.query_selector(selector)
    except Exception:
        return None


_LOGIN_BOX = ".login-container, .login-modal, .login-box, .sso-login-wrapper"


def _community_login_surface_ready(page) -> bool:
    """已登录、明确错误，或二维码/登录框已经可见。可见不等于登录成功。"""
    if _login_error(page):
        return True
    try:
        if _is_logged_in(page):
            return True
    except Exception:
        pass
    try:
        qr = _visible_login_element(page, SELECTORS["qrcode"])
        if qr is not None:
            box = qr.bounding_box() or {}
            if (box.get("width") or 0) >= 96 and (box.get("height") or 0) >= 96:
                return True
    except Exception:
        pass
    try:
        return _visible_login_element(page, _LOGIN_BOX) is not None
    except Exception:
        return False


def _wait_community_login_surface(page) -> None:
    """先探测再等。未就绪最多 10×800ms，就绪则立刻交给原抓码流程。"""
    for _ in range(10):
        try:
            if _community_login_surface_ready(page):
                return
        except Exception:
            pass
        page.wait_for_timeout(800)


def _community_identity(context, timeout_s=6):
    from redbook_read import _SessionError, community_status_from_context
    try:
        return community_status_from_context(context, timeout_s=timeout_s)
    except _SessionError as error:
        messages = {
            'verification_required': '社区账号需要验证，已停止登录恢复。',
            'ip_blocked': '社区账号受到网络风险限制，已停止登录恢复。',
            'signature_error': '社区身份校验失败，已停止登录恢复。',
            'api_error': '社区身份接口返回错误，已停止登录恢复。',
            'timeout': '社区身份校验超时，登录状态未确认。',
        }
        stages = {
            'cookie_read': '读取登录状态', 'cookie_select': '检查登录状态',
            'node_resolve': '准备身份校验', 'node_exit': '请求身份接口',
            'json_parse': '解析身份响应', 'identity_shape': '检查身份字段',
        }
        message = messages.get(error.code, '社区身份校验异常，登录状态未确认。')
        stage = stages.get(error.stage, '身份校验')
        raise RuntimeError(f"{message}（阶段：{stage}；类别：{error.code}）") from None


def cmd_login(a) -> int:
    """headless 友好登录：把二维码抠成 PNG 供扫码，轮询登录成功后持久化 cookie。
    REF login.go FetchQrcodeImage/WaitForLogin。远程无桌面环境靠图片扫码，非有头窗口。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        _die(f"需要 playwright：{e}", 3)

    qr_out = Path(a.qr_out).expanduser() if a.qr_out else DEFAULT_QR_OUT
    timeout_s = a.timeout or 180
    community_only = getattr(a, "community_only", False)
    sf = getattr(a, "status_file", None)
    profile_base = (a.profile_base if a.profile_base is not None else _community_profile_base()) if community_only else a.profile_base
    login_state.write_status(sf, "starting", "正在启动…")
    lock = _ProfileLock(_profile_dir(profile_base))
    try:
        lock.acquire(90)
    except TimeoutError:
        login_state.write_status(sf, "error", "账号页正在校验小红书，登录目录被占用。请关闭弹窗，等 10 秒再点登录。")
        _die("小红书登录目录正被占用（多半是后台 whoami）", 1)

    try:
        with sync_playwright() as p:
            try:
                ctx = _launch(p, headed=a.headed, base=profile_base, proxy=_proxy(a.proxy, a.no_proxy))
            except Exception as e:
                login_state.write_status(sf, "error", "浏览器没能打开。请关闭弹窗，等 10 秒再点登录，不要连点。")
                _die(f"启动浏览器失败：{e}", 1)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                if community_only:
                    if _community_identity(ctx):
                        login_state.write_status(sf, "success", "社区账号已登录")
                        print("✅ 社区账号身份已确认，无需扫码")
                        return 0
                elif _creator_account(ctx) is not None:
                    login_state.write_status(sf, "success", "已登录")
                    print("✅ 已登录（创作平台账号接口已确认），无需扫码")
                    return 0
                if a.headed:
                    login_state.write_status(
                        sf, "window_login",
                        "请在弹出的浏览器窗口里扫码，不要关掉那个窗口。")
                explore_failed = False
                try:
                    page.goto(EXPLORE_URL, wait_until="commit", timeout=30000)
                except Exception as error:
                    explore_failed = True
                    if community_only:
                        raise RuntimeError("小红书社区首页无法打开，请检查网络后重试") from None
                    print(f"小红书首页无法打开（{type(error).__name__}），尝试官方创作平台入口",
                          file=sys.stderr)

                # 等待页面稳定并完成可能的跳转（登录引导 / 风险拦截 / 已登录态）
                if not explore_failed:
                    _wait_community_login_surface(page)

                if not community_only and _is_logged_in(page):
                    login_state.write_status(sf, "success", "已登录")
                    print("✅ 已登录（cookie 已在持久化目录），无需扫码")
                    return 0

                # 抠二维码存 PNG（元素截图，不依赖 src 格式，最稳）
                qr = False
                try:
                    qr = _capture_login_qr(page, qr_out, creator_only=explore_failed, **({"community_only": True} if community_only else {}))
                    if not qr and not community_only:
                        login_state.write_status(sf, "success", "已登录")
                        print("✅ 已登录（创作平台登录态已在持久化目录），无需扫码")
                        return 0
                except Exception as error:
                    if not a.headed:
                        raise
                    login_state.write_status(
                        sf, "window_login",
                        f"{error}。请在弹出的浏览器窗口里完成登录（扫码或短信），不要关掉那个窗口。")
                if qr:
                    if a.headed:
                        login_state.write_status(
                            sf, "window_login",
                            "请在弹出的浏览器窗口里扫码，不要关掉那个窗口。也可以扫下面这张图。",
                            qr=str(qr_out))
                    else:
                        login_state.write_status(sf, "qr_ready", "二维码已就绪，请扫码", qr=str(qr_out))
                    print(f"📱 二维码已保存：{qr_out}")
                    print("   用小红书 App 扫码登录。若走 Easel Web UI，可在 outputs 里查看这张图。")
                    print("   （二维码有时效，约几分钟；过期请重跑 login）")
                print(f"⏳ 等待扫码确认（最长 {timeout_s}s）...", file=sys.stderr)

                # 轮询登录成功（扫码成功瞬间页面会跳转，查询崩了是正常的，重试继续等）
                deadline = (time.monotonic() if community_only else time.time()) + timeout_s
                next_refresh = time.monotonic() + 5
                next_account_check = next_refresh
                scanned = False
                refreshes = 0
                while (time.monotonic() if community_only else time.time()) < deadline:
                    try:
                        logged_in = False if community_only else _is_logged_in(page)
                    except Exception as e:
                        err = str(e).lower()
                        if a.headed and ("target" in err or "closed" in err or "has been closed" in err):
                            login_state.write_status(sf, "error", "登录窗口被关闭，请关闭弹窗后重试")
                            _die(f"登录窗口已关闭：{e}", 1)
                        logged_in = False
                    reason = _login_error(page) if not logged_in else ""
                    state = _qr_state(page) if not logged_in else ""
                    if state == "scanned" and not scanned:
                        scanned = True
                        login_state.write_status(sf, "scanned", "扫码成功，请在小红书 App 中确认；正在等待登录完成")
                    if community_only and time.monotonic() >= next_account_check:
                        remaining = deadline - time.monotonic()
                        logged_in = remaining > 0 and _community_identity(ctx, timeout_s=min(6, remaining))
                        next_account_check = time.monotonic() + 10
                    if not community_only and not logged_in and (reason or (
                            scanned and time.monotonic() >= next_account_check)):
                        logged_in = _creator_account(ctx) is not None
                        next_account_check = time.monotonic() + 5
                    if logged_in:
                        login_state.write_status(sf, "success", "登录成功")
                        print("✅ 登录成功，cookie 已持久化，下次免登")
                        try:
                            qr_out.unlink()  # 登录成功清掉二维码图，避免误扫过期码
                        except OSError:
                            pass
                        return 0
                    if reason and not a.headed:
                        raise RuntimeError(reason)
                    if qr and not scanned and state == "expired":
                        if refreshes >= 3 or not _refresh_expired_qr(page, qr_out):
                            login_state.write_status(sf, "expired", "小红书二维码已失效，请关闭后重新获取二维码")
                            return 1
                        refreshes += 1
                        login_state.write_status(sf, "qr_ready", "二维码已刷新，请重新扫码", qr=str(qr_out))
                    if qr and time.monotonic() >= next_refresh:
                        if not scanned:
                            try:
                                login_qr.capture_qr(page, qr_out, SELECTORS["qrcode"], attempts=1,
                                                    minimum_size=96)
                            except RuntimeError:
                                pass
                        next_refresh = time.monotonic() + 5
                    page.wait_for_timeout(2000)
                if scanned:
                    login_state.write_status(
                        sf, "error",
                        "已扫码，但未能确认账号登录成功。请确认已在 App 中同意登录；平台维护或网络异常也可能导致校验超时。")
                    print("已扫码，但账号登录校验超时；请检查 App 确认状态、平台提示和服务器网络。",
                          file=sys.stderr)
                    return 1
                login_state.write_status(sf, "expired", "二维码超时未扫")
                print(f"⏱️ {timeout_s}s 内未检测到登录成功（二维码可能已过期）。请重跑 login 再扫。",
                      file=sys.stderr)
                return 1
            except Exception as error:
                login_state.write_status(sf, "error", f"登录异常：{error}")
                print(f"小红书登录异常：{error}", file=sys.stderr)
                return 1
            finally:
                ctx.close()
    finally:
        lock.release()


def _plan_lines(kind: str, title: str, content: str, media: list[str], tags: list[str]) -> list[str]:
    tab = "上传图文" if kind == "image" else "上传视频"
    tl = calc_title_length(title) if title else 0
    lines = [
        f"草稿类型：{kind}    编辑页：{PUBLISH_URL}",
        f"标题：{title}（长度 {tl}/{TITLE_MAX}{'  ⚠️超限' if tl > TITLE_MAX else ''}）",
        f"正文：{content[:40]}{'...' if len(content) > 40 else ''}",
        f"媒体：{media}",
        f"话题：{tags}",
        "步骤：",
        f"  1. goto {PUBLISH_URL} → WaitLoad+DOMStable",
        f"  2. 点 tab「{tab}」（重试+遮挡检测）",
        f"  3. {'逐图上传等预览(≤60s/张)' if kind == 'image' else '上传视频等处理(≤10min)'}",
        "  4. 输标题/正文（逐字符）+ 话题联想点选",
        "  5. 平台 DOM 长度校验",
        "  6. 唯一可见且可用的「暂存离开/存草稿/保存草稿」按钮 → 点击",
        "  7. 成功校验：平台明确提示草稿保存成功；缺失即失败，不公开发布",
    ]
    return lines


def cmd_plan(a) -> int:
    kind = "video" if a.video else "image"
    media = [str(Path(a.video).expanduser())] if a.video else (
        [s.strip() for s in (a.images or "").split(",") if s.strip()])
    tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()]
    for ln in _plan_lines(kind, a.title or "<title>", a.content or "<content>", media, tags):
        print(ln)
    return 0


def _publish_impl(a, kind: str, attempt) -> int:
    if not a.title:
        _die("--title 必填")
    if calc_title_length(a.title) > TITLE_MAX:
        _die(f"标题过长（{calc_title_length(a.title)}/{TITLE_MAX}），请精简")
    if kind == "image":
        media = _abspaths(a.images)
        if not media:
            _die("--images 至少一张图片")
    else:
        if not a.video:
            _die("--video 必填")
        media = [_abspaths(a.video)[0]]
    tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()]

    # 出站内容安全闸门：真发前扫描标题/正文/话题，检出 API key/内部 URL/代理 IP/模型名/
    # "由 AI 生成" 等内部设置泄露即阻止发布（dry-run 只告警）。--allow-unsafe 手动放行。
    content_guard.guard_or_die([a.title, a.content, " ".join(tags)],
                               exec_mode=bool(a.exec),
                               allow_unsafe=getattr(a, "allow_unsafe", False),
                               label="小红书草稿内容")

    if not a.exec:
        print("dry-run（加 --exec 仅保存平台草稿，不公开发布）：\n")
        for ln in _plan_lines(kind, a.title, a.content or "", media, tags):
            print(ln)
        return 0

    attempt['stage'] = 'runtime'
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except Exception as e:
        attempt['primary_error'] = e
        _die('需要 Playwright 运行环境，无法启动草稿流程', 3)
    attempt['stage'] = 'profile_lock'
    lock = _ProfileLock(_profile_dir(a.profile_base))
    try:
        lock.acquire(90)
    except TimeoutError as error:
        attempt['primary_error'] = error
        _die("小红书登录目录正被占用，请稍后再保存草稿")
    try:
        attempt['stage'] = 'launch'
        with sync_playwright() as p:
            try:
                ctx = _launch(p, headed=a.headed, base=a.profile_base, proxy=_proxy(a.proxy, a.no_proxy))
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                attempt['page'] = page
                page.set_default_timeout(300000)
            except (Exception, SystemExit) as error:
                attempt.setdefault('primary_error', error)
                raise
            try:
                attempt['stage'] = 'navigation'
                try:
                    page.goto(PUBLISH_URL, wait_until="domcontentloaded")
                except Exception as error:
                    _stop_publish_auth(page, login_ok=None, stage='publish_navigation', error=error, attempt=attempt)
                page.wait_for_timeout(1500)
                attempt['stage'] = 'auth_probe'
                # Keep failed probe evidence local: unreadable DOM is not an
                # authenticated negative, even if a later login wall is visible.
                logged = False
                probe_error = None
                for _ in range(4):
                    try:
                        if page.query_selector(SELECTORS["login_ok"]) is not None:
                            logged = True
                            break
                    except Exception as error:
                        probe_error = error
                    page.wait_for_timeout(500)
                if not logged and "login" in page.url.lower():
                    _stop_publish_auth(page, login_ok=logged, error=probe_error, attempt=attempt)
                attempt['stage'] = 'tab'
                if kind == "image":
                    _click_publish_tab(page, "上传图文")
                    page.wait_for_timeout(1000)
                    attempt['stage'] = 'upload'
                    _upload_images(page, media)
                else:
                    _click_publish_tab(page, "上传视频")
                    page.wait_for_timeout(1000)
                    attempt['stage'] = 'upload'
                    _upload_video(page, media[0])
                attempt['stage'] = 'fill'
                _fill_and_save_draft(page, a.title, a.content or "", tags, attempt=attempt)
            except PWTimeout as e:
                attempt['primary_error'] = e
                _die('小红书草稿步骤超时；请先核对平台草稿，勿重复提交')
            except (Exception, SystemExit) as error:
                attempt.setdefault('primary_error', error)
                raise
            finally:
                if not a.keep_open:
                    try:
                        ctx.close()
                    except Exception as error:
                        _publish_cleanup_error(attempt, error)
    finally:
        try:
            lock.release()
        except Exception as error:
            _publish_cleanup_error(attempt, error)
    # 草稿不是已发布内容，绝不调用 calendar_ops.record_publish。
    print(json.dumps({"outcome": "draft_saved", "published": False}, ensure_ascii=False))
    return 0


def _publish(a, kind: str) -> int:
    attempt = {'stage': 'prepare', 'action_sent': False, 'confirmation_sent': False}
    try:
        return _publish_impl(a, kind, attempt)
    except (Exception, SystemExit) as error:
        primary = attempt.get('primary_error', error)
        if primary is not error and not isinstance(error, SystemExit):
            _publish_cleanup_error(attempt, error)
        if attempt.get('receipt_confirmed'):
            _publish_cleanup_error(attempt, error)
            print(json.dumps({'outcome': 'draft_saved', 'published': False}))
            return 0
        if not attempt.get('terminal_reported'):
            _publish_failure(attempt, primary)
        if isinstance(primary, SystemExit):
            raise primary
        if isinstance(error, SystemExit):
            raise
        _die('小红书草稿步骤失败；请先核对平台草稿，勿重复提交')


def cmd_publish(a) -> int:
    return _publish(a, "image")


def cmd_publish_video(a) -> int:
    return _publish(a, "video")


def cmd_whoami(a) -> int:
    """真校验登录态 + 读账号昵称/头像，输出单行 JSON（供 Web 后端解析）。
    xhs 须直连（--no-proxy），走代理会被判风险。抽不到昵称时 name 空但 loggedIn 仍准。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        result = {"loggedIn": False, "name": "", "avatar": ""}
        _whoami_failure(result, 'whoami_import', e, 'XHS_RUNTIME_UNAVAILABLE')
        print(json.dumps(result))
        return 0
    result = {"loggedIn": False, "name": "", "avatar": ""}
    lock = _ProfileLock(_profile_dir(a.profile_base))
    try:
        lock.acquire(4)
    except TimeoutError as error:
        _whoami_failure(result, 'whoami_profile_lock', error, 'XHS_PROFILE_BUSY')
        print(json.dumps(result, ensure_ascii=False))
        return 0
    stage = 'whoami_launch'
    try:
        with sync_playwright() as p:
            ctx = _launch(p, headed=False, base=a.profile_base, proxy=_proxy(a.proxy, a.no_proxy))
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                stage = 'whoami_account'
                account = _authenticated_account(ctx)
                if account is not None:
                    result["loggedIn"] = True
                    if not all(_account_identity(account).values()):
                        account = _self_profile_account(ctx, account)
                    result.update(_account_identity(account))
                    if not all((result["name"], result["avatar"])):
                        try:
                            fallback = _page_account_identity(page, account.get("userId"))
                            for field in ("name", "avatar"):
                                if not result[field]:
                                    result[field] = fallback[field]
                        except Exception:
                            pass
                else:
                    stage = 'whoami_page'
                    logged = _check_account_login(page)
                    result["loggedIn"] = logged
                    if logged:
                        result.update(_page_account_identity(page))
            finally:
                if sys.exc_info()[0] is None:
                    stage = 'whoami_close'
                    ctx.close()
                else:
                    try:
                        ctx.close()
                    except Exception:
                        pass
    except Exception as e:  # noqa: BLE001 — inconclusive diagnostics must not become logout
        _whoami_failure(result, stage, e)
    finally:
        lock.release()
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_selftest(_a) -> int:
    print("xhs_publish 自检（离线）...", file=sys.stderr)
    # 标题长度算法（REF title.go）
    assert calc_title_length("") == 0
    assert calc_title_length("abcd") == 2, "4 ASCII → (4+1)//2 = 2"
    assert calc_title_length("你好") == 2, "2 全角 → 4 字节 → 2"
    assert calc_title_length("你" * 20) == 20, "20 全角 → 20（上限）"
    assert calc_title_length("a") == 1
    # 选择器字典完整
    need = ["login_ok", "qrcode", "creator_tab", "upload_input_first", "img_preview",
            "title_input", "content_quill", "topic_item", "publish_btn_new", "publish_btn_old"]
    for k in need:
        assert k in SELECTORS and SELECTORS[k], f"缺选择器 {k}"
    # 路径解析
    try:
        _abspaths("/no/such/file_xyz.jpg")
        raise AssertionError("不存在文件未报错")
    except SystemExit:
        pass
    # profile 目录路由
    assert _profile_dir(None).name == PROFILE_NAME
    assert "cloak-research-profile" not in str(_profile_dir(None))
    cloak = _cloak_executable()
    assert cloak is None or cloak.is_file()
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="easel-xhs-lock-"))
    try:
        held = _ProfileLock(tmp)
        held.acquire(1)
        blocked = _ProfileLock(tmp)
        try:
            blocked.acquire(0.3)
            raise AssertionError("第二把锁应失败")
        except TimeoutError:
            pass
        finally:
            blocked.release()
        held.release()
        held.acquire(1)
        held.release()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 代理逻辑
    assert _proxy(None, True) is None, "--no-proxy 应禁用"
    assert _proxy("http://x:1", False) == "http://x:1", "显式代理优先"
    # plan 渲染
    lines = _plan_lines("image", "标题", "正文", ["/a.jpg"], ["#tag"])
    assert any("上传图文" in ln for ln in lines) and any("成功校验" in ln for ln in lines)
    lines_v = _plan_lines("video", "t", "c", ["/a.mp4"], [])
    assert any("上传视频" in ln for ln in lines_v)
    print("✅ selftest 通过（标题算法 + 选择器字典 + 路径/代理/路由 + plan 渲染）")
    return 0


def main() -> int:
    login_state.configure_stdio()
    ap = argparse.ArgumentParser(
        description="小红书平台草稿保存（Playwright；执行一律仅保存草稿，不公开发布）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    def add_common(p):
        p.add_argument("--profile-base", help="登录态根目录（默认 ~/.easel-browser-profiles）")
        p.add_argument("--proxy", help="外网代理（默认取 env，小红书是外网需代理）")
        p.add_argument("--no-proxy", action="store_true", help="禁用代理")

    def add_content(p):
        p.add_argument("--title", help="标题（≤20 全角）")
        p.add_argument("--content", help="正文")
        p.add_argument("--images", help="图片路径，逗号分隔（图文草稿）")
        p.add_argument("--video", help="视频路径（视频草稿）")
        p.add_argument("--tags", help="话题，逗号分隔（如 'AI,教程'）")
        p.add_argument("--exec", action="store_true", help="实际保存平台草稿（默认 dry-run，不公开发布）")
        p.add_argument("--draft", action="store_true", default=True, help="显式草稿模式（省略也仅保存草稿）")
        p.add_argument("--allow-unsafe", action="store_true",
                       help="放行草稿内容安全闸门（检出内部设置泄露仍保存，谨慎）")
        p.add_argument("--headed", action="store_true", help="有头模式（首次校验选择器用）")
        p.add_argument("--keep-open", action="store_true", help="草稿保存后不关浏览器")

    sub.add_parser("check", help="检查 playwright/内核").set_defaults(func=cmd_check)

    p = sub.add_parser("login", help="扫码登录并持久化（headless：抠二维码成图片）")
    add_common(p)
    p.add_argument("--community-only", action="store_true", help="仅确认社区账号登录态")
    p.add_argument("--qr-out", help=f"二维码图片输出路径（默认 {DEFAULT_QR_OUT}）")
    p.add_argument("--status-file", help="登录状态 JSON 输出路径（供 Web 后端轮询）")
    p.add_argument("--timeout", type=int, help="等待扫码超时秒数（默认 180）")
    p.add_argument("--headed", action="store_true", help="有头模式（本地有桌面时可窗口内扫）")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("plan", help="草稿保存步骤预览（离线）")
    add_content(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("publish", help="图文保存平台草稿")
    add_common(p); add_content(p)
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("publish-video", help="视频保存平台草稿")
    add_common(p); add_content(p)
    p.set_defaults(func=cmd_publish_video)

    p = sub.add_parser("whoami", help="真校验登录态 + 读昵称/头像（输出 JSON）")
    add_common(p)
    p.set_defaults(func=cmd_whoami)

    sub.add_parser("selftest", help="离线自检").set_defaults(func=cmd_selftest)

    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return 1
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
