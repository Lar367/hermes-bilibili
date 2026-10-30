"""Offline audit fixes: real SQLite and deterministic clocks, no account/network."""
import dataclasses
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from hermes_bilibili.core import PolicyError, Settings, Store


class CoreFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.db'
        self.now = 1800000000.0
        self.store = self.open()
        self.settings = Settings(owner_mid='99', writes_enabled=True, timezone='UTC')

    def open(self):
        store = Store(self.path, clock=lambda: self.now)
        self.addCleanup(store.close)
        return store

    def sql(self, statement, args=()):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(statement, args)

    def approved(self, suffix='D'):
        draft = self.store.create_draft('42', 'comment', 'BV1xx411c7m' + suffix,
            'Exact fixture', reason='Offline original evidence', score=18)
        return self.store.approve_draft(draft['id'], draft['digest'])

    def test_public_require_active_checks_persisted_account_pause(self):
        self.store.require_active('42')
        self.store.pause('42', reason='fixture', manual=True)
        with self.assertRaisesRegex(PolicyError, 'paused'):
            self.open().require_active('42')

    def test_sql_dispatched_marker_corruption_fails_closed(self):
        self.store.reserve_request('42', self.settings)
        self.sql('UPDATE requests SET dispatched=2')
        with self.assertRaises(PolicyError):
            self.store.status()

    def test_startup_migrates_expired_identity_without_erasing_pause(self):
        key = 'identity:' + hashlib.sha256(b'legacy-session').hexdigest()
        self.store.cache_put(key, {'mid': '42'}, 1)
        self.store.pause('42', reason='legacy risk', manual=True)
        self.now += 1000
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.store.identity_get(key), {'mid': '42'})
        self.assertEqual(self.store.cache_get(key), {'mid': '42'})
        self.assertTrue(self.store.status('42')['pause']['active'])
        with self.assertRaisesRegex(PolicyError, 'paused'):
            self.store.identity_bind(key, '42')

    def test_legacy_guard_and_sending_history_survive_additive_migration(self):
        draft = self.approved()
        operation = self.store.claim_send(draft['id'], '42', self.settings)['operation_id']
        self.store.reserve_request('42', self.settings, write=True, operation_id=operation)
        self.store.validate_dispatch('42', self.settings, write=True, operation_id=operation)
        self.store.close()
        self.sql('ALTER TABLE dispatch_guard DROP COLUMN request_id')
        self.sql('ALTER TABLE requests DROP COLUMN dispatched')
        self.sql('DROP TABLE approvals')
        self.sql('PRAGMA user_version=0')
        self.store = self.open()
        self.assertEqual(self.store.operation(operation)['state'], 'sending')
        self.assertTrue(self.store.status()['write_in_flight'])
        with self.assertRaisesRegex(PolicyError, 'in.flight'):
            self.store.validate_dispatch('42', self.settings)
        self.store.finish_dispatch(operation)
        self.assertFalse(self.store.status()['write_in_flight'])
        with self.assertRaises(PolicyError):
            self.store.validate_dispatch('42', self.settings, write=True, operation_id=operation)
        self.assertEqual(self.store.finish_send(operation, 'unknown')['state'], 'unknown')

    def test_read_dispatch_rechecks_budget_after_midnight(self):
        self.now = datetime(2026, 10, 28, 23, 59, 59, tzinfo=timezone.utc).timestamp()
        settings = dataclasses.replace(self.settings, read_daily=1)
        old = self.store.reserve_request('42', settings)
        self.now += 2
        newer = self.store.reserve_request('42', settings)
        self.now += 2
        self.store.validate_dispatch('42', settings, request_id=newer.request_id)
        self.store.finish_dispatch(request_id=newer.request_id)
        self.now += 2
        with self.assertRaisesRegex(PolicyError, 'budget'):
            self.store.validate_dispatch('42', settings, request_id=old.request_id)
        self.assertFalse(self.store.status()['write_in_flight'])

    def test_read_and_write_dispatch_share_guard_and_validate_exact_reservation(self):
        read = self.store.reserve_request('42', self.settings)
        self.now += float(read)
        self.store.validate_dispatch('42', self.settings, request_id=read.request_id)
        draft = self.approved()
        operation = self.store.claim_send(draft['id'], '42', self.settings)['operation_id']
        with self.assertRaisesRegex(PolicyError, 'in.flight'):
            self.store.reserve_request('42', self.settings, write=True, operation_id=operation)
        self.store.finish_dispatch(request_id=read.request_id)
        self.now += 2
        write = self.store.reserve_request('42', self.settings, write=True, operation_id=operation)
        for kwargs in ({'request_id':read.request_id}, {'request_id':write.request_id, 'operation_id':'wrong'}, {'request_id':write.request_id, 'operation_id':operation, 'dm':True}):
            with self.assertRaises(PolicyError):
                self.store.validate_dispatch('42', self.settings, write=True, **kwargs)
        self.store.validate_dispatch('42', self.settings, write=True, operation_id=operation, request_id=write.request_id)
        with self.assertRaisesRegex(PolicyError, 'in.flight'):
            self.store.validate_dispatch('43', self.settings)
        self.store.finish_dispatch(operation, request_id=write.request_id)

    def test_identity_is_permanent_immutable_and_migrates_expired_cache(self):
        key = 'identity:' + hashlib.sha256(b'offline-session').hexdigest()
        self.store.cache_put(key, {'mid': '42'}, 1)
        self.store.pause('42', reason='keep association', manual=True)
        self.now += 2
        self.assertEqual(self.store.identity_get(key), {'mid': '42'})
        self.assertEqual(self.open().identity_get(key), {'mid': '42'})
        with self.assertRaisesRegex(PolicyError, 'paused'):
            self.store.identity_bind(key, '42')
        self.now += 1000000
        with self.assertRaises(PolicyError):
            self.store.identity_bind(key, '43')
        self.store.resume('42')
        self.store.identity_bind(key, '42')
        self.assertEqual(self.store.identity_get(key), {'mid': '42'})
        other_key = 'identity:' + hashlib.sha256(b'another-session').hexdigest()
        self.assertIsNone(self.store.identity_get(other_key))
        self.store.pause('43', reason='paused account', manual=True)
        with self.assertRaisesRegex(PolicyError, 'paused'):
            self.store.identity_bind(other_key, '43')
        for invalid in ('identity:bad', 'plain'):
            with self.assertRaises(PolicyError):
                self.store.identity_get(invalid)
        with self.assertRaises(PolicyError):
            self.store.identity_bind(other_key, 42)

    def test_read_reservation_id_dispatch_guard_spacing_and_midnight_budget(self):
        self.now = datetime(2026, 10, 28, 23, 59, 59, tzinfo=timezone.utc).timestamp()
        settings = dataclasses.replace(self.settings, read_daily=1)
        reservation = self.store.reserve_request('42', settings)
        self.assertIsInstance(reservation, float)
        self.assertIsInstance(getattr(reservation, 'request_id', None), int)
        self.now += 2
        self.store.validate_dispatch('42', settings, request_id=reservation.request_id)
        other = self.open()
        next_read = other.reserve_request('43', settings)
        self.now += 2
        with self.assertRaisesRegex(PolicyError, 'in.flight'):
            other.validate_dispatch('43', settings, request_id=next_read.request_id)
        with self.assertRaisesRegex(PolicyError, 'ownership'):
            other.finish_dispatch(request_id=next_read.request_id)
        self.store.finish_dispatch(request_id=reservation.request_id)
        self.assertEqual(self.store.status('42')['usage']['42']['read_daily'], 1)
        with self.assertRaisesRegex(PolicyError, 'budget'):
            self.store.reserve_request('42', settings)
        with self.assertRaisesRegex(PolicyError, 'spacing'):
            other.validate_dispatch('43', settings, request_id=next_read.request_id)
        self.now += 2
        other.validate_dispatch('43', settings, request_id=next_read.request_id)
        other.finish_dispatch(request_id=next_read.request_id)
        with self.assertRaisesRegex(PolicyError, 'already'):
            other.validate_dispatch('43', settings, request_id=next_read.request_id)

    def test_timezone_is_fixed_for_read_write_claim_and_dispatch(self):
        self.store.reserve_request('42', self.settings)
        shifted = dataclasses.replace(self.settings, timezone='Asia/Shanghai')
        with self.assertRaisesRegex(PolicyError, '[Tt]imezone'):
            self.store.reserve_request('42', shifted)
        draft = self.approved()
        with self.assertRaisesRegex(PolicyError, '[Tt]imezone'):
            self.store.claim_send(draft['id'], '42', shifted)
        operation = self.store.claim_send(draft['id'], '42', self.settings)['operation_id']
        self.now += 120
        with self.assertRaisesRegex(PolicyError, '[Tt]imezone'):
            self.store.reserve_request('42', shifted, write=True, operation_id=operation)
        self.store.reserve_request('42', self.settings, write=True, operation_id=operation)
        with self.assertRaisesRegex(PolicyError, '[Tt]imezone'):
            self.store.validate_dispatch('42', shifted, write=True, operation_id=operation)
        with self.assertRaisesRegex(PolicyError, '[Tt]imezone'):
            self.store.validate_dispatch('42', shifted)

    def test_approval_event_required_and_migration_never_invents_it(self):
        draft = self.store.create_draft('42', 'comment', 'BV1xx411c7mD',
            'Exact fixture', reason='Offline original evidence', score=18)
        forged = dict(draft, state='approved', approval_expires_at=self.now + 600)
        self.sql('UPDATE drafts SET payload=? WHERE id=?', (json.dumps(forged), draft['id']))
        with self.assertRaisesRegex(PolicyError, '[Aa]pproval'):
            self.store.claim_send(draft['id'], '42', self.settings)
        self.sql('UPDATE drafts SET payload=? WHERE id=?', (json.dumps(draft), draft['id']))
        approved = self.store.approve_draft(draft['id'], draft['digest'])
        self.sql('UPDATE approvals SET expires_at=expires_at+1')
        with self.assertRaisesRegex(PolicyError, '[Aa]pproval'):
            self.store.claim_send(draft['id'], '42', self.settings)
        self.sql('DROP TABLE approvals')
        self.sql('PRAGMA user_version=0')
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.store.get_draft(draft['id'])['state'], 'approved')
        with self.assertRaisesRegex(PolicyError, '[Aa]pproval'):
            self.store.claim_send(draft['id'], '42', self.settings)
        with self.assertRaisesRegex(PolicyError, '[Aa]pproval'):
            self.store.approve_draft(draft['id'], draft['digest'])

    def test_schema_version_preflight_rejects_missing_columns_and_future_version(self):
        self.store.close()
        self.sql('ALTER TABLE requests RENAME COLUMN operation_id TO missing_operation_id')
        with self.assertRaisesRegex(PolicyError, 'schema'):
            self.open()
        self.sql('ALTER TABLE requests RENAME COLUMN missing_operation_id TO operation_id')
        self.sql('PRAGMA user_version=999')
        with self.assertRaisesRegex(PolicyError, 'schema'):
            self.open()
        self.sql('PRAGMA user_version=0')
        migrated = self.open()
        self.assertEqual(migrated._db.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertIsNotNone(migrated._db.execute("SELECT name FROM sqlite_master WHERE name='approvals'").fetchone())

    def test_corrupt_sql_timestamps_and_pause_flags_raise_policy_error(self):
        self.store.cache_put('fixture', {}, 60)
        self.sql("UPDATE cache SET expires_at='tomorrow'")
        with self.assertRaises(PolicyError):
            self.store.cache_get('fixture')
        self.sql('DELETE FROM cache')
        self.store.pause('42', reason='fixture')
        for column, value in [('until_time', 'tomorrow'), ('manual', 2), ('strikes', -1)]:
            self.sql('UPDATE pauses SET until_time=?,manual=0,strikes=1', (self.now + 300,))
            self.sql('UPDATE pauses SET ' + column + '=?', (value,))
            with self.subTest(column=column), self.assertRaises(PolicyError):
                self.store.status('42')
        self.sql('DELETE FROM pauses')
        self.store.reserve_request('42', self.settings)
        self.sql("UPDATE requests SET slot='tomorrow'")
        with self.assertRaises(PolicyError):
            self.store.reserve_request('42', self.settings)
        with self.assertRaises(PolicyError):
            self.store.status('42')
