"""Signed endpoint contracts; offline fixtures, not live compatibility evidence."""
import json
import unittest
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch

from hermes_bilibili.client import BiliClient, APIError, Response
from hermes_bilibili.core import PolicyError
from test_client import FakeStore, Fixtures, response, settings


NAV = {'wbi_img': {
    'img_url': 'https://i0.hdslb.com/7cd084941338484aae1ad9425b84077c.png',
    'sub_url': 'https://i0.hdslb.com/4932caff0ff746eab6f01bf08b70ac45.png'}}


class SignedEndpointTests(unittest.TestCase):
    def test_subtitles_use_signed_player_metadata(self):
        transport = Fixtures(response(NAV), response({'subtitle': {'subtitles': []}}),
                             response({'subtitle': {'subtitles': []}}))
        api = BiliClient(FakeStore(), settings(), transport=transport)
        with patch.object(api, 'video', return_value={'aid': '7', 'cid': '8'}):
            result = api.subtitles('BV1xx411c7mD')
        paths = [urlsplit(call[1]).path for call in transport.calls]
        self.assertEqual(paths[:2], ['/x/web-interface/nav', '/x/player/wbi/v2'])
        query = parse_qs(urlsplit(transport.calls[1][1]).query)
        self.assertEqual(query['bvid'], ['BV1xx411c7mD'])
        self.assertEqual(query['cid'], ['8'])
        self.assertEqual(len(query['w_rid'][0]), 32)
        self.assertFalse(result['available'])


    def test_valid_empty_primary_uses_subtitle_fallback(self):
        from hermes_bilibili.client import Response
        metadata = {'subtitle': {'subtitles': [{'lan':'zh-CN', 'subtitle_url':'https://aisubtitle.hdslb.com/fixture?auth_key=kept'}]}}
        transport = Fixtures(response(NAV), response({'subtitle': {'subtitles': []}}), response(metadata),
                             Response(200, {}, b'{"body":[{"from":0,"to":1,"content":"fixture"}]}'))
        api = BiliClient(FakeStore(), settings(), credentials={'sessdata':'SYNTHETIC'}, transport=transport)
        with patch.object(api, 'video', return_value={'aid':'7','cid':'8'}):
            result = api.subtitles('BV1xx411c7mD')
        self.assertTrue(result['available'])
        self.assertEqual(result['source'], 'subtitle_web_view')
        self.assertEqual(urlsplit(transport.calls[2][1]).path, '/x/v2/subtitle/web/view')
        self.assertEqual(parse_qs(urlsplit(transport.calls[2][1]).query)['oid'], ['7'])
        self.assertIn('auth_key=kept', transport.calls[3][1])
        self.assertNotIn('Cookie', transport.calls[3][2]['headers'])


    def test_malformed_primary_metadata_never_triggers_fallback(self):
        for data in (None, {}, {'subtitle': None}, {'subtitle': {}},
                     {'subtitle': {'subtitles': None}}, {'subtitle': {'subtitles': {}}}):
            with self.subTest(data=data):
                transport = Fixtures(response(NAV), response(data), response({'subtitle': {'subtitles': []}}))
                api = BiliClient(FakeStore(), settings(), transport=transport)
                with patch.object(api, 'video', return_value={'aid': '7', 'cid': '8'}):
                    with self.assertRaises(APIError):
                        api.subtitles('BV1xx411c7mD')
                self.assertEqual(len(transport.calls), 2)


    def test_primary_errors_never_trigger_fallback(self):
        from hermes_bilibili.client import Response
        failures = [response({}, code=-412), response({}, code=-101),
                    response({}, status=429), response({}, status=401),
                    Response(200, {}, b'<html>UNTRUSTED</html>'),
                    RuntimeError('SYNTHETIC transport error')]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                store = FakeStore()
                transport = Fixtures(response(NAV), failure, response({'subtitle': {'subtitles': []}}))
                api = BiliClient(store, settings(), transport=transport)
                with patch.object(api, 'video', return_value={'aid': '7', 'cid': '8'}):
                    with self.assertRaises(APIError):
                        api.subtitles('BV1xx411c7mD')
                self.assertEqual(len(transport.calls), 2)
                self.assertEqual(len(store.finished), 2)
                if not isinstance(failure, Exception) and b'<html>' not in failure.body and (failure.status in (429, 401) or failure.json().get('code') in (-412, -101)):
                    self.assertEqual(len(store.pauses), 1)

    def test_pause_during_fallback_wait_prevents_request(self):
        store = FakeStore()
        transport = Fixtures(response(NAV), response({'subtitle': {'subtitles': []}}),
                             response({'subtitle': {'subtitles': []}}))
        def sleep(_):
            if len(store.reservations) == 3:
                store.pause('anonymous', manual=True)
        api = BiliClient(store, settings(), transport=transport, sleep=sleep)
        with patch.object(api, 'video', return_value={'aid': '7', 'cid': '8'}):
            with self.assertRaises(PolicyError):
                api.subtitles('BV1xx411c7mD')
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(store.finished, [(None, 1), (None, 2)])

    def test_real_store_budget_counts_signing_and_metadata_before_fallback(self):
        from pathlib import Path
        import tempfile
        from hermes_bilibili.core import Settings, Store, PolicyError
        now = [1800000000.0]
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / 'state.db', clock=lambda: now[0])
            self.addCleanup(store.close)
            config = Settings(read_daily=2)
            transport = Fixtures(response(NAV), response({'subtitle': {'subtitles': []}}),
                                 response({'subtitle': {'subtitles': []}}))
            def sleep(delay):
                now[0] += delay
            api = BiliClient(store, config, transport=transport, sleep=sleep)
            with patch.object(api, 'video', return_value={'aid': '7', 'cid': '8'}):
                with self.assertRaises(PolicyError):
                    api.subtitles('BV1xx411c7mD')
            self.assertEqual(len(transport.calls), 2)


    def test_frames_real_client_signs_playback_and_builds_local_png(self):
        from pathlib import Path
        import tempfile
        import shutil
        import subprocess
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('optional ffmpeg/ffprobe unavailable')
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            fixture = root / 'fixture.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=10',
                            '-t', '2', '-pix_fmt', 'yuv420p', '-threads', '1', str(fixture)], check=True, timeout=20)
            payload = fixture.read_bytes()
            fixture.unlink()
            class MediaStore(FakeStore):
                path: Path
            store = MediaStore()
            store.path = root / 'state.sqlite'
            signed_url = 'https://a.bilivideo.com/fixture.mp4?deadline=fixture&sign=unaltered'
            transport = Fixtures(response(NAV), response({'durl': [{'url': signed_url, 'size': len(payload)}]}))
            api = BiliClient(store, settings(), transport=transport)
            calls = []
            def download(url, target):
                calls.append(url)
                Path(target).write_bytes(payload)
            with patch.object(api, 'video', return_value={'duration': 2, 'cid': '8'}), \
                    patch.object(api, 'download_public', side_effect=download):
                result = api.frames('BV1xx411c7mD', root / 'frames')
            self.assertEqual(urlsplit(transport.calls[1][1]).path, '/x/player/wbi/playurl')
            query = parse_qs(urlsplit(transport.calls[1][1]).query)
            self.assertEqual(query['qn'], ['16'])
            self.assertEqual(query['fnval'], ['0'])
            self.assertEqual(len(query['w_rid'][0]), 32)
            self.assertEqual(calls, [signed_url])
            sheet = result['sheet']
            assert isinstance(sheet, str)
            self.assertEqual(Path(sheet).read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(list((root / 'frames').iterdir()), [Path(sheet)])


if __name__ == '__main__':
    unittest.main()
