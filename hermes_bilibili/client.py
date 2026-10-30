"""Restricted Bilibili HTTP support; no credentials are loaded from disk."""
from dataclasses import dataclass
from contextlib import contextmanager
import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit

API_HOSTS = frozenset({'api.bilibili.com', 'api.vc.bilibili.com', 'passport.bilibili.com'})
PUBLIC_HOSTS = frozenset({'i0.hdslb.com', 'i1.hdslb.com', 'i2.hdslb.com', 'aisubtitle.hdslb.com'})


def validate_url(url, hosts):
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.hostname not in hosts or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443) or parts.fragment
                or parts.netloc not in {parts.hostname, parts.hostname + ':443'}
                or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
            raise ValueError()
        return parts
    except (ValueError, TypeError, AttributeError):
        raise APIError('URL rejected by HTTPS host policy') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def set_read_timeout(response, remaining):
    """Apply remaining read budget to stdlib's response socket when present.

    BytesIO fixtures have no socket. Alternate transports must document their
    own blocking contract; this is not a DNS/connect hard-interrupt timer.
    """
    fp = getattr(response, 'fp', None)
    raw = getattr(fp, 'raw', None)
    sock = getattr(raw, '_sock', None)
    if sock is not None:
        sock.settimeout(remaining)


class HTTPTransport:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, url, *, headers=None, data=None, max_bytes=2_000_000):
        parts = validate_url(url, API_HOSTS | PUBLIC_HOSTS)
        if method not in ('GET', 'POST') or type(max_bytes) is not int or not 1 <= max_bytes <= 20_000_000:
            raise APIError('Invalid HTTP request bounds')
        headers = dict(headers or {})
        for key, value in headers.items():
            if '\r' in str(value) or '\n' in str(value) or key.lower() in ('host', 'authorization', 'proxy-authorization'):
                raise APIError('Unsafe HTTP header')
            if key.lower() == 'cookie' and parts.hostname not in API_HOSTS:
                raise APIError('Credentials forbidden on public hosts')
        start = self.clock()
        try:
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                raw = self.opener.open(request, timeout=20)
            except urllib.error.HTTPError as exc:
                raw = exc
            with raw:
                chunks, size = [], 0
                while True:
                    remaining = 20 - (self.clock() - start)
                    if remaining <= 0:
                        raise APIError('HTTP response deadline exceeded')
                    set_read_timeout(raw, remaining)
                    block = getattr(raw, 'read1', raw.read)(min(65536, max_bytes - size + 1))
                    if self.clock() - start >= 20:
                        raise APIError('HTTP response deadline exceeded')
                    if not block:
                        break
                    size += len(block)
                    if size > max_bytes:
                        raise APIError('Response exceeded size limit')
                    chunks.append(block)
                return Response(raw.status, dict(raw.headers), b''.join(chunks))
        except APIError:
            raise
        except Exception:
            raise APIError('HTTP transport failed; outcome may be unknown') from None

from .core import PolicyError


class APIError(PolicyError):
    def __init__(self, message='Bilibili request failed', *, code=None, status=None, definitive=False, ambiguous=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.definitive = bool(definitive)
        self.ambiguous = not self.definitive if ambiguous is None else bool(ambiguous)


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes

    def json(self):
        try:
            value = json.loads(self.body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError, TypeError):
            raise APIError('Invalid JSON response') from None


import re
import time
from urllib.parse import urlencode, quote


def _number(value):
    import math
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10**15:
        raise APIError('Invalid numeric response')
    return value


def _bvid(value):
    if type(value) is not str or not re.fullmatch(r'BV[0-9A-Za-z]{10}', value):
        raise APIError('Invalid BV identifier')
    return value


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[0-9]+', str(value)):
        raise APIError('Invalid numeric identifier')
    return str(value)


import hashlib

WBI_MIXIN = (46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52)


def wbi_sign(params, img_key, sub_key, *, timestamp=None):
    if not re.fullmatch(r'[a-f0-9]{32}', img_key) or not re.fullmatch(r'[a-f0-9]{32}', sub_key):
        raise APIError('Invalid WBI signing keys')
    key = ''.join((img_key + sub_key)[i] for i in WBI_MIXIN)[:32]
    values = {k: re.sub(r"[!'()*]", '', str(v)) for k, v in params.items() if k not in ('w_rid', 'wts')}
    values['wts'] = str(int(time.time() if timestamp is None else timestamp))
    values = dict(sorted(values.items()))
    values['w_rid'] = hashlib.md5((urlencode(values, quote_via=quote, safe='~') + key).encode()).hexdigest()
    return values


class BiliClient:
    def __init__(self, store, settings, *, credentials=None, transport=None, sleep=time.sleep):
        self.store, self.settings = store, settings
        self.transport, self.sleep = transport or HTTPTransport(), sleep
        self._account = None
        self._credentials = dict(credentials or {})
        for key, value in self._credentials.items():
            if key not in ('sessdata', 'bili_jct', 'buvid3') or not isinstance(value, str) or any(ord(c) < 33 or ord(c) > 126 or c in ';,' for c in value):
                raise APIError('Invalid credential format')
        # Never persist credentials: only SHA256 identity -> verified numeric mid.
        # A restarted client budgets nav against its last known authenticated account.
        secret = self._credentials.get('sessdata')
        self._identity_key = 'identity:' + hashlib.sha256(secret.encode()).hexdigest() if secret else None
        if self._identity_key:
            known = self.store.identity_get(self._identity_key)
            if isinstance(known, dict) and re.fullmatch(r'[0-9]+', str(known.get('mid', ''))):
                self._account = {'mid':str(known['mid']), 'name':''}

    @contextmanager
    def dispatch(self, *, write=False, operation_id=None, dm=False):
        """One reservation/guard shared by API, subtitles and public media."""
        account = self._account['mid'] if self._account else 'anonymous'
        delay = self.store.reserve_request(account, self.settings, write=write, operation_id=operation_id, dm=dm)
        acquired = False
        try:
            self.sleep(float(delay))
            self.store.validate_dispatch(account, self.settings, write=write, operation_id=operation_id,
                                         dm=dm, request_id=delay.request_id)
            acquired = True
            yield account
        finally:
            if acquired:
                self.store.finish_dispatch(operation_id, request_id=delay.request_id)

    def _request(self, path, params=None, *, method='GET', host='api.bilibili.com', write=False, operation_id=None, dm=False):
        headers = {'Accept': 'application/json', 'User-Agent': 'hermes-bilibili/1.0'}
        names = {'sessdata': 'SESSDATA', 'bili_jct': 'bili_jct', 'buvid3': 'buvid3'}
        cookie = '; '.join(names[k] + '=' + v for k, v in self._credentials.items() if v)
        if cookie: headers['Cookie'] = cookie
        url = 'https://' + host + path
        data = None
        if method == 'GET' and params: url += '?' + urlencode(params, quote_via=quote, safe='~')
        elif method == 'POST':
            data = urlencode(params or {}).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        with self.dispatch(write=write, operation_id=operation_id, dm=dm) as account:
            try:
                result = self.transport.request(method, url, headers=headers, data=data)
            except Exception:
                raise APIError('Request failed; outcome may be unknown') from None
            try:
                retry_after = max(0, float(next((v for k, v in result.headers.items() if k.lower() == 'retry-after'), 0)))
                if not __import__('math').isfinite(retry_after): retry_after = 0
            except (ValueError, TypeError): retry_after = 0
            if result.status in (412, 429, 401):
                self.store.pause(account, reason='HTTP risk or authentication failure', retry_after=retry_after, manual=result.status == 401)
            if result.status != 200:
                raise APIError('HTTP request rejected', status=result.status,
                               definitive=result.status in (400, 401, 403, 404, 405, 412, 413, 415, 422, 429))
            value = result.json()
            code = value.get('code')
            if type(code) is int and code in (-412, -352, -799, -509, -10403, 12051, 10031, 12015, -105, -101):
                self.store.pause(account, reason='API risk or authentication failure', retry_after=retry_after, manual=code in (12015, -105, -101))
            if type(code) is not int or code != 0:
                raise APIError('API request rejected', code=code if type(code) is int else None, definitive=type(code) is int and code != 0)
            return value.get('data')

    def _signed_request(self, path, params):
        nav = self._request('/x/web-interface/nav')
        try:
            img = nav['wbi_img']
            keys = [urlsplit(img[k]).path.rsplit('/', 1)[-1].split('.')[0] for k in ('img_url', 'sub_url')]
        except (KeyError, TypeError, AttributeError):
            raise APIError('WBI signing unavailable') from None
        return self._request(path, wbi_sign(params, *keys))

    def search(self, keyword, limit=5):
        if not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 200 or type(limit) is not int or not 1 <= limit <= 20:
            raise APIError('Invalid search bounds')
        data = self._signed_request('/x/web-interface/wbi/search/type',
                                    {'search_type': 'video', 'keyword': keyword, 'page': 1, 'page_size': limit})
        import html
        try:
            items = [{'bvid': _bvid(v['bvid']), 'title': html.unescape(re.sub('<[^>]*>', '', str(v.get('title', ''))))[:500],
                      'mid': _id(v['mid']), 'untrusted': True} for v in data.get('result', [])[:limit]]
        except (KeyError, TypeError, AttributeError):
            raise APIError('Invalid search response') from None
        return {'items': items, 'untrusted': True}

    def video(self, bvid):
        bvid = _bvid(bvid)
        key = 'video:' + (self._account['mid'] if self._account else 'anonymous') + ':' + bvid
        cached = self.store.cache_get(key)
        if cached is not None:
            return cached
        data = self._request('/x/web-interface/view', {'bvid': bvid})
        try:
            if _bvid(data['bvid']) != bvid:
                raise APIError('Video identifier mismatch')
            result = {'bvid': bvid, 'aid': _id(data['aid']), 'cid': _id(data['cid']),
                      'title': str(data.get('title', ''))[:500], 'desc': str(data.get('desc', ''))[:12000],
                      'duration': _number(data.get('duration', 0)),
                      'owner': {'mid': _id(data['owner']['mid']), 'name': str(data['owner'].get('name', ''))[:200]},
                      'stat': {k: _number(data.get('stat', {}).get(k, 0)) for k in ('view', 'like', 'reply', 'favorite')},
                      'pages': [{'cid': _id(p['cid']), 'page': _number(p.get('page', 1)),
                                 'part': str(p.get('part', ''))[:500], 'duration': _number(p.get('duration', 0))}
                                for p in data.get('pages', [])[:100]], 'untrusted': True}
        except (KeyError, TypeError, AttributeError):
            raise APIError('Invalid video response') from None
        self.store.cache_put(key, result, self.settings.cache_ttl_seconds)
        return result

    def comments(self, bvid, limit=10):
        bvid = _bvid(bvid)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise APIError('Invalid comment bounds')
        video = self.video(bvid)
        data = self._request('/x/v2/reply', {'type': 1, 'oid': video['aid'], 'pn': 1, 'ps': limit, 'sort': 2})
        try:
            items = [{'remote_id': _id(r['rpid']), 'mid': _id(r['member']['mid']),
                      'name': str(r['member'].get('uname', ''))[:200],
                      'text': str(r['content']['message'])[:2000], 'ctime': _number(r.get('ctime', 0)),
                      'untrusted': True} for r in (data.get('replies') or [])[:limit]]
        except (KeyError, TypeError, AttributeError):
            raise APIError('Invalid comment response') from None
        return {'bvid': bvid, 'items': items, 'untrusted': True}

    @staticmethod
    def _subtitle_tracks(data):
        if not isinstance(data, dict):
            raise APIError('Invalid subtitle metadata')
        subtitle = data.get('subtitle')
        if not isinstance(subtitle, dict):
            raise APIError('Invalid subtitle metadata')
        tracks = subtitle.get('subtitles')
        if not isinstance(tracks, list):
            raise APIError('Invalid subtitle tracks')
        return tracks

    def subtitles(self, bvid):
        bvid = _bvid(bvid)
        video = self.video(bvid)
        data = self._signed_request('/x/player/wbi/v2', {'bvid': bvid, 'cid': video['cid']})
        tracks = self._subtitle_tracks(data)
        source = 'player_wbi_v2'
        if not tracks:
            data = self._request('/x/v2/subtitle/web/view', {
                'oid': video['aid'], 'pid': video['aid'],
                'context_ext': json.dumps({'video_type': 1}, separators=(',', ':')),
                'type': 1, 'cur_production_type': 0, 'preferred_language': 'ai-zh', 'playlist_switch': 0})
            tracks = self._subtitle_tracks(data)
            source = 'subtitle_web_view'
        try:
            items = []
            for track in tracks[:3]:
                url = track['subtitle_url']
                if url.startswith('//'):
                    url = 'https:' + url
                validate_url(url, PUBLIC_HOSTS)
                with self.dispatch() as account:
                    try:
                        raw = self.transport.request('GET', url, headers={'Accept':'application/json'}, max_bytes=1_000_000)
                    except Exception:
                        raise APIError('Subtitle transport failed') from None
                    if raw.status != 200:
                        if raw.status in (401, 412, 429):
                            self.store.pause(account, reason='Subtitle HTTP risk', manual=raw.status == 401)
                        raise APIError('Subtitle request rejected', status=raw.status)
                    cues = raw.json().get('body')
                if not isinstance(cues, list):
                    raise APIError('Invalid subtitle response')
                segments, total = [], 0
                for cue in cues[:2000]:
                    start, end = _number(cue['from']), _number(cue['to'])
                    if end < start or end > 86400 or not isinstance(cue['content'], str):
                        raise APIError('Invalid subtitle timestamp or text')
                    text = cue['content'][:1000]
                    if total + len(text) > 60000:
                        break
                    total += len(text)
                    segments.append({'start':start, 'end':end, 'text':text})
                items.append({'language':str(track.get('lan', ''))[:40], 'segments':segments, 'untrusted':True})
        except (KeyError, TypeError, AttributeError):
            raise APIError('Invalid subtitle response') from None
        return {'bvid':bvid, 'available':bool(items), 'items':items, 'source':source,
                'reason':'' if items else 'No subtitles available', 'untrusted':True}

    def _claimed(self, draft, kind):
        if not self.settings.writes_enabled or not self.settings.owner_mid or not self._credentials.get('bili_jct'):
            raise APIError('Writes require owner configuration and CSRF credentials')
        if not isinstance(draft, dict) or not isinstance(draft.get('operation_id'), str):
            raise APIError('A claimed approved operation is required')
        try:
            original = self.store.operation(draft['operation_id'])
        except (KeyError, ValueError):
            raise APIError('Claimed operation not found') from None
        bound = ('id', 'operation_id', 'account', 'kind', 'target', 'text', 'reason', 'score', 'share', 'digest', 'state')
        if (any(k not in original or draft.get(k) != original[k] for k in bound)
                or original['state'] != 'sending' or original['kind'] != kind):
            raise APIError('Draft does not match claimed approved operation')
        _id(draft['account'])
        if type(draft['text']) is not str or not 1 <= len(draft['text']) <= (1000 if kind == 'comment' else 300):
            raise APIError('Invalid send text')
        if kind == 'comment':
            _bvid(draft['target'])
            if draft['share'] is not True:
                raise APIError('Comment must be an approved share')
        elif draft['target'] != self.settings.owner_mid or draft['account'] == self.settings.owner_mid or draft['share'] is not False:
            raise APIError('DM restricted to the configured owner')
        actual = self.account()['mid']
        if actual != draft['account']:
            raise APIError('Authenticated account does not match approved operation')
        return original

    def send_comment(self, draft):
        self._claimed(draft, 'comment')
        video = self.video(draft['target'])
        data = self._request('/x/v2/reply/add', {'type':1, 'oid':video['aid'], 'message':draft['text'],
                             'csrf':self._credentials['bili_jct']}, method='POST', write=True,
                             operation_id=draft['operation_id'])
        try:
            remote_id = _id(data['rpid'])
            if int(remote_id) <= 0:
                raise APIError('Missing accepted comment identifier; outcome unknown')
        except (KeyError, TypeError):
            raise APIError('Missing accepted comment identifier; outcome unknown') from None
        return {'remote_id':remote_id, 'accepted':True}

    def send_dm(self, draft):
        self._claimed(draft, 'dm')
        relation = self._request('/x/relation', {'fid':draft['target']})
        if not isinstance(relation, dict) or type(relation.get('attribute')) is not int or relation['attribute'] != 6:
            raise APIError('Owner DM requires mutual following')
        key = 'device:dm:' + draft['account']
        device = self.store.cache_get(key)
        if not isinstance(device, dict) or not re.fullmatch(r'[0-9A-F-]{36}', str(device.get('id', ''))):
            # A non-secret stable device identifier, persisted in plugin SQLite data.
            # Deterministic fallback means expiration/restart cannot rotate the device.
            import uuid
            namespace = uuid.UUID('0dd3e507-8bd9-4e02-8b44-14ce6ea3df48')
            device = {'id':str(uuid.uuid5(namespace, str(getattr(self.store, 'path', 'plugin-data')) + ':' + draft['account'])).upper()}
            self.store.cache_put(key, device, 86400)
        params = {'msg[sender_uid]':draft['account'], 'msg[receiver_id]':draft['target'],
                  'msg[receiver_type]':1, 'msg[msg_type]':1, 'msg[msg_status]':0,
                  'msg[content]':json.dumps({'content':draft['text']}, ensure_ascii=False),
                  'msg[dev_id]':device['id'], 'msg[timestamp]':int(time.time()),
                  'csrf':self._credentials['bili_jct'], 'csrf_token':self._credentials['bili_jct']}
        data = self._request('/web_im/v1/web_im/send_msg', params, method='POST', host='api.vc.bilibili.com',
                             write=True, operation_id=draft['operation_id'], dm=True)
        try:
            remote_id = _id(data['msg_key'])
            if int(remote_id) <= 0:
                raise APIError('Missing accepted message identifier; outcome unknown')
        except (KeyError, TypeError):
            raise APIError('Missing accepted message identifier; outcome unknown') from None
        return {'remote_id':remote_id, 'accepted':True}

    def verify(self, operation):
        remote_id = str(operation.get('remote_id', '')) if isinstance(operation, dict) else ''
        failed = {'verified':False, 'remote_id':remote_id, 'reason':'No matching readback evidence'}
        if not isinstance(operation, dict) or operation.get('kind') != 'comment':
            return dict(failed, reason='Reliable DM readback is unsupported')
        try:
            original = self.store.operation(operation['operation_id'])
            bound = ('id', 'operation_id', 'digest', 'state', 'account', 'kind', 'target', 'text', 'remote_id')
            if (any(k not in original or original[k] != operation.get(k) for k in bound)
                    or original.get('state') not in ('unverified', 'unknown', 'confirmed')):
                return failed
            # Readback evidence is bound to the durable accepted operation.
            operation = original
            bvid = _bvid(operation['target'])
            _id(remote_id)
            if int(remote_id) <= 0 or self.account()['mid'] != operation['account']:
                return failed
            video = self.video(bvid)
            data = self._request('/x/v2/reply/detail', {'type':1, 'oid':video['aid'], 'root':remote_id})
            root = data.get('root')
            if (isinstance(root, dict) and _id(root.get('rpid')) == remote_id
                    and _id(root.get('oid')) == video['aid'] and type(root.get('type')) is int and root['type'] == 1
                    and _id(root['member']['mid']) == operation['account']
                    and root['content']['message'] == operation['text']):
                return {'verified':True, 'remote_id':remote_id, 'reason':'Exact comment readback matched'}
        except (PolicyError, KeyError, TypeError, AttributeError, ValueError):
            return dict(failed, reason='Readback unavailable or invalid; not verified')
        return failed

    def download_public(self, url, target, **kwargs):
        from .media import download_public
        def check_response(raw):
            if raw.status in (401, 412, 429):
                account = self._account['mid'] if self._account else 'anonymous'
                self.store.pause(account, reason='Public media HTTP risk', manual=raw.status == 401)
        return download_public(url, target, dispatch=self.dispatch, on_response=check_response, **kwargs)

    def frames(self, bvid, output_dir):
        from .media import extract_frames
        result = extract_frames(self, _bvid(bvid), output_dir)
        return dict(result, sheet=result['path'], untrusted=True)

    def account(self):
        value = self._request('/x/web-interface/nav')
        if not isinstance(value, dict) or value.get('isLogin') is not True:
            raise APIError('Authenticated account required')
        account = {'mid': _id(value.get('mid')), 'name': str(value.get('uname', ''))[:200]}
        if self._account is not None and account['mid'] != self._account['mid']:
            raise APIError('Authenticated session identity changed; manual review required')
        self.store.require_active(account['mid'])
        if self._identity_key:
            self.store.identity_bind(self._identity_key, account['mid'])
        self._account = account
        return dict(account)
