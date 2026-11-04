"""Offline request contract for the bundled search client; no network or auth."""
import json
from pathlib import Path
import subprocess


def test_search_requests_image_formats_like_existing_detail_client():
    root = Path(__file__).resolve().parents[1]
    script = """
import { XhsClient } from './skills/builtin/skill-xhs-analyzer/dist/lib/client.js';
const client = new XhsClient({a1:'offline',web_session:'offline',webId:'offline'});
client.mainApiPost = async (path, body) => ({path,body});
const result = await client.searchNotes('家庭保险',1,20,'popularity_descending',0);
console.log(JSON.stringify(result));
"""
    run = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root,
                         capture_output=True, text=True, timeout=5, check=True)
    request = json.loads(run.stdout)
    assert request['path'] == '/api/sns/web/v1/search/notes'
    assert request['body']['keyword'] == '家庭保险'
    assert request['body']['sort'] == 'popularity_descending'
    assert request['body']['image_formats'] == ['jpg', 'webp', 'avif']
