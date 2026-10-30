"""Parent replay of audit scenarios against repaired production code, offline."""
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hermes_bilibili.core import Store, Settings, PolicyError
from hermes_bilibili.client import BiliClient, Response, wbi_sign


def ok(data):
    return Response(200, {}, json.dumps({'code':0,'data':data}).encode())


class Transport:
    def __init__(self, now, responses=None):
        self.now, self.responses, self.calls = now, list(responses or []), []
    def request(self, method, url, **kwargs):
        self.calls.append({'method':method,'url':url,'at':self.now[0]})
        return self.responses.pop(0) if self.responses else ok({})


def denied(fn, text=None):
    try:
        fn()
    except PolicyError as exc:
        if text is not None:
            assert text.lower() in str(exc).lower(), str(exc)
        return str(exc)
    raise AssertionError('expected a safe policy rejection')


def run():
    result = {}
    with tempfile.TemporaryDirectory(prefix='bili-fixed-replay-', dir=os.environ['TMPDIR']) as tmp:
        base = Path(tmp)
        now = [1800000000.0]
        stores = []
        def store(name):
            item = Store(base / name / 'state.db', clock=lambda:now[0])
            stores.append(item)
            return item
        def api(s, transport, sleep=None, credentials=None):
            c = BiliClient(s, Settings(timezone='UTC'), transport=transport,
                           sleep=sleep or (lambda delay:now.__setitem__(0,now[0]+delay)), credentials=credentials)
            c._account = {'mid':'42'}
            return c
        try:
            s = store('late-get')
            transport = Transport(now)
            newer = api(s,transport)
            def stalled(delay):
                now[0] += 120
                newer._request('/newer')
            older = api(s,transport,stalled)
            reason = denied(lambda:older._request('/older'),'spacing')
            assert len(transport.calls)==1
            result['CORE-001-late-get']={'passed':True,'actual_calls':1,'rejection':reason}

            now[0] = datetime(2026,10,28,23,59,59,tzinfo=timezone.utc).timestamp()
            s = store('midnight')
            transport = Transport(now)
            c = api(s,transport,lambda delay:now.__setitem__(0,now[0]+2))
            c.settings = dataclasses.replace(c.settings,read_daily=1)
            c._request('/before-midnight')
            denied(lambda:c._request('/after-midnight'),'budget')
            assert len(transport.calls)==1 and s.status('42')['usage']['42']['read_daily']==1
            result['CORE-001-midnight']={'passed':True,'actual_calls_today':1,'reported_read_daily':1}

            s = store('timezone')
            utc = Settings(timezone='UTC')
            s.reserve_request('42',utc)
            denied(lambda:s.reserve_request('42',dataclasses.replace(utc,timezone='Asia/Shanghai')),'timezone')
            result['CORE-002']={'passed':True,'policy':'reject conflicting budget timezone'}

            s = store('approval')
            d = s.create_draft('42','comment','BV1xx411c7mD','exact',reason='Offline evidence',score=18)
            forged = dict(d,state='approved',approval_expires_at=now[0]+600)
            with sqlite3.connect(s.path) as db:
                db.execute('UPDATE drafts SET payload=? WHERE id=?',(json.dumps(forged),d['id']))
            denied(lambda:s.claim_send(d['id'],'42',Settings(owner_mid='99',writes_enabled=True,timezone='UTC')),'approval')
            result['CORE-003']={'passed':True,'forged_lifecycle_denied':True}

            s = store('cache')
            s.cache_put('fixture',{},60)
            with sqlite3.connect(s.path) as db:
                db.execute("UPDATE cache SET expires_at='tomorrow'")
            denied(lambda:s.cache_get('fixture'),'timestamp')
            result['CORE-004']={'passed':True,'exception':'PolicyError'}

            legacy = base/'legacy.db'
            with sqlite3.connect(legacy) as db:
                db.execute('CREATE TABLE requests(id INTEGER PRIMARY KEY,account TEXT NOT NULL,slot REAL NOT NULL,write_request INTEGER NOT NULL,dm INTEGER NOT NULL)')
            denied(lambda:Store(legacy),'schema')
            result['CORE-005']={'passed':True,'schema_rejected_before_use':True,'database_preserved':legacy.exists()}

            now[0]=1800000000.0
            s = store('identity')
            creds={'sessdata':'SYNTHETIC-AUDIT-FIXTURE'}
            key='identity:'+hashlib.sha256(creds['sessdata'].encode()).hexdigest()
            s.cache_put(key,{'mid':'42'},1)
            s.pause('42',reason='fixture',manual=True)
            now[0]+=86401
            transport=Transport(now,[ok({'isLogin':True,'mid':42})])
            c=BiliClient(s,Settings(timezone='UTC'),credentials=creds,transport=transport,sleep=lambda _:None)
            denied(c.account,'paused')
            assert not transport.calls
            result['CLIENT-01']={'passed':True,'requests_after_cache_expiry':0}

            s=store('switch')
            transport=Transport(now,[ok({'isLogin':True,'mid':42}),ok({'isLogin':True,'mid':43})])
            c=BiliClient(s,Settings(timezone='UTC'),credentials=creds,transport=transport,sleep=lambda d:now.__setitem__(0,now[0]+d))
            c.account()
            denied(c.account,'identity changed')
            assert s.identity_get(key)=={'mid':'42'}
            result['CLIENT-02']={'passed':True,'binding_unchanged':True}

            s=store('verify')
            cfg=Settings(owner_mid='99',writes_enabled=True,timezone='UTC')
            d=s.create_draft('42','comment','BV1xx411c7mD','exact',reason='Offline evidence',score=18)
            s.approve_draft(d['id'],d['digest'])
            op=s.claim_send(d['id'],'42',cfg)
            op=s.finish_send(op['operation_id'],'unverified',remote_id='91')
            transport=Transport(now)
            c=api(s,transport)
            check=c.verify(dict(op,remote_id='92'))
            assert check['verified'] is False and transport.calls==[]
            result['CLIENT-03']={'passed':True,'forged_remote_id_network_calls':0}

            s=store('subtitle')
            transport=Transport(now,[ok({'subtitle':{'subtitles':[{'subtitle_url':'https://aisubtitle.hdslb.com/fixture'}]}})])
            waits=[]
            def wait_pause(delay):
                now[0]+=delay
                waits.append(delay)
                if len(waits)==2:s.pause('42',reason='fixture',manual=True)
            c=api(s,transport,wait_pause)
            with patch.object(c,'video',return_value={'aid':'7','cid':'8'}):
                denied(lambda:c.subtitles('BV1xx411c7mD'),'paused')
            assert len(transport.calls)==1
            result['CLIENT-04']={'passed':True,'subtitle_cdn_calls_after_pause':0}

            keys=('7cd084941338484aae1ad9425b84077c','4932caff0ff746eab6f01bf08b70ac45')
            signed=wbi_sign({'search_type':'video','keyword':'hello world','page':1,'page_size':5},*keys,timestamp=1702204169)
            assert signed['w_rid']=='9ad6cd39b9b952f654b1e0286cdf95f7'
            result['CLIENT-05']={'passed':True,'w_rid':signed['w_rid']}
        finally:
            for s in stores:s.close()
    return result


if __name__=='__main__':
    output=run()
    Path(__file__).with_name('audit-replay-results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
    print(json.dumps(output,ensure_ascii=False,indent=2))
