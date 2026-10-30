import importlib
from pathlib import Path
import tempfile
import unittest


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_model_tool_rejects_unknown_fields_before_opening_service(self):
        try:
            module = importlib.import_module('hermes_bilibili.integration')
        except ModuleNotFoundError:
            self.fail('Hermes adapter is not implemented')
        class Context:
            def get_config(self, *a, **kw):
                raise AssertionError('should reject before loading config')
        import json
        output = json.loads(module.invoke_tool(Context(),'bili_send',{'draft_id':'a', 'approve':True}))
        self.assertFalse(output['success'])
        self.assertEqual(output['error_code'],'invalid_arguments')

    def test_status_is_offline_and_closes_per_call_store(self):
        from hermes_bilibili import integration
        from unittest.mock import patch
        import json
        class Store:
            closed = False
            def status(self):
                return {'draft_count': 0}
            def close(self):
                self.closed = True
        class Service:
            def __init__(self):
                self.store = Store()
                self.settings = type('Settings', (), {'writes_enabled':False})()
        service = Service()
        with patch.object(integration, 'open_service', create=True, return_value=service):
            result = json.loads(integration.invoke_tool(None, 'bili_status', {}))
        self.assertTrue(result['success'])
        self.assertFalse(result['data']['writes_enabled'])
        self.assertTrue(service.store.closed)

    def test_operator_approval_requires_exact_digest_and_never_appears_as_tool(self):
        from hermes_bilibili import integration
        from unittest.mock import patch
        class Store:
            def approve_draft(self, ident, digest):
                return {'id':ident,'digest':digest,'state':'approved'}
            def close(self):
                pass
        service = type('Service', (), {'store':Store()})()
        with patch.object(integration, 'open_service', create=True, return_value=service):
            import json
            result = json.loads(integration.operator_command(None, 'approve d1 exactdigest'))
        self.assertEqual(result['data']['digest'], 'exactdigest')
        self.assertNotIn('bili_approve', integration.ALLOWED)

    def test_dispatch_read_prepare_send_verify_memory_and_cycle(self):
        from hermes_bilibili import integration
        from unittest.mock import patch
        import json
        class Store:
            def __init__(self): self.cache = {}
            def close(self): pass
            def cache_put(self, key, value, ttl): self.cache[key] = value
            def remember(self, kind, text, **kw): return {'kind':kind,'text':text}
            def memories(self, **kw): return []
        class Client:
            def video(self, query): return {'bvid':query,'untrusted':True}
        class Service:
            store = Store()
            client = Client()
            def prepare(self, *args): return {'state':'draft'}
            def send(self, ident): return {'state':'unverified'}
            def verify(self, ident): return {'verified':False}
        service = Service()
        service.store.path = self.root/'state.db'
        with patch.object(integration, 'open_service', return_value=service):
            for name, args in [
                ('bili_read',{'action':'video','query':'BV1234567890'}),
                ('bili_prepare',{'kind':'comment','target':'BV1234567890','text':'x','reason':'理由足够长且有意义','score':15}),
                ('bili_send',{'draft_id':'d'}), ('bili_verify',{'operation_id':'o'}),
                ('bili_memory',{'action':'append','kind':'public','text':'实际经历'}),
                ('bili_memory',{'action':'list'}),
            ]:
                with self.subTest(name=name):
                    result = json.loads(integration.invoke_tool(None,name,args))
                    self.assertTrue(result['success'], result)
                    if name == 'bili_read':
                        self.assertTrue(result['data']['evidence_id'].startswith('evidence:'))
                        self.assertIn(result['data']['evidence_id'], service.store.cache)

    def test_cycle_completion_rejects_fabricated_evidence(self):
        from hermes_bilibili import integration
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory() as temp:
            class Store:
                path = Path(temp) / 'state.db'
                def close(self): pass
                def cache_get(self, key): return None
            service = type('Service', (), {'store':Store()})()
            with patch.object(integration, 'open_service', return_value=service):
                output = json.loads(integration.invoke_tool(None,'bili_cycle',{'action':'complete','run_id':'x','summary':'not read','evidence':['fabricated']}))
            self.assertFalse(output['success'])
            self.assertIn('evidence', output['error'])

    def test_role_context_has_bounded_persona_and_private_memory_without_global_prompt_changes(self):
        from hermes_bilibili import integration
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'persona.md').write_text('角色背景' * 100)
            class Store:
                path = directory / 'state.db'
                def memories(self, **kw): return [{'kind':'private','text':'实际记录'}]
            service = type('Service', (), {'store':Store(),'settings':type('Settings', (), {'persona_max_chars':20})()})()
            context = integration.role_context(service)
            self.assertEqual(len(context['persona']), 20)
            self.assertEqual(context['memories'][0]['kind'], 'private')
            self.assertIn('不可信', context['boundary'])

    def test_cli_refuses_noninteractive_approval_and_exits_nonzero(self):
        from hermes_bilibili import integration
        from unittest.mock import patch
        with patch('sys.stdin.isatty', return_value=False), patch('sys.stdout.isatty', return_value=False):
            code = integration.cli_command(None, ['approve','id','digest'])
        self.assertEqual(code, 2)

    def test_frames_dispatch_preserves_media_boundary(self):
        from hermes_bilibili import integration
        from unittest.mock import patch
        import json
        class Store:
            def close(self): pass
            def cache_put(self, *a): pass
        class Client:
            def frames(self, bvid, output_dir):
                return {'sheet':str(output_dir / 'frames.jpg'),'untrusted':True}
        service = type('Service', (), {'store':Store(),'client':Client()})()
        service.store.path = self.root/'state.db'
        with patch.object(integration,'open_service',return_value=service):
            result = json.loads(integration.invoke_tool(None,'bili_frames',{'bvid':'BV1234567890'}))
        self.assertTrue(result['success'], result)
        self.assertTrue(result['data']['untrusted'])

    def test_bad_types_missing_fields_and_unknown_actions_are_rejected_before_io(self):
        from hermes_bilibili import integration
        from unittest.mock import patch
        import json
        with patch.object(integration,'open_service',side_effect=AssertionError('must not open')):
            for name, args in [
                ('bili_read',{'action':'shell','query':'x'}),
                ('bili_read',{'action':'search','query':'x','limit':True}),
                ('bili_prepare',{'kind':'comment'}),
                ('bili_send',{}),
                ('bili_cycle',{'action':'complete','run_id':True,'summary':'x','evidence':[]}),
                ('bili_memory',{'action':'append','kind':'private','text':{'x':'not text'}}),
            ]:
                with self.subTest(name=name,args=args):
                    output = json.loads(integration.invoke_tool(None,name,args))
                    self.assertEqual(output.get('error_code'),'invalid_arguments',output)

    def test_completion_rejects_actual_but_other_run_evidence(self):
        from hermes_bilibili import integration
        from hermes_bilibili.cycle import CyclePlanner
        from unittest.mock import patch
        import json
        planner=CyclePlanner(self.root/'cycle.db')
        planner.record_evidence('evidence:outside',{'action':'video'})
        class Store:
            path=self.root/'state.db'
            def close(self):pass
            def cache_get(self,key): return {'action':'video'}
        service=type('Service',(),{'store':Store()})()
        with patch.object(integration,'open_service',return_value=service), patch.object(CyclePlanner,'complete') as complete:
            result=json.loads(integration.invoke_tool(None,'bili_cycle',{'action':'complete','run_id':'different-run','summary':'真实但不属于本轮','evidence':['evidence:outside']}))
        self.assertFalse(result['success'])
        complete.assert_not_called()

    def test_operator_recovery_is_not_model_tool_and_is_audited(self):
        from hermes_bilibili import integration
        from unittest.mock import patch
        from hermes_bilibili.cycle import CyclePlanner
        import json
        class Store:
            path=self.root/'state.db'
            resumed=None
            def resume(self, account): self.resumed=account
            def close(self):pass
        service=type('Service',(),{'store':Store()})()
        with patch.object(integration,'open_service',return_value=service), patch.object(CyclePlanner,'abort',return_value={'state':'aborted'}) as abort:
            result=json.loads(integration.operator_command(None,'cycle-abort r1 "人工确认中断"'))
            self.assertTrue(result['success'])
            abort.assert_called_once_with('r1','人工确认中断')
            result=json.loads(integration.operator_command(None,'resume 111'))
            self.assertTrue(result['success'])
            self.assertEqual(service.store.resumed,'111')

    def test_completed_cycle_reports_secondary_journal_failure_without_claiming_failure(self):
        from hermes_bilibili import integration
        from hermes_bilibili.cycle import CyclePlanner
        from unittest.mock import patch
        import json
        class Store:
            path=self.root/'state.db'
            def close(self):pass
            def remember(self,*a,**kw):raise OSError('fixture disk issue')
        service=type('Service',(),{'store':Store()})()
        with patch.object(integration,'open_service',return_value=service), patch.object(CyclePlanner,'validate_evidence',return_value=True), patch.object(CyclePlanner,'complete',return_value={'state':'complete'}):
            result=json.loads(integration.invoke_tool(None,'bili_cycle',{'action':'complete','run_id':'r','summary':'已跳过，没有编造浏览','evidence':[]}))
        self.assertTrue(result['success'],result)
        self.assertFalse(result['data']['journal_written'])


if __name__ == '__main__':
    unittest.main()
