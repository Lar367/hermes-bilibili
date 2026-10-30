"""Independent offline media review; synthetic fixtures only, no networking.
Run from repository: PYTHONDONTWRITEBYTECODE=1 python audits/fix-20261029-125129/media-repro.py
"""
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from hermes_bilibili import media
import importlib
from email.message import Message
_fixture = importlib.import_module('test_client')
FakeStore, settings = _fixture.FakeStore, _fixture.settings
from hermes_bilibili.client import BiliClient

URL = 'https://a.bilivideo.com/fixture?signature=SYNTHETIC_SECRET'


class MediaReview(unittest.TestCase):
    def test_expired_dispatch_wait_must_not_start_network(self):
        now = [0.0]
        class WaitingStore(FakeStore):
            def reserve_request(self, account, config, **kwargs):
                delay = super().reserve_request(account, config, **kwargs)
                long_delay = type(delay)(31)
                long_delay.request_id = delay.request_id
                return long_delay
        class Raw(io.BytesIO):
            status, headers = 200, {}
        class Opener:
            calls = 0
            def open(self, *args, **kwargs):
                self.calls += 1
                self.opened_at = now[0]
                return Raw(b'fixture')
        def sleep(delay):
            now[0] += delay
        store, opener = WaitingStore(), Opener()
        api = BiliClient(store, settings(), sleep=sleep)
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'download'
            with self.assertRaisesRegex(media.MediaError, 'deadline'):
                api.download_public(URL, target, opener=opener, clock=lambda:now[0])
            self.assertFalse(target.exists())
        self.assertEqual(store.finished, [(None, 1)])
        self.assertEqual(opener.calls, 0, 'expired 30s budget still calls opener at t=31s')

    def test_raw_http_error_closed_redacted_and_paused_inside_guard(self):
        for status in (401, 412, 429):
            class GuardStore(FakeStore):
                def pause(self, account, **kwargs):
                    assert self.validated and not self.finished
                    super().pause(account, **kwargs)
            store = GuardStore()
            body = io.BytesIO(b'SYNTHETIC_SECRET')
            error = urllib.error.HTTPError(URL, status, 'SYNTHETIC_SECRET', Message(), body)
            class Opener:
                def open(self, *args, **kwargs):
                    raise error
            api = BiliClient(store, settings())
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaises(media.MediaError) as caught:
                    api.download_public(URL, Path(root)/'download', opener=Opener())
            self.assertNotIn('SYNTHETIC_SECRET', str(caught.exception))
            self.assertTrue(body.closed)
            self.assertEqual(store.pauses[0][1]['manual'], status == 401)
            self.assertEqual(store.finished, [(None, 1)])

    def test_retention_byte_ceiling_preserves_newest_and_unrelated(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            unrelated = root/'notes.png'
            unrelated.write_bytes(b'keep')
            paths = []
            for i in range(11):
                path = root / ('BV1xx411c7mD-' + f'{i:032x}' + '.png')
                with path.open('wb') as handle:
                    handle.truncate(5_000_000)
                os.utime(path, (i + 1, i + 1))
                paths.append(path)
            media.prune_frames(root, preserve=paths[-1])
            owned = [p for p in paths if p.exists()]
            self.assertEqual(len(owned), 10)
            self.assertEqual(sum(p.stat().st_size for p in owned), 50_000_000)
            self.assertFalse(paths[0].exists())
            self.assertTrue(paths[-1].exists())
            self.assertEqual(unrelated.read_bytes(), b'keep')

    def test_retention_symlink_refused_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            outside = root/'unrelated'
            outside.write_bytes(b'keep')
            owned = []
            for i in range(21):
                path = root / ('BV1xx411c7mD-' + f'{i:032x}' + '.png')
                path.write_bytes(b'x')
                owned.append(path)
            link = root / ('BV1xx411c7mD-' + 'f'*32 + '.png')
            link.symlink_to(outside)
            with self.assertRaises(media.MediaError):
                media.prune_frames(root)
            self.assertTrue(all(p.exists() for p in owned))
            self.assertTrue(link.is_symlink())
            self.assertEqual(outside.read_bytes(), b'keep')


if __name__ == '__main__':
    scratch = Path('/home/admin/.hermes/cache/scratch')
    with tempfile.TemporaryDirectory(prefix='media-repro-', dir=scratch) as root:
        os.environ.update(HOME=root, USERPROFILE=root, HERMES_HOME=root, TMPDIR=root)
        tempfile.tempdir = root
        def denied(*args, **kwargs):
            raise AssertionError('Network forbidden during offline review')
        with patch.object(socket.socket, 'connect', denied), patch.object(socket, 'create_connection', denied), patch.object(socket, 'getaddrinfo', denied):
            unittest.main(verbosity=2)
