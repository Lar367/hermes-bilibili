"""Offline media safety checks and real local ffmpeg execution."""
import importlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

try:
    media = importlib.import_module('hermes_bilibili.media')
except ModuleNotFoundError:
    media = None


class URLTests(unittest.TestCase):
    def test_media_url_has_strict_public_cdn_https_boundary(self):
        self.assertIsNotNone(media, 'media module required')
        self.assertEqual(media.validate_media_url('https://upos-sz-mirrorcos.bilivideo.com/fixture.mp4').hostname,
                         'upos-sz-mirrorcos.bilivideo.com')
        for value in ('http://a.bilivideo.com/x', 'https://a.bilivideo.com.evil/x',
                      'https://evil@a.bilivideo.com/x', 'https://a.bilivideo.com:444/x',
                      'https://api.bilibili.com/x', 'https://a.bilivideo.com/x#fragment',
                      'https://a.bilivideo.com./x', 'file:///etc/passwd'):
            with self.subTest(value=value), self.assertRaises(media.MediaError):
                media.validate_media_url(value)


class DownloadTests(unittest.TestCase):
    def test_download_is_cookieless_bounded_no_redirect_and_deadline_checked(self):
        self.assertTrue(hasattr(media, 'download_public'), 'bounded downloader required')
        class Raw(io.BytesIO):
            status = 200
            headers = {}
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append((request, timeout))
                return Raw(b'fixture bytes')
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'clip.mp4'
            media.download_public('https://a.bilivideo.com/x', target, max_bytes=100, opener=Opener())
            self.assertEqual(target.read_bytes(), b'fixture bytes')
            self.assertFalse(any(k.lower() == 'cookie' for k in calls[0][0].headers))
            self.assertLessEqual(calls[0][1], 20)
            target.unlink()
            with self.assertRaises(media.MediaError):
                media.download_public('https://a.bilivideo.com/x', target, max_bytes=5, opener=Opener())
            self.assertFalse(target.exists())
            with self.assertRaises(media.MediaError):
                media.download_public('https://a.bilivideo.com/x', target, opener=Opener(), clock=iter([0, 100]).__next__)
            self.assertFalse(target.exists())


    def test_download_uses_single_chunk_reads_for_deadline_checks(self):
        class Raw(io.BytesIO):
            status, headers = 200, {}
            def read(self, size=-1): raise AssertionError('buffer filling read forbidden')
        class Opener:
            def open(self, *args, **kwargs): return Raw(b'fixture')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'media'
            media.download_public('https://a.bilivideo.com/x', path, opener=Opener())
            self.assertEqual(path.read_bytes(), b'fixture')

    def test_expired_dispatch_wait_does_not_open_network(self):
        from contextlib import contextmanager
        now = [0.0]
        class Raw(io.BytesIO):
            status, headers = 200, {}
        class Opener:
            calls = 0
            def open(self, *args, **kwargs):
                self.calls += 1
                return Raw(b'fixture')
        @contextmanager
        def dispatch():
            now[0] += 31
            yield
        opener = Opener()
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'media'
            with self.assertRaisesRegex(media.MediaError, 'deadline'):
                media.download_public('https://a.bilivideo.com/x', target,
                                      opener=opener, dispatch=dispatch, clock=lambda: now[0])
            self.assertFalse(target.exists())
        self.assertEqual(opener.calls, 0)

    def test_download_open_time_reduces_socket_read_budget(self):
        from types import SimpleNamespace
        now = [0.0]
        timeouts = []
        class Socket:
            timeout = None
            def settimeout(self, value):
                self.timeout = value
                timeouts.append(value)
        sock = Socket()
        class Raw(io.BytesIO):
            status, headers = 200, {}
            fp = SimpleNamespace(raw=SimpleNamespace(_sock=sock))
            def read1(self, size: int | None = -1):
                now[0] += min(19, sock.timeout if sock.timeout is not None else 19)
                raise TimeoutError('fixture timeout')
        class Opener:
            def open(self, *args, **kwargs):
                now[0] += 19
                return Raw()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(media.MediaError):
                media.download_public('https://a.bilivideo.com/x', Path(root)/'media',
                                      opener=Opener(), timeout=20, clock=lambda: now[0])
        self.assertLessEqual(now[0], 20)
        self.assertEqual(timeouts, [1.0])

    def test_download_rejects_nonfinite_deadline_before_open(self):
        class Opener:
            calls = 0
            def open(self, *args, **kwargs):
                self.calls += 1
                raise AssertionError('must not open')
        with tempfile.TemporaryDirectory() as root:
            for timeout in (float('nan'), float('inf'), True):
                opener = Opener()
                with self.assertRaises(media.MediaError):
                    media.download_public('https://a.bilivideo.com/x', Path(root) / 'clip', timeout=timeout, opener=opener)
                self.assertEqual(opener.calls, 0)


class FramesTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe optional')
    def test_real_local_ffmpeg_collage_cleans_original_and_stays_in_plugin_data(self):
        self.assertTrue(hasattr(media, 'extract_frames'), 'frames helper required')
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            fixture = root / 'fixture.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=10',
                            '-t', '2', '-pix_fmt', 'yuv420p', '-threads', '1', str(fixture)], check=True, timeout=20)
            payload = fixture.read_bytes()
            fixture.unlink()
            class FakeClient:
                store = SimpleNamespace(path=root / 'state.sqlite')
                def download_public(self, url, target):
                    return media.download_public(url, target)
                def video(self, bvid): return {'duration':2, 'cid':'8'}
                def _signed_request(self, path, params):
                    return self._request(path, params)
                def _request(self, path, params):
                    self.request = (path, params)
                    return {'durl':[{'url':'https://a.bilivideo.com/fixture.mp4', 'size':len(payload)}]}
            api = FakeClient()
            def download(url, target, **kwargs):
                Path(target).write_bytes(payload)
            commands = []
            real_run = subprocess.run
            def run(*args, **kwargs):
                commands.append(args[0])
                return real_run(*args, **kwargs)
            with patch.object(media, 'download_public', side_effect=download), patch.object(media.subprocess, 'run', side_effect=run):
                result = media.extract_frames(api, 'BV1xx411c7mD', root / 'frames')
            for command in commands:
                self.assertIn('-f', command)
                self.assertEqual(command[command.index('-f') + 1], 'mov')
            collage = Path(result['path'])
            self.assertTrue(collage.is_file())
            self.assertEqual(collage.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
            self.assertTrue(result['untrusted'])
            self.assertEqual(list((root / 'frames').iterdir()), [collage])
            self.assertEqual('/x/player/wbi/playurl', api.request[0])
            with patch.object(api, 'video', side_effect=AssertionError('must not access metadata outside plugin data')) as fetched:
                with self.assertRaises(media.MediaError):
                    media.extract_frames(api, 'BV1xx411c7mD', root / '..' / 'escape-frames')
                fetched.assert_not_called()
            with self.assertRaises(media.MediaError):
                media.extract_frames(api, 'BV1xx411c7mD', root.parent / 'escape-frames')
            with patch.object(api, 'video', return_value={'duration':601, 'cid':'8'}):
                with self.assertRaises(media.MediaError): media.extract_frames(api, 'BV1xx411c7mD', root / 'frames')
            with patch.object(media, 'download_public', side_effect=download), patch.object(media.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffprobe', 15)):
                with self.assertRaises(media.MediaError): media.extract_frames(api, 'BV1xx411c7mD', root / 'frames')
            self.assertEqual(list((root / 'frames').iterdir()), [collage])


class ResourceFixTests(unittest.TestCase):
    def test_retention_only_removes_owned_pngs_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            keep = root/'unrelated.png'
            keep.write_bytes(b'keep')
            for i in range(22):
                (root/('BV1xx411c7mD-' + f'{i:032x}' + '.png')).write_bytes(b'x')
            media.prune_frames(root)
            self.assertEqual(len(list(root.iterdir())), 21)
            self.assertEqual(keep.read_bytes(), b'keep')
            link = root/('BV1xx411c7mD-' + 'f'*32 + '.png')
            link.symlink_to(keep)
            with self.assertRaises(media.MediaError): media.prune_frames(root)
            self.assertEqual(keep.read_bytes(), b'keep')


if __name__ == '__main__':
    unittest.main()
