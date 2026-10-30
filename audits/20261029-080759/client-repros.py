#!/usr/bin/env python3
"""Offline audit probes. No real credentials, profiles, or Bilibili traffic."""
import os
import sys
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
import hashlib
import urllib.parse

REPO = Path('/home/admin/projects/hermes-bilibili')
sys.path.insert(0, str(REPO))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
root_context = tempfile.TemporaryDirectory(prefix='bili-client-audit-', dir=os.environ['TMPDIR'])
ROOT = Path(root_context.name)
os.environ['HOME'] = str(ROOT / 'home')
Path(os.environ['HOME']).mkdir()
from hermes_bilibili.client import BiliClient, Response, APIError, wbi_sign
from hermes_bilibili.core import Store, Settings, PolicyError
from hermes_bilibili import media

# Fail closed if an accidental network path is reached.
import socket
socket.socket.connect = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('network prohibited'))

class Fixtures:
    def __init__(self, *values): self.values, self.calls = list(values), []
    def request(self, method, url, **kwargs):
        self.calls.append({'method':method, 'url':url, 'headers':kwargs.get('headers', {})})
        value = self.values.pop(0)
        if isinstance(value, Exception): raise value
        return Response(200, {}, json.dumps({'code':0, 'data':value}).encode())

results = []
def record(name, **data): results.append(dict(probe=name, **data))

# Identity cache expires but per-account manual pause does not.
clock = [1700000000.0]
store = Store(ROOT / 'identity.sqlite', clock=lambda: clock[0])
cfg = Settings()
creds = {'sessdata':'NONSECRET-FIXTURE'}
first = BiliClient(store, cfg, credentials=creds, transport=Fixtures({'isLogin':True,'mid':9}), sleep=lambda _:None)
first.account()
store.pause('9', reason='audit fixture', manual=True)
blocked = Fixtures({'isLogin':True,'mid':9})
try: BiliClient(store, cfg, credentials=creds, transport=blocked, sleep=lambda _:None).account()
except PolicyError: pass
clock[0] += 86401
transport = Fixtures({'isLogin':True,'mid':9})
api = BiliClient(store, cfg, credentials=creds, transport=transport, sleep=lambda _:None)
account = api.account()
record('identity_expiry', before_expiry_calls=len(blocked.calls), after_expiry_calls=len(transport.calls), returned_account=account['mid'], reserved_accounts=[row[0] for row in store._db.execute('SELECT account FROM requests').fetchall()])
store.close()

# Actual new account is paused but nav is dispatched under the old cache identity.
clock[0] += 100
store = Store(ROOT / 'switch.sqlite', clock=lambda:clock[0])
transport = Fixtures({'isLogin':True,'mid':9}, {'isLogin':True,'mid':8})
api = BiliClient(store, cfg, credentials=creds, transport=transport, sleep=lambda _:None)
api.account()
store.pause('8', reason='audit new account paused', manual=True)
clock[0] += 10
new = api.account()
record('actual_account_switch', new_account=new['mid'], calls=len(transport.calls), reserved_accounts=[row[0] for row in store._db.execute('SELECT account FROM requests').fetchall()])
store.close()

# Exact readback is checked against caller-provided remote_id, not persisted id/state.
clock[0] += 100
store = Store(ROOT / 'verify.sqlite', clock=lambda:clock[0])
write_cfg = Settings(owner_mid='42', writes_enabled=True)
draft = store.create_draft('9', 'comment', 'BV1xx411c7mD', 'same literal text', reason='audit fixture', score=18)
store.approve_draft(draft['id'], draft['digest'])
op = store.claim_send(draft['id'], '9', write_cfg)
op = store.finish_send(op['operation_id'], 'unverified', remote_id='91')
root_reply = {'rpid':92,'oid':7,'type':1,'member':{'mid':9},'content':{'message':op['text']}}
transport = Fixtures({'isLogin':True,'mid':9}, {'root':root_reply})
api = BiliClient(store, cfg, transport=transport, sleep=lambda _:None)
with patch.object(api, 'video', return_value={'aid':'7'}):
    checked = api.verify(dict(op, remote_id='92'))
record('readback_remote_id', stored_remote_id=store.operation(op['operation_id'])['remote_id'], supplied_remote_id='92', result=checked)
store.close()

# A pause introduced while waiting does not stop subtitle CDN dispatch.
clock[0] += 100
store = Store(ROOT / 'subtitle.sqlite', clock=lambda:clock[0])
class SubtitleFixtures(Fixtures):
    def request(self, method, url, **kwargs):
        if 'aisubtitle' in url:
            self.calls.append({'method':method,'url':url,'headers':kwargs.get('headers',{})})
            return Response(200, {}, b'{"body":[{"from":0,"to":1,"content":"fixture"}]}')
        return super().request(method,url,**kwargs)
transport = SubtitleFixtures({'subtitle':{'subtitles':[{'subtitle_url':'https://aisubtitle.hdslb.com/fixture.json'}]}})
waits = []
def sleep_and_pause(delay):
    clock[0] += max(delay, 0)
    waits.append(delay)
    if len(waits) == 2: store.pause('anonymous', reason='pause during subtitle wait', manual=True)
api = BiliClient(store, cfg, transport=transport, sleep=sleep_and_pause)
with patch.object(api, 'video', return_value={'aid':'7','cid':'8'}): value = api.subtitles('BV1xx411c7mD')
record('subtitle_dispatch_pause', calls_after_pause=[c['url'] for c in transport.calls if 'aisubtitle' in c['url']], available=value['available'])
store.close()

# Compare encoding to upstream's encodeURIComponent algorithm using a real Node call.
keys = ('7cd084941338484aae1ad9425b84077c','4932caff0ff746eab6f01bf08b70ac45')
params = {'search_type':'video','keyword':'hello world','page':1,'page_size':5}
ours = wbi_sign(params,*keys,timestamp=1702204169)
node_script = "import {signWbi} from '/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/bili-api.mjs'; console.log(signWbi("+json.dumps(params)+",{img_url:'https://fixture/"+keys[0]+".png',sub_url:'https://fixture/"+keys[1]+".png'},1702204169000))"
upstream = subprocess.run(['node','--input-type=module','-e',node_script], check=True, capture_output=True, text=True, timeout=15).stdout.strip()
upstream_id = urllib.parse.parse_qs(upstream)['w_rid'][0]
record('wbi_space_encoding', python_w_rid=ours['w_rid'], upstream_w_rid=upstream_id, differs=ours['w_rid'] != upstream_id, upstream_query=upstream)

# CDN payload can be a local HLS manifest referencing another local media file.
local_video = ROOT / 'private-local.ts'
subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc=size=160x90:rate=10','-t','2','-pix_fmt','yuv420p','-threads','1','-f','mpegts',str(local_video)], check=True, capture_output=True, timeout=20)
manifest = ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:2.0,\n'+str(local_video)+'\n#EXT-X-ENDLIST\n').encode()
class MediaClient:
    store = SimpleNamespace(path=ROOT / 'media-state.sqlite')
    def video(self, _): return {'duration':2,'cid':'8'}
    def _request(self, path, params): return {'durl':[{'url':'https://a.bilivideo.com/fixture.mp4','size':len(manifest)}]}
def fake_download(url, target, **kwargs): Path(target).write_bytes(manifest)
try:
    with patch.object(media, 'download_public', side_effect=fake_download):
        collage = media.extract_frames(MediaClient(), 'BV1xx411c7mD', ROOT / 'frames')
    record('ffmpeg_local_manifest', reproduced=True, png_magic=Path(collage['path']).read_bytes()[:8].hex(), manifest_referenced_local_file=local_video.name, local_fixture_bytes=local_video.stat().st_size)
except Exception as exc:
    source = ROOT / 'manifest.mp4'
    source.write_bytes(manifest)
    debug = subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file','-show_entries','format=duration:stream=codec_type,width,height','-of','json',str(source)], capture_output=True, text=True, timeout=15)
    record('ffmpeg_local_manifest', reproduced=False, error=str(exc), probe_stderr=debug.stderr[:1500], probe_stdout=debug.stdout[:1500])

print(json.dumps({'network':'prohibited; fixture transports only; subprocesses local', 'home':'temporary under TMPDIR', 'results':results}, ensure_ascii=False, indent=2))
root_context.cleanup()
