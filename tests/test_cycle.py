import importlib
from pathlib import Path
import tempfile
import unittest


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def planner(self, clock, rng=lambda: 0):
        try:
            module = importlib.import_module('hermes_bilibili.cycle')
        except ModuleNotFoundError:
            self.fail('persisted reading-cycle planner is not implemented')
        return module.CyclePlanner(Path(self.temp.name) / 'cycle.db', clock=clock, random=rng)

    def test_plan_persists_and_only_one_claim_is_allowed(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        stamp = datetime(2026, 10, 28, 21, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        settings = {'timezone': 'Asia/Shanghai', 'windows': [{'start':'20:00', 'end':'23:00'}], 'skip_chance': 0}
        first = self.planner(lambda: stamp)
        plan = first.start(settings)
        self.assertEqual(plan['state'], 'running')
        second = self.planner(lambda: stamp)
        repeated = second.start(settings)
        self.assertEqual(repeated['state'], 'running')
        self.assertFalse(repeated['claimed'])
        self.assertEqual(repeated['run_id'], plan['run_id'])
        first.complete(plan['run_id'], '仅记录实际读取的内容', ['BV1234567890'])
        self.assertEqual(second.start(settings)['state'], 'complete')

    def test_overnight_window_is_active_after_midnight(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        stamp = datetime(2026, 10, 29, 0, 30, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        plan = self.planner(lambda: stamp).start({'timezone':'Asia/Shanghai', 'windows':[{'start':'23:00','end':'01:00'}], 'skip_chance':0})
        self.assertEqual(plan['state'], 'running')
        self.assertTrue(plan['claimed'])

    def test_non_skipped_cycle_requires_evidence_and_is_not_replayed(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        stamp = datetime(2026, 10, 28, 21, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        planner = self.planner(lambda: stamp)
        plan = planner.start({'timezone':'Asia/Shanghai', 'windows':[{'start':'20:00','end':'23:00'}], 'skip_chance':0})
        with self.assertRaises(ValueError):
            planner.complete(plan['run_id'], 'nothing actually read', [])
        planner.complete(plan['run_id'], 'read evidence', ['evidence-1'])
        with self.assertRaises(ValueError):
            planner.complete(plan['run_id'], 'again', ['evidence-1'])

    def test_reading_same_overnight_window_does_not_run_again_after_midnight(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('Asia/Shanghai')
        now = [datetime(2026, 10, 28, 23, 30, tzinfo=zone).timestamp()]
        planner = self.planner(lambda: now[0])
        settings = {'timezone':'Asia/Shanghai','windows':[{'start':'23:00','end':'01:00'}],'skip_chance':0}
        first = planner.start(settings)
        planner.complete(first['run_id'], 'read actual material', ['e1'])
        now[0] = datetime(2026, 10, 29, 0, 30, tzinfo=zone).timestamp()
        second = planner.start(settings)
        self.assertFalse(second['claimed'])
        self.assertEqual(second['run_id'], first['run_id'])

    def test_simultaneous_cycle_claims_have_one_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime
        from zoneinfo import ZoneInfo
        stamp = datetime(2026, 10, 28, 21, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        planner = self.planner(lambda: stamp)
        settings = {'timezone':'Asia/Shanghai','windows':[{'start':'20:00','end':'23:00'}],'skip_chance':0}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: planner.start(settings), range(8)))
        self.assertEqual(sum(x['claimed'] for x in results),1)
        self.assertEqual(len({x['run_id'] for x in results}),1)

    def test_cycle_sidecar_symlink_is_rejected(self):
        directory = Path(self.temp.name)
        outside = directory / 'outside'
        outside.write_text('untouched')
        (directory / 'cycle.db-wal').symlink_to(outside)
        with self.assertRaises(ValueError):
            self.planner(lambda: 0)
        self.assertEqual(outside.read_text(),'untouched')

    def test_running_cycle_blocks_next_day_until_operator_review(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('Asia/Shanghai')
        now = [datetime(2026,10,28,21,tzinfo=zone).timestamp()]
        planner = self.planner(lambda: now[0])
        settings = {'timezone':'Asia/Shanghai','windows':[{'start':'20:00','end':'23:00'}],'skip_chance':0}
        first = planner.start(settings)
        now[0] = datetime(2026,10,29,21,tzinfo=zone).timestamp()
        second = planner.start(settings)
        self.assertFalse(second['claimed'])
        self.assertEqual(second['run_id'],first['run_id'])

    def test_cycle_storage_protects_existing_parent_permissions(self):
        import os
        directory = Path(self.temp.name)
        os.chmod(directory,0o755)
        self.planner(lambda:0)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_cycle_evidence_is_bound_to_run_and_cannot_be_reused(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        stamp = datetime(2026,10,28,21,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        planner = self.planner(lambda:stamp)
        plan = planner.start({'timezone':'Asia/Shanghai','windows':[{'start':'20:00','end':'23:00'}],'skip_chance':0})
        self.assertTrue(hasattr(planner,'record_evidence'))
        planner.record_evidence('evidence:real', {'action':'video','query':'BV1234567890'})
        self.assertTrue(planner.validate_evidence(plan['run_id'],['evidence:real']))
        self.assertFalse(planner.validate_evidence('other-run',['evidence:real']))
        self.assertFalse(planner.validate_evidence(plan['run_id'],['evidence:fake']))

    def test_operator_abort_is_audited_and_does_not_replay_same_day(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        stamp = datetime(2026,10,28,21,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        planner = self.planner(lambda:stamp)
        settings={'timezone':'Asia/Shanghai','windows':[{'start':'20:00','end':'23:00'}],'skip_chance':0}
        plan=planner.start(settings)
        self.assertTrue(hasattr(planner,'abort'))
        planner.abort(plan['run_id'],'人工确认中断，未完成浏览')
        self.assertEqual(planner.start(settings)['state'],'aborted')
        self.assertFalse(planner.start(settings)['claimed'])


if __name__ == '__main__':
    unittest.main()
