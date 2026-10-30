"""独立离线审查；只写 TMPDIR 内状态库，禁用真实 socket。"""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hermes_bilibili.client import APIError, BiliClient, Response
from hermes_bilibili.core import PolicyError, Settings, Store

BVID = 'BV1xx411c7mD'
URL = 'https://aisubtitle.hdslb.com/sub.json?auth_key=123-a%2Bb%2Fc&extra=%E4%B8%AD'
NAV = {'wbi_img': {'img_url': 'https://i0.hdslb.com/7cd084941338484aae1ad9425b84077c.png', 'sub_url': 'https://i0.hdslb.com/4932caff0ff746eab6f01bf08b70ac45.png'}}
VIDEO = {'bvid': BVID, 'aid': 7, 'cid': 8, 'duration': 2, 'owner': {'mid': 9}}
EMPTY = {'subtitle': {'subtitles': []}}
TRACK = {'subtitle': {'subtitles': [{'lan': 'zh-CN', 'subtitle_url': URL}]}}
FILES = ['hermes_bilibili/client.py', 'hermes_bilibili/media.py', 'hermes_bilibili/core.py', 'tests/test_client_compat.py', 'tests/test_client.py', 'tests/test_client_fixes.py', 'tests/test_media.py', 'README.md']
HASHES = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}
EVIDENCE = []

def ok(data):
    return Response(200, {}, json.dumps({'code': 0, 'data': data}).encode())

class OfflineTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError('意外请求')
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

def sequence(primary=None, fallback=None):
    return [ok(VIDEO), ok(NAV), ok(EMPTY) if primary is None else primary,
            ok(TRACK) if fallback is None else fallback,
            Response(200, {}, b'{"body":[{"from":0,"to":1,"content":"fixture"}]}')]

class SQLiteBoundaryTests(unittest.TestCase):
    def setup_api(self, responses, budget=30, pause_at=None):
        temp = tempfile.TemporaryDirectory(prefix='compat-review-', dir=os.environ['TMPDIR'])
        self.addCleanup(temp.cleanup)
        now = [1800000000.0]
        store = Store(Path(temp.name) / 'state.db', clock=lambda: now[0])
        self.addCleanup(store.close)
        other = Store(store.path, clock=lambda: now[0])
        self.addCleanup(other.close)
        transport = OfflineTransport(responses)
        def sleep(delay):
            now[0] += float(delay)
            if pause_at is not None and len(transport.calls) + 1 == pause_at:
                other.pause('anonymous', reason='独立 SQLite 连接在等待中暂停', manual=True)
        api = BiliClient(store, Settings(read_daily=budget), credentials={'sessdata': 'SYNTHETIC'}, transport=transport, sleep=sleep)
        return api, store, transport

    def state(self, store):
        import sqlite3
        with sqlite3.connect(store.path) as db:
            rows = db.execute('SELECT id,dispatched FROM requests ORDER BY id').fetchall()
            guards = db.execute('SELECT COUNT(*) FROM dispatch_guard').fetchone()[0]
            cached = db.execute('SELECT value FROM cache').fetchall()
        self.assertEqual(guards, 0)
        self.assertFalse(any('auth_key' in row[0] for row in cached))
        return rows

    def test_budget_counts_all_uncached_phases_and_never_exceeds_limit(self):
        for budget in range(6):
            with self.subTest(budget=budget):
                api, store, transport = self.setup_api(sequence(), budget=budget)
                if budget < 5:
                    with self.assertRaisesRegex(PolicyError, 'budget'):
                        api.subtitles(BVID)
                else:
                    result = api.subtitles(BVID)
                    self.assertEqual(result['source'], 'subtitle_web_view')
                    self.assertTrue(result['available'])
                    self.assertEqual(transport.calls[-1][1], URL)
                    self.assertFalse(any(k.lower() == 'cookie' for k in transport.calls[-1][2]['headers']))
                    self.assertEqual(transport.calls[-1][2]['max_bytes'], 1000000)
                    for call in transport.calls[:-1]:
                        self.assertIn('Cookie', call[2]['headers'])
                    query = parse_qs(urlsplit(transport.calls[3][1]).query)
                    self.assertEqual(query, {'oid': ['7'], 'pid': ['7'], 'context_ext': ['{"video_type":1}'], 'type': ['1'], 'cur_production_type': ['0'], 'preferred_language': ['ai-zh'], 'playlist_switch': ['0']})
                self.assertEqual(len(transport.calls), budget)
                self.assertEqual(len(self.state(store)), budget)
                EVIDENCE.append({'case': 'real_sqlite_budget', 'budget': budget, 'actual_requests': len(transport.calls)})

    def test_pause_from_independent_connection_blocks_every_wait_phase(self):
        for phase in range(1, 6):
            with self.subTest(phase=phase):
                api, store, transport = self.setup_api(sequence(), pause_at=phase)
                with self.assertRaisesRegex(PolicyError, 'paused'):
                    api.subtitles(BVID)
                self.assertEqual(len(transport.calls), phase - 1)
                rows = self.state(store)
                self.assertEqual(rows, [(n, int(n < phase)) for n in range(1, phase + 1)])
                self.assertTrue(store.status('anonymous')['pause']['active'])
                EVIDENCE.append({'case': 'real_sqlite_pause_after_wait', 'phase': phase, 'actual_requests': len(transport.calls), 'reservations': rows})

    def test_risk_api_http_and_malformed_primary_never_fallback(self):
        errors = [ok(None), ok({}), ok({'subtitle': {}}), ok({'subtitle': {'subtitles': None}}), ok({'subtitle': {'subtitles': {}}}), ok({'subtitle': {'subtitles': [None]}}), ok({'subtitle': {'subtitles': [{'subtitle_url': 'https://evil.example/x'}]}}), Response(200, {}, b'{"code":-412}'), Response(200, {}, b'{"code":-101}'), Response(429, {}, b'{}'), Response(401, {}, b'{}'), Response(412, {}, b'{}'), Response(200, {}, b'<html>'), RuntimeError('offline failure')]
        for index, failure in enumerate(errors):
            with self.subTest(index=index):
                api, store, transport = self.setup_api(sequence(primary=failure))
                with self.assertRaises(APIError):
                    api.subtitles(BVID)
                self.assertEqual(len(transport.calls), 3)
                self.state(store)
                risk = index in (7, 8, 9, 10, 11)
                self.assertEqual(store.status('anonymous')['pause']['active'], risk)
                EVIDENCE.append({'case': 'primary_failure_no_fallback', 'index': index, 'paused': risk, 'actual_requests': 3})

    def test_fallback_error_or_empty_never_recurses(self):
        for fallback in [ok(EMPTY), ok(None), Response(200, {}, b'{"code":-412}'), Response(429, {}, b'{}')]:
            with self.subTest(fallback=fallback.body):
                api, store, transport = self.setup_api(sequence(fallback=fallback))
                if fallback.body == ok(EMPTY).body:
                    self.assertFalse(api.subtitles(BVID)['available'])
                else:
                    with self.assertRaises(APIError):
                        api.subtitles(BVID)
                self.assertEqual(len(transport.calls), 4)
                self.state(store)
                EVIDENCE.append({'case': 'fallback_single_attempt', 'actual_requests': 4})

    def test_nonempty_primary_avoids_fallback_and_preserves_full_url(self):
        api, store, transport = self.setup_api([ok(VIDEO), ok(NAV), ok(TRACK), Response(200, {}, b'{"body":[]}')])
        result = api.subtitles(BVID)
        self.assertEqual(result['source'], 'player_wbi_v2')
        self.assertEqual(transport.calls[-1][1], URL)
        self.assertEqual(len(transport.calls), 4)
        self.assertFalse(any(k.lower() == 'cookie' for k in transport.calls[-1][2]['headers']))
        self.state(store)
        EVIDENCE.append({'case': 'nonempty_primary', 'actual_requests': 4, 'exact_url_and_cookie_boundary': True})

if __name__ == '__main__':
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('禁止真实网络')), patch.object(socket, 'create_connection', side_effect=AssertionError('禁止真实网络')):
        outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SQLiteBoundaryTests))
    after = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}
    result = {'successful': outcome.wasSuccessful(), 'tests_run': outcome.testsRun, 'subcase_count': len(EVIDENCE), 'evidence': EVIDENCE, 'reviewed_hashes': HASHES, 'source_unchanged_during_probe': HASHES == after}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if outcome.wasSuccessful() and HASHES == after else 1)
