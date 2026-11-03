"""Profile-scoped external research records, separate from the legacy idea pool."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class DiscoveryInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    kind: Literal['post', 'account', 'question', 'news', 'trend'] = 'post'
    status: Literal['reference', 'candidate', 'drafted', 'archived'] = 'reference'
    url: str = Field(default='', max_length=3000)
    account: str = Field(default='', max_length=150)
    platform: str = Field(default='小红书', max_length=50)
    published_at: str = Field(default='', max_length=100)
    metrics: str = Field(default='', max_length=400)
    evidence: str = Field(default='', max_length=5000)
    angle: str = Field(default='', max_length=1500)
    audience: str = Field(default='', max_length=500)

    @field_validator('title')
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError('请填写标题')
        return value.strip()

    @field_validator('url')
    @classmethod
    def public_link(cls, value):
        value = value.strip()
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('来源链接须为 http 或 https 网页地址')
        return value


class DiscoveryStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.Lock()

    def _path(self, persona: str):
        # The human-readable persona stays in the JSON; no user input enters paths.
        return self.directory / (hashlib.sha256(persona.encode()).hexdigest() + '.json')

    def _read(self, persona: str):
        path = self._path(persona)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('persona') != persona or not isinstance(data.get('records'), list):
            raise ValueError('选题记录格式错误，请保留原文件并检查备份')
        return data['records']

    def _write(self, persona, records):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(persona)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'persona': persona, 'records': records}, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)

    @staticmethod
    def _source_key(url, kind):
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        if host in {'www.xiaohongshu.com', 'xiaohongshu.com'}:
            # Public share tokens change; the note/profile id does not.
            match = re.search(r'/([0-9a-f]{24})/?$', parsed.path)
            if match:
                return ('xiaohongshu', kind, match.group(1))
        return (host, parsed.path, parsed.query, kind)

    def list(self, persona):
        with self.lock:
            return self._read(persona)

    def save(self, persona: str, value: DiscoveryInput, record_id: str | None = None):
        with self.lock:
            records = self._read(persona)
            now = int(time.time())
            if record_id:
                item = next((r for r in records if r['id'] == record_id), None)
                if item is None:
                    raise KeyError(record_id)
                item.update(value.model_dump(), updated_at=now)
            else:
                # Same URL within a profile is one research record, not several cards.
                if value.url:
                    key = self._source_key(value.url, value.kind)
                    duplicate = next((r for r in records if r['url'] and self._source_key(r['url'], r['kind']) == key), None)
                    if duplicate:
                        return duplicate
                item = dict(value.model_dump(), id=uuid.uuid4().hex, captured_at=now, updated_at=now)
                records.insert(0, item)
            self._write(persona, records)
            return item


def register_discovery(app, directory: Path, profile_exists):
    from fastapi import HTTPException

    store = DiscoveryStore(directory)

    def check(persona):
        if not persona or not profile_exists(persona):
            raise HTTPException(400, '请先选择有效的工作画像')

    @app.get('/api/discovery')
    def discovery_list(persona: str):
        check(persona)
        return store.list(persona)

    @app.post('/api/discovery')
    def discovery_create(req: DiscoveryInput, persona: str):
        check(persona)
        return store.save(persona, req)

    @app.put('/api/discovery/{record_id}')
    def discovery_update(record_id: str, req: DiscoveryInput, persona: str):
        check(persona)
        try:
            return store.save(persona, req, record_id)
        except KeyError:
            raise HTTPException(404, '当前画像中没有这条选题记录')

    return store
