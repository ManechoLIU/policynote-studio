"""Official insurance feed: fixed HTML contracts, dates and failure semantics."""
from __future__ import annotations

import asyncio
from datetime import date
import importlib
import ssl
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def module():
    return importlib.import_module('easel.insurance_trends')


ASSOCIATION = '''<ul>
<li><span>11-04</span><a title="中国保险行业协会人身保险利率研究" href="/art/2026/11/4/art_22_109376.html"><b>截断标题</b>...</a><a href="/art/2026/11/4/art_22_109376.html">[详情]</a></li>
<li><a href="/art/2026/10/28/art_22_109363.html#top">保险团体标准工作</a></li>
<li><a href="https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1277195&amp;itemId=915&amp;generaltype=0"><b>保险监管新规</b></a><span>2026-10-30</span></li>
<li><span>10-23</span><a href="https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1275881&amp;itemId=915">保险业启动救灾应急响应</a></li>
<li><a href="https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1277248&amp;itemId=915" title="金融监管总局党委传达学习全会精神">政治会议</a></li>
</ul>'''
MEDICAL = '''<div class="content-news-item"><div><a href="/ybdt/20261103/002014d9.html" title="各区动态 | 医保基金管理破解老年就医难题">截断</a></div><div class="content-news-date">2026-11-02</div></div>
<ul><li><a href="/qtwj/20261012/f0e952.html">调整医疗机构医保医用耗材目录通知</a></li>
<li><a href="/ybdt/20261030/8b299.html"><em>护理院虚构医药服务骗保案</em></a></li>
<li><a href="/szyw/20261103/abc.html">医保政治新闻</a></li>
<li><a href="/ybdt/20261103/ddd.html">市医保局党组传达学习贯彻全会精神</a></li>
<li><a href="/ybdt/index.html">医保动态</a></li></ul>'''


def test_module_is_available():
    assert callable(module().insurance_trends)


def test_association_full_titles_nested_text_dates_and_groups():
    parsed = module()._parse_association(ASSOCIATION, date(2026, 11, 4))
    assert [x['title'] for x in parsed['insurance']] == ['中国保险行业协会人身保险利率研究', '保险团体标准工作']
    assert parsed['insurance'][0]['published_at'] == '2026-11-04'
    assert parsed['insurance'][0]['source'] == '中国保险行业协会'
    assert parsed['insurance'][1]['url'].endswith('art_22_109363.html')
    assert [x['published_at'] for x in parsed['regulation']] == ['2026-10-30', '']
    assert all(x['source'] == '国家金融监督管理总局' and x['hot'] == '' for x in parsed['regulation'])


def test_medical_uses_displayed_date_before_url_date_and_retains_local_source():
    items = module()._parse_medical(MEDICAL, date(2026, 11, 4))['medical']
    assert len(items) == 3
    assert [x['published_at'] for x in items] == ['2026-11-02', '2026-10-30', '2026-10-12']
    assert {x['source'] for x in items} == {'上海市医保局'}


def test_rejects_unsafe_links_scripts_future_and_old_articles():
    html = '''<script><a href="/art/2026/11/4/art_22_1.html">保险伪造脚本</a></script>
    <a href="javascript:alert(1)" title="保险脚本">x</a>
    <a href="https://evil.example/art/2026/11/4/art_22_2.html">保险外站</a>
    <a href="https://www.iachina.cn@evil.example/art/2026/11/4/art_22_3.html">保险用户信息</a>
    <a href="http://www.iachina.cn/art/2026/11/4/art_22_4.html">保险明文</a>
    <a href="/art/2026/11/5/art_22_5.html">保险未来</a>
    <a href="/art/2026/1/1/art_22_6.html">保险旧闻</a>
    <a href="/art/2026/11/4/art_22_7.html">保险有效</a>'''
    items = module()._parse_association(html, date(2026, 11, 4))['insurance']
    assert [x['title'] for x in items] == ['保险有效']


def test_dedup_is_canonical_and_order_is_deterministic():
    html = '''<li><a href="/art/2026/11/3/art_22_2.html#x">保险乙</a></li>
    <li><a href="https://www.iachina.cn/art/2026/11/3/art_22_2.html?utm_source=test">保险乙</a></li>
    <li><a href="/art/2026/11/4/art_22_1.html">保险甲</a></li>'''
    items = module()._parse_association(html, date(2026, 11, 4))['insurance']
    assert [x['title'] for x in items] == ['保险甲', '保险乙']


@pytest.fixture
def feed(monkeypatch):
    m = module()
    monkeypatch.setattr(m, '_CACHE', {})
    monkeypatch.setattr(m, '_today', lambda: date(2026, 11, 4))
    monkeypatch.setattr(m.time, 'time', lambda: 1000.0)
    monkeypatch.setattr(m, '_fetch_html', lambda url: ASSOCIATION if url == m.ASSOCIATION_URL else MEDICAL)
    return m


def test_query_and_limit_apply_after_full_cache(feed, monkeypatch):
    first = asyncio.run(feed.insurance_trends(query='没有匹配', limit=1))
    assert all(g['items'] == [] and g['ok'] for g in first['trends'])
    monkeypatch.setattr(feed, '_fetch_html', lambda _: pytest.fail('fresh cache should not fetch'))
    second = asyncio.run(feed.insurance_trends(query='保险', limit=1))
    assert len(second['trends'][0]['items']) == 1
    third = asyncio.run(feed.insurance_trends(query='上海市医保局', limit=60))
    assert len(third['trends'][2]['items']) == 3
    assert second['trends'][0]['updated_at'] == 1000.0


def test_partial_failure_visible_without_fake_timestamps(feed, monkeypatch):
    def fetch(url):
        if url == feed.MEDICAL_URL:
            raise TimeoutError('private upstream internals')
        return ASSOCIATION
    monkeypatch.setattr(feed, '_fetch_html', fetch)
    result = asyncio.run(feed.insurance_trends())
    medical = result['trends'][2]
    assert medical['ok'] is False and medical['stale'] is False
    assert medical['items'] == [] and medical['updated_at'] is None
    assert medical['error'] and 'private upstream' not in medical['error']
    assert all(g['ok'] for g in result['trends'][:2])


def test_stale_cache_keeps_original_time_and_ages_out_items(feed, monkeypatch):
    asyncio.run(feed.insurance_trends())
    monkeypatch.setattr(feed.time, 'time', lambda: 1500.0)
    monkeypatch.setattr(feed, '_fetch_html', lambda _: (_ for _ in ()).throw(OSError('failed')))
    groups = asyncio.run(feed.insurance_trends())['trends']
    assert all(not g['ok'] and g['stale'] and g['updated_at'] == 1000 for g in groups)
    monkeypatch.setattr(feed, '_today', lambda: date(2027, 5, 1))
    old = asyncio.run(feed.insurance_trends())['trends']
    assert all(not item['published_at'] for g in old for item in g['items'])


def test_empty_source_is_failure_with_last_good(feed, monkeypatch):
    asyncio.run(feed.insurance_trends())
    monkeypatch.setattr(feed.time, 'time', lambda: 1500.0)
    monkeypatch.setattr(feed, '_fetch_html', lambda _: '<html>访问验证</html>')
    groups = asyncio.run(feed.insurance_trends())['trends']
    assert all(not g['ok'] and g['stale'] and g['error'] for g in groups)


def test_fetch_requires_fixed_source_and_verified_tls(monkeypatch):
    m = module()
    with pytest.raises(ValueError):
        m._fetch_html('https://evil.example/')
    context = m._ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    request = m._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example/')
    assert request is None


def test_association_uses_publication_path_not_event_date_or_admin_notice():
    html = '''<li><a href="/art/2026/9/15/art_22_109299.html" title="中国保险行业协会调研人身险经营">保险调研</a><div>2026年9月9日召开调研会议</div></li>
    <li><a href="/art/2026/11/2/art_24_109368.html">中国保险行业协会设备采购比选公告</a></li>
    <li><a href="/art/2026/9/18/art_24_109305.html">中国保险行业协会招聘公告</a></li>'''
    items = module()._parse_association(html, date(2026, 11, 4))['insurance']
    assert len(items) == 1
    assert items[0]['published_at'] == '2026-09-15'


def test_limit_clamped_and_cache_not_restricted_to_first_query(feed):
    groups = asyncio.run(feed.insurance_trends(limit=0))['trends']
    assert all(len(g['items']) == 1 for g in groups)
    groups = asyncio.run(feed.insurance_trends(limit=9999))['trends']
    assert len(groups[0]['items']) == 2


def test_aggregate_updated_never_fresh_stamps_cached_or_failed_sources(feed, monkeypatch):
    first = asyncio.run(feed.insurance_trends())
    assert first['updated'] == 1000
    monkeypatch.setattr(feed.time, 'time', lambda: 1100.0)
    cached = asyncio.run(feed.insurance_trends())
    assert cached['updated'] == first['updated']
    monkeypatch.setattr(feed.time, 'time', lambda: 1500.0)
    monkeypatch.setattr(feed, '_fetch_html', lambda _: (_ for _ in ()).throw(OSError('failed')))
    stale = asyncio.run(feed.insurance_trends())
    assert stale['updated'] == first['updated']
    monkeypatch.setattr(feed, '_CACHE', {})
    failed = asyncio.run(feed.insurance_trends())
    assert failed['updated'] == 0
