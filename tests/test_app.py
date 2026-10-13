import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

temporary = tempfile.TemporaryDirectory()
os.environ['SCRIBE_DATA_DIR'] = temporary.name
import app
from fastapi.testclient import TestClient
from transcript import extract_urls, parse_subtitles, timestamp, to_srt


class TranscriptTests(unittest.TestCase):
    def test_concatenated_links_and_share_text(self):
        self.assertEqual(extract_urls('分享 https://www.douyin.com/video/1https://www.douyin.com/video/2\nhttps://www.douyin.com/video/1'),
                         ['https://www.douyin.com/video/1', 'https://www.douyin.com/video/2'])

    def test_vtt_rolling_and_intentional_repetition(self):
        cues = parse_subtitles('WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n你好\n\n00:00:01.000 --> 00:00:03.000\n你好\n世界\n\n00:00:04.000 --> 00:00:05.000\n世界\n', 'vtt')
        self.assertEqual([s['text'] for s in cues], ['你好', '世界', '世界'])

    def test_srt_parser(self):
        self.assertEqual(parse_subtitles('1\n00:00:01,000 --> 00:00:02,000\nHello\n', 'srt')[0]['start'], 1)

    def test_json3_and_escaping(self):
        raw = json.dumps({'events':[{'tStartMs':1500, 'dDurationMs':2000, 'segs':[{'utf8':'<b>Hello</b> &amp; bye'}]}]})
        cue = parse_subtitles(raw, 'json3')[0]
        self.assertEqual(cue['text'], 'Hello & bye')
        self.assertIn('00:00:01,500 --> 00:00:03,500', to_srt([cue]))
        self.assertEqual(timestamp(59.9999), '00:01:00,000')


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        with app.LOCK:
            app.JOBS.clear()

    def test_health_and_page(self):
        self.assertEqual(self.client.get('/api/health').json()['app'], 'video-scribe')
        self.assertIn('逐字', self.client.get('/').text)

    def test_public_tunnel_requires_access_token(self):
        public = TestClient(app.app, base_url='https://demo.trycloudflare.com')
        with patch.object(app, 'PUBLIC_TOKEN', 'secret-token'):
            self.assertEqual(public.get('/', follow_redirects=False).status_code, 401)
            unlocked = public.get('/?access=secret-token')
            self.assertEqual(unlocked.status_code, 200)
            self.assertIn('逐字', unlocked.text)

    def test_bad_inputs_and_origin(self):
        for link in ['no link', 'http://127.0.0.1/test', 'https://www.douyin.com:bad/video/1', 'https://douyin.com.evil.test/a']:
            self.assertEqual(self.client.post('/api/links', data={'links':link}).status_code, 400)
        self.assertEqual(self.client.post('/api/links', data={'links':'https://www.douyin.com/video/1'}, headers={'Origin':'https://evil.test'}).status_code, 403)
        self.assertEqual(self.client.post('/api/upload', files={'file':('a.exe',b'test')}).status_code, 400)
        self.assertEqual(self.client.post('/api/upload', files={'file':('a.wav',b'')}).status_code, 400)

    def test_queue_cancel_export_delete(self):
        with patch.object(app.POOL, 'submit'):
            ids = self.client.post('/api/links', data={'links':'https://www.douyin.com/video/1https://youtu.be/abc'}).json()['ids']
        self.assertEqual(len(ids), 2)
        self.assertEqual(self.client.get('/api/jobs').json()[0]['status'], 'queued')
        self.assertEqual(self.client.get(f'/api/jobs/{ids[0]}/export/txt').status_code, 409)
        self.client.post(f'/api/jobs/{ids[0]}/cancel')
        self.assertEqual(self.client.get(f'/api/jobs/{ids[0]}').json()['status'], 'cancelled')
        app.update(ids[1], status='done', title='測試字幕', text='後臺', segments=[{'start':0,'end':1.5,'text':'後臺'}])
        txt = self.client.get(f'/api/jobs/{ids[1]}/export/txt')
        self.assertEqual(txt.content.decode('utf-8-sig'), '后台')
        self.assertIn('00:00:01,500', self.client.get(f'/api/jobs/{ids[1]}/export/srt').text)
        csv_text = self.client.get(f'/api/jobs/{ids[1]}/export/csv').content.decode('utf-8-sig')
        self.assertIn('视频标题,视频链接,序号,开始时间,结束时间,逐字内容', csv_text)
        self.assertIn('测试字幕,https://youtu.be/abc,1,00:00:00.000,00:00:01.500,后台', csv_text)
        archive = zipfile.ZipFile(io.BytesIO(self.client.get('/api/export.zip').content))
        self.assertEqual(len(archive.namelist()), 3)
        selected = self.client.get(f'/api/export.zip?ids={ids[1]}')
        self.assertEqual(selected.status_code, 200)
        self.assertIn('selected-transcripts.zip', selected.headers['content-disposition'])
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(selected.content)).namelist()), 3)
        workbook = self.client.get(f'/api/export.xlsx?ids={ids[1]}')
        self.assertEqual(workbook.status_code, 200)
        self.assertIn('selected-transcripts.xlsx', workbook.headers['content-disposition'])
        xlsx = zipfile.ZipFile(io.BytesIO(workbook.content))
        self.assertEqual(xlsx.read('xl/workbook.xml').count(b'<sheet '), 1)
        strings = xlsx.read('xl/sharedStrings.xml').decode('utf-8')
        self.assertIn('测试字幕', strings)
        self.assertIn('后台', strings)
        self.assertEqual(self.client.get('/api/export.zip?ids=bad').status_code, 400)
        self.assertEqual(self.client.delete(f'/api/jobs/{ids[1]}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/jobs/{ids[1]}').status_code, 404)

    def test_upload_schedules_local_file(self):
        with patch.object(app.POOL, 'submit') as submit:
            response = self.client.post('/api/upload', data={'model':'tiny'}, files={'file':('test.wav',b'RIFFtest')})
        self.assertEqual(response.status_code, 200)
        config = submit.call_args.args[1]
        self.assertEqual(Path(config['media']).read_bytes(), b'RIFFtest')
        self.assertEqual(config['mode'], 'speech')

    def test_browser_auth_requires_explicit_batch_consent(self):
        data = {'links':'https://www.douyin.com/video/123', 'browser':'edge'}
        with patch.object(app.POOL, 'submit') as submit:
            self.assertEqual(self.client.post('/api/links', data=data).status_code, 400)
            submit.assert_not_called()
            data['browser_consent'] = 'true'
            self.assertEqual(self.client.post('/api/links', data=data).status_code, 200)
            self.assertEqual(submit.call_args.args[1]['browser'], 'edge')
        self.assertNotIn('browser', self.client.get('/api/jobs').json()[0])

    def test_cookie_sources_are_mutually_exclusive(self):
        response = self.client.post('/api/links', data={'links':'https://www.douyin.com/video/123', 'browser':'chrome', 'browser_consent':'true'},
                                    files={'cookies':('cookies.txt', b'# Netscape HTTP Cookie File\n')})
        self.assertEqual(response.status_code, 400)


class BrowserAuthTests(unittest.TestCase):
    def test_only_requested_platform_cookies_are_used(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from browser_auth import add_browser_cookies
        from worker import QuietLogger
        logger = QuietLogger()
        def fake_extract(browser, logger):
            logger.info('test')
            logger.warning('test', only_once=True)
            return [SimpleNamespace(domain=d) for d in ['.douyin.com', 'www.douyin.com', 'douyin.com.evil.test', '.private.test']]
        ydl = SimpleNamespace(cookiejar=Mock())
        with patch('yt_dlp.cookies.extract_cookies_from_browser', side_effect=fake_extract):
            add_browser_cookies(ydl, 'edge', 'https://www.douyin.com/video/123', logger)
        self.assertEqual([c.args[0].domain for c in ydl.cookiejar.set_cookie.call_args_list], ['.douyin.com', 'www.douyin.com'])

    def test_friendly_error_types(self):
        from browser_auth import friendly_error
        self.assertEqual(friendly_error('Fresh cookies are needed')[0], 'platform_auth')
        self.assertEqual(friendly_error('Could not copy Chrome cookie database')[0], 'browser_locked')
        self.assertEqual(friendly_error('Failed to decrypt with DPAPI')[0], 'browser_encrypted')


class BridgeMediaTests(unittest.TestCase):
    def test_douyin_and_media_domain_validation(self):
        from bridge_media import is_browser_source, is_douyin, validate_media_url
        self.assertTrue(is_douyin('https://www.douyin.com/video/123'))
        self.assertFalse(is_douyin('https://douyin.com.evil.test/video/123'))
        self.assertTrue(is_browser_source('https://www.xiaohongshu.com/explore/123'))
        self.assertTrue(is_browser_source('https://xhslink.com/a/abc'))
        self.assertFalse(is_browser_source('https://xiaohongshu.com.evil.test/explore/123'))
        self.assertEqual(validate_media_url('https://v26-web.douyinvod.com/a.mp4'), 'https://v26-web.douyinvod.com/a.mp4')
        self.assertEqual(validate_media_url('https://sns-video-bd.xhscdn.com/a.mp4', 'xiaohongshu'), 'https://sns-video-bd.xhscdn.com/a.mp4')
        for url in ('http://v26-web.douyinvod.com/a', 'https://127.0.0.1/a', 'https://douyinvod.com.evil.test/a'):
            with self.assertRaises(RuntimeError):
                validate_media_url(url)

    def test_bridge_errors_are_friendly(self):
        from browser_auth import friendly_error
        self.assertEqual(friendly_error('BRIDGE_UNAVAILABLE: no extension')[0], 'bridge_unavailable')
        self.assertEqual(friendly_error('BRIDGE_MEDIA_MISSING: no video')[0], 'bridge_media_missing')


if __name__ == '__main__':
    unittest.main()
