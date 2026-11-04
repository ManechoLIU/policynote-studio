"""热点雷达回归测试：各平台响应格式归一化 + 取不到数据要能被识别出来。

背景（2026-10-01）：热点雷达六个平台里三个是空的——B站其实是解析器的锅（备用源返回 50 条
纯字符串数组，而解析器只认对象数组，全被丢掉），知乎/头条则是压根没有备用源且主源被限流。
这个文件守住两件事：各家格式都解析得出来、以及"取不到"要和"真的没数据"分开。
"""
from __future__ import annotations

import asyncio
import json
import ssl
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "web"))

import app as web  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_trend_cache(monkeypatch):
    monkeypatch.setattr(web, "_TREND_CACHE", {})


# ---- 各家响应格式 ----

def test_parse_hot_handles_plain_string_array():
    """xxapi 的 B站热榜：data 就是一串标题字符串，没有对象包着。"""
    items = web._parse_hot({"code": 200, "data": ["凉哈皮谈首登亚运解说台", "   ", "holymoly亚运金牌"]})
    assert [i["title"] for i in items] == ["凉哈皮谈首登亚运解说台", "holymoly亚运金牌"]
    assert all(i["hot"] == "" and i["url"] == "" for i in items)


def test_parse_hot_handles_capitalized_fields():
    """头条官方热榜：Title / HotValue / Url 全是大写开头，热度还会是数字。"""
    items = web._parse_hot({"data": [
        {"ClusterId": 1, "Title": "农民交公粮能否视同缴社保", "HotValue": 1234567,
         "Url": "https://www.toutiao.com/trending/1/"},
    ]})
    assert items == [{"title": "农民交公粮能否视同缴社保", "hot": "1234567",
                      "url": "https://www.toutiao.com/trending/1/"}]


def test_parse_hot_handles_nested_target_and_rewrites_zhihu_url():
    """知乎官方热榜：正文在 target 里，链接给的是 api.zhihu.com，要点开得换成网页地址。"""
    items = web._parse_hot({"data": [
        {"type": "hot_list_feed", "card_id": "Q_1",
         "target": {"id": 2088649572151194406, "title": "迪拜航空客机返航",
                    "url": "https://api.zhihu.com/questions/2088649572151194406",
                    "metrics_area": {"text": "387 万热度"}}},
    ]})
    assert items == [{"title": "迪拜航空客机返航", "hot": "387 万热度",
                      "url": "https://www.zhihu.com/question/2088649572151194406"}]


def test_parse_hot_keeps_original_shape_working():
    """60s 那套老格式（data 直接是对象数组）不能被新写法改坏。"""
    items = web._parse_hot({"data": [{"title": "迪拜航空确认航班发生事故", "hot": "118万",
                                      "url": "https://s.weibo.com/x"}]})
    assert items == [{"title": "迪拜航空确认航班发生事故", "hot": "118万", "url": "https://s.weibo.com/x"}]


def test_parse_hot_accepts_nested_data_wrapper():
    items = web._parse_hot({"data": {"list": [{"word": "热词", "num": 42}]}})
    assert items == [{"title": "热词", "hot": "42", "url": ""}]


@pytest.mark.parametrize("bad", [
    {}, {"data": None}, {"data": "不是列表"}, {"data": [1, 2, 3]}, {"data": [{"hot": "1"}]}, [],
])
def test_parse_hot_returns_empty_on_junk(bad):
    """垃圾/空响应要安静地返回空列表，不能抛异常（抛了整条平台就断）。"""
    assert web._parse_hot(bad) == []


def test_trend_web_url_passes_through_other_urls():
    assert web._trend_web_url("") == ""
    assert web._trend_web_url("https://s.weibo.com/x") == "https://s.weibo.com/x"
    assert web._trend_web_url("https://api.zhihu.com/questions/abc") == "https://api.zhihu.com/questions/abc"


# ---- 源表本身 ----

def test_every_platform_has_a_source():
    """每个能选中的平台都要有主源，别出现"按钮在但没人给它取数据"。"""
    assert set(web.TREND_SOURCES) == set(web.TREND_LABELS)
    for pf, (primary, backup) in web.TREND_SOURCES.items():
        assert primary and primary.startswith("http"), f"{pf} 缺主源"
        if backup:
            assert backup.startswith("http")


# ---- 接口：取不到 vs 真的没有 ----

def test_trends_marks_unavailable_platform(monkeypatch):
    """/api/trends 要给每个平台带 ok 标：空列表＝这个平台取不到，不是"今天没热搜"。"""
    web._TREND_CACHE.clear()
    monkeypatch.setattr(web, "_fetch_platform",
                        lambda pf: [{"title": f"{pf} 的头条", "hot": "", "url": ""}] if pf == "weibo" else [])
    data = asyncio.run(web.api_trends("weibo,bilibili", 15))
    by_pf = {g["platform"]: g for g in data["trends"]}
    assert by_pf["weibo"]["ok"] is True and len(by_pf["weibo"]["items"]) == 1
    assert by_pf["bilibili"]["ok"] is False and by_pf["bilibili"]["items"] == []


def test_trends_ignores_unknown_platform(monkeypatch):
    """乱传平台名不能把接口带崩，也不能凭空多出渠道。"""
    web._TREND_CACHE.clear()
    monkeypatch.setattr(web, "_fetch_platform", lambda pf: [])   # 测试不打真实网络
    data = asyncio.run(web.api_trends("weibo,不存在", 5))
    assert [g["platform"] for g in data["trends"]] == ["weibo"]
    assert [g["label"] for g in data["trends"]] == ["微博"]


@pytest.mark.parametrize("primary_result", [None, {"data": []}, {"data": "invalid"}])
def test_fetch_platform_falls_back_on_error_or_empty_response(monkeypatch, primary_result):
    primary, backup = web.TREND_SOURCES["weibo"]
    calls = []

    def fetch(url):
        calls.append(url)
        if url == primary:
            if primary_result is None:
                raise OSError("unavailable")
            return primary_result
        return {"data": [{"title": "备用热点"}]}

    monkeypatch.setattr(web, "_http_get_json", fetch)
    assert web._fetch_platform("weibo") == [{"title": "备用热点", "hot": "", "url": ""}]
    assert calls == [primary, backup]


def test_trends_reuses_cached_items_when_all_sources_fail(monkeypatch):
    items = [{"title": "缓存热点", "hot": "", "url": ""}]
    web._TREND_CACHE["weibo"] = (0, items)
    monkeypatch.setattr(web, "_fetch_platform", lambda platform: [])
    data = asyncio.run(web.api_trends("weibo", 15))
    assert data["trends"][0]["items"] == items
    assert data["trends"][0]["ok"] is True
    assert data["trends"][0]["stale"] is True
    assert data["trends"][0]["error"] == "源响应异常"
    assert data["trends"][0]["updated_at"] == 0
    assert data["updated"] == 0


def test_fetch_stops_after_primary_hits(monkeypatch):
    primary, _backup = web.TREND_SOURCES["douyin"]
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": [{"title": "主源"}]}

    monkeypatch.setattr(web, "_http_get_json", fetch)
    assert web._fetch_platform("douyin")[0]["title"] == "主源"
    assert calls == [primary]


def test_http_get_json_uses_certifi_and_keeps_tls_checks(monkeypatch):
    seen = {}

    class Resp:
        def read(self):
            return b'{"data":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(req, timeout=None, context=None):
        seen["timeout"] = timeout
        seen["context"] = context
        return Resp()

    monkeypatch.setattr(web.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(web, "_TREND_SSL", None)
    assert web._http_get_json("https://example.test/hot") == {"data": []}
    ctx = seen["context"]
    assert seen["timeout"] == 8
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.verify_mode != ssl.CERT_NONE
    assert len(ctx.get_ca_certs()) > 0


def test_ssl_context_appends_certifi_bundle(monkeypatch):
    calls = []
    real = ssl.SSLContext.load_verify_locations

    def spy(self, cafile=None, capath=None, cadata=None):
        calls.append(cafile)
        return real(self, cafile=cafile, capath=capath, cadata=cadata)

    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", spy)
    monkeypatch.setattr(web, "_TREND_SSL", None)
    ctx = web._trend_ssl_context()
    assert any(isinstance(ca, str) and ca.endswith("cacert.pem") for ca in calls)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


@pytest.mark.parametrize("exc,kind", [
    (ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"), "证书校验失败"),
    (urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate")), "证书校验失败"),
    (urllib.error.URLError(TimeoutError("timed out")), "网络异常"),
    (urllib.error.URLError("CERTIFICATE_VERIFY_FAILED"), "证书校验失败"),
    (urllib.error.HTTPError("https://secret.example/hot", 502, "bad", {}, None), "源响应异常"),
    (json.JSONDecodeError("expecting value", "<html>", 0), "源响应异常"),
])
def test_source_errors_are_typed_and_do_not_leak(exc, kind):
    assert web._classify_trend_error(exc) == kind
    assert "http" not in web._classify_trend_error(exc)
    assert "secret" not in web._classify_trend_error(exc)


def test_certificate_failure_is_visible_without_cache(monkeypatch):
    def boom(_url):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED unable to get local issuer"))

    monkeypatch.setattr(web, "_http_get_json", boom)
    data = asyncio.run(web.api_trends("weibo", 12))
    row = data["trends"][0]
    assert row["ok"] is False and row["items"] == []
    assert row["stale"] is False
    assert row["error"] == "证书校验失败"
    assert row["updated_at"] is None
    assert data["updated"] == 0
    assert "http" not in row["error"] and "issuer" not in row["error"]


def test_primary_certificate_error_still_uses_backup(monkeypatch):
    primary, backup = web.TREND_SOURCES["weibo"]

    def fetch(url):
        if url == primary:
            raise urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
        return {"data": [{"title": "备用热点", "hot": "1"}]}

    monkeypatch.setattr(web, "_http_get_json", fetch)
    data = asyncio.run(web.api_trends("weibo", 12))
    row = data["trends"][0]
    assert row["ok"] is True and row["items"][0]["title"] == "备用热点"
    assert row["stale"] is False and row["error"] is None
    assert data["updated"] == row["updated_at"]


def test_network_failure_prefers_certificate_when_both_fail(monkeypatch):
    primary, backup = web.TREND_SOURCES["zhihu"]

    def fetch(url):
        if url == primary:
            raise urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(web, "_http_get_json", fetch)
    data = asyncio.run(web.api_trends("zhihu", 12))
    assert data["trends"][0]["error"] == "证书校验失败"
    assert data["updated"] == 0


def test_fresh_cache_skips_network_and_is_not_stale(monkeypatch):
    ts = time.time() - 10
    items = [{"title": "新缓存", "hot": "1", "url": ""}]
    web._TREND_CACHE["weibo"] = (ts, items)

    def boom(_url):
        raise AssertionError("fresh cache must not fetch")

    monkeypatch.setattr(web, "_http_get_json", boom)
    before = int(time.time())
    data = asyncio.run(web.api_trends("weibo", 15))
    row = data["trends"][0]
    assert row["items"] == items and row["ok"] is True
    assert row["stale"] is False and row["error"] is None
    assert row["updated_at"] == int(ts)
    assert data["updated"] == int(ts)
    assert data["updated"] < before


def test_platforms_fetch_concurrently(monkeypatch):
    state = {"n": 0, "overlap": False}
    lock = threading.Lock()

    def fetch(_url):
        with lock:
            state["n"] += 1
            if state["n"] >= 2:
                state["overlap"] = True
        time.sleep(0.25)
        with lock:
            state["n"] -= 1
        return {"data": [{"title": "并发热点"}]}

    monkeypatch.setattr(web, "_http_get_json", fetch)
    started = time.perf_counter()
    data = asyncio.run(web.api_trends("weibo,douyin", 5))
    elapsed = time.perf_counter() - started
    assert state["overlap"] is True
    assert elapsed < 0.45
    assert [g["platform"] for g in data["trends"]] == ["weibo", "douyin"]
    assert all(g["ok"] and g["error"] is None for g in data["trends"])


def test_limit_clamp_and_cache_keeps_full_list(monkeypatch):
    monkeypatch.setattr(
        web, "_fetch_platform",
        lambda pf: [{"title": str(i), "hot": "", "url": ""} for i in range(40)],
    )
    wide = asyncio.run(web.api_trends("weibo", 100))
    assert len(wide["trends"][0]["items"]) == 30
    narrow = asyncio.run(web.api_trends("weibo", 0))
    assert len(narrow["trends"][0]["items"]) == 1
    assert narrow["trends"][0]["stale"] is False
