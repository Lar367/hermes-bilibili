"""End-to-end offline fixture: real adapter, service, client, SQLite and approval."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hermes_bilibili.client import BiliClient, Response
from hermes_bilibili.core import Settings, Store
from hermes_bilibili.service import Service
from hermes_bilibili import integration


def response(data):
    return Response(200, {'Content-Type':'application/json'}, json.dumps({'code':0,'data':data}).encode())


class EndToEndTests(unittest.TestCase):
    def test_draft_operator_approval_send_readback_and_no_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'state.db'
            settings=Settings(owner_mid='42',writes_enabled=True)
            text='Explicitly approved fixture comment'
            nav={'isLogin':True,'mid':9,'uname':'Fixture'}
            video={'bvid':'BV1xx411c7mD','aid':7,'cid':8,'owner':{'mid':10},'duration':30}
            reply={'rpid':91,'oid':7,'type':1,'member':{'mid':9},'content':{'message':text}}
            responses=[response(nav),response(nav),response(nav),response(video),response({'rpid':91}),response(nav),response({'root':reply})]
            class Transport:
                calls=[]
                def request(self, method, url, **kwargs):
                    self.calls.append((method,url))
                    if not responses:
                        raise AssertionError('unexpected fixture request')
                    return responses.pop(0)
            transport=Transport()
            now = [1800000000.0]
            def sleep(delay):
                now[0] += delay
            def open_service(_):
                store=Store(path, clock=lambda: now[0])
                return Service(store,settings,client=BiliClient(store,settings,credentials={'sessdata':'fixture-session','bili_jct':'fixture-csrf'},transport=transport,sleep=sleep))
            with patch.object(integration,'open_service',side_effect=open_service):
                draft=json.loads(integration.invoke_tool(None,'bili_prepare',{'kind':'comment','target':'BV1xx411c7mD','text':text,'reason':'Fixture content supported by evidence','score':18}))['data']
                with StoreContext(path) as store:
                    self.assertEqual(store.get_draft(draft['id'])['state'],'draft')
                approved=json.loads(integration.operator_command(None,'approve '+draft['id']+' '+draft['digest']))
                self.assertEqual(approved['data']['state'],'approved')
                sent=json.loads(integration.invoke_tool(None,'bili_send',{'draft_id':draft['id']}))
                self.assertTrue(sent['success'],sent)
                self.assertEqual(sent['data']['state'],'confirmed')
                self.assertEqual(sent['data']['remote_id'],'91')
                replay=json.loads(integration.invoke_tool(None,'bili_send',{'draft_id':draft['id']}))
                self.assertFalse(replay['success'])
            self.assertEqual(sum(method=='POST' for method,url in transport.calls),1)
            self.assertEqual(responses,[])


class StoreContext:
    def __init__(self,path): self.store=Store(path)
    def __enter__(self): return self.store
    def __exit__(self,*args): self.store.close()


if __name__=='__main__':
    unittest.main()
