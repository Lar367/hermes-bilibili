"""Offline core/service audit; creates only TMPDIR fixtures, never reads secrets."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from multiprocessing import get_context

ROOT = Path('/home/admin/projects/hermes-bilibili')
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from hermes_bilibili.core import Store, Settings, PolicyError
from hermes_bilibili.client import BiliClient, Response
from hermes_bilibili.service import Service


def network_forbidden(*a, **kw):
    raise AssertionError('network forbidden in audit')
socket.create_connection = network_forbidden
socket.socket.connect = network_forbidden


def settings(**kw):
    return Settings(owner_mid='99', writes_enabled=True, timezone='UTC', **kw)


def approved(store, suffix='D'):
    d = store.create_draft('42', 'comment', 'BV1xx411c7m' + suffix, 'fixture exact text', reason='Offline original source', score=18)
    return store.approve_draft(d['id'], d['digest'])


class Transport:
    def __init__(self, now):
        self.now, self.calls = now, []
    def request(self, method, url, **kw):
        self.calls.append({'method': method, 'at': self.now[0]})
        return Response(200, {}, b'{"code":0,"data":{}}')


def client(store, cfg, transport, sleep):
    c = BiliClient(store, cfg, transport=transport, sleep=sleep)
    c._account = {'mid': '42'}
    return c


def process_claim(path, identifier, now, queue):
    try:
        s = Store(path, clock=lambda: now)
        try:
            d = s.claim_send(identifier, '42', settings())
            queue.put({'state': d['state'], 'operation_id': d['operation_id']})
        finally:
            s.close()
    except PolicyError as exc:
        queue.put({'denied': str(exc)})


def process_crash_guard(path, identifier, now):
    s = Store(path, clock=lambda: now)
    op = s.claim_send(identifier, '42', settings())
    s.reserve_request('42', settings(), write=True, operation_id=op['operation_id'])
    s.validate_dispatch('42', settings(), write=True, operation_id=op['operation_id'])
    os._exit(23)


def run():
    result = {}
    with tempfile.TemporaryDirectory(prefix='core-independent-audit-', dir=os.environ['TMPDIR']) as base:
        base = Path(base)
        os.environ['HOME'] = str(base/'home')
        os.environ['HERMES_HOME'] = str(base/'hermes')
        Path(os.environ['HOME']).mkdir()
        Path(os.environ['HERMES_HOME']).mkdir()
        now = [1800000000.0]
        with_store = []
        def store(name):
            s = Store(base/name/'state.db', clock=lambda: now[0])
            with_store.append(s)
            return s
        try:
            # Reproduce real transport invocations at identical actual times.
            s = store('read-spacing')
            cfg = settings()
            t = Transport(now)
            newer = client(s, cfg, t, lambda d: now.__setitem__(0, now[0]+d))
            def stalled(delay):
                now[0] += 120
                newer._request('/offline-newer')
            older = client(s, cfg, t, stalled)
            older._request('/offline-older')
            assert len(t.calls) == 2 and t.calls[0]['at'] == t.calls[1]['at']
            result['read_dispatch_spacing'] = {'confirmed': True, 'transport_calls': t.calls, 'request_rows': [dict(r) for r in s._db.execute('SELECT * FROM requests')]}

            # Reservation is charged to yesterday although both GETs go out today.
            now[0] = datetime(2026,10,28,23,59,59,tzinfo=timezone.utc).timestamp()
            s = store('read-midnight')
            cfg = settings(read_daily=1)
            t = Transport(now)
            def midnight(delay):
                now[0] += 2
            c = client(s, cfg, t, midnight)
            c._request('/offline-before-midnight')
            c.sleep = lambda d: now.__setitem__(0, now[0]+d)
            c._request('/offline-after-midnight')
            usage = s.status('42')['usage']['42']
            assert len(t.calls) == 2 and usage['read_daily'] == 1
            assert all(datetime.fromtimestamp(x['at'], timezone.utc).date().isoformat() == '2026-10-29' for x in t.calls)
            result['read_dispatch_midnight_budget'] = {'confirmed': True, 'read_daily_limit': 1, 'actual_calls_today': len(t.calls), 'reported_usage': usage, 'transport_calls': t.calls}

            # Timezone change reinterprets already consumed daily budget.
            now[0] = datetime(2026,10,28,1,0,tzinfo=timezone.utc).timestamp()
            s = store('timezone-change')
            utc = settings(read_daily=1)
            s.reserve_request('42',utc)
            now[0] = datetime(2026,10,28,20,0,tzinfo=timezone.utc).timestamp()
            try:
                s.reserve_request('42',utc)
            except PolicyError:
                denied_utc = True
            else:
                denied_utc = False
            shifted = Settings(owner_mid='99',writes_enabled=True,timezone='Asia/Shanghai',read_daily=1)
            s.reserve_request('42',shifted)
            assert denied_utc
            result['timezone_change_budget'] = {'confirmed':True,'same_utc_day_requests':2,'daily_limit':1,'before_change_denied':denied_utc,'after_change_accepted':True,'new_usage':s.status('42')['usage']['42']}

            # Same timezone issue also allows six approved shares in original UTC day.
            now[0] = datetime(2026,10,28,1,0,tzinfo=timezone.utc).timestamp()
            s = store('timezone-shares')
            t = Transport(now)
            c = client(s,settings(),t,lambda d:now.__setitem__(0,now[0]+d))
            for suffix in 'DEF':
                op = s.claim_send(approved(s,suffix)['id'],'42',c.settings)
                c._request('/offline-comment',method='POST',write=True,operation_id=op['operation_id'])
                s.finish_send(op['operation_id'],'unverified',remote_id='1')
                now[0]+=120
            now[0] = datetime(2026,10,28,20,0,tzinfo=timezone.utc).timestamp()
            fourth = approved(s,'G')
            try:
                s.claim_send(fourth['id'],'42',c.settings)
            except PolicyError:
                original_limit_denied=True
            else:
                original_limit_denied=False
            c.settings=Settings(owner_mid='99',writes_enabled=True,timezone='Asia/Shanghai')
            for suffix in 'GHI':
                d=fourth if suffix=='G' else approved(s,suffix)
                op=s.claim_send(d['id'],'42',c.settings)
                c._request('/offline-comment',method='POST',write=True,operation_id=op['operation_id'])
                s.finish_send(op['operation_id'],'unverified',remote_id='1')
                now[0]+=120
            assert original_limit_denied and len(t.calls)==6
            result['timezone_change_shares']={'confirmed':True,'share_daily_limit':3,'actual_posts_same_original_day':6,'original_timezone_denied_fourth':True,'new_usage':s.status('42')['usage']['42']}

            # Mutable lifecycle fields are not protected by digest or transition records.
            now[0] = 1800000000.0
            s = store('lifecycle-corruption')
            d = s.create_draft('42','comment','BV1xx411c7mD','fixture exact text',reason='Offline original source',score=18)
            corrupted = dict(d, state='approved', approval_expires_at=now[0]+600)
            with sqlite3.connect(s.path) as db:
                db.execute('UPDATE drafts SET payload=? WHERE id=?',(json.dumps(corrupted),d['id']))
            claimed = s.claim_send(d['id'],'42',settings())
            assert claimed['state']=='sending'
            result['lifecycle_corruption'] = {'confirmed': True, 'operator_approval_calls': 0, 'digest_unchanged': d['digest']==claimed['digest'], 'resulting_state': claimed['state'], 'scope': 'requires local SQLite mutation; not a remote exploit'}

            # Cache SQL columns are not shape/type checked and accept non-expiring NULL.
            s = store('cache-corruption')
            s.cache_put('fixture', {'value': 1}, 1)
            with sqlite3.connect(s.path) as db:
                db.execute("UPDATE cache SET expires_at='tomorrow' WHERE key='fixture'")
            try:
                s.cache_get('fixture')
            except Exception as exc:
                result['cache_expiry_corruption'] = {'exception':type(exc).__name__, 'safe_policy_error':isinstance(exc,PolicyError), 'message':str(exc)}
            assert result['cache_expiry_corruption']['exception']=='TypeError'

            # Distinct OS processes share one claim; not just threads/connections.
            s = store('process-race')
            d = approved(s)
            ctx = get_context('spawn')
            queue = ctx.Queue()
            ps = [ctx.Process(target=process_claim,args=(str(s.path),d['id'],now[0],queue)) for _ in range(4)]
            for p in ps: p.start()
            for p in ps: p.join(15)
            claims = [queue.get(timeout=3) for _ in ps]
            assert all(p.exitcode==0 for p in ps)
            assert sum(c.get('state')=='sending' for c in claims)==1
            result['multiprocess_single_claim'] = {'passed': True,'children':len(ps),'accepted':sum(c.get('state')=='sending' for c in claims),'denied':sum('denied' in c for c in claims)}

            # Abrupt child exit leaves guard durable; resume doesn't recover it.
            s = store('crash-guard')
            d = approved(s)
            crash = ctx.Process(target=process_crash_guard,args=(str(s.path),d['id'],now[0]))
            crash.start(); crash.join(15)
            assert crash.exitcode==23
            durable = Store(s.path,clock=lambda:now[0]); with_store.append(durable)
            assert durable.status()['write_in_flight']
            durable.resume('42')
            assert durable.status()['write_in_flight']
            op = durable.get_draft(d['id'])['operation_id']
            durable.finish_dispatch(op)
            assert not durable.status()['write_in_flight']
            result['crash_guard']={'passed':True,'child_exit':crash.exitcode,'guard_survives':True,'resume_clears_guard':False,'explicit_matching_finish_clears_guard':True,'scope':'documented fail-closed policy; no supported operator unlock command'}

            # Direct core dispatch before its future slot isn't rejected.
            s = store('future-slot')
            cfg = settings()
            s.reserve_request('43',cfg)
            op = s.claim_send(approved(s)['id'],'42',cfg)
            delay = s.reserve_request('42',cfg,write=True,operation_id=op['operation_id'])
            s.validate_dispatch('42',cfg,write=True,operation_id=op['operation_id'])
            assert delay==1.5 and s.operation(op['operation_id'])['dispatched_at']>now[0]
            result['future_slot_core_contract']={'confirmed':True,'required_wait':delay,'clock':now[0],'dispatched_at':s.operation(op['operation_id'])['dispatched_at'],'scope':'BiliClient normally calls sleep; direct API contract only'}

            # Simulated old schema without operation_id: CREATE IF NOT EXISTS is not migration.
            legacy=base/'legacy'/'state.db'
            legacy.parent.mkdir()
            with sqlite3.connect(legacy) as db:
                db.execute('CREATE TABLE requests(id INTEGER PRIMARY KEY,account TEXT NOT NULL,slot REAL NOT NULL,write_request INTEGER NOT NULL,dm INTEGER NOT NULL)')
            old = Store(legacy,clock=lambda:now[0]); with_store.append(old)
            try:
                old.reserve_request('42',cfg)
            except PolicyError as exc:
                result['legacy_schema']={'error':str(exc),'columns':[r['name'] for r in old._db.execute('PRAGMA table_info(requests)')],'scope':'synthetic earlier schema, not proven released version'}
            assert 'legacy_schema' in result
        finally:
            for s in with_store: s.close()
    return result


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
