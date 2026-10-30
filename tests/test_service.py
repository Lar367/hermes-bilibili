import importlib
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hermes_bilibili.core import PolicyError, Settings, Store
from hermes_bilibili.service import Service


class FakeStore:
    def __init__(self):
        self.draft = None
        self.finished = []
    def create_draft(self, account, kind, target, text, **kw):
        self.draft = dict(id='draft-1', digest='digest-1', account=account, kind=kind, target=target, text=text, **kw)
        return self.draft
    def claim_send(self, draft_id, account, settings):
        return dict(self.draft, operation_id='op-1')
    def finish_send(self, op, state, **kw):
        self.finished.append(state)
        return dict(operation_id=op, state=state, **kw)
    def operation(self, op):
        return dict(self.draft, operation_id=op, remote_id='123')
    def remember(self, kind, text, **kw):
        return {'kind':kind,'text':text}
    def memories(self, **kw):
        return []


class FakeClient:
    def account(self):
        return {'mid':'111', 'name':'test account'}
    def send_comment(self, draft):
        return {'accepted':True,'remote_id':'123'}
    def verify(self, op):
        return {'verified':False,'remote_id':'123','reason':'readback not visible'}


class FakeSettings:
    owner_mid = '222'
    persona_max_chars = 6000


class ServiceTests(unittest.TestCase):
    def service(self):
        try:
            module = importlib.import_module('hermes_bilibili.service')
        except ModuleNotFoundError:
            self.fail('service not implemented')
        store = FakeStore()
        service = module.Service(store, FakeSettings(), client=FakeClient())
        return service, store

    def test_post_acceptance_without_readback_is_unverified_not_confirmed(self):
        service, store = self.service()
        service.prepare('comment','BV1234567890','分享一条有内容的评论','这是值得认真阅读的内容',15)
        result = service.send('draft-1')
        self.assertEqual(store.finished, ['unverified'])
        self.assertEqual(result['state'],'unverified')

    def test_confirmed_requires_positive_readback_and_ambiguous_send_never_retries(self):
        service, store = self.service()
        service.prepare('comment','BV1234567890','内容足够具体','这是值得认真阅读的内容',15)
        service.client.verify = lambda op: {'verified':True,'remote_id':'123'}
        self.assertEqual(service.send('draft-1')['state'], 'confirmed')
        calls = []
        def timeout(draft):
            calls.append(draft)
            raise TimeoutError('fixture timeout')
        service.client.send_comment = timeout
        self.assertEqual(service.send('draft-1')['state'],'unknown')
        self.assertEqual(len(calls),1)

    def test_definitive_platform_rejection_is_failed_not_unknown(self):
        service, store = self.service()
        service.prepare('comment','BV1234567890','内容足够具体','这是值得认真阅读的内容',15)
        class Rejected(Exception):
            code = -111
        def reject(draft):
            raise Rejected('fixture rejection')
        service.client.send_comment = reject
        self.assertEqual(service.send('draft-1')['state'], 'failed')

    def test_account_switch_blocks_verification_without_readback(self):
        service, store = self.service()
        service.prepare('comment','BV1234567890','内容足够具体','这是值得认真阅读的内容',15)
        service.client.account = lambda: {'mid':'999'}
        with self.assertRaises(ValueError):
            service.verify('op-1')

    def test_explicit_definitive_transport_rejection_is_failed(self):
        service, store = self.service()
        service.prepare('comment','BV1234567890','内容足够具体','这是值得认真阅读的内容',15)
        from hermes_bilibili.client import APIError
        def reject(draft):
            raise APIError('HTTP rejected', status=403, definitive=True)
        service.client.send_comment = reject
        self.assertEqual(service.send('draft-1')['state'], 'failed')

    def test_post_acceptance_is_persisted_before_attempting_readback(self):
        service, store=self.service()
        service.prepare('comment','BV1234567890','内容足够具体','这是值得认真阅读的内容',15)
        def readback(op):
            self.assertEqual(store.finished,['unverified'])
            return {'verified':True,'remote_id':'123'}
        service.client.verify=readback
        self.assertEqual(service.send('draft-1')['state'],'confirmed')
        self.assertEqual(store.finished,['unverified','confirmed'])


    def test_readback_uses_persisted_accepted_state(self):
        service, store = self.service()
        service.prepare('comment','BV1234567890','内容足够具体','这是值得认真阅读的内容',15)
        seen = []
        def verify(operation):
            seen.append(operation)
            return {'verified': False}
        service.client.verify = verify
        service.send('draft-1')
        self.assertEqual(seen[0].get('state'), 'unverified')
        self.assertEqual(seen[0]['remote_id'], '123')


class SimulatedProcessExit(BaseException):
    """Bypass Service's normal exception handling, as process termination would."""


class RecordingFakeClient(FakeClient):
    """Offline-only transport fixture; never constructs a network client."""

    def __init__(self):
        self.send_calls = []
        self.verify_calls = []
        self.send_error: BaseException | None = None
        self.verify_error: BaseException | None = None
        self.verification = {'verified': False, 'reason': 'fixture not visible'}

    def send_comment(self, draft):
        self.send_calls.append(dict(draft))
        if self.send_error is not None:
            raise self.send_error
        return super().send_comment(draft)

    def verify(self, op):
        self.verify_calls.append(dict(op))
        if self.verify_error is not None:
            raise self.verify_error
        return dict(self.verification)


class SQLiteServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bili-service-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'fixture-profile' / 'state.sqlite'
        self.now = 1800000000.0
        self.settings = Settings(owner_mid='222', writes_enabled=True)
        self.client = RecordingFakeClient()

    def store(self):
        store = Store(self.path, clock=lambda: self.now)
        self.addCleanup(store.close)
        return store

    def approved(self, service):
        draft = service.prepare('comment', 'BV1234567890', '内容足够具体', '这是值得认真阅读的内容', 15)
        return service.store.approve_draft(draft['id'], draft['digest'])

    def test_sqlite_acceptance_write_failure_never_reaches_readback_or_confirmation(self):
        store = self.store()
        service = Service(store, self.settings, client=self.client)
        draft = self.approved(service)
        self.client.verification = {'verified': True, 'remote_id': '123'}
        # Fail an actual SQLite write, rather than replacing the Store with a fake.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""
                CREATE TRIGGER fixture_fail_acceptance BEFORE INSERT ON drafts
                WHEN NEW.payload LIKE '%"state":"unverified"%'
                BEGIN SELECT RAISE(ABORT, 'fixture persistence failure'); END
            """)

        with self.assertRaises(PolicyError):
            service.send(draft['id'])
        self.assertEqual(len(self.client.send_calls), 1)
        self.assertEqual(self.client.verify_calls, [])
        operation_id = self.client.send_calls[0]['operation_id']
        store.close()

        reopened = self.store()
        persisted = reopened.operation(operation_id)
        self.assertEqual(persisted['state'], 'sending')
        self.assertFalse(persisted.get('remote_id'))
        with self.assertRaises(PolicyError):
            Service(reopened, self.settings, client=self.client).send(draft['id'])
        self.assertEqual(len(self.client.send_calls), 1)
        self.assertEqual(self.client.verify_calls, [])

    def test_process_exit_before_remote_id_leaves_sending_without_resend_or_success(self):
        store = self.store()
        service = Service(store, self.settings, client=self.client)
        draft = self.approved(service)
        self.client.send_error = SimulatedProcessExit('fixture POST interrupted before response')

        with self.assertRaises(SimulatedProcessExit):
            service.send(draft['id'])
        self.assertEqual(len(self.client.send_calls), 1)
        self.assertEqual(self.client.verify_calls, [])
        operation_id = self.client.send_calls[0]['operation_id']
        store.close()

        reopened = self.store()
        persisted = reopened.operation(operation_id)
        self.assertEqual(persisted['state'], 'sending')
        self.assertFalse(persisted.get('remote_id'))
        recovery_client = RecordingFakeClient()
        recovery = Service(reopened, self.settings, client=recovery_client)
        with self.assertRaises(PolicyError):
            recovery.send(draft['id'])
        result = recovery.verify(operation_id)
        self.assertIs(result['verified'], False)
        self.assertIs(result['retry_send'], False)
        self.assertNotEqual(result.get('state'), 'confirmed')
        self.assertEqual(recovery_client.send_calls, [])
        self.assertEqual(recovery_client.verify_calls, [persisted])
        reopened.close()
        self.assertEqual(self.store().operation(operation_id), persisted)

    def test_failed_operation_cannot_be_promoted_by_later_positive_readback(self):
        class FixtureRejection(Exception):
            code = -111

        store = self.store()
        service = Service(store, self.settings, client=self.client)
        draft = self.approved(service)
        self.client.send_error = FixtureRejection('fixture definitive rejection')
        result = service.send(draft['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(len(self.client.send_calls), 1)
        self.assertEqual(self.client.verify_calls, [])
        operation_id = result['operation_id']
        store.close()

        reopened = self.store()
        persisted = reopened.operation(operation_id)
        self.assertEqual(persisted['state'], 'failed')
        recovery_client = RecordingFakeClient()
        recovery_client.verification = {'verified': True, 'remote_id': '123'}
        recovery = Service(reopened, self.settings, client=recovery_client)
        with self.assertRaises(PolicyError):
            recovery.verify(operation_id)
        with self.assertRaises(PolicyError):
            recovery.send(draft['id'])
        self.assertEqual(recovery_client.send_calls, [])
        reopened.close()
        self.assertEqual(self.store().operation(operation_id), persisted)

    def test_remote_id_survives_readback_process_exit_and_recovers_without_resend(self):
        store = self.store()
        service = Service(store, self.settings, client=self.client)
        draft = self.approved(service)
        self.client.verify_error = SimulatedProcessExit('fixture termination during readback')

        with self.assertRaises(SimulatedProcessExit):
            service.send(draft['id'])
        self.assertEqual(len(self.client.send_calls), 1)
        self.assertEqual(len(self.client.verify_calls), 1)
        operation_id = self.client.send_calls[0]['operation_id']
        store.close()

        reopened = self.store()
        persisted = reopened.operation(operation_id)
        self.assertEqual(persisted['state'], 'unverified')
        self.assertEqual(persisted['remote_id'], '123')
        self.assertEqual(persisted['operation_id'], operation_id)
        self.assertEqual(reopened.status()['operation_counts'], {'unverified': 1})

        recovery_client = RecordingFakeClient()
        recovery_client.verification = {'verified': True, 'remote_id': '123'}
        recovered = Service(reopened, self.settings, client=recovery_client)
        with self.assertRaises(PolicyError):
            recovered.send(draft['id'])
        result = recovered.verify(operation_id)
        self.assertEqual(result['state'], 'confirmed')
        self.assertEqual(result['remote_id'], '123')
        self.assertEqual(recovery_client.verify_calls, [persisted])
        self.assertEqual(recovery_client.send_calls, [])
        reopened.close()

        durable = self.store().operation(operation_id)
        self.assertEqual(durable['state'], 'confirmed')
        self.assertEqual(durable['remote_id'], '123')
        self.assertEqual(durable['operation_id'], operation_id)


if __name__ == '__main__':
    unittest.main()
