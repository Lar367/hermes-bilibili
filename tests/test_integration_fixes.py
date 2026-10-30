"""Audit regression coverage for public Hermes entry points; no real network."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from hermes_bilibili import integration
from hermes_bilibili.core import Settings, Store
from hermes_bilibili.cycle import CyclePlanner


class AdapterAuditFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_search_enforces_configured_candidate_ceiling(self):
        class Client:
            def search(self, query, limit=5):
                return {'items': list(range(limit)), 'untrusted': True}
        for ceiling, expected in ((1, 1), (3, 3)):
            with self.subTest(ceiling=ceiling):
                store = Store(self.root / 'state.db')
                service = SimpleNamespace(settings=Settings(max_candidates=ceiling), store=store, client=Client())
                with patch.object(integration, 'open_service', return_value=service):
                    output = json.loads(integration.invoke_tool(None, 'bili_read', {'action':'search', 'query':'fixture', 'limit':20}))
                self.assertTrue(output['success'], output)
                self.assertEqual(len(output['data']['items']), expected)


    def test_invalid_persona_does_not_claim_or_strand_cycle(self):
        (self.root / 'persona.md').write_bytes(b'\xff invalid utf8')
        store = Store(self.root / 'state.db')
        service = SimpleNamespace(settings=Settings(), store=store)
        start = CyclePlanner.start
        now = datetime(2026, 10, 29, 21, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()

        def fixed_start(planner, settings):
            planner.clock = lambda: now
            planner.random = lambda: 0
            return start(planner, settings)

        with patch.object(integration, 'open_service', return_value=service), patch.object(CyclePlanner, 'start', fixed_start):
            result = json.loads(integration.invoke_tool(None, 'bili_cycle', {'action':'start'}))
        self.assertFalse(result['success'])
        states = CyclePlanner(self.root / 'cycle.db').status()
        self.assertFalse(any(row['state'] == 'running' for row in states), states)


    def test_search_zero_candidates_refuses_before_network(self):
        store = Store(self.root / 'state.db')
        class Client:
            def search(self, *args, **kwargs):
                raise AssertionError('network must not be reached')
        service = SimpleNamespace(settings=Settings(max_candidates=0), store=store, client=Client())
        with patch.object(integration, 'open_service', return_value=service):
            result = json.loads(integration.invoke_tool(None, 'bili_read', {'action':'search', 'query':'fixture'}))
        self.assertFalse(result['success'])
        self.assertIn('max_candidates=0', result['error'])


    def test_search_default_limit_obeys_candidate_ceiling(self):
        store = Store(self.root / 'state.db')
        limits = []
        class Client:
            def search(self, query, limit=5):
                limits.append(limit)
                return {'items': list(range(limit)), 'untrusted': True}
        service = SimpleNamespace(settings=Settings(max_candidates=2), store=store, client=Client())
        with patch.object(integration, 'open_service', return_value=service):
            result = json.loads(integration.invoke_tool(None, 'bili_read', {'action':'search', 'query':'fixture'}))
        self.assertTrue(result['success'], result)
        self.assertEqual(limits, [2])

    def test_memory_context_failure_prevents_cycle_claim(self):
        store = Store(self.root / 'state.db')
        service = SimpleNamespace(settings=Settings(), store=store)
        with patch.object(integration, 'open_service', return_value=service), patch.object(store, 'memories', side_effect=ValueError('corrupt memory')), patch.object(CyclePlanner, 'start') as start:
            result = json.loads(integration.invoke_tool(None, 'bili_cycle', {'action':'start'}))
        self.assertFalse(result['success'])
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
