import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/shared/scripts'))
import ai_video as video

class RealAPIVideoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.args = argparse.Namespace(model=None, prompt='test', duration=5,
            ratio='9:16', audio='auto', output=str(Path(self.tmp.name)/'clip.mp4'),
            poll_interval=1, timeout=10)
        self.env = patch.dict(os.environ, {'REAL_API_KEY':'test-secret',
            'REAL_API_BASE_URL':'https://example.com/v1',
            'REAL_API_VIDEO_MODEL':'wan3.0-video','REAL_API_VIDEO_RESOLUTION':'720P'}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)

    def test_provider_available(self):
        self.assertEqual(video.resolve_provider('real-api'), 'real-api')
        self.assertEqual(video.resolve_model('real-api'), 'wan3.0-video')

    def test_create_once_and_resume_polling(self):
        with patch.object(video,'http_request',return_value={'job_id':12,'status':'pending'}) as create, patch.object(video,'_poll',return_value={'status':'succeeded','data':{'url':'https://example.com/v.mp4'}}) as poll, patch.object(video,'download_video',return_value=Path(self.args.output)):
            video.generate_real_api(self.args,None)
            self.assertEqual(create.call_count,1)
            self.assertEqual(create.call_args.kwargs['payload']['duration'],5)
            self.assertEqual(create.call_args.kwargs['payload']['size'],'9:16')
            self.assertEqual(create.call_args.args[0],'https://example.com/v1/videos/generations/jobs')
            self.assertEqual(poll.call_args.args[0],'https://example.com/v1/videos/generations/jobs/12')
            with self.assertRaises(SystemExit): video.generate_real_api(self.args,None)
            self.assertEqual(create.call_count,1)
            self.args.job_id=12
            video.cmd_resume_real_api(self.args)
            self.assertEqual(create.call_count,1)

    def test_unknown_submission_blocks_repeat(self):
        with patch.object(video,'http_request',side_effect=TimeoutError) as create:
            with self.assertRaises(SystemExit): video.generate_real_api(self.args,None)
            with self.assertRaises(SystemExit): video.generate_real_api(self.args,None)
            self.assertEqual(create.call_count,1)
            receipt=json.loads(Path(self.args.output+'.job.json').read_text())
            self.assertEqual(receipt['status'],'unknown')
            self.assertNotIn('test-secret',json.dumps(receipt))

    def test_authenticated_get_and_post_refuse_redirects(self):
        import urllib.request
        opener = video._real_api_opener()
        handler = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
        req = urllib.request.Request('https://example.com/jobs/12', headers={'Authorization':'Bearer test-secret'})
        self.assertIsNone(handler.redirect_request(req, None, 302, 'Found', {}, 'https://other.example/jobs/12'))
        with patch.object(video, 'http_request', return_value={'status':'succeeded'}) as request:
            video._poll('https://example.com/jobs/12', {}, 1, 10, status_path=('status',), opener=opener)
            self.assertIs(request.call_args.kwargs['opener'], opener)

    def test_receipt_failure_preserves_unknown_and_reports_job(self):
        with patch.object(video, 'http_request', return_value={'job_id':12}), patch.object(Path, 'replace', side_effect=OSError), patch.object(video, 'fail', wraps=video.fail) as fail:
            with self.assertRaises(SystemExit): video.generate_real_api(self.args,None)
            self.assertIn('job_id=12',fail.call_args.args[0])
            self.assertEqual(json.loads(Path(self.args.output+'.job.json').read_text())['status'],'unknown')

    def test_invalid_input_never_submits(self):
        with patch.object(video,'http_request') as create:
            for duration, ratio, image in [(0,'9:16',None),(31,'9:16',None),(5,None,None),(5,'9:16','local.png')]:
                self.args.duration=duration; self.args.ratio=ratio
                with self.assertRaises(SystemExit): video.generate_real_api(self.args,image)
            create.assert_not_called()

if __name__=='__main__': unittest.main()
