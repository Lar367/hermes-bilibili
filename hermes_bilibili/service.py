"""Workflow boundary: draft approval and readback are not prompt conventions."""


class Service:
    def __init__(self, store, settings, *, client):
        self.store, self.settings, self.client = store, settings, client

    def prepare(self, kind, target, text, reason, score):
        account = self.client.account()['mid']
        if kind == 'dm' and target != self.settings.owner_mid:
            raise ValueError('private messages may only target the configured owner')
        return self.store.create_draft(account, kind, target, text, reason=reason, score=score, share=kind == 'comment')

    def send(self, draft_id):
        account = self.client.account()['mid']
        draft = self.store.claim_send(draft_id, account, self.settings)
        operation_id = draft['operation_id']
        try:
            result = self.client.send_comment(draft) if draft['kind'] == 'comment' else self.client.send_dm(draft)
        except Exception as exc:
            # A nonzero parsed business code is a definitive API rejection, not a timeout.
            code = getattr(exc, 'code', None)
            definitive = getattr(exc, 'definitive', False) is True or (type(code) is int and code != 0)
            return self.store.finish_send(operation_id, 'failed' if definitive else 'unknown', error='平台明确拒绝；本草稿不可重发' if definitive else '发送未确认；禁止重发，请核查')
        remote_id = str(result.get('remote_id', ''))
        accepted = self.store.finish_send(operation_id, 'unverified', remote_id=remote_id, error='接口已接受，等待回读核查')
        try:
            check = self.client.verify(accepted)
        except Exception:
            check = {'verified': False, 'reason': '回读失败，未确认可见性'}
        if check.get('verified') is True:
            return self.store.finish_send(operation_id, 'confirmed', remote_id=remote_id)
        return {**accepted, 'verification':check}

    def verify(self, operation_id):
        operation = self.store.operation(operation_id)
        account = self.client.account()['mid']
        if account != operation['account']:
            raise ValueError('account changed; verification refused')
        check = self.client.verify(operation)
        if check.get('verified') is True:
            return self.store.finish_send(operation_id, 'confirmed', remote_id=str(check.get('remote_id', operation.get('remote_id', ''))))
        return {'operation_id': operation_id, 'verified': False, 'reason': check.get('reason', '没有匹配的回读证据'), 'retry_send': False}
