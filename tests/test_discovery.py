import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError
from easel.discovery import DiscoveryInput, DiscoveryStore


class DiscoveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DiscoveryStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_profile_isolation_and_cross_profile_update(self):
        item = self.store.save('寻欢', DiscoveryInput(title='真实素材'))
        self.assertEqual(self.store.list('保险'), [])
        with self.assertRaises(KeyError):
            self.store.save('保险', DiscoveryInput(title='不能改写'), item['id'])
        self.assertEqual(self.store.list('寻欢')[0]['title'], '真实素材')

    def test_duplicate_keeps_evidence_and_capture_time(self):
        value = DiscoveryInput(title='原帖', url='https://example.com/post', evidence='已核实原文')
        first = self.store.save('寻欢', value)
        again = self.store.save('寻欢', DiscoveryInput(title='重复', url=value.url))
        self.assertEqual(first, again)
        self.assertEqual(len(self.store.list('寻欢')), 1)
        updated = self.store.save('寻欢', value.model_copy(update={'status': 'archived'}), first['id'])
        self.assertEqual(updated['captured_at'], first['captured_at'])
        self.assertEqual(updated['status'], 'archived')

    def test_bad_links_blank_titles_and_unknown_metrics(self):
        for url in ['javascript:alert(1)', 'file:///C:/secret', 'https://user:pass@example.com']:
            with self.assertRaises(ValidationError):
                DiscoveryInput(title='原帖', url=url)
        with self.assertRaises(ValidationError):
            DiscoveryInput(title='  ')
        self.assertEqual(DiscoveryInput(title='待采集').metrics, '')

    def test_xhs_share_tokens_do_not_duplicate_a_note(self):
        first = self.store.save('寻欢', DiscoveryInput(title='原帖', url='https://www.xiaohongshu.com/search_result/6a61d663000000000f02ac81?xsec_token=a'))
        again = self.store.save('寻欢', DiscoveryInput(title='分享链接', url='https://www.xiaohongshu.com/explore/6a61d663000000000f02ac81?xsec_token=b'))
        self.assertEqual(first['id'], again['id'])

    def test_corrupt_store_is_not_silently_overwritten(self):
        self.store.save('寻欢', DiscoveryInput(title='保留'))
        path = next(Path(self.temp.name).glob('*.json'))
        path.write_text('broken', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            self.store.save('寻欢', DiscoveryInput(title='新记录'))
        self.assertEqual(path.read_text(encoding='utf-8'), 'broken')


if __name__ == '__main__':
    unittest.main()
