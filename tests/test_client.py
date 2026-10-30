"""Offline fixtures only: no live Bilibili or account access."""
import importlib
import io
import json
import unittest
from unittest.mock import patch

try:
    client = importlib.import_module('hermes_bilibili.client')
except ModuleNotFoundError:
    client = None


class ResponseTests(unittest.TestCase):
    def test_json_rejects_non_object_and_redacts_upstream(self):
        self.assertIsNotNone(client, 'client module must exist')
        self.assertEqual(client.Response(200, {}, b'{"code":0}').json(), {'code': 0})
        for body in (b'[]', b'not-json SECRET', b'{"x":NaN}'):
            with self.subTest(body=body), self.assertRaises(client.APIError) as caught:
                client.Response(200, {}, body).json()
            self.assertNotIn('SECRET', str(caught.exception))


class TransportTests(unittest.TestCase):
    def test_transport_is_https_exact_host_bounded_and_no_redirect(self):
        self.assertTrue(hasattr(client, 'HTTPTransport'), 'restricted transport required')
        transport = client.HTTPTransport()
        for url in ('http://api.bilibili.com/x', 'https://api.bilibili.com.evil/x',
                    'https://evil@api.bilibili.com/x', 'https://api.bilibili.com:444/x',
                    'https://api.bilibili.com./x', 'https://api.bilibili.com/x#frag'):
            with self.subTest(url=url), self.assertRaises(client.APIError):
                transport.request('GET', url)
        class Raw:
            status = 200
            headers = {}
            def read(self, size):
                self.size = size
                return b'12345'
            def __enter__(self): return self
            def __exit__(self, *args): pass
        raw = Raw()
        with patch.object(transport.opener, 'open', return_value=raw) as opened:
            with self.assertRaises(client.APIError):
                transport.request('GET', 'https://api.bilibili.com/x', max_bytes=4)
            self.assertEqual(opened.call_args.kwargs['timeout'], 20)
            self.assertEqual(raw.size, 5)
        with self.assertRaises(client.APIError):
            transport.request('GET', 'https://i0.hdslb.com/x', headers={'Cookie': 'SECRET'})
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://api.bilibili.com/y'))


class FakeStore:
    """Explicit in-memory core contract fixture, not a production store."""
    def __init__(self):
        self.reservations, self.pauses, self.ops, self.cache = [], [], {}, {}
        self.identities, self.finished, self.validated = {}, [], []
    def identity_get(self, key): return self.identities.get(key)
    def identity_bind(self, key, mid):
        self.require_active(mid)
        if key in self.identities and self.identities[key]['mid'] != mid:
            raise client.PolicyError('Identity changed')
        self.identities[key] = {'mid':mid}
    def require_active(self, account):
        if any(a == account for a, _ in self.pauses):
            raise client.PolicyError('Paused fixture account')
    def reserve_request(self, account, settings, **kwargs):
        self.require_active(account)
        self.reservations.append((account, kwargs))
        class Reservation(float): pass
        delay = Reservation(0)
        delay.request_id = len(self.reservations)
        return delay
    def validate_dispatch(self, account, settings, **kwargs):
        self.require_active(account)
        self.validated.append((account, kwargs))
    def finish_dispatch(self, operation_id=None, *, request_id=None):
        self.finished.append((operation_id, request_id))
    def pause(self, account, **kwargs): self.pauses.append((account, kwargs))
    def operation(self, operation_id): return dict(self.ops[operation_id])
    def cache_get(self, key): return self.cache.get(key)
    def cache_put(self, key, value, ttl): self.cache[key] = value


class Fixtures:
    def __init__(self, *responses): self.responses, self.calls = list(responses), []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


def response(data=None, *, code=0, status=200, headers=None):
    return client.Response(status, headers or {}, json.dumps({'code': code, 'data': data}).encode())


def settings():
    from types import SimpleNamespace
    return SimpleNamespace(owner_mid='42', writes_enabled=True, cache_ttl_seconds=600)


class ReadTests(unittest.TestCase):
    def test_search_normalizes_untrusted_metadata_and_validates_bounds(self):
        self.assertTrue(hasattr(client.BiliClient, 'search'), 'search required')
        nav = {'wbi_img': {'img_url': 'https://i0.hdslb.com/7cd084941338484aae1ad9425b84077c.png', 'sub_url': 'https://i0.hdslb.com/4932caff0ff746eab6f01bf08b70ac45.png'}}
        transport = Fixtures(response(nav), response({'result': [{'bvid':'BV1xx411c7mD', 'title':'<em>Fixture</em>', 'mid':9007199254740993}]}))
        api = client.BiliClient(FakeStore(), settings(), transport=transport)
        result = api.search('test', limit=1)
        self.assertTrue(result['untrusted'])
        self.assertEqual(result['items'][0]['mid'], '9007199254740993')
        self.assertEqual(result['items'][0]['title'], 'Fixture')
        self.assertIn('w_rid=', transport.calls[-1][1])
        for keyword, limit in (('', 1), ('x', True), ('x', 21)):
            with self.assertRaises(client.APIError): api.search(keyword, limit=limit)
        self.assertEqual(len(transport.calls), 2)


class VideoTests(unittest.TestCase):
    def test_video_metadata_is_bounded_cached_and_account_scoped(self):
        self.assertTrue(hasattr(client.BiliClient, 'video'), 'video required')
        raw = {'bvid':'BV1xx411c7mD', 'aid':7, 'cid':8, 'title':'Fixture', 'desc':'Details',
               'duration':60, 'owner':{'mid':42, 'name':'Owner'}, 'stat':{'view':100},
               'pages':[{'cid':8, 'page':1, 'part':'First', 'duration':60}]}
        store, transport = FakeStore(), Fixtures(response(raw), response(raw))
        api = client.BiliClient(store, settings(), transport=transport)
        first = api.video('BV1xx411c7mD')
        self.assertEqual(first['aid'], '7')
        self.assertEqual(first['cid'], '8')
        self.assertTrue(first['untrusted'])
        self.assertEqual(api.video('BV1xx411c7mD'), first)
        self.assertEqual(len(transport.calls), 1)
        api._account = {'mid':'9', 'name':'Fixture'}
        api.video('BV1xx411c7mD')
        self.assertEqual(len(transport.calls), 2)
        for bad in ('bv1xx411c7mD', 'BV1xx411c7mD/evil', 7):
            with self.assertRaises(client.APIError): api.video(bad)
        self.assertEqual(len(transport.calls), 2)


class CommentsTests(unittest.TestCase):
    def test_comments_fetch_dynamic_replies_without_cache(self):
        self.assertTrue(hasattr(client.BiliClient, 'comments'), 'comments required')
        video = {'bvid':'BV1xx411c7mD', 'aid':'7', 'cid':'8'}
        item = {'rpid':91, 'member':{'mid':42, 'uname':'Fixture'}, 'content':{'message':'literal <b>text</b>'}, 'ctime':1}
        transport = Fixtures(response({'replies':[item]}), response({'replies':None}))
        api = client.BiliClient(FakeStore(), settings(), transport=transport)
        with patch.object(api, 'video', return_value=video):
            value = api.comments(video['bvid'], limit=1)
            self.assertEqual(value['items'][0]['text'], 'literal <b>text</b>')
            self.assertEqual(value['items'][0]['mid'], '42')
            self.assertTrue(value['untrusted'])
            self.assertEqual(api.comments(video['bvid'])['items'], [])
        for limit in (True, 0, 51):
            with self.assertRaises(client.APIError): api.comments(video['bvid'], limit=limit)
        self.assertEqual(len(transport.calls), 2)


class SubtitleTests(unittest.TestCase):
    def test_subtitles_validate_public_url_strip_cookies_and_bound_cues(self):
        self.assertTrue(hasattr(client.BiliClient, 'subtitles'), 'subtitles required')
        meta = {'subtitle':{'subtitles':[{'lan':'zh-CN','subtitle_url':'//aisubtitle.hdslb.com/fixture.json'}]}}
        body = {'body':[{'from':0, 'to':1.25, 'content':'Fixture'}, {'from':1.25, 'to':2, 'content':'Next'}]}
        nav = {'wbi_img': {'img_url': 'https://i0.hdslb.com/7cd084941338484aae1ad9425b84077c.png',
                           'sub_url': 'https://i0.hdslb.com/4932caff0ff746eab6f01bf08b70ac45.png'}}
        empty = {'subtitle':{'subtitles':[]}}
        transport = Fixtures(response(nav), response(meta), client.Response(200, {}, json.dumps(body).encode()),
                             response(nav), response(empty), response(empty))
        api = client.BiliClient(FakeStore(), settings(), credentials={'sessdata':'SECRET'}, transport=transport)
        with patch.object(api, 'video', return_value={'aid':'7', 'cid':'8'}):
            result = api.subtitles('BV1xx411c7mD')
            self.assertEqual(result['items'][0]['segments'][0], {'start':0, 'end':1.25, 'text':'Fixture'})
            self.assertTrue(result['untrusted'])
            self.assertNotIn('Cookie', transport.calls[2][2]['headers'])
            self.assertEqual(api.subtitles('BV1xx411c7mD')['reason'], 'No subtitles available')
        self.assertEqual(len(transport.calls), 6)


class SendTests(unittest.TestCase):
    def draft(self, kind='comment'):
        return dict(id='d1', operation_id='op1', account='9', kind=kind,
                    target='BV1xx411c7mD' if kind == 'comment' else '42', text='Exact fixture',
                    reason='Fixture reason', score=18, share=kind == 'comment', state='sending', digest='fixture')

    def test_comment_requires_bound_claim_actual_account_csrf_and_nonempty_remote_id(self):
        self.assertTrue(hasattr(client.BiliClient, 'send_comment'), 'comment send required')
        draft, store = self.draft(), FakeStore()
        store.ops['op1'] = dict(draft)
        transport = Fixtures(response({'isLogin':True, 'mid':9}), response({'rpid':91}))
        api = client.BiliClient(store, settings(), credentials={'bili_jct':'csrf'}, transport=transport)
        with patch.object(api, 'video', return_value={'aid':'7'}):
            self.assertEqual(api.send_comment(draft), {'accepted':True, 'remote_id':'91'})
        from urllib.parse import parse_qs
        post = transport.calls[-1]
        self.assertEqual(post[0], 'POST')
        self.assertEqual(parse_qs(post[2]['data'].decode())['csrf'], ['csrf'])
        self.assertEqual(store.reservations[-1][1], {'write':True, 'operation_id':'op1', 'dm':False})
        self.assertNotIn('verified', api.__dict__)
        for change in ({'text':'changed'}, {'account':'8'}, {'operation_id':'fake'}, {'state':'draft'}):
            with self.subTest(change=change), self.assertRaises(client.PolicyError):
                api.send_comment(dict(draft, **change))
        self.assertEqual(len(transport.calls), 2)
        other = client.BiliClient(store, settings(), transport=Fixtures())
        with self.assertRaises(client.PolicyError): other.send_comment(draft)


    def test_dm_is_owner_only_mutual_and_uses_persisted_device(self):
        self.assertTrue(hasattr(client.BiliClient, 'send_dm'), 'DM send required')
        draft, store = self.draft('dm'), FakeStore()
        store.ops['op1'] = dict(draft)
        from urllib.parse import parse_qs
        devices = []
        for _ in range(2):
            transport = Fixtures(response({'isLogin':True, 'mid':9}), response({'attribute':6}), response({'msg_key':123}))
            api = client.BiliClient(store, settings(), credentials={'bili_jct':'csrf'}, transport=transport)
            self.assertEqual(api.send_dm(draft), {'accepted':True, 'remote_id':'123'})
            fields = parse_qs(transport.calls[-1][2]['data'].decode())
            self.assertEqual(fields['msg[receiver_id]'], ['42'])
            self.assertEqual(fields['csrf'], ['csrf'])
            devices.append(fields['msg[dev_id]'])
            self.assertTrue(store.reservations[-1][1]['dm'])
        self.assertEqual(devices[0], devices[1])
        transport = Fixtures(response({'isLogin':True, 'mid':9}), response({'attribute':2}))
        api = client.BiliClient(store, settings(), credentials={'bili_jct':'csrf'}, transport=transport)
        with self.assertRaises(client.PolicyError): api.send_dm(draft)
        self.assertEqual(len(transport.calls), 2)
        with self.assertRaises(client.PolicyError): api.send_dm(dict(draft, target='99'))


class VerifyTests(unittest.TestCase):
    def test_verification_requires_real_reply_author_text_target_and_id(self):
        self.assertTrue(hasattr(client.BiliClient, 'verify'), 'verification required')
        draft = dict(SendTests().draft(), state='unverified', remote_id='91')
        item = {'rpid':91, 'oid':7, 'type':1, 'member':{'mid':9}, 'content':{'message':draft['text']}}
        for change, expected in (({}, True), ({'member':{'mid':8}}, False),
                                 ({'content':{'message':'different'}}, False), ({'oid':8}, False), ({'rpid':92}, False)):
            store = FakeStore()
            store.ops['op1'] = dict(draft)
            transport = Fixtures(response({'isLogin':True, 'mid':9}), response({'root':dict(item, **change)}))
            api = client.BiliClient(store, settings(), transport=transport)
            with patch.object(api, 'video', return_value={'aid':'7'}):
                result = api.verify(draft)
            self.assertEqual(result['verified'], expected)
            self.assertEqual(transport.calls[-1][0], 'GET')
            self.assertIn('/x/v2/reply/detail?', transport.calls[-1][1])
        dm = dict(draft, kind='dm')
        api = client.BiliClient(FakeStore(), settings(), transport=Fixtures())
        self.assertFalse(api.verify(dm)['verified'])


class EdgeTests(unittest.TestCase):
    def test_post_code_zero_without_remote_id_is_never_accepted_or_retried(self):
        for data in ({}, {'rpid':''}, {'rpid':0}):
            draft, store = SendTests().draft(), FakeStore()
            store.ops['op1'] = dict(draft)
            transport = Fixtures(response({'isLogin':True, 'mid':9}), response(data))
            api = client.BiliClient(store, settings(), credentials={'bili_jct':'csrf'}, transport=transport)
            with patch.object(api, 'video', return_value={'aid':'7'}):
                with self.assertRaises(client.APIError): api.send_comment(draft)
            self.assertEqual(len(transport.calls), 2)

    def test_account_mismatch_never_posts_and_upstream_errors_are_redacted(self):
        draft, store = SendTests().draft(), FakeStore()
        store.ops['op1'] = dict(draft)
        transport = Fixtures(response({'isLogin':True, 'mid':8}))
        api = client.BiliClient(store, settings(), credentials={'bili_jct':'csrf'}, transport=transport)
        with self.assertRaises(client.APIError): api.send_comment(draft)
        self.assertEqual(len(transport.calls), 1)
        api = client.BiliClient(FakeStore(), settings(), transport=Fixtures(RuntimeError('SECRET')))
        with self.assertRaises(client.APIError) as caught: api.account()
        self.assertNotIn('SECRET', str(caught.exception))

    def test_subtitle_foreign_url_and_invalid_timestamp_fail_closed(self):
        for url, cue in (('https://evil.test/sub', {'from':0,'to':1,'content':'x'}),
                         ('https://aisubtitle.hdslb.com/sub', {'from':2,'to':1,'content':'x'})):
            nav = {'wbi_img': {'img_url': 'https://i0.hdslb.com/7cd084941338484aae1ad9425b84077c.png',
                               'sub_url': 'https://i0.hdslb.com/4932caff0ff746eab6f01bf08b70ac45.png'}}
            transport = Fixtures(response(nav), response({'subtitle':{'subtitles':[{'subtitle_url':url}]}}),
                                 client.Response(200, {}, json.dumps({'body':[cue]}).encode()))
            api = client.BiliClient(FakeStore(), settings(), transport=transport)
            with patch.object(api, 'video', return_value={'aid':'7','cid':'8'}):
                with self.assertRaises(client.APIError): api.subtitles('BV1xx411c7mD')
            self.assertEqual(len(transport.calls), 2 if 'evil' in url else 3)


    def test_frames_bridge_returns_sheet_and_propagates_policy(self):
        self.assertTrue(hasattr(client.BiliClient, 'frames'), 'frames bridge required')
        from pathlib import Path
        api = client.BiliClient(FakeStore(), settings(), transport=Fixtures())
        with patch('hermes_bilibili.media.extract_frames', return_value={'path':'/fixture/sheet.png', 'untrusted':True}) as helper:
            value = api.frames('BV1xx411c7mD', Path('/fixture'))
        self.assertEqual(value['sheet'], '/fixture/sheet.png')
        helper.assert_called_once_with(api, 'BV1xx411c7mD', Path('/fixture'))

    def test_error_exposes_definitive_versus_ambiguous_without_raw_data(self):
        api = client.BiliClient(FakeStore(), settings(), transport=Fixtures(response({}, code=-400)))
        with self.assertRaises(client.APIError) as caught: api.account()
        self.assertTrue(caught.exception.definitive)
        self.assertFalse(caught.exception.ambiguous)
        api = client.BiliClient(FakeStore(), settings(), transport=Fixtures(RuntimeError('SECRET')))
        with self.assertRaises(client.APIError) as caught: api.account()
        self.assertFalse(caught.exception.definitive)
        self.assertTrue(caught.exception.ambiguous)


    def test_authenticated_identity_survives_client_restart_and_uses_account_pause_budget(self):
        store = FakeStore()
        transport = Fixtures(response({'isLogin':True,'mid':9}), response({'isLogin':True,'mid':9}))
        client.BiliClient(store, settings(), credentials={'sessdata':'fixture'}, transport=transport).account()
        restarted = client.BiliClient(store, settings(), credentials={'sessdata':'fixture'}, transport=transport)
        restarted.account()
        self.assertEqual(store.reservations[0][0], 'anonymous')
        self.assertEqual(store.reservations[1][0], '9')
        self.assertNotIn('fixture', ''.join(store.cache))
        other = client.BiliClient(store, settings(), credentials={'sessdata':'different'}, transport=Fixtures(response({'isLogin':True,'mid':8})))
        other.account()
        self.assertEqual(store.reservations[-1][0], 'anonymous')

    def test_real_store_dm_uses_valid_bounded_cache_ttl(self):
        import tempfile
        from pathlib import Path
        from hermes_bilibili.core import Store, Settings
        with tempfile.TemporaryDirectory() as root:
            clock = [1700000000.0]
            def advance(delay): clock[0] += delay
            store = Store(Path(root) / 'state.sqlite', clock=lambda:clock[0])
            self.addCleanup(store.close)
            config = Settings.from_mapping({'owner_mid':'42','writes_enabled':True})
            draft = store.create_draft('9', 'dm', '42', 'fixture', reason='Private fixture', score=0, share=False)
            store.approve_draft(draft['id'], draft['digest'])
            draft = store.claim_send(draft['id'], '9', config)
            transport = Fixtures(response({'isLogin':True,'mid':9}), response({'attribute':6}), response({'msg_key':123}))
            api = client.BiliClient(store, config, credentials={'bili_jct':'csrf'}, transport=transport, sleep=advance)
            self.assertEqual(api.send_dm(draft)['remote_id'], '123')
            with self.assertRaises(client.PolicyError): api.send_dm(draft)
            self.assertEqual(len([c for c in transport.calls if c[0] == 'POST']), 1)


    def test_http_definitive_client_rejection_and_ambiguous_server_failure(self):
        for status, definitive in ((400, True), (401, True), (412, True), (429, True), (500, False), (503, False)):
            api = client.BiliClient(FakeStore(), settings(), transport=Fixtures(response({}, status=status)))
            with self.assertRaises(client.APIError) as caught: api.account()
            self.assertEqual(caught.exception.definitive, definitive)
            self.assertEqual(caught.exception.ambiguous, not definitive)

    def test_paused_known_account_cannot_request_nav_after_client_restart(self):
        import tempfile
        from pathlib import Path
        from hermes_bilibili.core import Store, Settings
        with tempfile.TemporaryDirectory() as root:
            clock = [1700000000.0]
            def advance(delay): clock[0] += delay
            store = Store(Path(root) / 'state.sqlite', clock=lambda:clock[0])
            self.addCleanup(store.close)
            config = Settings()
            creds = {'sessdata':'fixture'}
            api = client.BiliClient(store, config, credentials=creds, transport=Fixtures(response({'isLogin':True,'mid':9})), sleep=advance)
            api.account()
            store.pause('9', reason='fixture pause', manual=True)
            transport = Fixtures()
            api = client.BiliClient(store, config, credentials=creds, transport=transport, sleep=advance)
            with self.assertRaises(client.PolicyError): api.account()
            self.assertEqual(transport.calls, [])


    def test_real_store_approved_comment_post_then_exact_readback_confirmed(self):
        import tempfile
        from pathlib import Path
        from hermes_bilibili.core import Store, Settings
        with tempfile.TemporaryDirectory() as root:
            clock = [1700000000.0]
            def advance(delay): clock[0] += delay
            store = Store(Path(root) / 'state.sqlite', clock=lambda:clock[0])
            self.addCleanup(store.close)
            config = Settings.from_mapping({'owner_mid':'42','writes_enabled':True})
            draft = store.create_draft('9', 'comment', 'BV1xx411c7mD', 'Exact fixture', reason='Fixture evidence', score=18)
            store.approve_draft(draft['id'], draft['digest'])
            draft = store.claim_send(draft['id'], '9', config)
            nav = {'isLogin':True, 'mid':9}
            root_reply = {'rpid':91,'oid':7,'type':1,'member':{'mid':9},'content':{'message':draft['text']}}
            transport = Fixtures(response(nav), response({'rpid':91}), response(nav), response({'root':root_reply}))
            api = client.BiliClient(store, config, credentials={'bili_jct':'csrf'}, transport=transport, sleep=advance)
            with patch.object(api, 'video', return_value={'aid':'7'}):
                sent = api.send_comment(draft)
                self.assertEqual(store.operation(draft['operation_id'])['state'], 'sending')
                accepted = store.finish_send(draft['operation_id'], 'unverified', remote_id=sent['remote_id'])
                check = api.verify(accepted)
            self.assertTrue(check['verified'])
            final = store.finish_send(draft['operation_id'], 'confirmed', remote_id=check['remote_id'])
            self.assertEqual(final['state'], 'confirmed')


class WBITests(unittest.TestCase):
    def test_canonical_wbi_vector(self):
        self.assertTrue(hasattr(client, 'wbi_sign'), 'canonical WBI signer required')
        result = client.wbi_sign({'foo': '114', 'bar': '514', 'baz': '1919810'},
            '7cd084941338484aae1ad9425b84077c', '4932caff0ff746eab6f01bf08b70ac45', timestamp=1702204169)
        self.assertEqual(result['w_rid'], '6149fdadf571698ca7e6a567265cd0ee')
        self.assertEqual(result['wts'], '1702204169')
        self.assertEqual(client.wbi_sign({'x': "a!b'c(d)e*f"}, '7cd084941338484aae1ad9425b84077c',
            '4932caff0ff746eab6f01bf08b70ac45', timestamp=1)['x'], 'abcdef')


class RiskTests(unittest.TestCase):
    def test_risk_and_auth_failures_pause_even_when_http_body_says_success(self):
        cases = [(s, 0, s == 401) for s in (412, 429, 401)]
        cases += [(200, c, c in (-101, 12015, -105)) for c in (-412, -352, -799, -509, -10403, 12051, 10031, 12015, -105, -101)]
        for status, code, manual in cases:
            with self.subTest(status=status, code=code):
                store = FakeStore()
                transport = Fixtures(response({}, status=status, code=code, headers={'Retry-After': '900'}))
                api = client.BiliClient(store, settings(), transport=transport)
                with self.assertRaises(client.APIError): api.account()
                self.assertEqual(len(store.pauses), 1, 'risk response must persist pause')
                self.assertEqual(store.pauses[0][1]['manual'], manual)
                self.assertEqual(store.pauses[0][1]['retry_after'], 900)
                self.assertEqual(len(transport.calls), 1)


class AccountTests(unittest.TestCase):
    def test_account_authentication_preserves_integer_precision(self):
        self.assertTrue(hasattr(client, 'BiliClient'), 'account client required')
        store = FakeStore()
        transport = Fixtures(response({'isLogin': True, 'mid': 9007199254740993, 'uname': 'Fixture'}))
        api = client.BiliClient(store, settings(), credentials={'sessdata': 'fixture', 'bili_jct': 'csrf'}, transport=transport)
        self.assertEqual(api.account(), {'mid': '9007199254740993', 'name': 'Fixture'})
        self.assertEqual(store.reservations[0][0], 'anonymous')
        self.assertIn('SESSDATA=fixture', transport.calls[0][2]['headers']['Cookie'])
        with self.assertRaises(client.APIError):
            client.BiliClient(store, settings(), credentials={'sessdata': 'bad\r\nheader'}, transport=transport)


if __name__ == '__main__':
    unittest.main()
