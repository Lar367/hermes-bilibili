"""Deterministic real SQLite regressions; HTTP responses are offline fixtures."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from hermes_bilibili.core import PolicyError, Settings, Store
from hermes_bilibili.client import BiliClient, Response


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bili-dispatch-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.now = 1800000000.0
        self.store = Store(Path(self.tmp.name) / 'state.db', clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.settings = Settings(owner_mid='99', writes_enabled=True, timezone='UTC')

    def claim(self, suffix='D'):
        draft = self.store.create_draft('42', 'comment', 'BV1xx411c7m' + suffix,
                                        'Exact fixture', reason='Offline fixture evidence', score=18)
        self.store.approve_draft(draft['id'], draft['digest'])
        return self.store.claim_send(draft['id'], '42', self.settings)

    def test_reservation_refuses_operation_after_approval_expires(self):
        operation = self.claim()
        self.now += 600
        with self.assertRaisesRegex(PolicyError, 'expir'):
            self.store.reserve_request('42', self.settings, write=True,
                                       operation_id=operation['operation_id'])
        self.assertEqual(self.store.status('42')['usage']['42']['write_daily'], 0)


    def test_pause_while_waiting_cancels_post_before_transport(self):
        operation = self.claim()
        calls = []
        store = self.store

        class Transport:
            def request(self, method, url, **kwargs):
                calls.append(method)
                return Response(200, {}, json.dumps({'code': 0, 'data': {'rpid': 91}}).encode())

        def waiting(delay):
            self.now += delay
            store.pause('42', reason='Fixture pause during queue wait', manual=True)

        api = BiliClient(store, self.settings, credentials={'bili_jct': 'fixture-csrf'},
                         transport=Transport(), sleep=waiting)
        api._account = {'mid': '42'}
        with self.assertRaisesRegex(PolicyError, 'paused'):
            api._request('/x/v2/reply/add', {'message': operation['text']}, method='POST',
                         write=True, operation_id=operation['operation_id'])
        self.assertEqual(calls, [])
        self.assertEqual(store.status('42')['usage']['42']['write_daily'], 1)


    def test_approval_expiring_during_wait_cancels_post(self):
        operation = self.claim()
        calls = []

        class Transport:
            def request(self, method, url, **kwargs):
                calls.append(method)
                return Response(200, {}, b'{"code":0,"data":{"rpid":91}}')

        def waiting(delay):
            self.now += 600

        api = BiliClient(self.store, self.settings, credentials={'bili_jct': 'fixture-csrf'},
                         transport=Transport(), sleep=waiting)
        api._account = {'mid': '42'}
        with self.assertRaisesRegex(PolicyError, 'expir'):
            api._request('/x/v2/reply/add', method='POST', write=True,
                         operation_id=operation['operation_id'])
        self.assertEqual(calls, [])


    def test_pre_midnight_claims_cannot_escape_next_days_share_cap(self):
        from datetime import datetime, timezone
        self.now = datetime(2026, 10, 28, 23, 59, tzinfo=timezone.utc).timestamp()
        old = [self.claim(suffix) for suffix in 'DEF']
        self.now += 60
        for operation in old:
            self.store.reserve_request('42', self.settings, write=True,
                                       operation_id=operation['operation_id'])
            self.store.validate_dispatch('42', self.settings, write=True,
                                        operation_id=operation['operation_id'])
            self.store.finish_dispatch(operation['operation_id'])
            self.store.finish_send(operation['operation_id'], 'unverified', remote_id='91')
            self.now += 120
        self.assertEqual(self.store.status('42')['usage']['42']['share_daily'], 3)
        with self.assertRaisesRegex(PolicyError, 'sharing limit'):
            self.claim('G')


    def test_reserved_before_midnight_dispatch_after_midnight_is_counted_today(self):
        from datetime import datetime, timezone
        self.now = datetime(2026, 10, 28, 23, 59, 59, tzinfo=timezone.utc).timestamp()
        operation = self.claim()
        self.store.reserve_request('42', self.settings, write=True,
                                   operation_id=operation['operation_id'])
        self.now += 2
        self.store.validate_dispatch('42', self.settings, write=True,
                                    operation_id=operation['operation_id'])
        self.store.finish_dispatch(operation['operation_id'])
        self.store.finish_send(operation['operation_id'], 'unverified', remote_id='91')
        usage = self.store.status('42')['usage']['42']
        self.assertEqual(usage['share_daily'], 1)
        self.assertEqual(usage['write_daily'], 1)
        self.settings = Settings(owner_mid='99', writes_enabled=True, timezone='UTC', share_daily=1)
        with self.assertRaisesRegex(PolicyError, 'sharing limit'):
            self.claim('G')


    def test_delayed_old_request_cannot_post_beside_newer_request(self):
        first, second = self.claim('D'), self.claim('E')
        posted = []
        store = self.store

        class Transport:
            def request(_, method, url, **kwargs):
                posted.append(self.now)
                return Response(200, {}, b'{"code":0,"data":{"rpid":91}}')

        newer = BiliClient(store, self.settings, transport=Transport(), sleep=lambda delay: None)
        newer._account = {'mid': '42'}

        def stalled(delay):
            self.now += 120
            newer._request('/x/v2/reply/add', method='POST', write=True,
                           operation_id=second['operation_id'])

        older = BiliClient(store, self.settings, transport=Transport(), sleep=stalled)
        older._account = {'mid': '42'}
        with self.assertRaisesRegex(PolicyError, 'spacing'):
            older._request('/x/v2/reply/add', method='POST', write=True,
                           operation_id=first['operation_id'])
        self.assertEqual(len(posted), 1)
        self.assertNotIn('dispatched_at', store.operation(first['operation_id']))


    def test_other_process_cannot_post_after_first_validates_but_before_transport(self):
        from unittest.mock import patch
        first, second = self.claim('D'), self.claim('E')
        other = Store(self.store.path, clock=lambda: self.now)
        self.addCleanup(other.close)
        posted = []

        class Transport:
            def request(_, method, url, **kwargs):
                posted.append(self.now)
                return Response(200, {}, b'{"code":0,"data":{"rpid":91}}')

        newer = BiliClient(other, self.settings, transport=Transport(), sleep=lambda _: None)
        newer._account = {'mid': '42'}
        validate = self.store.validate_dispatch

        def preempted(*args, **kwargs):
            validate(*args, **kwargs)
            self.now += 120
            with self.assertRaisesRegex(PolicyError, 'in.flight'):
                newer._request('/x/v2/reply/add', method='POST', write=True,
                               operation_id=second['operation_id'])

        older = BiliClient(self.store, self.settings, transport=Transport(), sleep=lambda _: None)
        older._account = {'mid': '42'}
        with patch.object(self.store, 'validate_dispatch', side_effect=preempted):
            older._request('/x/v2/reply/add', method='POST', write=True,
                           operation_id=first['operation_id'])
        self.assertEqual(len(posted), 1)


    def test_in_flight_guard_covers_risk_response_processing(self):
        first, second = self.claim('D'), self.claim('E')
        store = self.store
        newer = BiliClient(store, self.settings, transport=None, sleep=lambda _: None)
        newer._account = {'mid': '42'}

        class RiskResponse:
            status = 200
            headers = {}
            def json(_):
                self.now += 120
                with self.assertRaisesRegex(PolicyError, 'in.flight'):
                    newer._request('/x/v2/reply/add', method='POST', write=True,
                                   operation_id=second['operation_id'])
                return {'code': -412}

        class Transport:
            def request(_, *args, **kwargs):
                return RiskResponse()

        api = BiliClient(store, self.settings, transport=Transport(), sleep=lambda _: None)
        api._account = {'mid': '42'}
        with self.assertRaisesRegex(PolicyError, 'API request rejected'):
            api._request('/x/v2/reply/add', method='POST', write=True,
                         operation_id=first['operation_id'])
        self.assertTrue(store.status('42')['pause']['active'])


    def test_crashed_dispatch_guard_survives_restart_and_is_visible(self):
        first, second = self.claim('D'), self.claim('E')
        self.store.reserve_request('42', self.settings, write=True, operation_id=first['operation_id'])
        self.store.validate_dispatch('42', self.settings, write=True, operation_id=first['operation_id'])
        other = Store(self.store.path, clock=lambda: self.now)
        self.addCleanup(other.close)
        self.now += 86400
        self.assertTrue(other.status()['write_in_flight'])
        with self.assertRaisesRegex(PolicyError, 'in.flight'):
            other.reserve_request('42', self.settings, write=True, operation_id=second['operation_id'])
        with self.assertRaisesRegex(PolicyError, 'ownership'):
            other.finish_dispatch(second['operation_id'])
        self.assertTrue(other.status()['write_in_flight'])
        other.finish_dispatch(first['operation_id'])
        self.assertFalse(other.status()['write_in_flight'])


if __name__ == '__main__':
    unittest.main()
