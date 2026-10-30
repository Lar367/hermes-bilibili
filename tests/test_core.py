import concurrent.futures
from contextlib import closing
import dataclasses
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

try:
    from hermes_bilibili import core
except ImportError:
    core = None


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bili-core-', dir=os.environ.get('TMPDIR'))
        self.path = Path(self.tmp.name) / 'profile' / 'state.sqlite'
        self.now = 1800000000.0
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.tmp.cleanup()

    def store(self):
        store = core.Store(self.path, clock=lambda: self.now)
        self.stores.append(store)
        return store

    def settings(self, **kwargs):
        return core.Settings.from_mapping({'owner_mid': '99', 'writes_enabled': True, **kwargs})

    def draft(self, store, target='BV1xx411c7mD', **kwargs):
        return store.create_draft('42', 'comment', target, 'hello @owner', reason='Useful original source', score=16, **kwargs)

    def approved(self, store, target='BV1xx411c7mD', **kwargs):
        draft = self.draft(store, target, **kwargs)
        return store.approve_draft(draft['id'], draft['digest'])

    def test_settings_rejects_unknown_keys(self):
        self.assertIsNotNone(core, 'core implementation is missing')
        with self.assertRaises(core.PolicyError):
            core.Settings.from_mapping({'secret': 'not allowed'})
        self.assertFalse(core.Settings.from_mapping({}).writes_enabled)

    def test_settings_strict_bounded_immutable_validation(self):
        for change in ({'read_daily': True}, {'read_daily': 3001}, {'share_daily': 4},
                       {'write_hourly': -1}, {'writes_enabled': 1}, {'owner_mid': '4x'},
                       {'skip_chance': float('nan')}, {'min_gap_seconds': 1},
                       {'windows': [{'start': '24:00', 'end': '00:00'}]},
                       {'timezone': 'not/a/zone'}, {'preferences': 'x'}, {'min_score': 21}):
            with self.subTest(change=change), self.assertRaises(core.PolicyError):
                core.Settings.from_mapping(change)
        settings = core.Settings.from_mapping({'windows': [{'start': '23:00', 'end': '01:00'}], 'preferences': ['music']})
        self.assertEqual(settings.as_dict()['preferences'], ['music'])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            settings.writes_enabled = True
        with self.assertRaises(core.PolicyError):
            core.Settings(share_daily=4)

    def test_store_private_wal_paths_and_corruption_fail_closed(self):
        self.assertTrue(hasattr(core, 'Store'), 'Store is missing')
        store = self.store()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        link = Path(self.tmp.name) / 'link'
        link.symlink_to(self.path)
        with self.assertRaises(core.PolicyError):
            core.Store(link)
        parent_link = Path(self.tmp.name) / 'linked-profile'
        parent_link.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(core.PolicyError):
            core.Store(parent_link / 'other.sqlite')
        corrupt = Path(self.tmp.name) / 'corrupt.sqlite'
        corrupt.write_bytes(b'not a database')
        with self.assertRaises(core.PolicyError):
            core.Store(corrupt)
        self.assertEqual(corrupt.read_bytes(), b'not a database')
        self.assertEqual(store.status()['draft_count'], 0)

    def test_draft_digest_approval_expiry_and_exact_text(self):
        store = self.store()
        self.assertTrue(hasattr(store, 'create_draft'), 'draft creation missing')
        draft = self.draft(store)
        self.assertEqual(draft['text'], 'hello @owner')
        self.assertEqual(draft['expires_at'], self.now + 1800)
        self.assertEqual(store.get_draft(draft['id']), draft)
        with self.assertRaises(core.PolicyError):
            store.approve_draft(draft['id'], 'wrong')
        approved = store.approve_draft(draft['id'], draft['digest'])
        self.assertEqual(approved['state'], 'approved')
        self.assertEqual(approved['approval_expires_at'], self.now + 600)
        self.assertEqual(store.approve_draft(draft['id'], draft['digest']), approved)
        self.now += 1801
        with self.assertRaises(core.PolicyError):
            store.approve_draft(draft['id'], draft['digest'])
        self.assertNotIn('hello', json.dumps(store.status()))
        for change in ({'target': 'BVinvalid'}, {'text': 'x\x00'}, {'score': True}, {'reason': 'short'}, {'share': False}):
            args = dict(account='42', kind='comment', target='BV1xx411c7mD', text='text', reason='long reason', score=15)
            args.update(change)
            with self.subTest(change=change), self.assertRaises(core.PolicyError):
                store.create_draft(**args)

    def test_claim_requires_approval_account_switch_owner_and_freshness(self):
        store = self.store()
        self.assertTrue(hasattr(store, 'claim_send'), 'claim missing')
        draft = self.draft(store)
        with self.assertRaises(core.PolicyError):
            store.claim_send(draft['id'], '42', self.settings())
        draft = store.approve_draft(draft['id'], draft['digest'])
        for account, settings in [('43', self.settings()), ('42', self.settings(writes_enabled=False)), ('42', self.settings(owner_mid=''))]:
            with self.assertRaises(core.PolicyError):
                store.claim_send(draft['id'], account, settings)
        claim = store.claim_send(draft['id'], '42', self.settings())
        self.assertEqual(claim['state'], 'sending')
        self.assertTrue(claim['operation_id'])
        with self.assertRaises(core.PolicyError):
            store.claim_send(draft['id'], '42', self.settings())
        other = self.approved(store, 'BV1xx411c7mE')
        self.now += 600
        with self.assertRaises(core.PolicyError):
            store.claim_send(other['id'], '42', self.settings())
        dm = store.create_draft('42', 'dm', '98', 'hello', reason='private message', score=0, share=False)
        store.approve_draft(dm['id'], dm['digest'])
        with self.assertRaises(core.PolicyError):
            store.claim_send(dm['id'], '42', self.settings())
        low = store.create_draft('42', 'comment', 'BV1xx411c7mF', 'hello', reason='low score draft', score=12)
        store.approve_draft(low['id'], low['digest'])
        with self.assertRaises(core.PolicyError):
            store.claim_send(low['id'], '42', self.settings())

    def test_concurrent_approved_claims_never_exceed_three_shares(self):
        store = self.store()
        drafts = [self.approved(store, 'BV1xx411c7m' + suffix) for suffix in 'ABCDEFGH']
        stores = [self.store() for _ in drafts]
        def claim(pair):
            connection, draft = pair
            try:
                return connection.claim_send(draft['id'], '42', self.settings())
            except core.PolicyError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, zip(stores, drafts)))
        claims = [result for result in results if result is not None]
        self.assertEqual(len(claims), 3)
        self.assertEqual(len({result['operation_id'] for result in claims}), 3)
        self.assertEqual(sum(store.get_draft(d['id'])['state'] == 'sending' for d in drafts), 3)

    def test_target_dedup_blocks_unknown_for_seven_days_account_scoped(self):
        store = self.store()
        first = store.claim_send(self.approved(store)['id'], '42', self.settings())
        store.finish_send(first['operation_id'], 'unknown')
        duplicate = self.approved(store)
        with self.assertRaises(core.PolicyError):
            store.claim_send(duplicate['id'], '42', self.settings())
        other = store.create_draft('43', 'comment', first['target'], 'different account', reason='Useful original source', score=17)
        store.approve_draft(other['id'], other['digest'])
        self.assertEqual(store.claim_send(other['id'], '43', self.settings())['account'], '43')
        self.now += 7 * 86400
        fresh = self.approved(store)
        self.assertEqual(store.claim_send(fresh['id'], '42', self.settings())['state'], 'sending')
        failed = store.claim_send(self.approved(store, 'BV1xx411c7mE')['id'], '42', self.settings(share_daily=2))
        store.finish_send(failed['operation_id'], 'failed')
        retry = store.claim_send(self.approved(store, 'BV1xx411c7mE')['id'], '42', self.settings(share_daily=2))
        self.assertNotEqual(failed['operation_id'], retry['operation_id'])
        with self.assertRaises(core.PolicyError):
            store.claim_send(self.approved(store, 'BV1xx411c7mF')['id'], '42', self.settings(share_daily=2))

    def test_read_reservations_persist_global_spacing_and_reject_long_queue(self):
        store = self.store()
        settings = self.settings()
        self.assertTrue(hasattr(store, 'reserve_request'), 'request reservations missing')
        self.assertEqual(store.reserve_request('42', settings), 0)
        self.assertEqual(self.store().reserve_request('43', settings), 1.5)
        self.assertEqual(store.reserve_request('anonymous', settings), 3)
        for _ in range(18):
            store.reserve_request('42', settings)
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', settings)
        self.now += 31.5
        self.assertEqual(store.reserve_request('42', settings), 0)
        store.close()
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', settings)

    def test_read_budgets_account_scoped_hourly_and_local_daily(self):
        store = self.store()
        settings = self.settings(read_hourly=1, read_daily=2, timezone='UTC')
        store.reserve_request('42', settings)
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', settings)
        store.reserve_request('43', settings)
        self.now += 3600
        store.reserve_request('42', settings)
        self.now += 3600
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', settings)
        self.now += 86400
        self.assertEqual(store.reserve_request('42', settings), 0)
        with self.assertRaises(core.PolicyError):
            store.reserve_request('anonymous', self.settings(read_daily=0))

    def test_write_reservation_requires_bound_claim_and_only_one_attempt(self):
        store = self.store()
        settings = self.settings(write_daily=1)
        for kwargs in ({}, {'operation_id': 'missing'}):
            with self.assertRaises(core.PolicyError):
                store.reserve_request('42', settings, write=True, **kwargs)
        claimed = store.claim_send(self.approved(store)['id'], '42', settings)
        op = claimed['operation_id']
        for account, opts, extra in [('43', settings, {}), ('42', self.settings(writes_enabled=False), {}), ('42', settings, {'dm': True})]:
            with self.assertRaises(core.PolicyError):
                store.reserve_request(account, opts, write=True, operation_id=op, **extra)
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', settings, operation_id=op)
        self.assertEqual(store.reserve_request('42', settings, write=True, operation_id=op), 0)
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', settings, write=True, operation_id=op)
        store.finish_send(op, 'failed')
        another = store.claim_send(self.approved(store, 'BV1xx411c7mE')['id'], '42', settings)
        self.now += 120
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', settings, write=True, operation_id=another['operation_id'])
        self.now += 86400
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', self.settings(), write=True, operation_id=op)

    def test_write_spacing_and_dm_gap_persist_after_failed_request(self):
        store = self.store()
        settings = self.settings(dm_gap_seconds=180)
        first = store.claim_send(self.approved(store)['id'], '42', settings)
        store.reserve_request('42', settings, write=True, operation_id=first['operation_id'])
        store.finish_send(first['operation_id'], 'failed')
        second = store.claim_send(self.approved(store, 'BV1xx411c7mE')['id'], '42', settings)
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', settings, write=True, operation_id=second['operation_id'])
        self.now += 90
        self.assertEqual(store.reserve_request('42', settings, write=True, operation_id=second['operation_id']), 30)
        self.now += 150
        def dm_claim():
            draft = store.create_draft('42', 'dm', '99', 'private', reason='actual event', score=0, share=False)
            store.approve_draft(draft['id'], draft['digest'])
            return store.claim_send(draft['id'], '42', settings)
        dm = dm_claim()
        self.assertEqual(store.reserve_request('42', settings, write=True, dm=True, operation_id=dm['operation_id']), 0)
        self.now += 120
        later = dm_claim()
        with self.assertRaises(core.PolicyError):
            self.store().reserve_request('42', settings, write=True, dm=True, operation_id=later['operation_id'])
        self.now += 30
        self.assertEqual(store.reserve_request('42', settings, write=True, dm=True, operation_id=later['operation_id']), 30)

    def test_risk_pause_persists_account_bound_exponential_backoff(self):
        store = self.store()
        self.assertTrue(hasattr(store, 'pause'), 'persistent risk pause missing')
        store.pause('42', reason='HTTP risk', manual=True)
        pause = self.store().status('42')['pause']
        self.assertTrue(pause['manual'])
        self.assertEqual(pause['until'], self.now + 300)
        store.resume('42')
        self.assertFalse(store.status('42')['pause']['manual'])
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', self.settings())
        with self.assertRaises(core.PolicyError):
            store.claim_send(self.approved(store)['id'], '42', self.settings())
        store.reserve_request('43', self.settings())
        for wait in (600, 1200, 1800, 1800):
            store.pause('42', reason='HTTP risk')
            self.assertEqual(store.status('42')['pause']['until'], self.now + wait)
        store.pause('42', reason='Retry-After', retry_after=7200)
        self.assertEqual(store.status('42')['pause']['until'], self.now + 7200)
        store.pause('42', reason='risk cannot shorten')
        self.assertEqual(store.status('42')['pause']['until'], self.now + 7200)
        self.now += 7200
        self.assertFalse(store.status('42')['pause']['active'])
        store.reserve_request('42', self.settings())
        store.pause('42', reason='authentication', manual=True)
        self.now += 9000
        with self.assertRaises(core.PolicyError):
            store.reserve_request('42', self.settings())
        store.resume('42')
        store.reserve_request('42', self.settings())

    def test_cache_json_roundtrip_expiry_and_replacement_persist(self):
        store = self.store()
        self.assertTrue(hasattr(store, 'cache_put'), 'cache missing')
        value = {'untrusted': True, 'items': ['测试', 2]}
        self.assertIsNone(store.cache_get('42:video'))
        store.cache_put('42:video', value, 10)
        value['items'].append('mutated')
        self.assertEqual(self.store().cache_get('42:video')['items'], ['测试', 2])
        self.assertIsNone(store.cache_get('43:video'))
        self.now += 9
        self.assertIsNotNone(store.cache_get('42:video'))
        self.now += 1
        self.assertIsNone(store.cache_get('42:video'))
        store.cache_put('42:video', {'new': True}, 1)
        self.assertEqual(store.cache_get('42:video'), {'new': True})
        store.cache_put('42:video', {'new': False}, 0)
        self.assertIsNone(store.cache_get('42:video'))
        for ttl in (-1, True, float('nan'), float('inf')):
            with self.assertRaises(core.PolicyError):
                store.cache_put('x', {}, ttl)
        with self.assertRaises(core.PolicyError):
            store.cache_put('x', {'bad': float('nan')}, 1)

    def test_memory_persists_order_filters_and_bounded_json(self):
        store = self.store()
        self.assertTrue(hasattr(store, 'remember'), 'memory persistence missing')
        first = store.remember('public', 'Published evidence op-123', metadata={'operation_id': 'op-123'})
        self.assertEqual(first['text'], 'Published evidence op-123')
        self.assertEqual(first['created_at'], self.now)
        self.assertTrue(first['id'])
        second = store.remember('private', 'Owner conversation')
        third = store.remember('feedback', 'Owner prefers original sources')
        self.assertEqual([m['id'] for m in self.store().memories(limit=2)], [third['id'], second['id']])
        self.assertEqual(store.memories(kind='public'), [first])
        self.assertEqual(store.memories(limit=0), [])
        for kind, text, meta in [('unknown', 'text', {}), ('public', '', {}),
                                 ('public', 'x' * 12001, {}), ('public', 'x\x00', {}),
                                 ('public', 'x', {'bad': object()}), ('public', 'x', {'huge': 'x' * 20000})]:
            with self.assertRaises(core.PolicyError):
                store.remember(kind, text, metadata=meta)
        for kwargs in ({'limit': True}, {'limit': -1}, {'limit': 101}, {'kind': 'other'}):
            with self.assertRaises(core.PolicyError):
                store.memories(**kwargs)

    def test_status_lists_all_pauses_usage_and_states_without_content(self):
        store = self.store()
        settings = self.settings(timezone='UTC')
        claim = store.claim_send(self.approved(store)['id'], '42', settings)
        self.assertEqual(store.status('42')['usage']['42']['timezone'], 'UTC')
        store.reserve_request('42', settings, write=True, operation_id=claim['operation_id'])
        store.finish_send(claim['operation_id'], 'unknown', error='private error detail')
        store.reserve_request('43', settings)
        store.pause('42', reason='secret-risk-detail', manual=True)
        store.pause('anonymous', reason='HTTP risk')
        status = self.store().status()
        self.assertIn('pauses', status, 'global status hides account pauses')
        self.assertEqual(set(status['pauses']), {'42', 'anonymous'})
        self.assertTrue(status['pause']['active'])
        self.assertEqual(status['operation_counts'], {'unknown': 1})
        self.assertEqual(status['usage']['42']['write_daily'], 1)
        self.assertEqual(status['usage']['43']['read_hourly'], 1)
        self.assertEqual(status['usage']['43']['timezone'], 'UTC')
        self.assertEqual(store.status('43')['operation_counts'], {})
        self.assertEqual(set(store.status('43')['usage']), {'43'})
        serialized = json.dumps(status)
        for private in ('hello', 'Useful original', 'secret-risk-detail', 'private error detail', claim['operation_id']):
            self.assertNotIn(private, serialized)

    def test_persisted_json_corruption_fails_closed_with_safe_policy_error(self):
        store = self.store()
        draft = self.draft(store)
        for bad in ('not json', 'null', '[]', '{}', '{"digest":"wrong"}', '{"number":NaN}'):
            with self.subTest(table='drafts', bad=bad):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute('UPDATE drafts SET payload=? WHERE id=?', (bad, draft['id']))
                with self.assertRaises(core.PolicyError):
                    store.get_draft(draft['id'])
                with self.assertRaises(core.PolicyError):
                    store.status()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM drafts')
        store.cache_put('key', {}, 60)
        store.remember('public', 'actual observed event')
        for table, column, bad in [('cache', 'value', 'NaN'), ('cache', 'value', '{'),
                                    ('memories', 'payload', 'null'), ('memories', 'payload', '{')]:
            with self.subTest(table=table, bad=bad):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute(f'UPDATE {table} SET {column}=?', (bad,))
                with self.assertRaises(core.PolicyError):
                    store.cache_get('key') if table == 'cache' else store.memories()

    def test_cache_and_memory_reject_credentials_in_nested_json(self):
        store = self.store()
        for value in ({'SESSDATA': 'secret'}, {'payload': [{'Cookie': 'secret'}]}, {'bili_jct': 'secret'}, {'Authorization': 'secret'}):
            with self.subTest(value=value), self.assertRaises(core.PolicyError):
                store.cache_put('key', value, 10)
            with self.subTest(metadata=value), self.assertRaises(core.PolicyError):
                store.remember('private', 'actual event', metadata=value)

    def test_corrupt_draft_structure_cannot_bypass_fail_closed(self):
        store = self.store()
        original = self.approved(store)
        for field, value in [('state', 'invented'), ('expires_at', 'tomorrow'), ('created_at', None), ('approval_expires_at', False)]:
            broken = dict(original, **{field: value})
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('UPDATE drafts SET payload=? WHERE id=?', (json.dumps(broken), original['id']))
            with self.subTest(field=field), self.assertRaises(core.PolicyError):
                store.get_draft(original['id'])

    def test_rejection_and_single_claim_race_and_unverified_verification(self):
        store = self.store()
        for approve in (False, True):
            draft = self.approved(store) if approve else self.draft(store)
            self.assertEqual(store.reject_draft(draft['id'])['state'], 'rejected')
            with self.assertRaises(core.PolicyError):
                store.approve_draft(draft['id'], draft['digest'])
            with self.assertRaises(core.PolicyError):
                store.claim_send(draft['id'], '42', self.settings())
        draft = self.approved(store)
        connections = [self.store() for _ in range(6)]
        def claim(connection):
            try:
                return connection.claim_send(draft['id'], '42', self.settings())
            except core.PolicyError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            claims = [result for result in pool.map(claim, connections) if result]
        self.assertEqual(len(claims), 1)
        operation = claims[0]['operation_id']
        self.assertEqual(store.operation(operation)['text'], draft['text'])
        store.finish_send(operation, 'unverified', remote_id='post-accepted')
        with self.assertRaises(core.PolicyError):
            store.finish_send(operation, 'failed')
        self.assertEqual(store.finish_send(operation, 'confirmed', remote_id='readback')['remote_id'], 'readback')
        with self.assertRaises(core.PolicyError):
            store.reject_draft(draft['id'])

    def test_wal_sidecars_private_even_with_permissive_umask(self):
        old = os.umask(0)
        try:
            store = self.store()
            self.draft(store)
        finally:
            os.umask(old)
        for suffix in ('-wal', '-shm'):
            self.assertEqual(Path(str(self.path) + suffix).stat().st_mode & 0o777, 0o600)

    def test_integrity_initialization_failure_closes_database(self):
        real_connect = sqlite3.connect
        class IntegrityFailureConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == 'PRAGMA quick_check':
                    class Result:
                        def fetchone(self): return ['corrupt']
                    return Result()
                return super().execute(sql, *args)
        opened = []
        def connect(*args, **kwargs):
            connection = real_connect(*args, factory=IntegrityFailureConnection, **kwargs)
            opened.append(connection)
            return connection
        try:
            with patch.object(core.sqlite3, 'connect', connect), self.assertRaises(core.PolicyError):
                core.Store(self.path)
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute('SELECT 1')
        finally:
            for connection in opened:
                connection.close()

    def test_operation_lookup_rejects_missing_identifiers(self):
        store = self.store()
        self.draft(store)
        for identifier in (None, '', 'missing'):
            with self.subTest(identifier=identifier), self.assertRaises(core.PolicyError):
                store.operation(identifier)

    def test_operation_identifiers_are_unique_even_on_uuid_collision(self):
        store = self.store()
        first = self.approved(store)
        second = self.approved(store, 'BV1xx411c7mE')
        with patch.object(core.uuid, 'uuid4', return_value='same-operation-id'):
            store.claim_send(first['id'], '42', self.settings())
            with self.assertRaises(core.PolicyError):
                store.claim_send(second['id'], '42', self.settings())
        self.assertEqual(store.get_draft(second['id'])['state'], 'approved')

    def test_operations_terminal_unknown_never_replay_and_failed_releases_share_cap(self):
        store = self.store()
        self.assertTrue(hasattr(store, 'finish_send'), 'finish missing')
        claimed = store.claim_send(self.approved(store)['id'], '42', self.settings())
        operation = claimed['operation_id']
        unknown = store.finish_send(operation, 'unknown', error='ambiguous')
        self.assertEqual(store.operation(operation)['state'], 'unknown')
        self.assertEqual(store.finish_send(operation, 'unknown'), unknown)
        with self.assertRaises(core.PolicyError):
            store.claim_send(claimed['id'], '42', self.settings())
        with self.assertRaises(core.PolicyError):
            store.finish_send(operation, 'failed')
        self.assertEqual(store.finish_send(operation, 'confirmed', remote_id='123')['state'], 'confirmed')
        with self.assertRaises(core.PolicyError):
            store.finish_send(operation, 'sending')
        other = store.claim_send(self.approved(store, 'BV1xx411c7mE')['id'], '42', self.settings())
        self.assertEqual(store.finish_send(other['operation_id'], 'failed')['state'], 'failed')

    def test_duplicate_generated_draft_id_cannot_replace_approved_payload(self):
        from unittest.mock import patch
        store=self.store()
        first=self.approved(store)
        with patch('hermes_bilibili.core.uuid.uuid4',return_value=first['id']):
            with self.assertRaises(core.PolicyError):
                store.create_draft('42','comment','BV1234567890','different exact text',reason='A different content reason',score=19)
        self.assertEqual(store.get_draft(first['id'])['digest'],first['digest'])


if __name__ == '__main__':
    unittest.main()
