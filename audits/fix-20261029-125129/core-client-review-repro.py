"""Offline independent review probes. No real profiles, credentials or network.
Run: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/admin/projects/hermes-bilibili \
  /home/admin/projects/hermes-bilibili/.venv/bin/python -B audits/fix-20261029-125129/core-client-review-repro.py
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from hermes_bilibili.core import Store, Settings, PolicyError
from hermes_bilibili.client import BiliClient, HTTPTransport, APIError


def blocked(action, expected):
    try:
        action()
    except PolicyError as exc:
        assert expected.lower() in str(exc).lower(), str(exc)
        return str(exc)
    raise AssertionError('Expected fail-closed rejection: ' + expected)


def main():
    results = {}
    now = [1800000000.0]
    with tempfile.TemporaryDirectory(prefix='core-client-review-', dir=os.environ.get('TMPDIR')) as root:
        path = Path(root) / 'state.db'
        settings = Settings(owner_mid='99', writes_enabled=True, timezone='UTC')
        first = Store(path, clock=lambda: now[0])
        second = Store(path, clock=lambda: now[0])
        try:
            a = first.reserve_request('42', settings)
            first.validate_dispatch('42', settings, request_id=a.request_id)
            b = second.reserve_request('43', settings)
            now[0] += 10
            results['cross_connection_guard'] = blocked(
                lambda: second.validate_dispatch('43', settings, request_id=b.request_id), 'in flight')
            first.close()
            first = Store(path, clock=lambda: now[0])
            results['restart_guard_persists'] = first.status()['write_in_flight']
            assert results['restart_guard_persists']
            first.finish_dispatch(request_id=a.request_id)
            results['completion_spacing'] = blocked(
                lambda: second.validate_dispatch('43', settings, request_id=b.request_id), 'spacing')
            now[0] += 2
            second.validate_dispatch('43', settings, request_id=b.request_id)
            second.finish_dispatch(request_id=b.request_id)

            draft = first.create_draft('42', 'comment', 'BV1xx411c7mD', 'fixture',
                                       reason='fixture original evidence', score=18)
            first.approve_draft(draft['id'], draft['digest'])
            operation = first.claim_send(draft['id'], '42', settings)
            operation = first.finish_send(operation['operation_id'], 'unverified', remote_id='91')

            class NoNetwork:
                def request(self, *args, **kwargs):
                    raise AssertionError('Unexpected transport invocation')

            def sleep(delay):
                now[0] += delay

            api = BiliClient(first, settings, transport=NoNetwork(), sleep=sleep)
            assert not api.verify(dict(operation, remote_id='92'))['verified']
            results['real_store_remote_binding'] = 'rejected before transport'
            key = 'identity:' + hashlib.sha256(b'SYNTHETIC').hexdigest()
            first.identity_bind(key, '42')
            first.pause('42', reason='fixture pause', manual=True)
            now[0] += 86401
            api = BiliClient(second, settings, credentials={'sessdata':'SYNTHETIC'},
                             transport=NoNetwork(), sleep=sleep)
            results['permanent_identity_pause'] = blocked(api.account, 'paused')
        finally:
            first.close()
            second.close()

    # Both individual blocking operations complete before the supplied timeout,
    # but the unchanged per-operation timeout permits total elapsed time > 20s.
    clock = [0.0]

    class Raw:
        status, headers = 200, {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            raise AssertionError('Expected read1')
        def read1(self, size):
            clock[0] += 19.0
            return b'x'

    class Opener:
        def open(self, request, timeout):
            assert timeout == 20
            clock[0] += 19.0
            return Raw()

    transport = HTTPTransport(clock=lambda: clock[0])
    transport.opener = Opener()
    error = blocked(lambda: transport.request('GET', 'https://api.bilibili.com/x'), 'deadline')
    results['total_deadline_not_strict'] = {'elapsed':clock[0], 'error':error, 'configured_seconds':20}
    assert clock[0] == 38.0
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
