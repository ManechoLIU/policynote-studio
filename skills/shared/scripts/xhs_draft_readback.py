#!/usr/bin/env python3
"""只读核对创作者笔记管理页上是否可见某一精确标题。

不点击、不输入、不读取 cookie / storage / 响应体。标题只在本地与卡片标题比较。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from urllib.parse import urlsplit

from xhs_publish import _ProfileLock, _launch, _profile_dir

NOTE_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"
TOTAL_BUDGET_S = 30.0
LOCK_TIMEOUT_S = 1.0
LAUNCH_TIMEOUT_MS = 8000
OUTPUT_LIMIT = 2 * 1024 * 1024
_HOSTS = {"creator.xiaohongshu.com", "www.xiaohongshu.com"}
_NOTE_ID = re.compile(r"^[A-Za-z0-9]{8,32}$")
_PATH_ID = re.compile(r"/(?:explore|item|note)/([A-Za-z0-9]{8,32})/?$")
_WHEN = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?")
_DRAFT = {"草稿", "未发布", "待发布"}

_READ_JS = r"""() => {
  const visible = (e) => {
    if (!(e && e.getClientRects && e.getClientRects().length)) return false;
    for (let n = e; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (n.hidden || s.display === 'none' || s.visibility !== 'visible' || Number(s.opacity) === 0) return false;
    }
    return true;
  };
  const cards = [];
  document.querySelectorAll('.note-card').forEach((c) => {
    if (!visible(c)) return;
    const titleEl = c.querySelector('.note-card__title');
    if (!visible(titleEl)) return;
    const title = titleEl ? String(titleEl.textContent || '').trim() : '';
    const text = String(c.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 400);
    const statusMarkers = [...c.querySelectorAll('.note-card__status, .note-card__state, [role="status"], [data-status]')]
      .filter(e => visible(e) && !titleEl.contains(e))
      .map(e => String(e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim())
      .filter(text => ['草稿', '未发布', '待发布', '已发布'].includes(text));
    let href = '';
    const a = c.querySelector('a[href]');
    if (a) href = String(a.href || a.getAttribute('href') || '');
    let noteId = '';
    try {
      const raw = JSON.parse(c.getAttribute('data-impression') || '{}');
      const value = raw && raw.noteTarget && raw.noteTarget.value;
      noteId = value && value.noteId ? String(value.noteId) : '';
    } catch (e) { noteId = ''; }
    cards.push({ title, text, href, noteId, statusMarkers });
  });
  const bodyHint = String((document.body && document.body.innerText) || '').slice(0, 1500);
  return { cards, bodyHint };
}"""


def sync_playwright():
    from playwright.sync_api import sync_playwright as open_playwright
    return open_playwright()


def _emit(payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > OUTPUT_LIMIT:
        raw = json.dumps({
            "error_type": "output_limit",
            "visible_page_examined": False,
            "absence_conclusive": False,
            "matches": [],
            "page_state": "unknown",
        }, ensure_ascii=False, separators=(",", ":"))
    print(raw)


def _base_result(**extra) -> dict:
    result = {
        "error_type": None,
        "visible_page_examined": False,
        "absence_conclusive": False,
        "page_state": "unknown",
        "matches": [],
    }
    result.update(extra)
    result["absence_conclusive"] = False
    return result


def _valid_title(title: str) -> bool:
    if not isinstance(title, str) or not 1 <= len(title) <= 100:
        return False
    if title.strip() == "":
        return False
    return all(32 <= ord(ch) != 127 for ch in title)


def _clean_href(href: str) -> str | None:
    if not href or not href.startswith("https://"):
        return None
    parts = urlsplit(href)
    if parts.netloc not in _HOSTS or not parts.path.startswith("/"):
        return None
    return f"https://{parts.netloc}{parts.path}"


def _note_id(raw: str, href: str) -> str | None:
    if isinstance(raw, str) and _NOTE_ID.fullmatch(raw):
        return raw
    path = urlsplit(href).path if href else ""
    found = _PATH_ID.search(path or "")
    return found.group(1) if found else None


def _draft_status(markers) -> str:
    if not isinstance(markers, list):
        return 'unknown'
    labels = {mark for mark in markers if isinstance(mark, str)}
    if len(labels) == 1 and next(iter(labels)) in _DRAFT:
        return "draft"
    return "unknown"


def _when(text: str) -> str | None:
    found = _WHEN.findall(text or "")
    if len(found) == 1:
        return found[0]
    return None


def _match_cards(title: str, cards: list) -> list:
    matches = []
    if not isinstance(cards, list):
        return matches
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_title = card.get("title")
        if card_title != title:
            continue
        text = card.get("text") if isinstance(card.get("text"), str) else ""
        href_raw = card.get("href") if isinstance(card.get("href"), str) else ""
        matches.append({
            "title": card_title,
            "draft_status": _draft_status(card.get('statusMarkers')),
            "published_at_text": _when(text),
            "href": _clean_href(href_raw),
            "note_id": _note_id(card.get("noteId") or "", href_raw),
        })
    return matches


def _page_state(cards: list, body: str, url: str) -> str:
    if _login_url(url):
        return "login"
    if cards:
        return "cards"
    hint = ' '.join(body.split())
    if hint in ("扫码登录", "手机号登录"):
        return "login"
    if any(mark in body for mark in ("加载中", "正在加载")):
        return "loading"
    if hint in ("暂无笔记", "暂无内容", "还没有笔记", "没有笔记"):
        return "empty"
    return "unknown"


def _login_url(url: str) -> bool:
    parts = urlsplit(url or "")
    path = (parts.path or "") + " "
    return "passport" in (parts.netloc or "") or "/login" in path


def _examine(page, title: str) -> dict:
    url = str(getattr(page, "url", "") or "")
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'creator.xiaohongshu.com'
            or (parts.path.rstrip('/') != '/new/note-manager' and not _login_url(url))):
        return _base_result(error_type='unexpected_location')
    snapshot = page.evaluate(_READ_JS)
    if not isinstance(snapshot, dict):
        snapshot = {}
    cards = snapshot.get("cards") if isinstance(snapshot.get("cards"), list) else []
    body = snapshot.get("bodyHint") if isinstance(snapshot.get("bodyHint"), str) else ""
    state = _page_state(cards, body, url)
    examined = _base_result(visible_page_examined=True, page_state=state)
    if state == "login":
        examined["error_type"] = "auth_required"
        return examined
    examined["matches"] = _match_cards(title, cards)
    return examined


def _wait_for_page(page, title: str, deadline: float) -> dict:
    result = _base_result()
    while True:
        if time.monotonic() >= deadline:
            result['error_type'] = 'timeout'
            return result
        try:
            result = _examine(page, title)
        except Exception:
            result = _base_result()
        if result['page_state'] in ('cards', 'empty', 'login') or result['error_type'] == 'unexpected_location':
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result['error_type'] = 'timeout'
            return result
        time.sleep(min(0.2, remaining))


def readback(title: str) -> dict:
    deadline = time.monotonic() + TOTAL_BUDGET_S
    lock = _ProfileLock(_profile_dir(None))
    context = None
    result = None
    try:
        try:
            lock.acquire(LOCK_TIMEOUT_S)
        except TimeoutError:
            return _base_result(error_type="lock_timeout")
        if time.monotonic() >= deadline:
            return _base_result(error_type="timeout")
        with sync_playwright() as playwright:
            try:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    result = _base_result(error_type='timeout')
                else:
                    context = _launch(
                        playwright, False, None, None, login_assets=False,
                        timeout_ms=min(LAUNCH_TIMEOUT_MS, remaining_ms), retry_target_closed=False)
                    page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
                    remaining_ms = int((deadline - time.monotonic()) * 1000)
                    goto_timeout = min(LAUNCH_TIMEOUT_MS, remaining_ms)
                    if goto_timeout < 500:
                        result = _base_result(error_type='timeout')
                    else:
                        page.goto(NOTE_MANAGER_URL, timeout=goto_timeout, wait_until='domcontentloaded')
                        result = _wait_for_page(page, title, deadline)
            except Exception as error:
                result = _base_result(error_type='timeout' if isinstance(error, TimeoutError)
                                      or type(error).__name__ == 'TimeoutError' else 'navigation_failed')
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
    except Exception:
        if result is None:
            result = _base_result(error_type="navigation_failed")
    finally:
        try:
            lock.release()
        except Exception:
            pass
    return result if result is not None else _base_result(error_type='navigation_failed')


def execute(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description="只读核对小红书创作者笔记管理页中的精确标题")
    parser.add_argument("--title", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.json or not _valid_title(args.title):
        _emit(_base_result(error_type="invalid_title"))
        return 2
    _emit(readback(args.title))
    return 0


def main(argv: list[str] | None = None) -> None:
    try:
        code = execute(argv)
    except SystemExit:
        raise
    except Exception:
        _emit(_base_result(error_type="internal"))
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
