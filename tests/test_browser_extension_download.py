"""No browsers: the deployment artifact contains only the fixed public sources."""
import io
import zipfile

from fastapi.testclient import TestClient
from web import app as web

NAMES = ('manifest.json', 'background.js', 'relay.js', 'popup.html', 'popup.js', 'xhs-draft.mjs', 'README.md')


def test_fixed_extension_package_excludes_local_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    root = tmp_path / 'browser-extension'
    root.mkdir()
    for name in NAMES:
        (root / name).write_text(name)
    (root / '.env').write_text('PRIVATE')
    result = TestClient(web.app, base_url='http://127.0.0.1:7862').get('/api/browser-extension/download?path=.env')
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert set(archive.namelist()) == set(NAMES)
        assert b'PRIVATE' not in b''.join(archive.read(name) for name in archive.namelist())


def test_incomplete_or_symlink_package_is_not_served(monkeypatch, tmp_path):
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    root = tmp_path / 'browser-extension'
    root.mkdir()
    client = TestClient(web.app, base_url='http://127.0.0.1:7862')
    assert client.get('/api/browser-extension/download').status_code == 503
    for name in NAMES:
        (root / name).write_text(name)
    (root / 'README.md').unlink()
    (root / 'README.md').symlink_to(tmp_path / 'private')
    assert client.get('/api/browser-extension/download').status_code == 503
