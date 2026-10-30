"""Regression fixtures: synthetic credentials, no Bilibili or profile access."""
import hashlib
import unittest
from unittest.mock import patch
from test_client import client, FakeStore, Fixtures, response, settings


class IdentityFixTests(unittest.TestCase):
    def test_nav_binds_permanently_and_refuses_identity_switch(self):
        store = FakeStore()
        transport = Fixtures(response({'isLogin':True,'mid':9}), response({'isLogin':True,'mid':8}))
        api = client.BiliClient(store, settings(), credentials={'sessdata':'SYNTHETIC'}, transport=transport)
        api.account()
        self.assertEqual(store.identity_get(api._identity_key), {'mid':'9'})
        with self.assertRaises(client.PolicyError): api.account()
        self.assertEqual(api._account['mid'], '9')
        self.assertEqual(store.identity_get(api._identity_key), {'mid':'9'})

    def test_nav_checks_new_account_pause_before_return(self):
        store = FakeStore()
        store.pause('8', manual=True)
        api = client.BiliClient(store, settings(), transport=Fixtures(response({'isLogin':True,'mid':8})))
        with self.assertRaises(client.PolicyError): api.account()
        self.assertIsNone(api._account)

    def test_real_store_identity_pause_survives_86401_seconds(self):
        import tempfile
        from pathlib import Path
        from hermes_bilibili.core import Store, Settings
        clock = [1700000000.0]
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root)/'state.sqlite', clock=lambda:clock[0])
            self.addCleanup(store.close)
            config = Settings()
            api = client.BiliClient(store, config, credentials={'sessdata':'SYNTHETIC'},
                                    transport=Fixtures(response({'isLogin':True,'mid':9})), sleep=lambda _:None)
            api.account()
            store.pause('9', reason='fixture persistent pause', manual=True)
            clock[0] += 86401
            transport = Fixtures()
            restarted = client.BiliClient(store, config, credentials={'sessdata':'SYNTHETIC'}, transport=transport)
            with self.assertRaises(client.PolicyError): restarted.account()
            self.assertEqual(transport.calls, [])
            self.assertEqual(store.identity_get(api._identity_key), {'mid':'9'})

    def test_permanent_identity_blocks_nav_without_cache(self):
        store = FakeStore()
        key = 'identity:' + hashlib.sha256(b'SYNTHETIC').hexdigest()
        store.identities[key] = {'mid':'9'}
        store.pause('9', manual=True)
        transport = Fixtures(response({'isLogin':True, 'mid':9}))
        api = client.BiliClient(store, settings(), credentials={'sessdata':'SYNTHETIC'}, transport=transport)
        with self.assertRaises(client.PolicyError): api.account()
        self.assertEqual(transport.calls, [])


class VerifyBindingFixTests(unittest.TestCase):
    def test_verification_refuses_unbound_persistent_evidence_before_network(self):
        from test_client import SendTests
        base = dict(SendTests().draft(), state='unverified', remote_id='91')
        for change in ({'remote_id':'92'}, {'digest':'forged'}, {'operation_id':'other'}, {'state':'sending'}):
            store, transport = FakeStore(), Fixtures()
            store.ops['op1'] = base
            api = client.BiliClient(store, settings(), transport=transport)
            self.assertFalse(api.verify(dict(base, **change))['verified'])
            self.assertEqual(transport.calls, [])
        for state in ('draft', 'approved', 'sending', 'failed'):
            store, transport = FakeStore(), Fixtures()
            store.ops['op1'] = dict(base, state=state)
            api = client.BiliClient(store, settings(), transport=transport)
            self.assertFalse(api.verify(base)['verified'])
            self.assertEqual(transport.calls, [])


class DispatchFixTests(unittest.TestCase):
    def test_guard_releases_on_success_and_transport_error(self):
        for result in (response({'ok':True}), RuntimeError('synthetic failure')):
            store = FakeStore()
            api = client.BiliClient(store, settings(), transport=Fixtures(result))
            if isinstance(result, Exception):
                with self.assertRaises(client.APIError): api._request('/fixture')
            else:
                self.assertEqual(api._request('/fixture'), {'ok':True})
            self.assertEqual(store.finished, [(None, 1)])

    def test_read_dispatch_finishes_matching_request_even_on_risk(self):
        class GuardedStore(FakeStore):
            def pause(self, account, **kwargs):
                assert self.validated and not self.finished, 'risk must be processed inside guard'
                super().pause(account, **kwargs)
        store = GuardedStore()
        class RiskTransport:
            def request(self, *args, **kwargs):
                self.guard = not store.finished
                return response({}, code=-412)
        transport = RiskTransport()
        api = client.BiliClient(store, settings(), transport=transport, sleep=lambda _:None)
        with self.assertRaises(client.APIError): api._request('/x/test')
        self.assertTrue(transport.guard)
        self.assertEqual(store.validated[-1][1]['request_id'], 1)
        self.assertEqual(store.finished, [(None, 1)])
        self.assertEqual(len(store.pauses), 1)

    def test_subtitle_pause_during_wait_prevents_public_dispatch(self):
        store = FakeStore()
        meta = {'subtitle':{'subtitles':[{'subtitle_url':'https://aisubtitle.hdslb.com/fixture'}]}}
        nav = {'wbi_img': {'img_url': 'https://i0.hdslb.com/7cd084941338484aae1ad9425b84077c.png',
                           'sub_url': 'https://i0.hdslb.com/4932caff0ff746eab6f01bf08b70ac45.png'}}
        transport = Fixtures(response(nav), response(meta), client.Response(200, {}, b'{"body":[]}'))
        waits = []
        def sleep(delay):
            waits.append(delay)
            if len(waits) == 3: store.pause('anonymous', manual=True)
        api = client.BiliClient(store, settings(), transport=transport, sleep=sleep)
        with patch.object(api, 'video', return_value={'aid':'7','cid':'8'}):
            with self.assertRaises(client.PolicyError): api.subtitles('BV1xx411c7mD')
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(store.finished, [(None, 1), (None, 2)])


class EncodingFixTests(unittest.TestCase):
    def test_wbi_and_wire_query_match_node_space_unicode_plus_vector(self):
        import subprocess, json
        from urllib.parse import urlencode, quote
        keys = ('7cd084941338484aae1ad9425b84077c', '4932caff0ff746eab6f01bf08b70ac45')
        params = {'keyword':'hello 中文 +~', 'page':1}
        script = "const c=require('crypto');const p=" + json.dumps(params) + ";p.wts='1702204169';const keys=" + json.dumps(keys) + ";const mix=" + json.dumps(client.WBI_MIXIN) + ";const key=mix.map(i=>keys.join('')[i]).join('').slice(0,32);const q=Object.keys(p).sort().map(k=>encodeURIComponent(k)+'='+encodeURIComponent(String(p[k]))).join('&');console.log(JSON.stringify({q,rid:c.createHash('md5').update(q+key).digest('hex')}))"
        node = json.loads(subprocess.run(['node','-e',script], capture_output=True, text=True, check=True, timeout=10).stdout)
        signed = client.wbi_sign(params, *keys, timestamp=1702204169)
        self.assertEqual(signed['w_rid'], node['rid'])
        transport = Fixtures(response({}))
        api = client.BiliClient(FakeStore(), settings(), transport=transport)
        api._request('/fixture', signed)
        self.assertEqual(transport.calls[0][1].split('?',1)[1], node['q'] + '&w_rid=' + node['rid'])


class MediaDispatchFixTests(unittest.TestCase):
    def test_public_download_uses_client_guard_and_releases_on_failure(self):
        import io, tempfile
        from pathlib import Path
        from hermes_bilibili import media
        class Raw(io.BytesIO):
            status, headers = 429, {}
        store = FakeStore()
        class Opener:
            def open(self, request, timeout):
                assert store.validated and not store.finished
                assert not any(k.lower() == 'cookie' for k in request.headers)
                return Raw(b'')
        api = client.BiliClient(store, settings(), credentials={'sessdata':'SYNTHETIC'})
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(media.MediaError):
                api.download_public('https://a.bilivideo.com/x', Path(root)/'media', opener=Opener())
        self.assertEqual(store.finished, [(None, 1)])
        self.assertEqual(store.pauses[0][0], 'anonymous')


class DeadlineFixTests(unittest.TestCase):
    def test_open_time_reduces_socket_timeout_before_first_read(self):
        clock = [0.0]
        timeouts = []
        class Socket:
            timeout = None
            def settimeout(self, timeout):
                self.timeout = timeout
                timeouts.append(timeout)
        sock = Socket()
        from types import SimpleNamespace
        class Raw:
            status, headers = 200, {}
            fp = SimpleNamespace(raw=SimpleNamespace(_sock=sock))
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): raise AssertionError('read1 required')
            def read1(self, size):
                clock[0] += min(19, sock.timeout if sock.timeout is not None else 19)
                raise TimeoutError('fixture socket timeout')
        class Opener:
            def open(self, request, timeout):
                clock[0] += 19
                return Raw()
        transport = client.HTTPTransport(clock=lambda: clock[0])
        transport.opener = Opener()
        with self.assertRaises(client.APIError):
            transport.request('GET', 'https://api.bilibili.com/x')
        self.assertLessEqual(clock[0], 20)
        self.assertEqual(timeouts, [1.0])

    def test_transport_checks_total_deadline_between_read1_chunks(self):
        import io
        class Raw(io.BytesIO):
            status, headers = 200, {}
            def read(self, size): raise AssertionError('buffer-filling read forbidden')
            def read1(self, size): return b'x'
        transport = client.HTTPTransport(clock=iter([0, 0, 21]).__next__)
        with patch.object(transport.opener, 'open', return_value=Raw()):
            with self.assertRaisesRegex(client.APIError, 'deadline'):
                transport.request('GET', 'https://api.bilibili.com/x')


if __name__ == '__main__': unittest.main()
